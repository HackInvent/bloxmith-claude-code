"""Claude CLI adapter using only the public BloxSmith block contract."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from bloxsmith_app.block_api import BlockRuntimeOutput, BlockRuntimeResult, TEXT_PLAIN
from .session_state import ClaudeError, ClaudeCancelled, SessionFile, check_cancel, session_id

MAX_ACTIVATION_SECONDS = 240  # Leave cleanup time before the managed hook's 300s limit.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
PERMISSIONS = ("default", "dontAsk", "plan", "acceptEdits", "auto")


def boolean(value):
    """Normalize authored checkbox values without treating 'false' as true."""
    return value is True or value == 1 or str(value).lower() in {"true", "yes", "on"}


def normalize_config(raw, model):
    """Validate authored settings before processes, persistence or MCP resolution."""
    config = {**model.get("config", {}), **(raw or {})}
    for key in ("working_directory_input_enabled", "use_persistent_session", "dangerously_allow_all"):
        config[key] = boolean(config[key])
    for key in ("claude_binary", "working_directory", "model", "effort", "permission_mode", "session_generation"):
        config[key] = str(config.get(key) or "").strip()
        if "\x00" in config[key] or "\n" in config[key]:
            raise ClaudeError(f"Invalid {key}.")
    if not config["claude_binary"]:
        raise ClaudeError("Claude executable is required.")
    if config["model"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127}", config["model"]):
        raise ClaudeError("Invalid Claude model identifier.")
    if config["effort"] not in model.get("effort_catalog", [""]):
        raise ClaudeError("Unknown Claude effort. See effort_catalog in model.json.")
    if config["permission_mode"] not in PERMISSIONS:
        raise ClaudeError("Unknown permission mode. Bypass requires the explicit danger checkbox.")
    for key, default, upper in (("timeout_sec", 240, 240), ("max_prompt_chars", 250000, 1000000)):
        try:
            value = int(config.get(key, default))
        except (ValueError, TypeError) as error:
            raise ClaudeError(f"{key} must be an integer.") from error
        if value < 1:
            raise ClaudeError(f"{key} must be positive.")
        # Older releases allowed 7200 seconds, beyond the host's execution limit.
        config[key] = min(value, upper)
    refs = config.get("mcp_refs", [])
    if not isinstance(refs, list) or len(refs) > 32 or any(
            not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", ref) for ref in refs):
        raise ClaudeError("mcp_refs must contain at most 32 MCP registry references.")
    config["mcp_refs"] = list(dict.fromkeys(refs))
    mappings = config.get("instruction_inputs", {})
    if not isinstance(mappings, dict) or any(not str(k).isdigit() or not str(v).isdigit() for k, v in mappings.items()):
        raise ClaudeError("Instruction sources must map output IDs to input IDs.")
    config["instruction_inputs"] = {str(k): str(v) for k, v in mappings.items()}
    if len(config["session_generation"]) > 64:
        raise ClaudeError("Invalid session generation.")
    return config


def is_working_directory_port(port):
    """Recognize the native control type or the historical name on graph/runtime ports."""
    read = port.get if isinstance(port, dict) else lambda key, default=None: getattr(port, key, default)
    types = read("accepts") or read("types") or ()
    return (str(read("name", "")).strip().lower() == "working_directory"
            or "config/working-directory" in types)


def directory_input_enabled(config, ports):
    """Like Codex, keep an existing control port active even if its config flag is stale."""
    return boolean(config.get("working_directory_input_enabled")) or any(is_working_directory_port(p) for p in ports)


def working_directory(context, config):
    """Resolve input > explicit config, validate it, and never silently choose a process cwd."""
    value = str(config.get("working_directory") or "").strip()
    if directory_input_enabled(config, context.input_ports):
        for port in context.input_ports:
            if not is_working_directory_port(port):
                continue
            incoming = context.input_value(port.id, port.name, default="")
            if incoming is not None and not isinstance(incoming, str):
                raise ClaudeError("The working_directory input must contain a directory path as text.")
            if incoming and incoming.strip():
                value = incoming.strip()
                break
    if not value:
        raise ClaudeError("Working directory is required. Select a directory or provide it through the working_directory input.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = context.root_dir / path
    path = path.resolve()
    if not path.is_dir():
        raise ClaudeError("Working directory does not exist or is not a directory.")
    return path


def build_prompt(context, port, config):
    """Preserve stable port IDs, JSON, zero and false values; exclude control data."""
    mapping = config["instruction_inputs"]
    source_id = mapping.get(str(port.id))
    instruction = str(getattr(port, "instruction", "") or "")
    if source_id:
        source = next((p for p in context.input_ports if str(p.id) == source_id), None)
        if source is None:
            raise ClaudeError(f"Instruction input {source_id} is missing for output {port.id}.")
        supplied = context.input_value(source.id, source.name, default="")
        if supplied is not None and supplied != "":
            if not isinstance(supplied, str):
                raise ClaudeError("An instruction input must contain text.")
            if supplied.strip():
                instruction = supplied
    sections = []
    for input_port in context.input_ports:
        if str(input_port.id) in mapping.values() or is_working_directory_port(input_port):
            continue
        value = context.input_value(input_port.id, input_port.name, default=None)
        if value is None or value == "":
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        sections.append(f"[BEGIN INPUT {input_port.name}]\n{text}\n[END INPUT {input_port.name}]")
    if instruction.strip():
        sections.append("Instruction:\n\n" + instruction.strip())
    prompt = "\n\n".join(sections)
    if not prompt:
        raise ClaudeError("Claude Code prompt is empty.")
    if len(prompt) > config["max_prompt_chars"]:
        raise ClaudeError(f"Claude Code prompt too long: {len(prompt)} characters; limit {config['max_prompt_chars']}.")
    return prompt


def resolve_mcp(context, refs):
    """Translate application HTTP MCP references into an ephemeral Claude config."""
    resolver = context.services.get("resolve_mcp_reference")
    if refs and not callable(resolver):
        raise ClaudeError("The MCP registry resolver is unavailable.")
    servers, sensitive = {}, []
    for ref in refs:
        try:
            item = resolver(ref)
        except Exception as error:
            # Resolver diagnostics may contain connection data; do not echo them.
            raise ClaudeError(f"MCP reference '{ref}' is unavailable. Check Application Settings.") from error
        if not isinstance(item, dict) or not str(item.get("url", "")).startswith(("http://", "https://")):
            raise ClaudeError(f"MCP reference '{ref}' has an invalid HTTP configuration.")
        headers = item.get("http_headers") or {}
        if not isinstance(headers, dict):
            raise ClaudeError(f"MCP reference '{ref}' has invalid headers.")
        servers[ref] = {"type": "http", "url": str(item["url"]),
                        "headers": {str(k): str(v) for k, v in headers.items()}}
        sensitive.extend([str(item["url"]), *[str(v) for v in headers.values() if v]])
    return {"mcpServers": servers}, sensitive


def redact(context, value, sensitive):
    """Never persist MCP connection parameters in diagnostics or output metadata."""
    text = str(value or "")
    for token in sorted(set(sensitive), key=len, reverse=True):
        text = text.replace(token, "[redacted]")
    return context.redact_secrets(text)


def run_cli(context, args, *, prompt, directory, deadline, mcp):
    """Bound stdout/stderr on disk, use stdin and tie process lifetime to the host."""
    guard = Path(__file__).with_name("cli_guard.py")
    read_fd, write_fd = os.pipe()
    process = None
    try:
        with tempfile.TemporaryFile() as settings, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            settings.write(json.dumps(mcp).encode())
            settings.flush()
            settings.seek(0)
            call = args + ["--strict-mcp-config", "--mcp-config", f"/dev/fd/{settings.fileno()}"]
            process = subprocess.Popen(
                [sys.executable, "-I", str(guard), str(read_fd), str(settings.fileno()), *call],
                cwd=directory, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                pass_fds=(read_fd, settings.fileno()))
            os.close(read_fd)
            read_fd = -1
            sent = False
            while True:
                check_cancel(context)
                if time.monotonic() >= deadline:
                    raise ClaudeError("Claude Code timed out. No partial answer was published.")
                if os.fstat(stdout.fileno()).st_size > MAX_RESPONSE_BYTES or os.fstat(stderr.fileno()).st_size > MAX_RESPONSE_BYTES:
                    raise ClaudeError("Claude Code response exceeded the 4 MiB limit.")
                try:
                    process.communicate(input=None if sent else prompt.encode(), timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    sent = True
            if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > MAX_RESPONSE_BYTES:
                raise ClaudeError("Claude Code response exceeded the 4 MiB limit.")
            stdout.seek(0)
            stderr.seek(0)
            return process.returncode, stdout.read(MAX_RESPONSE_BYTES + 1).decode("utf-8", "replace"), stderr.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")
    finally:
        # Closing the lifetime pipe stops only the guard's private CLI group.
        os.close(write_fd)
        if read_fd >= 0:
            os.close(read_fd)
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def initialize(context, model):
    """Restore/forget the association at Run, without invoking Claude or emitting data."""
    try:
        config = normalize_config(context.config, model)
        if not config["use_persistent_session"] and not callable(context.services.get("get_block_storage_dir")):
            return BlockRuntimeResult()
        try:
            state = SessionFile(context)
        except Exception as error:
            if not config["use_persistent_session"] and "block_storage.missing_scope" in str(error):
                return BlockRuntimeResult()
            raise
        with state.locked(context, time.monotonic() + 5):
            record = state.read()
            # At Run, a dynamic input may not have received anything yet. Restore
            # the association now; the execution validates the actual directory.
            directory = record.get("working_directory", "") if directory_input_enabled(config, context.input_ports) else str(working_directory(context, config))
            identifier = state.reusable(record, directory=directory, generation=config["session_generation"]) if config["use_persistent_session"] else ""
            if record and not identifier:
                state.save("", directory=directory, generation=config["session_generation"])
            setter = context.services.get("set_node_runtime_value")
            if callable(setter):
                setter("claude_session_id", identifier)
            return BlockRuntimeResult(metadata={"claude_session_id": identifier})
    except ClaudeCancelled:
        return BlockRuntimeResult(status="cancelled")
    except Exception as error:
        return BlockRuntimeResult(status="failed", exit_code=1, error=str(error), logs=[f"[claude-code-init-error] {error}"])


def final_result(raw):
    """Accept the CLI's compact object or verbose JSON event array, never deltas."""
    try:
        result = json.loads(raw)
    except ValueError as error:
        raise ClaudeError("Claude Code returned invalid JSON. Update the CLI; no raw output was published.") from error
    if isinstance(result, list):
        finals = [item for item in result if isinstance(item, dict) and item.get("type") == "result"
                  and not item.get("parent_tool_use_id")]
        if len(finals) != 1:
            raise ClaudeError("Claude Code did not return exactly one final result.")
        result = finals[0]
    if not isinstance(result, dict) or result.get("type") != "result":
        raise ClaudeError("Claude Code did not return a final result.")
    return result


