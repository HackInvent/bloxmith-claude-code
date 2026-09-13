#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Role: Verifies Claude Code block behavior.
# File Name: F5.08_claude_code_block.py
# Author: Alexandre EL
# Email: alex@hackinvent.com
# Created Date: 2026-05-19
# -----------------------------------------------------------------------------

"""F5.08 - Claude Code block.

The test injects a fake `claude` executable and verifies that the block calls
Claude Code with `-p`, publishes stdout, renders its block-owned UI, and works
in both centralized and zeromq_active runtimes.
"""

# Test cases:
# - FB1/FB2/FB6 - Run text -> Claude Code -> display in centralized runtime and verify `claude -p` receives a prompt containing inputs and instruction.
# - FB1/FB2/FB6 - Run the same graph in zeromq_active runtime and verify stdout publication through the generic active worker.
# - FB3/FB5 - Execute a second output instruction and verify each output stores command metadata and stdout.
# - FB4 - Reject an oversized prompt before launching the Claude CLI.
# - UI - Render modal/inspector/node-card and verify block-owned tabs, bindings, assets, and last-command display.

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloxsmith_app.block_runtime import BlockRuntimeContext
from bloxsmith_app.block_ui import render_block_inspector_panel, render_block_modal, render_block_node_card
from ui_smoke_common import (
    create_run_api,
    data_edge,
    display_node,
    expect,
    graph_payload,
    isolated_server,
    text_node,
    wait_for_run_terminal,
)

from blocs.claude_code.block import ClaudeCodeBlock


@contextmanager
def fake_claude_cli(response_text: str = "fake claude response"):
    """Expose a fake `claude` binary that records argv and prints a response."""

    with TemporaryDirectory(prefix="bloxsmith-fake-claude-") as tmp:
        temp_dir = Path(tmp)
        capture_path = temp_dir / "claude_calls.jsonl"
        binary_path = temp_dir / "claude"
        binary_path.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

capture_path = Path(os.environ["CW_FAKE_CLAUDE_CAPTURE"])
capture_path.parent.mkdir(parents=True, exist_ok=True)
capture = {"argv": sys.argv[1:]}
if len(sys.argv) >= 3 and sys.argv[1] == "-p":
    capture["prompt"] = sys.argv[2]
