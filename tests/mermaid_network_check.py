"""Exercise sandboxed rendering over LAN HTTP with browser access checks enabled."""
import ipaddress
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
import test_remote
from codex_remote.web import RemoteWebServer


def main():
    try:
        # UDP connect chooses a local interface without sending any packets.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 9))
            lan = sock.getsockname()[0]
        if not ipaddress.ip_address(lan).is_private or ipaddress.ip_address(lan).is_loopback:
            raise OSError('No private LAN interface')
    except OSError:
        lan = None
    with patch.object(test_remote, 'RemoteWebServer', side_effect=lambda addr, *a, **kw: RemoteWebServer(('0.0.0.0', 0), *a, **kw)):
        h = test_remote.RemoteTests(); h.setUp()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            for host in (['127.0.0.1', lan] if lan else ['127.0.0.1']):
                page = browser.new_page(); errors, requests = [], []
                # Simulate browsers/proxies rejecting script fetches from the
                # opaque child. The renderer must work without either request.
                page.route('**/vendor/mermaid/mermaid.min.js', lambda route: route.abort())
                page.route('**/mermaid-frame.js', lambda route: route.abort())
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append(request.url))
                h.web.origin = f'http://{host}:{h.port}'
                page.goto(h.web.origin + '/')
                page.locator('#token').fill(h.token); page.locator('#login-form button').click()
                expect(page.locator('#app')).to_be_visible()
                page.evaluate(r"""() => {
                    const root = document.createElement('div'); root.id = 'diagram-probe'; document.body.prepend(root);
                    messageRenderer.markdown(root, '```mermaid\nflowchart LR\nA --> B\n```', true);
                }""")
                block = page.locator('#diagram-probe .mermaid-block')
                expect(block).to_have_attribute('data-status', 'rendered', timeout=15000)
                expect(block.locator('img')).to_be_visible()
                frame = next(f for f in page.frames if f.url.endswith('/mermaid-frame.html'))
                assert frame.evaluate('() => {try {return !!parent.document} catch(e) {return e.name}}') == 'SecurityError'
                assert page.locator('iframe').get_attribute('sandbox') == 'allow-scripts'
                assert frame.locator('script[src]').count() == 0
                assert frame.evaluate('fetch("/api/bootstrap").then(() => "allowed").catch(e => e.name)') == 'TypeError'
                frame.evaluate('''() => { const s = document.createElement('script');
                    s.textContent = 'window.unapprovedScript = true'; document.body.append(s); }''')
                assert frame.evaluate('typeof window.unapprovedScript') == 'undefined'
                assert all(url.startswith(h.web.origin) or url.startswith('data:') for url in requests)
                assert not errors, errors
                page.close()
            print('PASS: single-document Mermaid, blocked child scripts, restricted script hashes and isolation over loopback' + (' and LAN HTTP.' if lan else '; LAN test skipped (no private interface).'), flush=True)

            # At this rate the old 5.5 MB child script exceeds its 30 s
            # deadline. The compressed, self-contained frame stays below it.
            page = browser.new_page()
            page.goto(h.web.origin + '/')
            page.locator('#token').fill(h.token); page.locator('#login-form button').click()
            expect(page.locator('#app')).to_be_visible()
            cdp = page.context.new_cdp_session(page)
            cdp.send('Network.enable')
            cdp.send('Network.emulateNetworkConditions', {
                'offline': False, 'latency': 80, 'downloadThroughput': 128 * 1024,
                'uploadThroughput': 128 * 1024, 'connectionType': 'cellular3g'})
            page.evaluate(r"""() => {
                const root = document.createElement('div'); root.id = 'slow-probe'; document.body.prepend(root);
                messageRenderer.markdown(root, '```mermaid\nflowchart TD\nA[开始] --> B[并行任务]\nB --> C{完成？}\nC --> D[结束]\n```', true);
            }""")
            expect(page.locator('#slow-probe .mermaid-block')).to_have_attribute('data-status', 'rendered', timeout=30000)
            expect(page.locator('#slow-probe img')).to_be_visible()
            page.close()
            print('PASS: compressed renderer works over a throttled 128 KiB/s connection.', flush=True)

            # A failed load is visible and can recover without reloading the chat.
            page = browser.new_page()
            page.goto(h.web.origin + '/')
            page.locator('#token').fill(h.token); page.locator('#login-form button').click()
            expect(page.locator('#app')).to_be_visible()
            page.clock.install()
            page.route('**/mermaid-frame.html', lambda route: route.abort(), times=1)
            page.evaluate(r"""() => {
                const root = document.createElement('div'); root.id = 'retry-probe'; document.body.prepend(root);
                messageRenderer.markdown(root, [1, 2, 3].map(n => '```mermaid\nflowchart LR\nRetry' + n + ' --> Done\n```').join('\n'), true);
            }""")
            block = page.locator('#retry-probe .mermaid-block')
            expect(page.locator('iframe.mermaid-renderer')).to_have_count(1)
            page.clock.fast_forward(90_001)
            expect(page.locator('#retry-probe .mermaid-block[data-status=error]')).to_have_count(3, timeout=5000)
            assert all(node.get_attribute('open') is not None for node in block.locator('details').all())
            for _ in range(3): block.locator('.diagram-retry').first.click()
            expect(page.locator('#retry-probe .mermaid-block[data-status=rendered]')).to_have_count(3, timeout=15000)
            expect(block.locator('.diagram-retry')).to_have_count(0)
            print('PASS: one failed load releases all waiting diagrams; source and Retry recover without reloading.', flush=True)
            browser.close()
    finally:
        h.tearDown()


if __name__ == '__main__': main()
