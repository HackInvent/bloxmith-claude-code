# Claude Code Block

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->


## Role

`claude_code` executes the local Claude Code CLI with `-p` and emits the command stdout.

## Files

- `block.py`: prompt assembly, prompt length guard, `claude -p` execution, timeout handling, logs, and runtime outputs.
- `model.json`: default input, output instruction, CLI config, and runtime capabilities.
- `inspector_panel.html`: block-owned inspector UI for instruction and execution settings.
- `block_modal.html`: instruction-first modal UI with attributes and last-command tabs.
- `assets/css/block_modal.css`: modal layout and prompt editor styles.
- `assets/js/block_modal.js`: modal tab keyboard/click behavior.
- `node_card.html`: block-owned canvas card body.

## Ports

- Inputs:
  - `in` (`id: 1`): optional input; accepts generic messages, text, and JSON.
- Outputs:
  - `out` (`id: 1`): emits `message/*` and `text/plain`; its `instruction` field is appended to the generated prompt.

## Configuration

- `claude_binary`: executable name or path used for Claude Code. Default: `claude`.
- `timeout_sec`: process timeout in seconds.
- `max_prompt_chars`: maximum prompt length accepted before the CLI is launched.

## Runtime Behavior

`execute_runtime()` builds a prompt from all non-empty named inputs plus the target output instruction, then runs:

```text
claude -p <prompt>
```

The block runs once per output port. Each output emits stdout as `text/plain`. Non-zero exit codes, missing binaries, timeouts, and oversized prompts return a failed `BlockRuntimeResult` with diagnostic logs and metadata.

The block uses the generic block executor, so the same `execute_runtime()` implementation is used in One Shot Simulation (`centralized`) and Active Runtime (`zeromq_active`).

## UI Behavior

The inspector lets users edit the first output instruction plus CLI settings. Changes use the generic block UI field binding and are persisted only when the user clicks **Apply**.

The modal opens on the first output instruction tab, keeps attributes separate, and exposes a `Last cmd` tab with the latest command persisted in runtime metadata.

The modal declares `data-block-runtime-refresh="autonomous"`; block-owned JS preserves tab selection, draft instructions, and command diagnostics while runtime polling is active.

## Maintenance Notes

Claude Code behavior belongs in this block. Do not add Claude-specific branches to the orchestrator or active runtime worker; use the generic block executor contract instead.

## Compatibility policy

[compatibility.json](compatibility.json) records HackInvent's verified BloxSmith versions and test evidence. Only the versions listed above have been verified, using the block-owned suites in a **bundled-block test installation**. This is not a certification of managed-package installation, every browser/OS, or live provider availability. Other framework versions are unverified, not necessarily incompatible.

The block-version badge follows `model.json`, not a published Git tag. `unversioned` means that no block release version is declared; no number is inferred from the framework version. The framework still uses `model.json` for its runtime/install contract; the tester-owned JSON does not replace it. Official integration tests run in the private `bloxmith-blocs` workspace. Test helpers and the proprietary framework are not bundled in this public block repository.