with capture_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(capture, ensure_ascii=False) + "\\n")
print(os.environ.get("CW_FAKE_CLAUDE_RESPONSE", "fake claude response"))
sys.exit(int(os.environ.get("CW_FAKE_CLAUDE_EXIT", "0")))
""",
            encoding="utf-8",
        )
        binary_path.chmod(0o755)

        old_path = os.environ.get("PATH", "")
        old_capture = os.environ.get("CW_FAKE_CLAUDE_CAPTURE")
        old_response = os.environ.get("CW_FAKE_CLAUDE_RESPONSE")
        old_exit = os.environ.get("CW_FAKE_CLAUDE_EXIT")
        os.environ["PATH"] = f"{temp_dir}{os.pathsep}{old_path}"
        os.environ["CW_FAKE_CLAUDE_CAPTURE"] = str(capture_path)
        os.environ["CW_FAKE_CLAUDE_RESPONSE"] = response_text
        os.environ["CW_FAKE_CLAUDE_EXIT"] = "0"
        try:
            yield capture_path
        finally:
            os.environ["PATH"] = old_path
            if old_capture is None:
                os.environ.pop("CW_FAKE_CLAUDE_CAPTURE", None)
            else:
                os.environ["CW_FAKE_CLAUDE_CAPTURE"] = old_capture
            if old_response is None:
                os.environ.pop("CW_FAKE_CLAUDE_RESPONSE", None)
            else:
                os.environ["CW_FAKE_CLAUDE_RESPONSE"] = old_response
            if old_exit is None:
                os.environ.pop("CW_FAKE_CLAUDE_EXIT", None)
            else:
                os.environ["CW_FAKE_CLAUDE_EXIT"] = old_exit


def claude_code_node(*, two_outputs: bool = False, max_prompt_chars: int = 250000) -> dict:
    outputs = [
        {
            "id": 1,
            "name": "out",
            "title": "Out",
            "emits": ["message/*", "text/plain"],
            "multiplicity": "many",
            "instruction": "Return a concise answer from @in.",
        }
    ]
    if two_outputs:
        outputs.append(
            {
                "id": 2,
                "name": "summary",
                "title": "Summary",
                "emits": ["message/*", "text/plain"],
                "multiplicity": "many",
                "instruction": "Summarize @in.",
            }
        )
    return {
        "id": "claude-code-1",
        "kind": "claude_code",
        "title": "Claude Code test",
        "position": {"x": 360, "y": 120},
        "inputs": [
            {"id": 1, "name": "in", "title": "In", "accepts": ["message/*", "text/plain"], "multiplicity": "many"}
        ],
        "outputs": outputs,
        "config": {
            "claude_binary": "claude",
            "timeout_sec": 10,
            "max_prompt_chars": max_prompt_chars,
        },
    }


def run_claude_code_case(runtime_mode: str) -> None:
    with fake_claude_cli(response_text=f"fake claude {runtime_mode}") as capture_path:
        with isolated_server() as server:
            document = graph_payload(
                f"F5 Claude Code {runtime_mode}",
                [
                    text_node("text-1", "Texte Claude", "hello claude", 80, 120),
                    claude_code_node(),
                    display_node("display-1", "Affichage", 680, 120),
                ],
                [
                    data_edge("edge-text-claude", "text-1", 1, "claude-code-1", 1),
                    data_edge("edge-claude-display", "claude-code-1", 1, "display-1", 1),
                ],
            )
            created = create_run_api(server, document, runtime_mode=runtime_mode)
            run = wait_for_run_terminal(server, str(created.get("run_id") or ""), timeout_sec=20)

        calls = [json.loads(line) for line in capture_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expect(run.get("status") == "success", f"Le run Claude Code {runtime_mode} doit reussir.")
        expect(run.get("output_values", {}).get("claude-code-1:1", {}).get("value").strip() == f"fake claude {runtime_mode}", "stdout Claude Code incorrect.")
        expect(calls and calls[-1].get("argv", [None])[0] == "-p", "Claude Code doit etre appele avec -p.")
        prompt = str(calls[-1].get("prompt") or "")
        expect("hello claude" in prompt, "Le prompt Claude Code doit contenir l'input texte.")
        expect("Return a concise answer" in prompt, "Le prompt Claude Code doit contenir l'instruction.")
        logs = "\n".join(run.get("node_logs", {}).get("claude-code-1", []))
        expect("[claude-code-cmd]" in logs and " -p " in logs, "Les logs doivent exposer la commande Claude Code avec -p.")


def test_multiple_outputs_and_prompt_guard() -> None:
    """TC1 - Execute multiple output instructions and reject an oversized prompt."""

    with fake_claude_cli(response_text="multi") as capture_path:
        with isolated_server() as server:
            document = graph_payload(
                "F5 Claude Code multi",
                [
                    text_node("text-1", "Texte Claude", "multi hello", 80, 120),
                    claude_code_node(two_outputs=True),
                ],
                [data_edge("edge-text-claude", "text-1", 1, "claude-code-1", 1)],
            )
            created = create_run_api(server, document, runtime_mode="centralized")
            run = wait_for_run_terminal(server, str(created.get("run_id") or ""), timeout_sec=20)

        calls = [json.loads(line) for line in capture_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expect(run.get("status") == "success", "Le run multi-output Claude Code doit reussir.")
        expect(len(calls) == 2, "Claude Code doit etre appele une fois par sortie.")
        expect(run.get("output_values", {}).get("claude-code-1:2", {}).get("value").strip() == "multi", "La sortie 2 doit publier stdout.")
        result = run.get("results", {}).get("claude-code-1", {})
        expect("last_claude_command" in str(result), "La metadata doit conserver la derniere commande Claude Code.")

    block = ClaudeCodeBlock()
    result = block.execute_runtime(
        BlockRuntimeContext(
            run_id="unit-run",
            node_id="claude-code-guard",
            kind="claude_code",
            title="Claude guard",
            config={"claude_binary": "claude", "timeout_sec": 10, "max_prompt_chars": 8},
            inputs={"in": "0123456789"},
            input_content_types={"in": "text/plain"},
            input_message="0123456789",
            input_ports=(),
            output_ports=(type("Port", (), {"id": 1, "name": "out", "instruction": "too long"})(),),
            root_dir=ROOT,
            run_dir=ROOT,
        )
    )
    expect(result.status == "failed", "Un prompt trop long doit etre refuse avant execution.")
    expect("trop long" in result.error, "Le message d'erreur doit expliquer la limite de prompt.")


def test_claude_code_ui_contract() -> None:
    """TC2 - Render Claude Code block-owned modal, inspector, node-card, and assets."""

    node = claude_code_node()
    rendered = render_block_modal("claude_code", {"node": node, "runtime": {}})
    html = str(rendered.get("html") or "")
    assets = rendered.get("assets") or []
    css = (ROOT / "blocs/claude_code/assets/css/block_modal.css").read_text(encoding="utf-8")
    js = (ROOT / "blocs/claude_code/assets/js/block_modal.js").read_text(encoding="utf-8")

    expect("cw-claude-code-modal" in html, "Le modal Claude Code doit venir du bloc.")
    expect('data-block-runtime-refresh="autonomous"' in html, "Le modal Claude Code doit gerer son refresh runtime.")
    expect('data-claude-tab-id="output-1"' in html, "Le modal doit exposer l'onglet instruction de sortie.")
    expect('data-claude-tab-id="attributes"' in html, "Le modal doit exposer l'onglet Attributs.")
    expect('data-claude-tab-id="last-cmd"' in html, "Le modal doit exposer l'onglet Last cmd.")
    expect('data-block-output-field="instruction"' in html, "L'instruction doit rester liee a output.instruction.")
    expect('data-block-config-field="claude_binary"' in html, "Le binaire Claude doit etre editable.")
    expect('data-block-config-field="timeout_sec"' in html, "Le timeout doit etre editable.")
    expect({"kind": "css", "path": "assets/css/block_modal.css"} in assets, "Le CSS modal Claude doit etre declare.")
    expect({"kind": "js", "path": "assets/js/block_modal.js"} in assets, "Le JS modal Claude doit etre declare.")
    expect(".claude-modal-panel[hidden]" in css, "Le CSS doit cacher les panels inactifs.")
    expect("registry.claude_code" in js, "Le JS doit monter le modal via le registre block UI.")

    inspector = render_block_inspector_panel("claude_code", {"node": node})
    inspector_html = str(inspector.get("html") or "")
    expect("cw-claude-code-inspector" in inspector_html, "L'inspector Claude Code doit venir du bloc.")
    expect('data-block-output-field="instruction"' in inspector_html, "L'inspector doit editer l'instruction.")

    card = render_block_node_card("claude_code", {"node": node})
    card_html = str(card.get("html") or "")
    expect("data-claude-code-node-card" in card_html, "La node-card Claude Code doit venir du bloc.")


def main() -> None:
    test_claude_code_ui_contract()
    test_multiple_outputs_and_prompt_guard()
    run_claude_code_case("centralized")
    run_claude_code_case("zeromq_active")
    print("[ok] F5.08_claude_code_block")


if __name__ == "__main__":
    main()
