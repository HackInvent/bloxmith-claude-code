import { withProperties } from "./properties.js";

/** Release-local properties: tabs, MCP references and live diagnostic fragments. */
function mountOwned(root, api) {
  const controller = new AbortController();
  const { signal } = controller;
  const tabs = () => Array.from(root.querySelectorAll('[data-claude-modal-tab]'));
  const setText = (element, text) => {
    if (!element) return;
    if (window.CWI18n?.setText) window.CWI18n.setText(element, String(text || ''));
    else element.textContent = String(text || '');
  };
  const label = (element, key, fallback) => {
    if (!element) return;
    if (window.CWI18n?.set) window.CWI18n.set(element, `block.claude_code.${key}`, {}, fallback);
    else element.textContent = fallback;
  };
  function activate(tab, focus = false) {
    if (!tab) return;
    for (const candidate of tabs()) {
      const selected = candidate === tab;
      candidate.setAttribute('aria-selected', String(selected));
      candidate.tabIndex = selected ? 0 : -1;
    }
    for (const panel of root.querySelectorAll('[data-claude-modal-panel]')) {
      panel.hidden = panel.dataset.claudeTabId !== tab.dataset.claudeTabId;
    }
    if (focus) tab.focus();
  }
  function setField(key, value) {
    const field = root.querySelector(`[data-block-config-field="${key}"]`);
    if (!field) return;
    field.value = value;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));
  }
  /** Persist only the directory-port switch; leave every unsaved form field local. */
  async function syncDirectoryInput(checkbox) {
    const previous = !checkbox.checked;
    const feedback = root.querySelector('[data-claude-directory-feedback]');
    checkbox.disabled = true;
    checkbox.setAttribute('aria-busy', 'true');
    if (feedback) { feedback.hidden = false; feedback.setAttribute('role', 'status'); }
    label(feedback, 'directory_sync_pending', 'Updating the input port…');
    try {
      await api.applyAction('sync_working_directory_input', { enabled: checkbox.checked });
      if (signal.aborted || !root.isConnected) return;
      label(feedback, 'directory_sync_saved', 'Input port updated.');
    } catch (error) {
      if (signal.aborted || !root.isConnected) return;
      checkbox.checked = previous;
      feedback?.setAttribute('role', 'alert');
      label(feedback, 'directory_sync_failed', 'Port update failed. Reopen the properties to check its state.');
      api.log?.(`[claude-code-ui] Directory input synchronization failed: ${error.message}`);
    } finally {
      checkbox.removeAttribute('aria-busy');
      checkbox.disabled = signal.aborted || Boolean(api.isReadOnly?.());
    }
  }
  root.addEventListener('change', (event) => {
    if (event.target.matches('[data-claude-working-directory-input-enabled]')) {
      void syncDirectoryInput(event.target);
      return;
    }
    if (event.target.matches('[data-claude-mcp-ref]')) {
      setField('mcp_refs', JSON.stringify(Array.from(root.querySelectorAll('[data-claude-mcp-ref]:checked'), e => e.value)));
    }
    if (event.target.matches('[data-claude-instruction-source]')) {
      const mapping = {};
      for (const select of root.querySelectorAll('[data-claude-instruction-source]')) {
        if (select.value) mapping[select.dataset.claudeInstructionSource] = select.value;
      }
      setField('instruction_inputs', JSON.stringify(mapping));
    }
  }, { signal });
  root.addEventListener('click', async (event) => {
    const tab = event.target.closest('[data-claude-modal-tab]');
    if (tab) { activate(tab, true); return; }
    if (event.target.closest('[data-claude-reset-session]')) {
      setField('session_generation', crypto.randomUUID());
      label(root.querySelector('[data-claude-session-feedback]'), 'reset_pending', 'Apply to start a new session on the next execution.');
    }
    if (event.target.closest('[data-claude-copy-session]')) {
      try {
        await navigator.clipboard.writeText(root.querySelector('[data-claude-session-id]').textContent);
        label(root.querySelector('[data-claude-session-feedback]'), 'copied', 'Identifier copied.');
      } catch {
        label(root.querySelector('[data-claude-session-feedback]'), 'copy_failed', 'Copy unavailable. Select and copy the identifier.');
      }
    }
  }, { signal });
  root.addEventListener('keydown', (event) => {
    const tab = event.target.closest('[data-claude-modal-tab]');
    const list = tabs();
    if (!tab || !list.length) return;
    const i = list.indexOf(tab);
    const index = { ArrowRight: (i + 1) % list.length, ArrowDown: (i + 1) % list.length,
      ArrowLeft: (i + list.length - 1) % list.length, ArrowUp: (i + list.length - 1) % list.length,
      Home: 0, End: list.length - 1 }[event.key];
    if (index !== undefined) { event.preventDefault(); activate(list[index], true); }
  }, { signal });
  activate(tabs().find(tab => tab.getAttribute('aria-selected') === 'true') || tabs()[0]);

  // Poll only read-only fragments; never replace a form containing unsaved edits.
  let busy = false;
  async function refresh() {
    if (!root.isConnected) { dispose(); return; }
    if (busy || signal.aborted) return;
    const active = api.actions?.getActiveRuntimeContext?.() || {};
    if (!active.runId || !api.actions?.getRunSnapshot) return;
    busy = true;
    try {
      const run = await api.actions.getRunSnapshot(active.runId);
      if (signal.aborted || !root.isConnected || api.actions.getActiveRuntimeContext()?.runId !== active.runId) return;
      const result = run.results?.[root.dataset.nodeId] || {};
      const metadata = result.metadata || {};
      const id = result.runtime_state?.claude_session_id ?? result.claude_session_id ?? metadata.claude_session_id ?? '';
      setText(root.querySelector('[data-claude-session-id]'), id);
      const empty = root.querySelector('[data-claude-session-empty]');
      if (empty) empty.hidden = Boolean(id);
      const copy = root.querySelector('[data-claude-copy-session]');
      if (copy) copy.disabled = !id;
      setText(root.querySelector('[data-claude-last-command]'), result.last_claude_command || metadata.last_claude_command || '');
    } catch {
      // Keep the last known ID and all drafts intact during a temporary disconnect.
      api.log?.('[claude-code-ui] Runtime refresh unavailable.');
    } finally { busy = false; }
  }
  const timer = window.setInterval(refresh, 2000);
  function dispose() { window.clearInterval(timer); controller.abort(); }
  void refresh();
  return { dispose };
}

/** Keep the block behavior and add properties-only accessibility. */
export function mount(root, ...args) {
  return withProperties(mountOwned).call(this, root, ...args);
}
