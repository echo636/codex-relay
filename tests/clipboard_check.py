"""Clipboard, upload races, and draft ownership in Chromium; local fixture only."""
import base64
import json
import os
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from browser_locale import install as install_test_locale
install_test_locale()
from test_remote import RemoteTests


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


def paste(page, files, text='', items_only=False, drop=False):
    return page.evaluate("""({files,text,itemsOnly,drop}) => {
      const data = new DataTransfer();
      for (const file of files) data.items.add(new File([file.size ? new Uint8Array(file.size) : file.body || 'fixture'], file.name, {type:file.type || 'text/plain'}));
      if (text) data.setData('text/plain', text);
      const event = drop ? new DragEvent('drop', {dataTransfer:data,bubbles:true,cancelable:true}) : new ClipboardEvent('paste', {clipboardData:data,bubbles:true,cancelable:true});
      if (itemsOnly) Object.defineProperty(event, 'clipboardData', {value:{files:[],items:data.items,getData:type=>data.getData(type)}});
      document.getElementById(drop ? 'composer' : 'prompt').dispatchEvent(event);
      return event.defaultPrevented;
    }""", {'files': files, 'text': text, 'itemsOnly': items_only, 'drop': drop})


def until(page, expression):
    # Avoid Playwright's page-side eval polling: the product CSP disallows eval.
    for _ in range(100):
        if page.evaluate(expression): return
        page.wait_for_timeout(100)
    raise AssertionError('Timed out: ' + expression)


