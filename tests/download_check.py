"""Click actual browser downloads on desktop and phone; no native Codex sessions."""
import hashlib
import os
from pathlib import Path
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from test_remote import RemoteTests


def main():
    h = RemoteTests(); h.setUp()
    try:
        tid = h.new(); h.message(tid, 'hang', 'download-browser-start')
        turn = h.gateway.thread(tid)['turn_id']
        report = h.work / '报告 (final) #1.txt'; report.write_text('Download fixture\n中文文件名')
        outside = h.root / 'export.pdf'; outside.write_bytes(b'%PDF-1.4\nFixture')
        large = h.work / 'archive.zip'; large.write_bytes(b'archive fixture\0' * (1024 * 1024))
        unsafe = h.work / 'example.html'; unsafe.write_text('<script>window.downloadExecuted = true</script>')
        text = f'''[Relative](<{quote(report.name)}>)
[Absolute](<{quote(str(report))}>)
[Sandbox](sandbox:{quote(str(outside))})
[File URI]({report.as_uri()})
[Code reference](<{quote(str(report))}:12:4>)
[Reference][result]
![Local image](<{quote(str(outside))}>)
[Large archive](archive.zip) [HTML file](example.html)
[Missing](missing.txt) [Directory](.) [Website](https://example.org/)

[result]: <{quote(report.name)}>
'''
        h.gateway.rpc.call('fixture/event', {'method': 'item/completed', 'params': {'threadId': tid, 'turnId': turn, 'item': {'id': 'download-fixture', 'type': 'agentMessage', 'text': text}}})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            desktop = browser.new_page(viewport={'width': 1280, 'height': 900}, accept_downloads=True)
            phone = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, accept_downloads=True)
            phone.add_init_script("localStorage.setItem('codex-remote-language', 'zh-CN')")
            errors, requests = [], []
            for page in (desktop, phone):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append(request.url))
                page.goto(h.web.origin + '/?thread=' + tid)
                page.locator('#token').fill(h.token); page.locator('#login-form button').click()
                expect(page.locator('.md-file-link')).to_have_count(11)
            assert not any('/artifact?' in url for url in requests), 'Rendering alone must not fetch files'
            def download(page, label, path):
                with page.expect_download() as pending:
                    page.get_by_text(label, exact=True).click()
                result = pending.value
                assert result.failure() is None
                assert result.suggested_filename == path.name
                assert hashlib.sha256(Path(result.path()).read_bytes()).digest() == hashlib.sha256(path.read_bytes()).digest()
                assert page.url == h.web.origin + '/?thread=' + tid
            for label in ('Relative', 'Absolute', 'File URI', 'Code reference', 'Reference'):
                download(desktop, label, report)
            for page in (desktop, phone):
                download(page, 'Sandbox', outside)
                download(page, 'Local image', outside)
                download(page, 'Large archive', large)
                download(page, 'HTML file', unsafe)
                assert page.evaluate('window.downloadExecuted') is None
                page.get_by_text('Missing', exact=True).click()
                expect(page.locator('#notice')).to_contain_text('重新生成' if page.locator('html').get_attribute('lang').startswith('zh') else 'generate it again')
                page.get_by_text('Directory', exact=True).click()
                expect(page.locator('#notice')).to_contain_text('压缩' if page.locator('html').get_attribute('lang').startswith('zh') else 'archive')
                expect(page.get_by_role('link', name='Website', exact=True)).to_have_attribute('href', 'https://example.org/')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.reload(); expect(page.locator('.md-file-link')).to_have_count(11)
                download(page, 'Relative', report)
            assert not errors, errors
            print('PASS: desktop/phone downloads, Unicode names, relative/absolute/sandbox/file/reference links, large files, safe HTML, errors and history.', flush=True)
            browser.close()
    finally:
        h.tearDown()


if __name__ == '__main__': main()
