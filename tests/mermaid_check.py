"""Mermaid rendering and composer keys, using only the local protocol fixture."""
import os
from pathlib import Path
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from browser_locale import install as install_test_locale
install_test_locale()
from test_remote import RemoteTests
from clipboard_check import until


def main():
    h = RemoteTests(); h.setUp()
    out = ROOT / 'test-results'; out.mkdir(exist_ok=True)
    try:
        _, thread, _ = h.request('/api/threads', {'cwd': str(h.work), 'title': 'Mermaid fixture'}, h.cookie)
        tid = thread['id']
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            desktop = browser.new_page(viewport={'width':1280,'height':900}, color_scheme='light')
            phone = browser.new_page(viewport={'width':390,'height':844}, is_mobile=True, has_touch=True, color_scheme='dark')
            errors, requests = [], []
            for page in (desktop, phone):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and 'Content Security Policy' in msg.text else None)
                page.on('request', lambda request: requests.append(request.url))
                page.goto(h.web.origin + '/?thread=' + tid)
                page.locator('#token').fill(h.token); page.locator('#login-form button').click()
                expect(page.locator('#prompt')).to_be_enabled()
            prompt = desktop.locator('#prompt')
            prompt.fill('firstSecond'); prompt.evaluate('(n)=>n.setSelectionRange(5,5)')
            prompt.press('Control+Enter'); expect(prompt).to_have_value('first\nSecond')
            prompt.press('Meta+Enter'); expect(prompt).to_have_value('first\n\nSecond')
            prompt.press('Shift+Enter'); expect(prompt).to_have_value('first\n\n\nSecond')
            assert not h.gateway.history(tid)['turns']
            prompt.press('Enter')
            expect(desktop.locator('.turn-result[data-status="completed"]')).to_have_count(1)
            expect(prompt).to_have_value('')
            prompt.fill('中文输入法确认')
            prompt.evaluate("""n => {
                n.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
                n.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,isComposing:true,bubbles:true,cancelable:true}));
                n.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
                n.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
            }""")
            desktop.wait_for_timeout(100)
            assert len(h.gateway.history(tid)['turns']) == 1
            expect(prompt).to_have_value('中文输入法确认')
            prompt.press('Enter')
            expect(desktop.locator('.turn-result[data-status="completed"]')).to_have_count(2)
            prompt.fill('/help'); prompt.press('Control+Enter'); expect(prompt).to_have_value('/help\n')
            expect(desktop.locator('#command-help')).not_to_be_visible()
            prompt.fill('/help'); prompt.press('Enter')
            expect(desktop.locator('#command-help')).to_be_visible()
            desktop.locator('#command-help .dialog-close').click()
            print('PASS: Enter sends, Ctrl/Meta/Shift+Enter inserts newline, IME confirmation is not submitted, and slash commands retain Enter execution.', flush=True)

            prompt.fill('hang'); prompt.press('Enter')
            expect(desktop.locator('#stop')).to_be_visible()
            turn = h.gateway.thread(tid)['turn_id']
            def event(method, params): h.gateway.rpc.call('fixture/event', {'method':method,'params':params})
            start = '```mermaid\nflowchart LR\n  A[开始] -->'
            end = ' B[结束]\n```\n'
            event('item/started', {'threadId':tid,'turnId':turn,'item':{'id':'diagram-stream','type':'agentMessage','text':''}})
            event('item/agentMessage/delta', {'threadId':tid,'turnId':turn,'itemId':'diagram-stream','delta':start})
            for page in (desktop,phone):
                expect(page.locator('.mermaid-preview')).to_contain_text('图表生成中')
                expect(page.locator('.mermaid-image')).to_have_count(0)
            event('item/agentMessage/delta', {'threadId':tid,'turnId':turn,'itemId':'diagram-stream','delta':end})
            for page in (desktop,phone):
                expect(page.locator('.mermaid-block')).to_have_attribute('data-status','rendered',timeout=45000)
                until(page,"document.querySelector('.mermaid-image').naturalWidth > 0")
                svg = unquote(page.locator('.mermaid-image').get_attribute('src').split(',',1)[1])
                assert '<svg' in svg and '开始' in svg and '结束' in svg
                expect(page.locator('.mermaid-source')).not_to_have_attribute('open','')
            event('item/completed', {'threadId':tid,'turnId':turn,'item':{'id':'diagram-stream','type':'agentMessage','text':start+end}})
            event('turn/completed', {'threadId':tid,'turn':{'id':turn,'status':'completed'}})
            phone.reload()
            expect(phone.locator('.mermaid-block')).to_have_attribute('data-status','rendered',timeout=45000)
            old = phone.locator('.mermaid-image').get_attribute('src')
            phone.locator('#theme-toggle').click()
            until(phone,"document.querySelector('.mermaid-block').dataset.theme === 'light'")
            for _ in range(100):
                if phone.locator('.mermaid-image').get_attribute('src') != old: break
                phone.wait_for_timeout(100)
            assert phone.locator('.mermaid-image').get_attribute('src') != old
            phone.locator('.diagram-zoom').click()
            expect(phone.locator('.mermaid-block')).to_have_class('mermaid-block mermaid-expanded')
            phone.locator('.diagram-zoom').click()
            phone.locator('.mermaid-source summary').click()
            expect(phone.locator('.mermaid-source code')).to_contain_text('flowchart LR')
            phone.locator('.mermaid-source summary').click()
            for page,name in ((desktop,'desktop'),(phone,'phone')):
                page.locator('.mermaid-block').scroll_into_view_if_needed()
                page.screenshot(path=str(out/('mermaid-'+name+'.png')))

            samples = [
                'sequenceDiagram\n  participant U as 用户\n  participant A as Agent\n  U->>A: 执行任务\n  A-->>U: 完成',
                'classDiagram\n  Agent --> Tool\n  class Agent {\n    +run()\n  }',
                'stateDiagram-v2\n  [*] --> Working\n  Working --> Done\n  Done --> [*]',
                'erDiagram\n  USER ||--o{ TASK : owns\n  USER {\n    string name\n  }',
                'gantt\n  title Work\n  dateFormat YYYY-MM-DD\n  section Build\n  Code :a1, 2026-10-13, 2d',
                'pie title Result\n  "Pass" : 8\n  "Fail" : 2',
                'mindmap\n  root((Project))\n    Code\n    Tests',
            ]
            for i,source in enumerate(samples):
                desktop.evaluate("({id,text})=>upsert({id,type:'agentMessage',text},true)",{'id':f'example-{i}','text':'```mermaid\n'+source+'\n```'})
            expect(desktop.locator('.mermaid-block[data-status="rendered"]')).to_have_count(8,timeout=45000)
            for width in (320,390,1280):
                desktop.set_viewport_size({'width':width,'height':900})
                assert desktop.evaluate("document.documentElement.scrollWidth <= innerWidth && $('timeline').scrollWidth <= $('timeline').clientWidth")
            desktop.evaluate("() => upsert({id:'broken-diagram',type:'agentMessage',text:'```mermaid\\nflowchart LR\\n  A --> [broken\\n```'},true)")
            expect(desktop.locator('.mermaid-block[data-status="error"]')).to_have_count(1,timeout=30000)
            expect(desktop.locator('.mermaid-block[data-status="error"] details')).to_have_attribute('open','')
            assert all(url.startswith(h.web.origin) or url.startswith('data:') for url in requests), requests
            frame = next(f for f in desktop.frames if f.url.endswith('/mermaid-frame.html'))
            assert frame.evaluate("() => {try{return !!parent.document;}catch(e){return e.name;}}") == 'SecurityError'
            assert not errors, errors
            print('PASS: streamed/history Mermaid, 8 diagram types, theme refresh, image isolation, source fallback, zoom, responsive layout and no external requests.', flush=True)
            browser.close()
    finally: h.tearDown()


if __name__ == '__main__': main()