def main():
    h = RemoteTests(); h.setUp()
    try:
        tids = []
        for title in ('Clipboard draft A', 'Clipboard draft B'):
            code, thread, _ = h.request('/api/threads', {'cwd': str(h.work), 'title': title}, h.cookie)
            assert code == 201
            tids.append(thread['id'])
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            ctx = browser.new_context(permissions=['clipboard-read', 'clipboard-write'], viewport={'width': 1280, 'height': 900})
            page = ctx.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(h.web.origin + '/?thread=' + tids[0])
            page.locator('#token').fill(h.token); page.locator('#login-form button').click()
            expect(page.locator('#prompt')).to_be_enabled()

            # Exercise a real keyboard paste, not just our synthetic event helper.
            page.locator('#prompt').fill('before OLD after')
            page.locator('#prompt').evaluate('(node)=>node.setSelectionRange(7,10)')
            page.evaluate("navigator.clipboard.writeText('new text')")
            page.locator('#prompt').press('Control+v')
            expect(page.locator('#prompt')).to_have_value('before new text after')
            expect(page.locator('#attachments .attachment-chip')).to_have_count(0)
            assert not paste(page, [], 'normal text')

            data = base64.b64encode(png()).decode()
            page.evaluate("""async data => {
                const bytes = Uint8Array.from(atob(data), c=>c.charCodeAt(0));
                await navigator.clipboard.write([new ClipboardItem({'image/png': new Blob([bytes], {type:'image/png'})})]);
            }""", data)
            page.locator('#prompt').press('Control+v')
            expect(page.locator('#attachments img')).to_have_count(1)
            until(page, "document.querySelector('#attachments img').naturalWidth === 1")
            expect(page.locator('#prompt')).to_have_value('before new text after')
            assert paste(page, [{'name':'report.txt','body':'clipboard file content'}])
            expect(page.locator('#attachments .attachment-chip')).to_have_count(2)  # No duplicate from .items + .files.
            assert paste(page, [{'name':'fallback.csv'}], items_only=True)
            expect(page.locator('#attachments .attachment-chip')).to_have_count(3)
            assert paste(page, [{'name':'dropped.log'}], drop=True)
            expect(page.locator('#attachments .attachment-chip')).to_have_count(4)
            expect(page.locator('#upload-status')).to_be_hidden()
            page.locator('#send').click()
            expect(page.locator('#attachments .attachment-chip')).to_have_count(0)
            expect(page.locator('#messages')).to_contain_text('Fixture response')
            turns = h.gateway.history(tids[0])['turns']
            inputs = next(i['content'] for i in turns[-1]['items'] if i['type'] == 'userMessage')
            assert len([i for i in inputs if i['type'] == 'localImage']) == 1
            assert len([i for i in inputs if i['type'] == 'text' and i['text'].startswith('User attachment')]) == 3
            upload = h.gateway.store.rows('SELECT * FROM uploads WHERE name=?', ('report.txt',))[0]
            assert Path(upload['path']).read_text() == 'clipboard file content'
            print('PASS: real keyboard text/image paste, image preview, file bytes, item-only fallback, drag/drop, and attachment delivery.', flush=True)

            delayed = []
            def hold(route):
                if route.request.post_data_json['name'] == 'slow.txt': delayed.append(route)
                else: route.continue_()
            page.route('**/api/uploads', hold)
            page.locator('#prompt').fill('old')
            page.locator('#prompt').evaluate('(n)=>n.setSelectionRange(0,3)')
            assert paste(page, [{'name':'slow.txt'}], text='mixed text')
            expect(page.locator('#prompt')).to_have_value('mixed text')
            expect(page.locator('#upload-status')).to_be_visible()
            expect(page.locator('#send')).to_be_disabled()
            page.locator('#prompt').press('Enter')
            assert len(h.gateway.history(tids[0])['turns']) == 1
            page.locator('[data-thread-id="'+tids[1]+'"]').click()
            expect(page.locator('#prompt')).to_be_enabled()
            expect(page.locator('#upload-status')).to_be_hidden()
            expect(page.locator('#attachments .attachment-chip')).to_have_count(0)
            assert delayed
            delayed.pop().continue_()
            until(page, 'remoteUI.uploadCount() === 0')
            page.locator('[data-thread-id="'+tids[0]+'"]').click()
            expect(page.locator('#attachments')).to_contain_text('slow.txt')
            expect(page.locator('#prompt')).to_have_value('mixed text')
            expect(page.locator('#prompt')).to_be_enabled()
            page.locator('#attachments button').click()

            # A pending batch reserves its slots against another paste/selection.
            assert paste(page, [{'name':'slow.txt'}] + [{'name':f'file-{i}.txt'} for i in range(6)])
            assert paste(page, [{'name':'excess-a.txt'},{'name':'excess-b.txt'}])
            expect(page.locator('#notice')).to_contain_text('最多 8 个附件')
            page.locator('#file-input').set_input_files({'name':'eighth.txt','mimeType':'text/plain','buffer':b'last'})
            expect(page.locator('#upload-status')).to_contain_text('8 个附件')
            delayed.pop().continue_()
            expect(page.locator('#attachments .attachment-chip')).to_have_count(8)
            expect(page.locator('#upload-status')).to_be_hidden()
            for _ in range(8): page.locator('#attachments button').first.click()
            assert paste(page, [{'name':'too-large.bin','size':10*1024*1024+1}])
            expect(page.locator('#notice')).to_contain_text('10 MiB')
            expect(page.locator('#attachments .attachment-chip')).to_have_count(0)
            page.unroute('**/api/uploads', hold)

            def fail_one(route):
                if route.request.post_data_json['name'] == 'bad.txt': route.fulfill(status=413, content_type='application/json', body=json.dumps({'error':'fixture upload failure'}))
                else: route.continue_()
            page.route('**/api/uploads', fail_one)
            paste(page, [{'name':'bad.txt'},{'name':'good.txt'}])
            expect(page.locator('#attachments')).to_contain_text('good.txt')
            expect(page.locator('#notice')).to_contain_text('bad.txt 上传失败')
            expect(page.locator('#upload-status')).to_be_hidden()
            expect(page.locator('#send')).to_be_enabled()
            page.unroute('**/api/uploads', fail_one)
            page.route('**/api/uploads', hold)
            paste(page, [{'name':'slow.txt'}])
            expect(page.locator('#upload-status')).to_be_visible()
            page.locator('#logout').click()
            expect(page.locator('#login')).to_be_visible()
            delayed.pop().fulfill(status=201, content_type='application/json', body=json.dumps({'id':'a'*32,'name':'slow.txt','mime':'text/plain','size':1}))
            page.wait_for_timeout(200)
            assert page.evaluate('state.attachments.length') == 0
            assert not errors, errors
            print('PASS: mixed paste, pending-send guard, draft ownership on switching, shared count/size limits, upload failure recovery, and logout privacy.', flush=True)
            browser.close()
    finally: h.tearDown()


if __name__ == '__main__': main()
