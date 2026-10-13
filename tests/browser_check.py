"""Two independent browser contexts, including an iPhone-sized viewport. No model calls."""
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from browser_locale import install as install_test_locale
install_test_locale()
from test_remote import RemoteTests
from markdown_check import check_markdown
from commands_check import check_commands


def main():
    harness = RemoteTests(); harness.setUp()
    harness.gateway.worker.start()
    out = ROOT / 'test-results'; out.mkdir(exist_ok=True)
    errors = []
    try:
        with sync_playwright() as p:
            # Use real shared memory when available; some systems’ /tmp-backed
            # Chromium buffers can fail with ERR_INSUFFICIENT_RESOURCES.
            launch_options = {'headless': True, 'args': ['--no-sandbox']}
            if Path('/dev/shm').is_dir():
                shm = os.statvfs('/dev/shm')
                if shm.f_bavail * shm.f_frsize >= 512 * 1024 * 1024:
                    launch_options['ignore_default_args'] = ['--disable-dev-shm-usage']
            browser = p.chromium.launch(**launch_options)
            desktop = browser.new_context(viewport={'width': 1365, 'height': 950}, color_scheme='light')
            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, color_scheme='dark')
            page = desktop.new_page(); phone = mobile.new_page()
            for tab in (page, phone):
                tab.on('pageerror', lambda error: errors.append(str(error)))
                tab.on('console', lambda message: errors.append(message.text) if message.type == 'error' and ('Content Security Policy' in message.text or 'content security policy' in message.text) else None)
                tab.goto(harness.web.origin)
                expect(tab.locator('html')).to_have_attribute('data-theme', 'dark' if tab == phone else 'light')
                tab.locator('#login-theme-toggle').click()
                expect(tab.locator('html')).to_have_attribute('data-theme', 'light' if tab == phone else 'dark')
                tab.locator('#login-theme-toggle').click()
                tab.locator('#token').fill(harness.token)
                tab.locator('#login-form button').click()
                expect(tab.locator('#app')).to_be_visible()
            phone.locator('#theme-toggle').click()
            expect(phone.locator('html')).to_have_attribute('data-theme', 'light')
            phone.reload(); expect(phone.locator('html')).to_have_attribute('data-theme', 'light')
            phone.locator('#theme-toggle').click()
            expect(phone.locator('html')).to_have_attribute('data-theme', 'dark')
            assert phone.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(20, 26, 24)'
            expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            page.locator('#new-chat').click()
            page.locator('#chat-name').fill('跨设备同步测试')
            page.locator('#new-form button[type=submit]').click()
            expect(page.locator('#prompt')).to_be_enabled()
            expect(page.locator('#message-mode')).to_have_value('auto')
            page.locator('#prompt').fill('hello')
            page.locator('#send').click()
            expect(page.locator('#messages')).to_contain_text('Fixture response')
            phone.reload()
            expect(phone.locator('#messages')).to_contain_text('Fixture response')
            phone.locator('#message-mode').select_option('send')
            phone.locator('#prompt').fill('approval')
            phone.locator('#send').click()
            expect(phone.locator('#requests')).to_contain_text('echo fixture')
            expect(page.locator('#requests')).to_contain_text('echo fixture')
            expect(phone.locator('#activity-title')).to_have_text('等待你的回应…')
            phone.screenshot(path=str(out/'mobile-dark-approval.png'), full_page=True)
            phone.locator('#requests .decline').click()
            expect(page.locator('#requests .request')).to_have_count(0)
            expect(phone.locator('#stop')).to_be_hidden()
            expect(phone.locator('#activity')).to_be_hidden()
            phone.locator('#file-input').set_input_files({'name': 'notes.txt', 'mimeType': 'text/plain', 'buffer': b'attachment from phone'})
            expect(phone.locator('#attachments')).to_contain_text('notes.txt')
            phone.locator('#message-mode').select_option('send')
            phone.locator('#prompt').fill('read attachment')
            phone.locator('#send').click()
            expect(phone.locator('#attachments .attachment-chip')).to_have_count(0)
            expect(page.locator('#messages')).to_contain_text('notes.txt')
            expect(phone.locator('#stop')).to_be_hidden()
            phone.locator('#model-open').click()
            expect(phone.locator('#model-dialog')).to_be_visible()
            phone.locator('#model-select').select_option('fixture-model')
            phone.locator('#effort-select').select_option('high')
            phone.locator('#model-save').click()
            phone.locator('#message-mode').select_option('auto')
            phone.locator('#prompt').fill('hang')
            phone.locator('#send').click()
            expect(phone.locator('#stop')).to_be_visible()
            expect(phone.locator('#message-mode')).to_have_value('auto')
            expect(phone.locator('#activity-title')).to_have_text('Working… 正在工作')
            expect(phone.locator('#activity-time')).to_contain_text('本轮已运行')
            tid = phone.evaluate('state.current'); turn_id = harness.gateway.thread(tid)['turn_id']
            def progress(method, item):
                # Persist progress in the protocol fixture as well as emitting it,
                # so reload checks use the same authoritative history as production.
                harness.gateway.rpc.call('fixture/event', {'method': method, 'params': {'threadId': tid, 'turnId': turn_id, 'item': item}})
            thinking = {'id': 'thinking-activity-fixture', 'type': 'reasoning', 'summary': ['PRIVATE_REASONING_SENTINEL']}
            progress('item/started', thinking)
            expect(phone.locator('#activity-title')).to_have_text('正在思考…')
            expect(phone.locator('#messages')).not_to_contain_text('PRIVATE_REASONING_SENTINEL')
            progress('item/completed', thinking)
            command = {'id': 'command-activity-fixture', 'type': 'commandExecution', 'command': 'python train.py --epochs 3', 'status': 'inProgress'}
            progress('item/started', command)
            for tab in (page, phone): expect(tab.locator('#activity-title')).to_have_text('正在执行命令…')
            expect(phone.locator('#activity-detail')).to_have_text('python train.py --epochs 3')
            phone.reload(); expect(phone.locator('#activity-title')).to_have_text('正在执行命令…')
            expect(phone.locator('html')).to_have_attribute('data-theme', 'dark')
            phone.emulate_media(reduced_motion='reduce')
            assert phone.locator('.activity-spinner').evaluate('(n)=>getComputedStyle(n).animationName') == 'none'
            phone.emulate_media(reduced_motion='no-preference')
            phone.screenshot(path=str(out/'mobile-dark-working.png'), full_page=True)
            page.screenshot(path=str(out/'desktop-light-working.png'), full_page=True)
            mobile.set_offline(True)
            expect(phone.locator('#activity-title')).to_have_text('连接中断，正在重连…', timeout=10000)
            mobile.set_offline(False)
            expect(phone.locator('#activity-title')).to_have_text('正在执行命令…', timeout=15000)
            progress('item/completed', {**command, 'status': 'completed', 'exitCode': 0})
            expect(phone.locator('#activity-title')).to_have_text('Working… 正在工作')
            expect(phone.locator('#message-mode')).to_have_value('auto')
            phone.locator('#prompt').fill('mobile steering')
            phone.locator('#send').click()
            expect(page.locator('#messages')).to_contain_text('mobile steering')
            assert harness.gateway.thread(tid)['turn_id'] == turn_id
            expect(phone.locator('#queue-list .queue-row')).to_have_count(0)
            expect(phone.locator('#notice')).to_contain_text('已补充到当前任务')
            phone.locator('#message-mode').select_option('queue')
            phone.locator('#prompt').fill('queued task')
            phone.locator('#send').click()
            expect(phone.locator('#queue-list')).to_contain_text('queued task')
            phone.locator('#stop').click()
            expect(phone.locator('#stop')).to_be_hidden()
            expect(phone.locator('#activity')).to_be_hidden()
            expect(phone.locator('#queue-list .queue-row')).to_have_count(0)
            phone.reload()
            expect(phone.locator('#messages')).to_contain_text('mobile steering')
            # Viewed images load only when expanded, remain available after reload,
            # and are accessed through an authenticated conversation reference.
            picture = harness.work / 'preview.png'
            picture.write_bytes((ROOT / 'codex_remote/static/icon-192.png').read_bytes())
            phone.locator('#message-mode').select_option('auto')
            phone.locator('#prompt').fill('image'); phone.locator('#send').click()
            image_card = phone.locator('.message[data-type="imageView"]')
            expect(image_card).to_be_visible()
            expect(image_card.locator('img')).not_to_have_attribute('src')
            image_card.locator('summary').click()
            expect(image_card.locator('img')).to_be_visible()
            expect(image_card.locator('.image-status')).to_have_text('192 × 192')
            phone.reload()
            image_card.locator('summary').click()
            expect(image_card.locator('img')).to_be_visible()
            expect(image_card.locator('img')).to_be_in_viewport(ratio=0.95)
            expect(image_card.locator('.image-original')).to_have_attribute('target', '_blank')
            phone.screenshot(path=str(out/'mobile-image-preview.png'), full_page=True)
            page.locator('.message[data-type="imageView"] summary').click()
            expect(page.locator('.image-preview img')).to_be_visible()
            expect(page.locator('.image-preview img')).to_be_in_viewport(ratio=0.95)
            page.screenshot(path=str(out/'desktop-image-preview.png'), full_page=True)
            for width in (320, 360, 390):
                phone.set_viewport_size({'width': width, 'height': 844})
                assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Overflow at {width}px'
                bounds = phone.locator('#send').bounding_box()
                assert bounds['x'] + bounds['width'] <= width, f'Send clipped at {width}px'
            picture.unlink()
            phone.reload(); image_card.locator('summary').click()
            expect(image_card.locator('.image-status')).to_contain_text('图片暂时无法显示')
            picture.write_bytes((ROOT / 'codex_remote/static/icon-192.png').read_bytes())
            image_card.get_by_role('button', name='重新加载').click()
            expect(image_card.locator('img')).to_be_visible()
            phone.locator('#thread-tools').click()
            phone.locator('#rename-title').fill('手机继续的实验会话')
            phone.locator('#rename-chat').click()
            expect(phone.locator('#chat-title')).to_have_text('手机继续的实验会话')
            phone.locator('#thread-tools').click(); phone.locator('#browse-files').click()
            expect(phone.locator('#files-dialog')).to_be_visible()
            phone.locator('#files-dialog .dialog-close').click()
            # Markdown is rendered as safe DOM, including untrusted model HTML and URL schemes.
            result = phone.evaluate("""() => {const n=document.createElement('div');remoteUI.markdown(n,'<img src=x onerror=alert(1)>\\n[bad](javascript:alert)\\n**safe**');return {images:n.querySelectorAll('img').length,links:n.querySelectorAll('a').length,bold:n.querySelector('strong').textContent};}""")
            assert result == {'images': 0, 'links': 0, 'bold': 'safe'}, result
            check_markdown(page, phone, harness, out)
            check_commands(page, phone, harness, out)
            assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
            page.screenshot(path=str(out/'desktop.png'), full_page=True)
            phone.screenshot(path=str(out/'mobile.png'), full_page=True)
            # A terminal-owned fixture thread is visible without resume/ownership changes.
            harness.gateway.shared = True
            child = harness.work/'terminal-project'; child.mkdir()
            native = harness.gateway.rpc.call('thread/start', {'cwd': str(child)})['thread']
            harness.gateway.rpc.call('thread/name/set', {'threadId': native['id'], 'name': '终端中的运行会话'})
            turn = harness.gateway.rpc.call('turn/start', {'threadId': native['id'], 'input': [{'type': 'text', 'text': 'hang'}]})['turn']
            harness.gateway.discover()
            phone.goto(harness.web.origin+'/?thread='+native['id']); expect(phone.locator('#app')).to_be_visible()
            expect(phone.locator('#view-mode')).to_be_visible()
            expect(phone.locator('#composer')).to_be_visible()
            expect(phone.locator('#prompt')).to_be_enabled()
            expect(phone.locator('#run-state')).to_contain_text('运行中')
            expect(phone.locator('#messages')).to_contain_text('hang')
            assert native['id'] not in harness.gateway.loaded
            harness.gateway.rpc.call('turn/steer', {'threadId': native['id'], 'expectedTurnId': turn['id'], 'input': [{'type': 'text', 'text': 'terminal update while observed'}]})
            expect(phone.locator('#messages')).to_contain_text('terminal update while observed', timeout=10000)
            assert native['id'] not in harness.gateway.loaded
            phone.screenshot(path=str(out/'mobile-readonly.png'), full_page=True)
            phone.locator('#menu').click()
            phone.locator('#threads .thread').filter(has_text='终端中的运行会话').click()
            expect(phone.locator('#view-mode')).to_be_hidden()
            expect(phone.locator('#composer')).to_be_visible()
            phone.locator('#message-mode').select_option('auto')
            phone.locator('#prompt').fill('phone update after explicit control')
            phone.locator('#send').click()
            expect(phone.locator('#messages')).to_contain_text('phone update after explicit control')
            assert native['id'] in harness.gateway.loaded
            phone.locator('#stop').click(); expect(phone.locator('#stop')).to_be_hidden()
            # A saved notLoaded thread can be continued directly from the composer.
            saved = harness.gateway.rpc.call('thread/start', {'cwd': str(child)})['thread']
            harness.gateway.rpc.call('fixture/unload', {'threadId': saved['id']})
            harness.gateway.discover()
            phone.goto(harness.web.origin+'/?thread='+saved['id'])
            expect(phone.locator('#enable-control')).to_be_enabled()
            expect(phone.locator('#prompt')).to_be_enabled()
            assert saved['id'] not in harness.gateway.loaded
            phone.locator('#message-mode').select_option('send')
            phone.locator('#prompt').fill('stream'); phone.locator('#send').click()
            expect(phone.locator('#messages')).to_contain_text('Streaming before completion')
            expect(phone.locator('#activity-title')).to_have_text('正在回复…')
            expect(phone.locator('#stop')).to_be_visible()
            page.goto(harness.web.origin+'/?thread='+saved['id'])
            expect(page.locator('#messages')).to_contain_text('Streaming before completion')
            assert harness.gateway.thread(saved['id'])['turn_id']
            turn_id = harness.gateway.thread(saved['id'])['turn_id']
            harness.gateway.rpc.call('turn/steer', {'threadId': saved['id'], 'expectedTurnId': turn_id, 'input': [{'type': 'text', 'text': 'external terminal live update'}]})
            for tab in (page, phone): expect(tab.locator('#messages')).to_contain_text('external terminal live update')
            phone.reload()
            expect(phone.locator('#messages')).to_contain_text('Streaming before completion')
            expect(phone.locator('#messages')).to_contain_text('external terminal live update')
            phone.screenshot(path=str(out/'mobile-connected.png'), full_page=True)
            phone.locator('#stop').click(); expect(phone.locator('#stop')).to_be_hidden()
            # Native paginated threads have no readable history before their first message.
            for connect_first in (False, True):
                empty = harness.gateway.rpc.call('thread/start', {'cwd': str(child), 'historyMode': 'paginated'})['thread']
                harness.gateway.discover()
                phone.goto(harness.web.origin+'/?thread='+empty['id'])
                expect(phone.locator('#prompt')).to_be_enabled()
                expect(phone.locator('#view-description')).to_contain_text('直接发送第一条消息')
                expect(phone.locator('#notice')).to_be_empty()
                assert empty['id'] not in harness.gateway.loaded
                if connect_first:
                    phone.locator('#enable-control').click()
                    expect(phone.locator('#view-mode')).to_be_hidden()
                    phone.reload(); expect(phone.locator('#prompt')).to_be_enabled()
                phone.locator('#prompt').fill('first message in empty conversation')
                expect(phone.locator('#send')).to_be_enabled()
                phone.locator('#send').click()
                expect(phone.locator('#messages')).to_contain_text('Fixture response')
                page.goto(harness.web.origin+'/?thread='+empty['id'])
                expect(page.locator('#messages')).to_contain_text('first message in empty conversation')
                phone.reload(); expect(phone.locator('#messages')).to_contain_text('Fixture response')
                assert len(harness.gateway.history(empty['id'])['turns']) == 1
            # A selected thread can retain readable history when resume fails.
            # Do not recurse into resume or blank the transcript on a writer conflict.
            current = phone.evaluate('state.current')
            failed_attaches = []
            pattern = '**/api/threads/' + current + '/attach'
            def unavailable(route):
                failed_attaches.append(True)
                route.fulfill(status=409, content_type='application/json', body='{"error":"会话仍有写入进程占用"}')
            phone.route(pattern, unavailable)
            phone.reload()
            expect(phone.locator('#view-mode')).to_be_visible()
            expect(phone.locator('#messages')).to_contain_text('Fixture response')
            expect(phone.locator('#notice')).to_contain_text('写入进程占用')
            expect(phone.locator('#enable-control')).to_be_enabled()
            phone.evaluate('previewThread()')
            assert len(failed_attaches) == 1
            expect(phone.locator('#recover-chat')).to_be_enabled()
            phone.locator('#recover-chat').click()
            expect(phone.locator('#view-mode')).to_be_hidden()
            expect(phone.locator('#messages')).to_contain_text('Fixture response')
            expect(phone.locator('#chat-title')).to_contain_text('History fork')
            assert phone.evaluate('state.current') != current
            assert harness.gateway.thread(current)
            phone.unroute(pattern, unavailable)
            phone.goto(harness.web.origin+'/?thread='+current)
            expect(phone.locator('#prompt')).to_be_enabled()
            phone.evaluate('state.readOnly = true; buttons()')
            phone.locator('#enable-control').click()
            expect(phone.locator('#view-mode')).to_be_hidden()
            expect(phone.locator('#prompt')).to_be_enabled()
            assert not errors, errors
            browser.close()
        print('PASS: two-device sync, mobile approvals, attachments, model settings, steering, queue, stop, reload, rename, files, safe Markdown, responsive layout')
        print('Screenshots:', out)
        print('PASS: passive preview, click-to-connect, saved-session continuation on send, live output before completion, terminal updates on both clients, reload hydration')
        print('PASS: unmaterialized empty threads support preview, connect/reload, first send on the same thread, and cross-device history')
        print('PASS: system light/dark preference, persistent toggle, dark approvals, working/thinking/command/streaming/waiting indicators, reload, disconnect/reconnect and reduced motion')
        print('PASS: default mid-turn followups, explicit next-turn queue, expanded image preview, reload, missing-image retry, and 320/360/390px layouts')
        print('PASS: slash menu, native goal lifecycle/budgets/progress, two-device sync, stale-edit protection, reload, active-turn commands, compaction, literal slash input and phone layout')
    finally:
        harness.gateway.stopped.set()
        harness.gateway.close()
        harness.tearDown()


if __name__ == '__main__':
    main()