def execute(context, model):
    """Run outputs sequentially with one instance session and final text only."""
    records, outputs, logs, sensitive = [], [], [], []
    identifier = ""
    started = time.monotonic()
    metadata = {}
    try:
        config = normalize_config(context.config, model)
        directory = working_directory(context, config)
        prompts = [(port, build_prompt(context, port, config)) for port in context.output_ports]
        if not prompts:
            raise ClaudeError("At least one output is required.")
        check_cancel(context)
        mcp, sensitive = resolve_mcp(context, config["mcp_refs"])
        persistent = config["use_persistent_session"]
        # Stateless calls do not require instance scope. A scoped call still clears
        # a previously persisted association when the checkbox is disabled.
        state = None
        if persistent:
            state = SessionFile(context)
        elif callable(context.services.get("get_block_storage_dir")):
            try:
                state = SessionFile(context)
            except Exception as error:
                if "block_storage.missing_scope" not in str(error):
                    raise
        deadline = started + MAX_ACTIVATION_SECONDS
        with state.locked(context, deadline) if state else nullcontext():
            if state:
                record = state.read()
                identifier = state.reusable(record, directory=directory, generation=config["session_generation"]) if persistent else ""
                if not persistent:
                    state.save("", directory=directory, generation=config["session_generation"])
            setter = context.services.get("set_node_runtime_value")
            if callable(setter):
                setter("claude_session_id", identifier)
            metadata["claude_session_id"] = identifier
            for port, prompt in prompts:
                check_cancel(context)
                args = [config["claude_binary"], "-p", "--output-format", "json", "--permission-mode",
                        "bypassPermissions" if config["dangerously_allow_all"] else config["permission_mode"],
                        "--permission-prompts", "none"]
                for key in ("model", "effort"):
                    if config[key]:
                        args.extend([f"--{key}", config[key]])
                if persistent:
                    if identifier:
                        args.extend(["--resume", identifier])
                    else:
                        identifier = str(uuid4())
                        args.extend(["--session-id", identifier])
                else:
                    args.append("--no-session-persistence")
                command = shlex.join(args + ["--strict-mcp-config", "--mcp-config", "<ephemeral>"]) + " < <prompt via stdin>"
                metadata.update(last_claude_command=command, model=config["model"], effort=config["effort"], working_directory=str(directory))
                logs.append(f"[claude-code-cmd] {context.node_id}.{port.id}: {command}")
                code, raw, stderr = run_cli(context, args, prompt=prompt, directory=directory,
                    deadline=min(deadline, time.monotonic() + config["timeout_sec"]), mcp=mcp)
                logs.append(f"[claude-code-exit] {context.node_id}.{port.id}: exit_code={code}")
                if code:
                    raise ClaudeError(redact(context, stderr, sensitive).strip()[:4000] or f"Claude Code exited with code {code}.")
                if len(raw.encode()) > MAX_RESPONSE_BYTES:
                    raise ClaudeError("Claude Code response exceeded the 4 MiB limit.")
                result = final_result(raw)
                if result.get("is_error") or result.get("subtype") not in {None, "success"}:
                    raise ClaudeError(redact(context, result.get("result") or "Claude Code could not complete the task.", sensitive)[:4000])
                text = result.get("result")
                if not isinstance(text, str):
                    raise ClaudeError("Claude Code final result is not text.")
                check_cancel(context)
                if persistent:
                    returned_id = session_id(result.get("session_id"))
                    if returned_id != identifier:
                        raise ClaudeError("Claude Code returned a different session. The saved association was not changed.")
                    state.save(identifier, directory=directory, generation=config["session_generation"])
                metadata["claude_session_id"] = identifier if persistent else ""
                if callable(setter):
                    setter("claude_session_id", identifier if persistent else "")
                usage = result.get("usage") or {}
                safe_usage = {k: v for k, v in usage.items() if k in {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"} and isinstance(v, (int, float))} if isinstance(usage, dict) else {}
                records.append({"port_id": port.id, "usage": safe_usage, "prompt_chars": len(prompt)})
                outputs.append(BlockRuntimeOutput(port_id=port.id, port_name=port.name,
                    value=redact(context, text, sensitive), content_type=TEXT_PLAIN,
                    metadata={**metadata, "usage": safe_usage, "prompt_chars": len(prompt)}))
        metadata.update(outputs=records, duration=round(time.monotonic() - started, 3))
        logs.append(f"[done] Claude Code {context.node_id}: {len(outputs)} output(s) emitted.")
        return BlockRuntimeResult(outputs=outputs, logs=logs, metadata=metadata,
                                  last_message=outputs[-1].value, worker_received=outputs[-1].value)
    except ClaudeCancelled as error:
        return BlockRuntimeResult(status="cancelled", logs=logs + [f"[claude-code-cancelled] {context.node_id}"], metadata=metadata)
    except Exception as error:
        message = redact(context, str(error), sensitive)[:4000]
        return BlockRuntimeResult(status="failed", error=message, exit_code=1, last_message=message,
            logs=logs + [f"[claude-code-error] {context.node_id}: {message}"], metadata=metadata)
