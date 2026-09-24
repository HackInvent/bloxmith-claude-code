#!/usr/bin/env python3
"""Managed/linked canvas cards, module mounting, Apply, locales and responsive layout."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from playwright.sync_api import sync_playwright, expect
from block_test_artifacts import artifact_path
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import (isolated_server, graph_payload, create_project_api, project_editor_url,
    attach_console_guards, assert_no_blocking_console_errors, get_project_graph_api)
from claude_fixtures import node
from blocs.claude_code.block import ClaudeCodeBlock


def card_cases(version):
    """Exercise empty and oversized content without calling a CLI or a provider."""
    empty = ClaudeCodeBlock().build_node_payload(node_id="card-empty", position={"x": 660, "y": 160})
    long = ClaudeCodeBlock().build_node_payload(node_id="card-long", position={"x": 340, "y": 400})
    for item in (empty, long):
        item["block_version"] = version
    long["title"] = ('Long <agent> & "quoted" title — ' * 8).strip()
    long["outputs"][0]["instruction"] = '<script>not_executed()</script> Long instruction without overflow. ' * 20
    long["config"].update(model="claude-" + "extended-model-" * 12, effort="high")
    return empty, long


def assert_card(page, node_id, *, title, preview=None, summary=None):
    """Require loaded styling and real geometry, not just a successful HTML response."""
    card = page.locator(f'.canvas-node[data-node-id="{node_id}"] [data-claude-code-node-card]')
    expect(card).to_be_visible()
    expect(card).to_have_css('display', 'grid')
    expect(card.locator('h3')).to_have_text(title)
    expect(card.locator('h3')).to_have_attribute('title', title)
    if preview is not None:
        expect(card.locator('.block-node-card-main')).to_have_text(preview)
        expect(card.locator('.block-node-card-main')).to_have_attribute('title', preview)
    if summary is not None:
        expect(card.locator('.block-node-card-meta')).to_have_text(summary)
        expect(card.locator('.block-node-card-meta')).to_have_attribute('title', summary)
    expect(card.locator('script')).to_have_count(0)
    bounds = card.evaluate('''root => {
      const shell=root.closest('.canvas-node'), s=shell.getBoundingClientRect();
      const nodes=[...root.children], boxes=nodes.map(n=>n.getBoundingClientRect());
      const title=root.querySelector('h3'), preview=root.querySelector('.block-node-card-main');
      const status=shell.querySelector(':scope > .node-status').getBoundingClientRect();
      const kind=root.querySelector('.claude-card-kind').getBoundingClientRect();
      return {
        inside:boxes.every(b=>b.left>=s.left+2 && b.right<=s.right-2 && b.top>=s.top+2 && b.bottom<=s.bottom-2),
        separated:boxes.every((b,i)=>!i || b.top>=boxes[i-1].bottom+2),
        statusClear:kind.right+2<=status.left || kind.top>=status.bottom+2,
        titleSize:parseFloat(getComputedStyle(title).fontSize),
        titleWeight:parseInt(getComputedStyle(title).fontWeight),
        titleOverflow:getComputedStyle(title).textOverflow,
        previewSize:parseFloat(getComputedStyle(preview).fontSize),
        previewClamp:getComputedStyle(preview).webkitLineClamp,
        width:shell.offsetWidth, height:shell.offsetHeight,
        ports:shell.querySelectorAll('.node-port').length
      };
    }''')
    assert bounds['inside'] and bounds['separated'] and bounds['statusClear'], bounds
    assert bounds['titleSize'] >= 15 and bounds['titleWeight'] >= 600, bounds
    assert bounds['titleOverflow'] == 'ellipsis' and bounds['previewSize'] >= 14, bounds
    assert bounds['previewClamp'] == '2' and bounds['ports'] == 2, bounds
    return bounds


def open_modal(page):
    """Use the real canvas interaction, not synthetic HTML detached from the app."""
    page.locator('.canvas-node[data-node-id="claude-test"] h3').dblclick()
    modal = page.locator('.cw-claude-code-modal')
    expect(modal).to_be_visible()
    return modal


def close_modal(modal):
    if modal.is_visible():
        modal.locator('[data-close-block-modal]').first.click()


def saved_node(server, project, node_id='claude-test'):
    """Read authoritative graph state, never infer persistence from a checked checkbox."""
    graph = get_project_graph_api(server, project['project_id'])
    return next(n for n in graph.get('document', graph)['nodes'] if n['id'] == node_id)


def toggle_directory(page, surface, enabled):
    """Wait for the dedicated action, including its local graph reconciliation."""
    checkbox = surface.locator('[data-claude-working-directory-input-enabled]')
    with page.expect_response(lambda r: r.url.endswith('/ui-action') and r.request.method == 'POST'
                              and r.request.post_data_json.get('action') == 'sync_working_directory_input') as response:
        checkbox.set_checked(enabled)
    assert not response.value.json().get('error'), response.value.json()
    expect(checkbox).to_be_enabled()
    expect(checkbox).to_be_checked(checked=enabled)


def main():
    for origin in ("managed", "linked"):
        with isolated_server() as server, sync_playwright() as playwright:
            model = install_test_package(server, "claude_code", origin=origin)
            consumer = node(version=model["version"], persistent=True)
            consumer["config"]["mcp_refs"] = ["missing-ref"]
            empty, long = card_cases(model["version"])
            for item in (consumer, empty, long):
                rendered = surface_payload(server, model, item, surface="node_card")
                assert rendered['assets'] and '<h3' in rendered['html'], rendered
            for surface in ("modal", "inspector_panel"):
                rendered = surface_payload(server, model, consumer, surface=surface)
                assert 'data-block-config-field="model"' in rendered["html"]
                assert 'missing-ref' in rendered["html"]
            project = create_project_api(server, document=graph_payload("Claude properties", [consumer, empty, long], []))["project"]
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = attach_console_guards(page)
                page.goto(project_editor_url(server.base_url, project["project_id"], workspace_project_id=project["workspace_project_id"]))
                assert_card(page, 'claude-test', title=consumer['title'], preview='Return a concise answer.', summary='CLI model · 8 s')
                empty_bounds = assert_card(page, 'card-empty', title=empty['title'], preview='No instruction', summary='CLI model · 240 s')
                long_bounds = assert_card(page, 'card-long', title=long['title'])
                assert (empty_bounds['width'], empty_bounds['height']) == (long_bounds['width'], long_bounds['height'])
                page.screenshot(path=artifact_path(f'claude-{origin}-canvas-cards.png'))
                page.locator('.canvas-node[data-node-id="card-empty"]').screenshot(path=artifact_path(f'claude-{origin}-empty-card.png'))
                modal = open_modal(page)
                page.screenshot(path=artifact_path(f'claude-{origin}-initial.png'))
                expect(modal.locator('[data-block-config-field="model"]')).to_be_visible()
                expect(modal.locator('[data-block-apply]')).to_be_disabled()
                modal.locator('[data-block-config-field="model"]').fill('sonnet')
                modal.locator('[data-block-config-field="effort"]').select_option('low')
                toggle_directory(page, modal, True)
                saved = saved_node(server, project)
                directories = [p for p in saved['inputs'] if p['name'] == 'working_directory']
                assert len(directories) == 1 and 'config/working-directory' in directories[0]['accepts'], saved
                assert directories[0]['execution_requirement'] == 'not_required_for_execution'
                assert saved['config']['working_directory_input_enabled'] is True
                assert saved['config']['model'] == '', 'Toggling a port must not save the model draft'
                expect(modal.locator('[data-block-config-field="model"]')).to_have_value('sonnet')
                toggle_directory(page, modal, False)
                assert not any(p['name'] == 'working_directory' for p in saved_node(server, project)['inputs'])
                toggle_directory(page, modal, True)
                modal.locator('[data-claude-reset-session]').click()
                expect(modal.locator('[data-claude-session-feedback]')).not_to_be_empty()
                generation = modal.locator('[data-block-config-field="session_generation"]').input_value()
                tabs = modal.locator('[data-claude-modal-tab]')
                tabs.nth(0).press('ArrowRight')
                expect(tabs.nth(1)).to_have_attribute('aria-selected', 'true')
                editor = modal.locator('[data-block-output-field="instruction"]')
                expect(editor).to_be_visible()
                editor.fill('Keep this instruction draft.')
                modal.locator('[data-claude-instruction-source="1"]').select_option('1')
                tabs.nth(2).click()
                expect(modal.locator('[data-claude-mcp-ref]')).to_be_checked()
                modal.locator('[data-claude-mcp-ref]').uncheck()
                tabs.nth(0).click()
                for width, height, label in ((1440, 900, 'desktop'), (390, 740, 'mobile'), (320, 568, 'small')):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_function('''() => {
                      const b=document.querySelector('.cw-claude-code-modal').getBoundingClientRect();
                      return b.left>=-1 && b.right<=innerWidth+1 && b.bottom<=innerHeight+1;
                    }''', timeout=5000)
                    bounds = modal.evaluate('''panel => {
                      const b=panel.getBoundingClientRect();
                      const a=panel.querySelector('[data-block-apply]').getBoundingClientRect();
                      const c=panel.querySelector('[data-close-block-modal]').getBoundingClientRect();
                      return {inside:b.left>=-1 && b.right<=innerWidth+1 && b.bottom<=innerHeight+1,
                        apply:a.bottom<=innerHeight && a.top>=0, close:c.top>=0,
                        overflow:panel.scrollWidth>panel.clientWidth+1, background:getComputedStyle(panel).backgroundColor};
                    }''')
                    page.screenshot(path=artifact_path(f'claude-{origin}-{label}.png'))
                    assert bounds['inside'] and bounds['apply'] and bounds['close'] and not bounds['overflow'], (label, bounds)
                    assert bounds['background'] not in {'transparent', 'rgba(0, 0, 0, 0)'}, bounds
                page.set_viewport_size({"width": 1440, "height": 900})
                expect(modal.locator('[data-block-apply]')).to_be_enabled()
                with page.expect_response(lambda r: '/api/blocks/' in r.url and r.url.endswith('/ui-action') and r.request.method == 'POST') as applied:
                    modal.locator('[data-block-apply]').click()
                assert not applied.value.json().get('error'), applied.value.json()
                close_modal(modal)
                # Applying can replace the canvas asynchronously. Reopen from the
                # saved graph rather than racing a node that is being re-mounted.
                page.reload()
                # New settings must reach the card, not only the modal form.
                # The extra directory input was created immediately, before Apply.
                expect(page.locator('.canvas-node[data-node-id="claude-test"] .block-node-card-main')).to_have_text('Instruction from input')
                expect(page.locator('.canvas-node[data-node-id="claude-test"] .block-node-card-meta')).to_have_text('Sonnet · low · 8 s')
                modal = open_modal(page)
                expect(modal.locator('[data-block-config-field="model"]')).to_have_value('sonnet')
                expect(modal.locator('[data-block-config-field="effort"]')).to_have_value('low')
                expect(modal.locator('[data-block-config-field="session_generation"]')).to_have_value(generation)
                modal.locator('[data-claude-tab-id="instructions"][role="tab"]').click()
                expect(modal.locator('[data-block-output-field="instruction"]')).to_have_value('Keep this instruction draft.')
                expect(modal.locator('[data-claude-instruction-source="1"]')).to_have_value('1')
                modal.locator('[data-claude-tab-id="mcp"][role="tab"]').click()
                expect(modal.locator('[data-claude-mcp-ref]')).to_have_count(0)
                close_modal(modal)
                graph = get_project_graph_api(server, project["project_id"])
                assert 'working_directory' in str(graph), graph
                # The inspector uses exactly the same immediate action and keeps drafts.
                page.evaluate("localStorage.setItem('bloxsmith.inspectorPinned', 'true')")
                page.reload()
                page.locator('.canvas-node[data-node-id="claude-test"] h3').click()
                inspector = page.locator('.cw-claude-code-inspector')
                expect(inspector).to_be_visible()
                inspector.locator('[data-block-config-field="working_directory"]').fill('inspector-draft')
                toggle_directory(page, inspector, False)
                assert not any(p['name'] == 'working_directory' for p in saved_node(server, project)['inputs'])
                expect(inspector.locator('[data-block-config-field="working_directory"]')).to_have_value('inspector-draft')
                assert saved_node(server, project)['config']['working_directory'] == ''
                toggle_directory(page, inspector, True)
                expect(page.locator('.canvas-node[data-node-id="claude-test"] [data-claude-code-node-card]')).to_be_visible()
                page.screenshot(path=artifact_path(f'claude-{origin}-directory-inspector.png'))
                # Test translation through the application's supported selector.
                page.goto(server.base_url + '/')
                page.locator('#homeApplicationSettingsButton').click()
                page.locator('#applicationLanguageSelect').select_option('fr')
                page.wait_for_function("window.CWMessages.getLanguage() === 'fr'")
                page.goto(project_editor_url(server.base_url, project["project_id"], workspace_project_id=project["workspace_project_id"]))
                assert_card(page, 'card-empty', title=empty['title'], preview='Instruction vide', summary='Modèle CLI · 240 s')
                expect(page.locator('.canvas-node[data-node-id="claude-test"] .block-node-card-main')).to_have_text('Instruction via un input')
                page.screenshot(path=artifact_path(f'claude-{origin}-canvas-french.png'))
                page.locator('.canvas-node[data-node-id="card-empty"]').screenshot(path=artifact_path(f'claude-{origin}-empty-card-french.png'))
                modal = open_modal(page)
                expect(modal.locator('[data-claude-tab-id="attributes"][role="tab"]')).to_have_text('Paramètres')
                expect(modal.locator('[data-claude-reset-session]')).to_have_text('Nouvelle session à la prochaine exécution')
                page.screenshot(path=artifact_path(f'claude-{origin}-french.png'))
                close_modal(modal)
                assert page.evaluate('!window.CWBlockUiBlocks?.claude_code')
                assert_no_blocking_console_errors(errors)
            finally:
                browser.close()
        print(f'[ok] Claude {origin} properties', flush=True)


if __name__ == '__main__':
    main()
