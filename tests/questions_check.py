"""Async question cards, native reply envelopes and blocking questions; fixtures only."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
from playwright.sync_api import sync_playwright, expect
from test_remote import RemoteTests


def main():
    h = RemoteTests(); h.setUp()
    try:
        tid = h.new()
        h.message(tid, 'hang', 'question-fixture-initial')
        turn = h.gateway.thread(tid)['turn_id']
        item = {'id': 'call-question-fixture', 'type': 'agentMessage', 'text': 'A few preferences:', 'questions': [
            {'title': 'Choose a format', 'options': ['Compact', 'Detailed']},
            {'title': 'Additional details', 'options': None},
            {'title': 'Optional context'},
        ]}
        def event(method, **params):
            h.gateway.rpc.call('fixture/event', {'method': method, 'params': {'threadId': tid, 'turnId': turn, **params}})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'], ignore_default_args=['--disable-dev-shm-usage'])
            desktop = browser.new_page(viewport={'width': 1280, 'height': 900})
            phone = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            phone.add_init_script("localStorage.setItem('codex-remote-language', 'zh-CN')")
            errors, sent = [], []
            for page in (desktop, phone):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: sent.append(request.post_data_json) if request.url.endswith('/message') else None)
                page.goto(h.web.origin + '/?thread=' + tid)
                page.locator('#token').fill(h.token); page.locator('#login-form button').click()
                expect(page.locator('#prompt')).to_be_enabled()
            event('item/started', item=item)
            card = desktop.locator('.async-questions')
            expect(card.locator('legend')).to_have_count(3)
            expect(card.get_by_label('Compact', exact=True)).to_be_checked()
            expect(phone.locator('.async-questions h3')).to_have_text('Codex 想确认一下')
            assert not sent  # Preselection never submits an answer.
            card.get_by_label('Detailed', exact=True).check()
            details = card.locator('textarea').nth(1)
            answer = 'Keep line breaks\n<img src=x onerror=alert(1)>'
            details.fill(answer); details.focus()
            details.evaluate('(n) => n.setSelectionRange(3, 7)')
            item['text'] += ' Work continues.'
            event('item/completed', item=item)
            expect(desktop.locator('.message.agent .content').first).to_contain_text('Work continues.')
            expect(details).to_have_value(answer); expect(details).to_be_focused()
            assert details.evaluate('(n) => [n.selectionStart, n.selectionEnd]') == [3, 7]

            # A definite HTTP failure preserves both draft and idempotency key.
            desktop.route('**/api/threads/*/message', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'Fixture unavailable'})), times=1)
            card.locator('button[type=submit]').click()
            expect(card.locator('.error')).to_have_text('Fixture unavailable')
            expect(details).to_have_value(answer)
            if desktop.locator('.topbar [data-language-select]').count():
                desktop.locator('.topbar [data-language-select]').select_option('zh-CN')
                expect(card.locator('h3')).to_have_text('Codex 想确认一下')
                expect(details).to_have_value(answer)
                expect(card.get_by_label('Detailed', exact=True)).to_be_checked()
                desktop.locator('.topbar [data-language-select]').select_option('en')
                expect(card.locator('h3')).to_have_text('Codex has a question')
                expect(details).to_have_value(answer)
            card.locator('button[type=submit]').click()
            expect(card.locator('.question-answer').nth(1)).to_contain_text(answer)
            expect(phone.locator('.question-answer').nth(1)).to_contain_text(answer)
            assert sent[0] == sent[1] and sent[1]['mode'] == 'auto'
            replies = json.loads(sent[1]['text'].split('\n', 2)[1])
            assert replies == [
                {'answer': 'Detailed', 'question': 'Choose a format', 'questionItemId': '["request_user_input_async","call-question-fixture",0]'},
                {'answer': answer, 'question': 'Additional details', 'questionItemId': '["request_user_input_async","call-question-fixture",1]'},
            ]
            assert len(h.gateway.history(tid)['turns']) == 1  # Steered, not queued for another turn.
            expect(card).to_have_attribute('data-status', 'pending')
            phone.locator('.async-questions textarea').nth(2).fill('From another device')
            phone.locator('.async-questions button[type=submit]').click()
            for page in (desktop, phone):
                expect(page.locator('.async-questions')).to_have_attribute('data-status', 'answered')
                assert all('send_user_message_question_reply' not in text for text in page.locator('.message.user').all_text_contents())
                expect(page.locator('.question-reply img')).to_have_count(0)
                page.reload()
                expect(page.locator('.async-questions')).to_have_attribute('data-status', 'answered')
                expect(page.locator('.async-questions button[type=submit]')).to_be_hidden()
            print('PASS: live/history questions, native reply IDs, partial answers, steering, retry, drafts/focus, two-device sync and safe text.', flush=True)

            # A question remains answerable after the original turn has finished.
            event('item/completed', item={'id': 'idle-question', 'type': 'agentMessage', 'text': '', 'questions': [{'title': 'Next step?'}]})
            event('turn/completed', turn={'id': turn, 'status': 'completed'})
            idle = desktop.locator('[data-question-item="idle-question"]')
            expect(idle.locator('button')).to_be_disabled()
            idle.locator('textarea').fill('Continue with the next step')
            idle.locator('button').click()
            expect(idle).to_have_attribute('data-status', 'answered')
            h.until(lambda: h.gateway.thread(tid)['status'] == 'completed')
            assert len(h.gateway.history(tid)['turns']) == 2
            desktop.locator('#prompt').fill('question'); desktop.locator('#prompt').press('Enter')
            expect(desktop.locator('#requests .request')).to_be_visible()
            desktop.locator('#requests input').fill('Blocking answer')
            desktop.locator('#requests button.approve').click()
            expect(desktop.locator('#requests .request')).to_have_count(0)
            h.until(lambda: h.gateway.thread(tid)['status'] == 'completed')
            desktop.evaluate("() => upsert({id:'plain-envelope',type:'userMessage',content:[{type:'text',text:'<send_user_message_question_reply>not JSON</send_user_message_question_reply>'}]},true)")
            expect(desktop.locator('.message.user').last).to_contain_text('not JSON')
            phone.screenshot(path=str(ROOT / 'test-results' / 'questions-phone.png'), full_page=True)
            assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            print('PASS: idle turn continuation, free-text-only questions, original blocking questions and malformed envelope fallback.', flush=True)
            browser.close()
    finally:
        h.tearDown()


if __name__ == '__main__':
    (ROOT / 'test-results').mkdir(exist_ok=True)
    main()
