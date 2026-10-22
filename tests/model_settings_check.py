"""Reasoning choices and browser-to-RPC delivery, using the local protocol fixture."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from test_remote import RemoteTests


def main():
    h = RemoteTests(); h.setUp()
    levels = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
    h.gateway.model_cache = [
        {'model': 'fixture-model', 'supportedReasoningEfforts': [{'reasoningEffort': e} for e in levels]},
        {'model': 'limited-fixture', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}, {'reasoningEffort': 'high'}]},
        {'model': 'no-reasoning-fixture', 'supportedReasoningEfforts': []},
    ]
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            for mobile in (False, True):
                tid = h.new()
                page = browser.new_page(viewport={'width': 390 if mobile else 1280, 'height': 844}, is_mobile=mobile, has_touch=mobile)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(h.web.origin + '/?thread=' + tid)
                page.locator('#token').fill(h.token); page.locator('#login-form button').click()
                expect(page.locator('#prompt')).to_be_enabled()

                def options():
                    return page.locator('#effort-select option').evaluate_all('(nodes) => nodes.map(n => n.value)')

                def send(effort, model=None):
                    page.locator('#model-save').click()
                    expect(page.locator('#model-open')).to_contain_text(effort)
                    page.locator('#prompt').fill('Check reasoning selection')
                    with patch.object(h.gateway.rpc, 'call', wraps=h.gateway.rpc.call) as rpc:
                        with page.expect_response(lambda r: r.url.endswith('/message') and r.request.method == 'POST') as pending:
                            page.locator('#send').click()
                        response = pending.value
                        assert response.status == 200, response.text()
                        starts = [c.args[1] for c in rpc.call_args_list if c.args[0] == 'turn/start']
                        assert len(starts) == 1 and starts[0]['effort'] == effort, starts
                        assert starts[0].get('model') == model, starts
                    h.until(lambda: h.gateway.thread(tid)['status'] == 'completed')
                    expect(page.locator('#stop')).to_be_hidden()

                for effort in ('max', 'ultra'):
                    page.locator('#model-open').click()
                    expect(page.locator('#model-select')).to_have_value('')
                    assert options() == ['', *levels], options()
                    page.locator('#effort-select').select_option(effort)
                    page.locator('#model-save').click()
                    page.locator('#model-open').click()
                    expect(page.locator('#effort-select')).to_have_value(effort)
                    send(effort)

                page.locator('#model-open').click()
                page.locator('#model-select').select_option('limited-fixture')
                assert options() == ['', 'medium', 'high']
                expect(page.locator('#effort-select')).to_have_value('')
                page.locator('#custom-model').fill('custom-fixture')
                assert {'max', 'ultra'} <= set(options())
                page.locator('#effort-select').select_option('ultra')
                page.locator('#custom-model').fill('another-custom-fixture')
                expect(page.locator('#effort-select')).to_have_value('ultra')
                send('ultra', 'another-custom-fixture')

                page.locator('#model-open').click()
                expect(page.locator('#custom-model')).to_have_value('another-custom-fixture')
                expect(page.locator('#effort-select')).to_have_value('ultra')
                page.locator('#model-select').select_option('fixture-model')
                expect(page.locator('#custom-model')).to_have_value('')
                expect(page.locator('#effort-select')).to_have_value('ultra')
                page.locator('#model-select').select_option('no-reasoning-fixture')
                assert options() == ['']
                page.locator('#custom-model').fill('fixture-model')
                assert options() == ['', *levels]
                page.close()
            assert not errors, errors
            browser.close()
        print('PASS: desktop/mobile inherited max/ultra, saved selection, exact turn/start parameters, custom model refresh and model-specific restrictions.')
    finally:
        h.tearDown()


if __name__ == '__main__':
    main()
