"""English default, Chinese switching, draft preservation and untranslated content."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.browsers'))
from playwright.sync_api import sync_playwright,expect
from test_remote import RemoteTests


def main():
    h=RemoteTests();h.setUp()
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'],ignore_default_args=['--disable-dev-shm-usage'])
            page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(h.web.origin)
            expect(page.locator('html')).to_have_attribute('lang','en')
            expect(page.locator('label[for=token]')).to_have_text('Gateway access token')
            page.locator('#token').fill(h.token);page.locator('#login-form button[type=submit]').click()
            page.locator('#menu').click();page.locator('#new-chat').click()
            page.locator('#new-form button[type=submit]').click()
            expect(page.locator('#prompt')).to_be_enabled()
            draft='模型 / Model — keep this draft unchanged'
            page.locator('#prompt').fill(draft)
            page.locator('.topbar [data-language-select]').select_option('zh-CN')
            expect(page.locator('html')).to_have_attribute('lang','zh-CN')
            expect(page.locator('#prompt')).to_have_value(draft)
            expect(page.locator('#settings-open')).to_have_text('设备与通知')
            self_error=page.evaluate("""async () => { const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json','Accept-Language':'zh-CN'},body:JSON.stringify({token:'fixture-wrong'})});return (await r.json()).error; }""")
            assert self_error=='访问口令不正确。',self_error
            page.locator('.topbar [data-language-select]').select_option('en')
            expect(page.locator('html')).to_have_attribute('lang','en')
            expect(page.locator('#prompt')).to_have_value(draft)
            expect(page.locator('#settings-open')).to_have_text('Devices and notifications')
            text=page.evaluate("""() => { const node=document.createElement('div'); window.remoteUI.markdown(node,'模型 / Model **运行中**'); return node.textContent; }""")
            assert '模型 / Model 运行中' in text,text
            for width in (390,768,1365):
                page.set_viewport_size({'width':width,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Page overflow at {width}'
            assert not errors,errors
            browser.close()
        print('PASS: English default, Chinese UI/API, locale persistence, draft preservation, untouched content and responsive layout.')
    finally:h.tearDown()


if __name__=='__main__':main()
