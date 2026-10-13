"""Mobile list filtering, visible API errors, and persistent turn completion markers."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT/'.browsers'))
from playwright.sync_api import sync_playwright, expect
from browser_locale import install as install_test_locale
install_test_locale()
from test_remote import RemoteTests


def main():
    h = RemoteTests(); h.setUp(); h.gateway.shared = True
    try:
        def create(name=None, source='cli', preview=''):
            t = h.gateway.rpc.call('thread/start', {'cwd': str(h.work), 'fixtureName': name,
                'fixtureSource': source, 'fixturePreview': preview})['thread']
            h.gateway._adopt(t); return t['id']
        coding = create('Manipulation 项目开发', preview='debug code')
        other = create('网页开发', preview='web code')
        empty = create()
        experiment = create('实验批任务', 'exec', 'simulation input')
        child = create('子代理代码检查', {'subAgent': {'thread_spawn': {}}}, 'review')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            phone = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            desk = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            for tab in (phone, desk):
                tab.on('pageerror', lambda e: errors.append(str(e)))
                tab.goto(h.web.origin); tab.locator('#token').fill(h.token); tab.locator('#login-form button').click()
                expect(tab.locator('#app')).to_be_visible()
                expect(tab.locator('#threads .thread')).to_have_count(2)
                assert tab.evaluate('state.current') not in [empty, experiment, child]
            phone.locator('#menu').click()
            phone.locator('#show-hidden').check()
            expect(phone.locator('#threads .thread')).to_have_count(5)
            phone.locator('[data-thread-id="'+experiment+'"]').click()
            phone.locator('#thread-tools').click(); phone.locator('#visibility-chat').click()
            phone.locator('#menu').click(); phone.locator('#show-hidden').uncheck()
            expect(phone.locator('#threads .thread')).to_have_count(3)
            desk.evaluate('refresh()')
            expect(desk.locator('[data-thread-id="'+experiment+'"]').first).to_be_visible()
            phone.locator('[data-thread-id="'+coding+'"]').click()
            phone.locator('#thread-tools').click(); phone.locator('#visibility-chat').click()
            desk.evaluate('refresh()')
            expect(desk.locator('[data-thread-id="'+coding+'"]').first).to_have_count(0)
            phone.reload()
            assert phone.evaluate('state.current') != coding
            # A direct link still opens hidden history; hiding never removes it.
            phone.goto(h.web.origin+'/?thread='+coding)
            expect(phone.locator('#prompt')).to_be_enabled()
            phone.locator('#prompt').fill('hang'); phone.locator('#send').click()
            expect(phone.locator('#activity')).to_be_visible()
            turn = h.gateway.thread(coding)['turn_id']
            error = {'message': 'Reconnecting... 1/8', 'additionalDetails': 'HTTP 403: Client not allowed'}
            def event(method, params): h.gateway.rpc.call('fixture/event', {'method': method, 'params': params})
            event('error', {'threadId': coding, 'turnId': turn, 'error': error, 'willRetry': True})
            expect(phone.locator('#activity-title')).to_have_text('模型连接重试中…')
            expect(phone.locator('#activity-detail')).to_contain_text('Client not allowed')
            event('error', {'threadId': coding, 'turnId': turn, 'error': error, 'willRetry': False})
            event('turn/completed', {'threadId': coding, 'turn': {'id': turn, 'status': 'failed', 'error': error, 'durationMs': 1234}})
            event('thread/status/changed', {'threadId': coding, 'status': {'type': 'idle'}})
            expect(phone.locator('.turn-result[data-status="failed"]')).to_have_count(1)
            expect(phone.locator('#activity')).to_be_hidden()
            phone.reload()
            expect(phone.locator('.turn-result[data-status="failed"]')).to_contain_text('Client not allowed')
            expect(phone.locator('#run-state')).to_have_text('执行失败')
            phone.locator('#prompt').fill('hello'); phone.locator('#send').click()
            expect(phone.locator('.turn-result[data-status="completed"]')).to_have_count(1)
            phone.reload()
            expect(phone.locator('.turn-result[data-status="completed"]')).to_contain_text('本轮已完成')
            phone.locator('#prompt').fill('hang'); phone.locator('#send').click()
            expect(phone.locator('#stop')).to_be_visible(); phone.locator('#stop').click()
            expect(phone.locator('.turn-result[data-status="interrupted"]')).to_contain_text('本轮已中断')
            assert not errors, errors
            browser.close()
        print('PASS: filtered coding list, reveal/restore/hide across devices, hidden deep links, retry details, failure/completion/interruption markers, and reload persistence.')
    finally: h.tearDown()


if __name__ == '__main__': main()
