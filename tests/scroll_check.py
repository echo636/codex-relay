"""Long transcript and bottom-follow regressions. Uses only the protocol fixture."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from browser_locale import install as install_test_locale
install_test_locale()
from test_remote import RemoteTests


def settled(page, condition):
    for _ in range(150):
        if page.evaluate('() => (' + condition + ')'):
            return
        page.wait_for_timeout(100)
    raise AssertionError('Timed out: ' + condition)


def main():
    harness = RemoteTests(); harness.setUp()
    errors = []
    try:
        # This fixture supplies the transcript at the RPC boundary. Give its
        # list entry a real title so it is not classified as an empty draft.
        code, thread, _ = harness.request('/api/threads', {
            'cwd': str(harness.work), 'title': 'Long transcript fixture'
        }, harness.cookie)
        assert code == 201, thread
        tid = thread['id']
        old = json.dumps({'method': 'item/agentMessage/delta', 'params': {'threadId': tid, 'itemId': 'obsolete', 'delta': 'OLD_REPLAY_SENTINEL'}})
        with harness.gateway.store.lock, harness.gateway.store.db:
            harness.gateway.store.db.executemany('INSERT INTO events(thread_id,payload) VALUES(?,?)', [(tid, old)] * 3000)
        items = [{'id': f'tool-{i}', 'type': 'commandExecution', 'command': 'python train.py', 'aggregatedOutput': 'log line\n' * 500, 'exitCode': 0} for i in range(600)]
        items.append({'id': 'race', 'type': 'agentMessage', 'text': 'prefix'})
        real_call = harness.gateway.rpc.call

        def history(method, params=None, **kwargs):
            result = real_call(method, params, **kwargs)
            if method == 'thread/turns/list' and params.get('cursor') is None:
                sequence = result['_transportSeq']
                for delta, seq in [('prefix', sequence - 1), ('suffix', sequence + 1)]:
                    harness.gateway.store.event(tid, 'item/agentMessage/delta', {'threadId': tid, 'itemId': 'race', 'delta': delta},
                                                transport_seq=seq, transport_epoch=harness.gateway.event_epoch)
                result.update(data=[{'id': 'long-turn', 'status': 'inProgress', 'items': items}], nextCursor='older')
            return result

        with patch.object(harness.gateway.rpc, 'call', side_effect=history), sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            for name, viewport in [('desktop', {'width': 1365, 'height': 950}), ('mobile', {'width': 390, 'height': 844})]:
                ctx = browser.new_context(viewport=viewport, is_mobile=name == 'mobile', has_touch=name == 'mobile')
                page = ctx.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(harness.web.origin)
                page.locator('#token').fill(harness.token); page.locator('#login-form button').click()
                expect(page.locator('#prompt')).to_be_enabled()
                settled(page, '!pendingRenders.size && !renderFrame')
                expect(page.locator('.message.agent').last).to_contain_text('prefixsuffix')
                expect(page.locator('#messages')).not_to_contain_text('OLD_REPLAY_SENTINEL')
                expect(page.locator('.message.tool')).to_have_count(600)
                expect(page.locator('.tool-content')).to_have_count(0)
                settled(page, "$('timeline').scrollHeight - $('timeline').scrollTop - $('timeline').clientHeight < 2")
                # Read older content and ensure incoming output does not move it.
                page.evaluate("$('timeline').scrollTop = 500")
                jump = page.locator('#jump-bottom'); expect(jump).to_be_visible()
                settled(page, '!followLatest')
                before = page.locator('#timeline').evaluate('(t) => t.scrollTop')
                page.evaluate("upsert({id:'stream',type:'agentMessage',text:'Streaming output\\n'.repeat(80)},false)")
                settled(page, '!pendingRenders.size && !renderFrame')
                page.wait_for_timeout(100)
                assert abs(page.locator('#timeline').evaluate('(t)=>t.scrollTop') - before) <= 1
                # One click must keep following through later batches and layout changes.
                jump.click()
                page.evaluate(r"""() => {
                    window.renderCalls=0;
                    const original=remoteUI.markdown;
                    remoteUI.markdown=(...args)=>{window.renderCalls++;return original(...args)};
                    for(let i=0;i<200;i++) updateEvent({method:'item/agentMessage/delta',params:{itemId:'burst',delta:'more text\n'}});
                    setTimeout(()=>{
                        $('notice').textContent='Composer height changes '.repeat(20);
                        const late=el('div','message','Late image dimensions');
                        late.style.height='400px'; $('messages').append(late);
                    },100);
                }""")
                page.wait_for_timeout(350)
                settled(page, "!pendingRenders.size && $('timeline').scrollHeight - $('timeline').scrollTop - $('timeline').clientHeight < 2")
                assert page.evaluate('window.renderCalls') < 10
                expect(jump).to_be_hidden()
                # Explicit upward gestures stop following even while output continues.
                page.locator('#timeline').hover(); page.mouse.wheel(0, -650)
                settled(page, '!followLatest')
                page.wait_for_timeout(150)
                before = page.locator('#timeline').evaluate('(t)=>t.scrollTop')
                page.evaluate("upsert({id:'last',type:'agentMessage',text:'latest response\\n'.repeat(60)},true)")
                settled(page, '!pendingRenders.size && !renderFrame')
                page.wait_for_timeout(100)
                assert abs(page.locator('#timeline').evaluate('(t)=>t.scrollTop') - before) <= 1
                page.emulate_media(reduced_motion='reduce')
                jump.click()
                settled(page, "$('timeline').scrollHeight - $('timeline').scrollTop - $('timeline').clientHeight < 2")
                # A server connection with a new sequence counter must not be filtered.
                harness.gateway.store.event(tid, 'item/completed', {'threadId': tid, 'item': {'id':'new-epoch','type':'agentMessage','text':'new connection event'}}, transport_seq=1, transport_epoch='new-fixture-connection')
                expect(page.locator('#messages')).to_contain_text('new connection event')
                # Prepending an older page preserves the existing message's position.
                page.evaluate("$('notice').textContent=''")
                pending = []
                page.route('**/history?**', lambda route: pending.append(route))
                page.locator('#older').click()
                for _ in range(100):
                    if pending: break
                    page.wait_for_timeout(50)
                assert pending
                anchor = page.evaluate("state.items.get('tool-0').node.getBoundingClientRect().top")
                pending.pop().fulfill(status=200, content_type='application/json', body=json.dumps({'turns':[{'status':'completed','items':[{'id':'old-page','type':'agentMessage','text':'Older message\n\n'*100}]}], 'nextCursor':None}))
                expect(page.locator('#messages')).to_contain_text('Older message')
                page.wait_for_timeout(100)
                assert abs(page.evaluate("state.items.get('tool-0').node.getBoundingClientRect().top") - anchor) <= 2
                page.locator('[data-item-id="tool-0"] summary').click()
                expect(page.locator('[data-item-id="tool-0"] .tool-content')).to_be_visible()
                expect(page.locator('.tool-content')).to_have_count(1)
                print(name + ': 3,000 old events skipped; 600 collapsed tools deferred; snapshot race, batched rendering, single-click following, manual scrolling, resize and history anchors passed', flush=True)
                ctx.close()
            browser.close()
        assert not errors, errors
    finally:
        harness.tearDown()


if __name__ == '__main__':
    main()
