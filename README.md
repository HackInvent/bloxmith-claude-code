# Claude Code Block

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->

## Role

`claude_code` is a local Claude Code agent block, modeled on Codex: named inputs,
an instruction per output, a working directory, selectable model/effort,
persistent sessions, application MCP references and live session diagnostics.

It uses the public generic block contract in both `centralized` and
`zeromq_active` runtimes. It requires no framework change. The technical kind stays
`claude_code`, so existing nodes and package references remain valid.

## Quick start

1. Install Claude Code **2.1.259 or newer** on the machine running Studio and sign
   in with `claude auth login` as that machine's Studio user. The local CLI account
   is used; this block does not ask for, save or log an API key.
2. Add **Claude Code**, connect data to `in`, and connect `out` to a consumer.
3. Open **Settings**, choose a trusted working directory, then select a model
   alias or enter an exact model ID. Empty model/effort use the CLI defaults.
4. In **Instructions**, write the instruction for each output. Click **Apply**.
5. Run the saved blueprint instance. The final answer is published as text. With
   persistence enabled, subsequent calls and Runs resume this block's session.

The adapter is POSIX/Linux-only (file locks, anonymous file descriptors and a
private CLI process group). Its CLI flags were checked against Claude Code
2.1.278. Model access and effort support depend on the account and model; the block
does not silently switch models when a request fails.

## Ports and instructions

- `in` (`id: 1`): accepts generic messages, text and JSON. Its default execution
  requirement is `required_for_execution`; change readiness in the Ports inspector when needed.
- `working_directory`: created immediately when its checkbox is enabled, in either
  the modal or the inspector; no **Apply** click is needed. A non-empty
  value takes priority over the configured folder. Disabling the setting removes
  this optional port **and its links**.
- `out` (`id: 1`): final Claude answer as `text/plain`. Its `instruction` is the task.

Add data inputs/outputs through the standard **Ports** inspector. Each output has
its own instruction editor. The instruction-source selector binds an existing
data input to an output by **stable port ID**, not position or display order. A
non-empty text value replaces that output's stored instruction; an empty or whitespace-only value
uses the stored instruction. Missing referenced ports fail explicitly. Inputs
selected as instructions and directory control inputs are excluded from data
sections of the prompt. JSON values, `0` and `false` are preserved.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `claude_binary` | `claude` | Executable name or path; never evaluated by a shell. |
| `working_directory` | empty | Explicit CLI folder, required unless the directory input supplies one. Relative paths resolve from the runtime root and `~` is expanded. Missing/nonexistent directories fail before launch. |
| `working_directory_input_enabled` | `false` | Immediately create/remove the optional `working_directory` control input, like native Codex. |
| `model` / `effort` | empty / empty | CLI defaults, or explicit selections. Choices live in `model.json`, not Python. Exact custom model IDs are accepted. |
| `permission_mode` | `default` | `default`, `dontAsk`, `plan`, `acceptEdits`, or `auto`. |
| `dangerously_allow_all` | `false` | Explicitly select `bypassPermissions`. Grants unrestricted tool execution; use only in a trusted, isolated environment. |
| `use_persistent_session` | `true` | Resume the session owned by this block and instance. Saved instance scope is required. Disable for standalone runs. |
| `session_generation` | empty | Reset marker written by **New session on next execution**, then Apply. Not a session ID. |
| `instruction_inputs` | `{}` | Output-ID to input-ID bindings, edited through the source selectors. |
| `mcp_refs` | `[]` | At most 32 unique application MCP references. No raw URL, token or header in the blueprint. |
| `timeout_sec` | `240` | Per-call timeout, bounded to 1–240 seconds; older larger values clamp to 240. |
| `max_prompt_chars` | `250000` | Prompt guard, bounded to 1–1,000,000 characters. |

The whole activation (all outputs and session-lock wait) is limited to 240 seconds
to leave cleanup time before the framework's 300-second managed hook limit.
Responses and stderr are capped at 4 MiB per call. No permission dialog can be
answered by this unattended block: `--permission-prompts none` denies requests
that would need one. CLI-managed policies and account permissions still apply.

## Runtime behavior

The CLI receives the prompt on stdin, not in the command line:

```text
claude -p --output-format json --permission-mode default --permission-prompts none \
  --strict-mcp-config --mcp-config <anonymous-descriptor>
```

Calls run sequentially, once per output. Only the final successful `result` text
is emitted; CLI JSON, tool activity and unfinished answers are not published.
Failures, cancellations, invalid JSON, missing sessions and limit violations fail
explicitly without output publication or automatic retry. A missing saved Claude
conversation is not silently replaced: use **New session on next execution**.

Stop/cancellation closes the call's lifetime pipe. A block-owned helper establishes
its own POSIX session **before** launching Claude, so it can stop that private
process group even if the framework kills the isolated hook process. It cannot
target the Studio process group. Already completed tool side effects cannot be
rolled back. Incoming messages do not interrupt a running call; scheduling and
readiness remain under the framework's normal execution policy.

### Persistent sessions

`get_block_storage_dir()` provides the block's instance directory. This block owns
`claude_session.json` and `claude_session.lock` inside it. The association file is
bounded, atomic and mode `0600`; it contains an ID, directory and reset/ownership
markers, never credentials or a transcript. Calls across outputs, runs and hook
processes serialize under a file lock. The CLI itself owns the conversation files.

Run restores the saved ID before inputs are consumed. Disabling persistence clears
the association at the next Run or execution and uses `--no-session-persistence`.
Resetting the generation, changing the working directory or copying the state to
another instance starts a distinct session. A corrupted state file fails visibly
instead of being overwritten. Do not remove a lock file while a call is active.

