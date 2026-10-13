"""Rendering assertions used by the browser suite with the fake app-server."""
from playwright.sync_api import expect


def check_markdown(page, phone, harness, out):
    phone.locator('#prompt').fill('markdown-stream'); phone.locator('#send').click()
    for tab in (phone, page):
        message = tab.locator('.message.agent').last
        expect(message.locator('h1')).to_have_text('公式与 Markdown')
        expect(message.locator('.math-source')).to_contain_text(r'\exp')
        expect(message.locator('.math-inline .katex')).to_have_count(2)
    tid = phone.evaluate('state.current')
    harness.gateway.rpc.call('fixture/markdown/finish', {'threadId': tid})
    for tab in (phone, page):
        message = tab.locator('.message.agent').last
        expect(message).to_contain_text('完整渲染结束。')
        expect(message.locator('.math-source')).to_have_count(0)
        expect(message.locator('.katex')).to_have_count(8)
        expect(message.locator('.katex math')).to_have_count(8)
        expect(message.locator('blockquote p')).to_have_count(2)
        expect(message.locator('ol > li > ul > li')).to_have_count(2)
        expect(message.locator('.task-check.checked')).to_have_count(1)
        expect(message.locator('.task-check')).to_have_count(2)
        expect(message.locator('table th')).to_have_count(3)
        expect(message.locator('table td')).to_have_count(6)
        expect(message.locator('em')).to_have_text('斜体')
        expect(message.locator('s')).to_have_text('旧结论')
        expect(message.locator('pre .hljs-keyword').first).to_have_text('def')
        expect(message.locator('pre .katex')).to_have_count(0)
        expect(message.locator('script')).to_have_count(0)
        expect(message.locator('pre code')).to_contain_text('<script>alert(1)</script>')
        expect(message).to_contain_text('金额 $20 和 $30，转义 $40')
        assert message.locator('td').nth(1).evaluate('(n) => getComputedStyle(n).textAlign') == 'center'
        assert tab.evaluate("""async () => {await document.fonts.ready; return document.fonts.check('16px KaTeX_Main') && document.fonts.check('16px KaTeX_Math');}"""), 'Math fonts did not load'
        message.locator('h1').scroll_into_view_if_needed()
        tab.screenshot(path=str(out / ('markdown-dark.png' if tab == phone else 'markdown-light.png')))
    # Copy the original text, including indentation, from highlighted code.
    phone.evaluate("""() => {window.copiedCode = ''; Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => {window.copiedCode = text;}}});}""")
    phone.locator('.message.agent').last.locator('.copy-code').click()
    expect(phone.locator('.message.agent').last.locator('.copy-code')).to_have_text('已复制')
    assert phone.evaluate('window.copiedCode').startswith('def energy(mass: float) -> float:\n    # Formula')
    phone.reload()
    expect(phone.locator('.message.agent').last.locator('.katex')).to_have_count(8)
    expect(phone.locator('.message.agent').last.locator('pre .hljs-keyword').first).to_have_text('def')
    # A completed reply must render identically when loaded from stored history.
    expect(phone.locator('.message.agent').last).to_contain_text('完整渲染结束。')

    # Untrusted HTML, Markdown URLs and TeX commands cannot create active content.
    unsafe = r'''<script>window.pwned = true</script><img src=x onerror=alert(1)>
[bad](javascript:alert%281%29) [bad](data:text/html,evil) [bad](vbscript:evil)
![tracker](https://example.org/tracker.png)
$\href{javascript:alert(1)}{bad}$ $\htmlStyle{color:red}{x}$
$\includegraphics{https://example.org/tracker.png}$
$\def\a{\a}\a$ $\notARealCommand{<img onerror=alert(1)>}$
'''
    result = phone.evaluate("""text => {
      const n = document.createElement('div'); remoteUI.markdown(n, text);
      return {active: n.querySelectorAll('script,img,iframe,object,embed,style,[onclick],[onerror]').length,
        badLinks: [...n.querySelectorAll('a')].filter(a => !/^https?:/.test(a.href)).length,
        badClasses: n.querySelectorAll('.enclosing').length,
        fallback: n.querySelectorAll('.math-source').length,
        externalImageLinks: n.querySelectorAll('.md-image-link').length};
    }""", unsafe)
    assert result['active'] == result['badLinks'] == result['badClasses'] == 0, result
    assert result['fallback'] >= 2 and result['externalImageLinks'] == 1, result
    # Delimiters inside code, escaped currency, malformed and incomplete input.
    cases = [
        (r'\$5 and $10 and $20', 0),
        (r'`$x$` and `\(x\)`', 0),
        ('```tex\n$x$\n\\[y\\]\n```', 0),
        ('    $indented_code$\n', 0),
        (r'$x_1$ and \(y_2\)', 2),
        (r'Inline $$x^2$$ formula', 1),
        (r'\[\frac{1}{2}\]', 1),
        ('> $$\n> x^2\n> $$', 1),
        (r'\(\text{price: \$5}\)', 1),
        (r'\(unfinished', 0),
        (r'$\frac{1}{2', 0),
    ]
    for text, count in cases:
        result = phone.evaluate("""text => {const n = document.createElement('div'); remoteUI.markdown(n, text); return {count: n.querySelectorAll('.katex').length, text:n.textContent};}""", text)
        assert result['count'] == count, (text, result)
        if 'unfinished' in text: assert result['text'].rstrip('\n') == text

    # Expandable commands, diffs and JSON use the same safe code renderer.
    phone.evaluate("""() => {
      upsert({id:'format-command', type:'commandExecution', command:'python train.py --epochs 3 && echo "done"', aggregatedOutput:'epoch 1/3: loss=0.15\\n<script>literal output</script>', exitCode:0}, true);
      upsert({id:'format-diff', type:'fileChange', changes:[{path:'model.py', diff:'@@ -1 +1 @@\\n-old = 1\\n+new = 2'}]}, true);
      upsert({id:'format-json', type:'mcpToolCall', server:'test', tool:'read', arguments:{enabled:true, count:3}, result:{status:'ok', items:[1,2]}}, true);
    }""")
    for kind in ('command', 'diff', 'json'):
        phone.locator(f'[data-item-id="format-{kind}"] summary').click()
    expect(phone.locator('[data-item-id="format-command"] .hljs-string')).to_have_text('"done"')
    expect(phone.locator('[data-item-id="format-command"] script')).to_have_count(0)
    expect(phone.locator('[data-item-id="format-command"] pre').last).to_contain_text('<script>literal output</script>')
    expect(phone.locator('[data-item-id="format-diff"] .hljs-addition')).to_have_text('+new = 2')
    expect(phone.locator('[data-item-id="format-diff"] .hljs-deletion')).to_have_text('-old = 1')
    expect(phone.locator('[data-item-id="format-json"] .hljs-attr').first).to_have_text('"arguments"')
    phone.locator('[data-item-id="format-diff"]').scroll_into_view_if_needed()
    phone.screenshot(path=str(out/'tool-code-dark.png'))
    # Large tables, code, and equations scroll within the message at phone widths.
    for width in (320, 360, 390):
        phone.set_viewport_size({'width': width, 'height': 844})
        phone.evaluate(r'''() => upsert({id:'overflow-math', type:'agentMessage', text:
          '$$' + Array(40).fill('x_i^2').join(' + ') + '$$\n\n' +
          '|A|B|C|D|E|\n|-|-|-|-|-|\n|a|b|c|d|e|\n\n' +
          '```python\nprint("' + 'abcdefghij'.repeat(70) + '")\n```'}, true)''')
        expect(phone.locator('.message.agent:last-child .math-block')).to_have_count(1)
        result = phone.evaluate('''() => ({page:document.documentElement.scrollWidth<=innerWidth,
          timeline:document.getElementById('timeline').scrollWidth<=document.getElementById('timeline').clientWidth,
          scrollable:[...document.querySelector('.message.agent:last-child').querySelectorAll('.math-block,.table-scroll,pre')].map(n=>n.scrollWidth>n.clientWidth)})''')
        assert result['page'] and result['timeline'] and all(result['scrollable']), (width, result)
    phone.set_viewport_size({'width': 390, 'height': 844})
    phone.reload(); expect(phone.locator('.message.agent').last.locator('.katex')).to_have_count(8)
