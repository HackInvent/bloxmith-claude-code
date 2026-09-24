"""Autonomous Claude Code block: public runtime, UI bindings and release assets."""

from html import escape
import json

from bloxsmith_app.block_api import (
    BlockDefinition, render_inspector_template, render_node_card_template,
    render_path_browser_control,
)
from .runtime import (execute, initialize, normalize_config, boolean, PERMISSIONS,
                      is_working_directory_port, directory_input_enabled)


class ClaudeCodeBlock(BlockDefinition):
    """One Claude CLI call per output, with optional instance-scoped continuation."""

    kind = "claude_code"

    def initialize_runtime(self, context):
        """Restore the session identifier before any business input arrives."""
        return initialize(context, self.model)

    def execute_runtime(self, context):
        """Use the identical generic adapter in centralized and active runtimes."""
        return execute(context, self.model)

    def normalize_config(self, config):
        """Expose strict block-owned configuration validation for tooling/tests."""
        return normalize_config(config, self.model)

    def text(self, key, fallback):
        """Mark static UI text for block-owned English/French localization."""
        full = f"block.claude_code.{key}"
        return f'<span data-i18n="{full}">{escape(self.translate(full, fallback=fallback))}</span>'

    def field(self, config, key, title, *, kind="text", attrs="", options=None):
        """Render accessible generic config bindings, including inline checkboxes."""
        value = config.get(key, "")
        label = self.text(key, title)
        binding = f'data-block-config-field="{key}"'
        if kind == "checkbox":
            return f'<label class="claude-check"><input type="checkbox" {binding}{" checked" if boolean(value) else ""}>{label}</label>'
        if options is not None:
            control = f'<select {binding}>' + "".join(
                f'<option value="{escape(str(k), quote=True)}"{" selected" if str(k) == str(value) else ""}>{escape(str(v))}</option>'
                for k, v in options) + '</select>'
        else:
            numeric = 'data-block-value-type="integer"' if kind == "number" else ""
            control = f'<input type="{kind}" {binding} {numeric} {attrs} value="{escape(str(value), quote=True)}">'
        return f'<label class="field-group">{label}{control}</label>'

    def _config(self, node):
        """Keep even invalid authored values editable; validation happens at Apply/Run."""
        config = {**self.default_config(), **(node.get("config") or {})}
        config["working_directory_input_enabled"] = directory_input_enabled(config, node.get("inputs") or [])
        return config

    def _configuration_html(self, node, payload):
        """Group common tasks first, with permissions and limits in Advanced."""
        config = self._config(node)
        picker = render_path_browser_control(
            input_id="claude-working-directory", label="Working directory", value=config["working_directory"],
            placeholder="Select a directory or use the input", select_mode="directory",
            label_key="block.claude_code.working_directory", placeholder_key="block.claude_code.directory_placeholder",
            input_attrs='data-block-config-field="working_directory"')
        model_options = [(item["id"], item["label"]) for item in self.model["model_catalog"]]
        if config["model"] not in {item[0] for item in model_options}:
            model_options.append((config["model"], config["model"]))
        choices = ''.join(f'<option value="{escape(str(k), quote=True)}">{escape(str(v))}</option>' for k, v in model_options)
        result = (payload.get("runtime") or {}).get("result") or {}
        meta = result.get("metadata") or {}
        state = result.get("runtime_state") or {}
        identifier = str(state.get("claude_session_id", result.get("claude_session_id", meta.get("claude_session_id", ""))) or "")
        return (
            '<div class="claude-settings-stack"><section class="claude-modal-section">'
            f'<h3>{self.text("execution", "Execution")}</h3>'
            f'<label class="field-group">{self.text("title", "Block name")}<input data-block-title-field value="{escape(str(node.get("title") or self.default_title()), quote=True)}"></label>'
            f'{picker}<label class="claude-check"><input type="checkbox" data-claude-working-directory-input-enabled{" checked" if config["working_directory_input_enabled"] else ""}>'
            f'{self.text("working_directory_input_enabled", "Use an input for the working directory")}</label>'
            f'<p class="field-hint">{self.text("directory_hint", "This switch applies immediately. Turning it off removes the input and its links. A non-empty input overrides the configured directory.")}</p>'
            '<p class="field-hint" data-claude-directory-feedback role="status" aria-live="polite" hidden></p>'
            '<div class="claude-config-grid">'
            f'<label class="field-group">{self.text("model", "Model")}<input data-block-config-field="model" list="claude-models" placeholder="CLI default" data-i18n-placeholder="block.claude_code.cli_default" value="{escape(str(config["model"]), quote=True)}"><datalist id="claude-models">{choices}</datalist></label>'
            f'{self.field(config, "effort", "Effort", options=[(e, e or "CLI default") for e in self.model["effort_catalog"]])}</div>'
            f'<p class="field-hint">{self.text("model_hint", "Choose an alias or enter an exact model ID. Access and effort support depend on your Claude account.")}</p>'
            '</section><section class="claude-modal-section">'
            f'<h3>{self.text("session", "Session")}</h3>'
            f'{self.field(config, "use_persistent_session", "Use a persistent Claude session", kind="checkbox")}'
            f'<p class="field-hint">{self.text("session_hint", "One session per block and blueprint instance. All outputs share it sequentially. Disabling persistence forgets the association at the next execution, not the Claude transcript.")}</p>'
            f'<div class="claude-session-row"><code data-claude-session-id>{escape(identifier)}</code>'
            f'<span data-claude-session-empty{" hidden" if identifier else ""}>{self.text("session_empty", "Available after the first successful execution")}</span>'
            f'<button type="button" class="ghost-btn" data-claude-copy-session{" disabled" if not identifier else ""}>{self.text("copy", "Copy")}</button></div>'
            f'<input type="hidden" data-block-config-field="session_generation" value="{escape(str(config["session_generation"]), quote=True)}">'
            f'<button type="button" class="ghost-btn" data-claude-reset-session>{self.text("reset_session", "New session on next execution")}</button>'
            '<p class="field-hint" data-claude-session-feedback role="status"></p>'
            '</section><details class="claude-modal-section"><summary>'
            f'{self.text("advanced", "Permissions and limits")}</summary><div class="claude-advanced">'
            f'{self.field(config, "permission_mode", "Permission mode", options=[(p, p) for p in PERMISSIONS])}'
            f'{self.field(config, "dangerously_allow_all", "Danger: bypass all permission checks", kind="checkbox")}'
            f'<p class="claude-warning">{self.text("permissions_hint", "Claude can read or modify the working directory according to these permissions. This unattended block cannot show approval dialogs. The danger option grants unrestricted tool execution.")}</p>'
            f'{self.field(config, "claude_binary", "Claude executable")}'
            '<div class="claude-config-grid">'
            f'{self.field(config, "timeout_sec", "Timeout per call (s)", kind="number", attrs="min=1 max=240 step=1")}'
            f'{self.field(config, "max_prompt_chars", "Prompt limit (characters)", kind="number", attrs="min=1 max=1000000 step=1000")}</div>'
            f'<p class="field-hint">{self.text("limits_hint", "240 seconds maximum for the whole activation. Authentication uses the local Claude CLI; no API key is stored in this block.")}</p>'
            '</div></details></div>'
        )

    def _instructions_html(self, node):
        """Per-output editors and explicit data-input overrides keyed by stable ID."""
        config = self._config(node)
        mapping = config.get("instruction_inputs") or {}
        panels = []
        for port in node.get("outputs", []):
            pid = str(port["id"])
            source = str(mapping.get(pid, ""))
            options = f'<option value="">{escape(self.translate("block.claude_code.fixed_instruction", fallback="Use the instruction below"))}</option>'
            inputs = [p for p in node.get("inputs", []) if not is_working_directory_port(p)]
            for p in inputs:
                options += f'<option value="{p["id"]}"{" selected" if str(p["id"]) == source else ""}>{escape(str(p.get("name") or p["id"]))} (#{p["id"]})</option>'
            if source and source not in {str(p["id"]) for p in inputs}:
                options += f'<option value="{escape(source, quote=True)}" selected>#{escape(source)} — unavailable</option>'
            panels.append(
                f'<section class="claude-modal-section claude-output-editor"><h3>{escape(str(port.get("title") or port.get("name") or pid))}</h3>'
                f'<label class="field-group">{self.text("instruction_source", "Instruction source")}<select data-claude-instruction-source="{pid}">{options}</select></label>'
                f'<label class="field-group">{self.text("instruction", "Instruction")}<textarea rows="14" spellcheck="false" data-block-output-field="instruction" data-block-output-port-id="{pid}">{escape(str(port.get("instruction") or ""))}</textarea></label>'
                f'<p class="field-hint">{self.text("instruction_hint", "A non-empty selected input replaces this instruction. Other inputs are included as named data. Port order does not change the mapping.")}</p></section>')
        return f'<input type="hidden" data-block-config-field="instruction_inputs" data-block-value-type="json" value="{escape(json.dumps(mapping), quote=True)}">' + ''.join(panels)

    def _mcp_html(self, node, payload):
        """Show server-owned summaries only; never expose connection parameters."""
        selected = self._config(node).get("mcp_refs") or []
        servers = [v for v in (payload.get("mcp_server_refs") or []) if isinstance(v, dict)]
        known = {str(v.get("ref")) for v in servers}
        servers += [{"ref": ref, "name": ref} for ref in selected if ref not in known]
        rows = []
        for item in servers:
            ref = str(item.get("ref") or "")
            if ref:
                status = self.text("mcp_ready", "Configured") if item.get("configured") else self.text("mcp_missing", "Missing or incomplete configuration")
                rows.append(f'<label class="claude-mcp-option"><input type="checkbox" data-claude-mcp-ref value="{escape(ref, quote=True)}"{" checked" if ref in selected else ""}><span><strong>{escape(str(item.get("name") or ref))}</strong><small>{escape(ref)} · {status}</small></span></label>')
        if payload.get("mcp_server_refs_error"):
            rows.append(f'<p role="alert">{self.text("mcp_error", "MCP registry unavailable. Saved selections are preserved.")}</p>')
        if not rows:
            rows.append(f'<p>{self.text("mcp_empty", "No MCP server configured. Add one in Application Settings.")}</p>')
        return (f'<section class="claude-modal-section"><h3>{self.text("mcp", "MCP servers")}</h3>'
                f'<input type="hidden" data-block-config-field="mcp_refs" data-block-value-type="json" value="{escape(json.dumps(selected), quote=True)}">'
                + ''.join(rows) + f'<p class="field-hint">{self.text("mcp_hint", "Only selected application references are used. URLs, headers and credentials are resolved at execution and are never stored in the blueprint.")}</p></section>')

    def render_modal(self, *, node, payload=None):
        """Render an opaque, responsive modal with pinned actions and owned tabs."""
        payload = payload or {}
        result = (payload.get("runtime") or {}).get("result") or {}
        command = str(result.get("last_claude_command") or (result.get("metadata") or {}).get("last_claude_command") or "")
        panels = [("attributes", "settings", "Settings", self._configuration_html(node, payload)),
                  ("instructions", "instructions", "Instructions", self._instructions_html(node)),
                  ("mcp", "mcp", "MCP servers", self._mcp_html(node, payload)),
                  ("ports", "ports", "Ports", self._render_generic_modal_ports(node)),
                  ("last-cmd", "diagnostics", "Diagnostics", f'<section class="claude-modal-section"><h3>{self.text("last_command", "Last command")}</h3><pre data-claude-last-command>{escape(command)}</pre><p class="field-hint">{self.text("command_hint", "The prompt and MCP connection parameters are intentionally omitted.")}</p></section>')]
        tabs, body = [], []
        for index, (key, text_key, title, content) in enumerate(panels):
            selected = index == 0
            tabs.append(f'<button type="button" class="claude-modal-tab" data-claude-modal-tab data-claude-tab-id="{key}" id="claude-tab-{key}" role="tab" aria-controls="claude-panel-{key}" aria-selected="{str(selected).lower()}" tabindex="{0 if selected else -1}">{self.text(text_key, title)}</button>')
            body.append(f'<section class="claude-modal-panel" data-claude-modal-panel data-claude-tab-id="{key}" id="claude-panel-{key}" role="tabpanel" aria-labelledby="claude-tab-{key}"{"" if selected else " hidden"}>{content}</section>')
        template = (self.directory / "block_modal.html").read_text(encoding="utf-8")
        for key, value in {"node_id": escape(str(node.get("id") or ""), quote=True),
                           "node_title": escape(str(node.get("title") or self.default_title())),
                           "tabs": ''.join(tabs), "panels": ''.join(body)}.items():
            template = template.replace("{{ " + key + " }}", value)
        return {"html": template, "context": {"node_id": node.get("id"), "node_kind": self.kind}}

    def render_inspector_panel(self, *, node, payload=None):
        """Reuse the same settings and instructions with the standard ports editor."""
        template = (self.directory / "inspector_panel.html").read_text(encoding="utf-8")
        template = template.replace("{{ settings }}", self._configuration_html(node, payload or {}))
        template = template.replace("{{ instructions }}", self._instructions_html(node))
        template = template.replace("{{ mcp }}", self._mcp_html(node, payload or {}))
        return {"html": render_inspector_template(template=template, node=node, payload=payload),
                "context": {"node_id": node.get("id"), "full_panel": True}}

    def render_node_card(self, *, node, payload=None):
        """Show a bounded instruction and settings preview inside the standard shell."""
        config = self._config(node)
        port = next(iter(node.get("outputs") or []), {})
        instruction = " ".join(str(port.get("instruction") or "").split())
        instruction_key = ""
        if (config.get("instruction_inputs") or {}).get(str(port.get("id"))):
            instruction_key = "block.claude_code.card_input_instruction"
            instruction = self.translate(instruction_key, fallback="Instruction from input")
        elif not instruction:
            instruction_key = "block.claude_code.card_empty_instruction"
            instruction = self.translate(instruction_key, fallback="No instruction")
        if len(instruction) > 240:
            instruction = instruction[:239] + "…"
        model_id = str(config.get("model") or "")
        model_label = next((item["label"] for item in self.model["model_catalog"] if item["id"] == model_id), model_id)
        if not model_id:
            model_label = self.translate("block.claude_code.card_cli_model", fallback="CLI model")
        try:
            timeout = str(min(int(config["timeout_sec"]), 240))
        except (TypeError, ValueError, OverflowError):
            timeout = "—"
        settings = " · ".join(str(value) for value in (config.get("effort"), f"{timeout} s") if value)
        rendered = render_node_card_template(block=self, node=node, node_classes=["claude-code-node"], replacements={
            "title": node.get("title") or self.default_title(), "instruction": instruction,
            "instruction_key": instruction_key, "configuration": f"{model_label} · {settings}",
            "configuration_key": "" if model_id else "block.claude_code.card_cli_configuration",
            "configuration_params": json.dumps({"settings": f" · {settings}"})})
        # Authored text and model IDs are data, never translation keys.
        rendered["html"] = rendered["html"].replace(' data-i18n=""', '').replace(' data-i18n-title=""', '')
        return rendered

    def _directory_port_operations(self, node, enabled):
        """Build idempotent public operations; keep unrelated ports and existing IDs intact."""
        existing = next((p for p in node.get("inputs", []) if is_working_directory_port(p)), None)
        if enabled and existing is None:
            return [{"op": "create_port", "node_id": node["id"], "direction": "input",
                     "name": "working_directory", "title": "Working directory",
                     "accepts": ["config/working-directory", "text/plain", "message/*"],
                     "multiplicity": "many", "required": False, "execution_requirement": "not_required_for_execution"}]
        if not enabled and existing is not None:
            return [{"op": "delete_port", "node_id": node["id"], "direction": "input", "port_id": existing["id"], "cascade": True}]
        return []

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Persist the directory switch immediately; other authored fields still use Apply."""
        if action == "sync_working_directory_input":
            if not node.get("id"):
                return {"error": "missing_node_id"}
            enabled = boolean(values.get("enabled"))
            return {"graph_operations": [
                {"op": "update_node_config", "node_id": node["id"], "config": {"working_directory_input_enabled": enabled}},
                *self._directory_port_operations(node, enabled)], "rerender_inspector": False}
        result = super().handle_ui_action(node=node, action=action, values=values, payload=payload)
        patch = result.get("node_patch") or {}
        if "config" not in patch:
            return result
        try:
            config = self.normalize_config({**self._config(node), **patch["config"]})
        except ValueError as error:
            return {"error": str(error)}
        patch["config"] = config
        operations = self._directory_port_operations(node, config["working_directory_input_enabled"])
        if operations:
            result.update(graph_operations=operations, rerender_inspector=True)
        return result