### MCP

Select registered servers in **MCP servers**. HTTP connections are resolved only
at execution through `resolve_mcp_reference(ref)`. The translated Claude MCP JSON
lives in an inherited **anonymous temporary file**, not a persisted config file,
command argument, runtime state or graph. Known connection values are redacted
from results and diagnostics. `--strict-mcp-config` excludes unselected user/project
MCP definitions, including when the selection is empty. Other local Claude settings,
hooks and `CLAUDE.md` remain governed by the CLI and chosen working directory.

## UI behavior and Codex differences

The properties modal groups **Settings**, **Instructions**, **MCP servers**,
**Ports** and **Diagnostics**. Common settings and copyable session identity are
visible before advanced permissions/limits. The layout is opaque and responsive;
Close/Apply stay visible while content scrolls. Tabs support arrow/Home/End keys.
The inspector offers the same settings and instruction editors.

The directory-input checkbox changes topology immediately, including removal of
that port's links when unchecked. **Cancel** does not undo this explicit port
operation. Other edits remain local until **Apply**, including session reset
requests. Toggling the port does not save or discard those drafts. The switch is
disabled while its request is pending, and failures are visible. Runtime
polling updates only session/command fragments, preserving drafts and the active
tab. English is the pivot language, with a block-owned French catalog.

### Alignment with native Codex

The directory port uses `config/working-directory`, `text/plain`, `message/*`,
`multiplicity: many` and `not_required_for_execution`, just like Codex. It is
recognized by its control type or historical `working_directory` name, so changing
the display order or a stale config flag cannot turn it into prompt data. Existing
ports are reused, not duplicated or silently renumbered. Repeated synchronization
is idempotent. An input value only overrides the directory for that execution;
blank/whitespace input falls back to the configured folder. Both sources blank
now fail explicitly: **the previous implicit runtime-root fallback is removed**.
At Run, dynamic-directory sessions can initialize before the input arrives; the
directory is validated before launching the actual CLI call.
As with Codex, an optional input does not delay execution. If a PUSH source must
arrive first, set this port to **Required for execution** in the Ports inspector.

Both packages retain the shared configuration pattern: named inputs, per-output
instructions, a persistent block/instance session, explicit danger permission,
prompt limits, redacted command diagnostics and Apply for ordinary settings.
Provider-specific model, effort, permission and authentication options are not
renamed into Codex flags that the other CLI would not understand.

- This is a generic managed/linked package, not the specialized Codex executor.
- The framework currently reserves the detached manual-conversation surface for
  trusted bundled blocks. Claude Code does not advertise that unsupported surface.
- Codex's paired bottom instruction/effective ports remain a framework boundary:
  `set_codex_output_instruction_override` refuses non-Codex nodes, while generic
  deletion refuses those protected control ports. A generic atomic operation is
  needed for full parity. Claude keeps explicit ordinary data-input bindings;
  it does not bypass the protection or rewrite graph files.
- There is no implicit global Codex instruction and no new-message preemption.

## Files and verification

- `block.py`: public hooks, UI rendering and Apply validation.
- `runtime.py`: prompts, CLI JSON adapter, limits and MCP resolution.
- `session_state.py`: atomic association and cross-process locking.
- `cli_guard.py`: CLI lifetime supervision, including forced host termination.
- `model.json`: ports, defaults, model/effort catalogs, assets and locales.
- HTML templates, `assets/`, `locales/`: release-local UI, CSS and ES modules.
- `tests/`: hermetic CLI, real package integration and browser tests.

From the private workspace:

```sh
python3 -B tests/run_tests.py claude_code --refresh-framework --timeout-sec 300
```

The suites use a fake CLI (no account, network call or token cost), plus actual
managed/linked installs, scoped sessions and both runtime engines. Browser tests
mount real assets, save edits and check desktop/mobile geometry, opacity, tabs and
locale catalogs. Framework copies, screenshots and reports stay in ignored test
directories; no developer paths or credentials belong in the test scripts.

Protocol references: [Claude CLI reference](https://code.claude.com/docs/en/cli-reference),
[programmatic execution](https://code.claude.com/docs/en/headless),
[MCP configuration](https://code.claude.com/docs/en/mcp).

## Compatibility policy

[compatibility.json](compatibility.json) records HackInvent's verified BloxSmith
versions and pinned test evidence, not a certification of every OS, browser or
live provider. Other versions are unverified, not necessarily incompatible.
The declared block version is independent of the framework version and published
Git tags. Official tests run in the private `bloxmith-blocs` workspace; the
proprietary framework and workspace helpers are not included in this public block.

Keep Claude behavior in this repository and use only `bloxsmith_app.block_api`.
Never add Claude-specific branches to the framework for this package.

## Properties ergonomics

The canvas card has its own release-scoped stylesheet: a type badge, a prominent
block name, a two-line instruction preview and a model/effort/timeout summary.
Empty instructions and input overrides are explicit. Long titles and settings
are truncated visually with tooltips; the standard shell, status and ports are
unchanged. Browser tests check these cards before opening any properties surface.

Modal and inspector styles are owned by this package and scoped to its exact
release. Forms adapt to narrow panels, checkboxes stay beside their labels, and
long values do not widen the inspector. Existing labels are associated with
controls; keyboard navigation complements the block’s own tab handlers.
These presentation helpers do not change port bindings, authored settings,
runtime behavior or the block’s original surface cleanup.
