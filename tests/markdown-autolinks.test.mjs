import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkReadableAutolinks } from '../src/markdown-autolinks.ts';

const render = source => renderToStaticMarkup(createElement(Markdown, { remarkPlugins: [remarkGfm, remarkReadableAutolinks] }, source));
const hrefs = html => [...html.matchAll(/<a href="([^"]+)"/g)].map(match => match[1]);

test('bare links end before CJK sentence punctuation while prose stays visible', () => {
  for (const punctuation of ['，', '。', '、', '；', '：', '！', '？', '（', '）', '」', '》']) {
    const source = `页面见 https://example.com/${punctuation}页面标题为 Athena`;
    const html = render(source);
    assert.deepEqual(hrefs(html), ['https://example.com/'], punctuation);
    assert.ok(html.includes(`https://example.com/</a>${punctuation}页面标题为 Athena`), html);
  }
  const html = render('Visit www.example.com/，然后查看 https://second.example/中文路径。');
  assert.deepEqual(hrefs(html), ['http://www.example.com/', 'https://second.example/%E4%B8%AD%E6%96%87%E8%B7%AF%E5%BE%84']);
  assert.ok(html.includes('</a>，然后查看 '));
  assert.ok(html.includes('中文路径</a>。'));
});

test('explicit Markdown, reference and angle destinations keep their intended punctuation', () => {
  const target = 'https://example.com/，合法路径';
  const encoded = 'https://example.com/%EF%BC%8C%E5%90%88%E6%B3%95%E8%B7%AF%E5%BE%84';
  for (const source of [`[页面](${target})`, `[${target}](${target})`, `<${target}>`, `[页面][ref]\n\n[ref]: ${target}`]) {
    assert.deepEqual(hrefs(render(source)), [encoded], source);
  }
});

test('encoded URL paths are not decoded or mistaken for prose separators', () => {
  const target = 'https://example.com/%EF%BC%8C%E9%A1%B5?q=a%EF%BC%9Ab#part%EF%BC%81';
  assert.deepEqual(hrefs(render(target)), [target]);
  assert.deepEqual(hrefs(render(`${target}，这才是正文`)), [target]);
  const ordinary = 'https://example.com/a,b;c:d?q=x,y';
  assert.deepEqual(hrefs(render(ordinary)), [ordinary]);
});

test('inline code and fenced code preserve the original URL text without creating links', () => {
  const text = 'https://example.com/，页面标题为';
  for (const source of [`\`${text}\``, `\`\`\`text\n${text}\n\`\`\``]) {
    const html = render(source);
    assert.deepEqual(hrefs(html), []);
    assert.ok(html.includes(text));
  }
});

test('nested lists and tables retain surrounding formatting and visible punctuation', () => {
  const html = render('- **来源**：https://example.com/，确认后继续\n\n| 来源 |\n| --- |\n| https://second.example/。随后 |');
  assert.deepEqual(hrefs(html), ['https://example.com/', 'https://second.example/']);
  assert.ok(html.includes('<strong>来源</strong>'));
  assert.ok(html.includes('</a>，确认后继续'));
  assert.ok(html.includes('</a>。随后</td>'));
});

test('rendering does not rewrite source or trim nodes without verifiable source positions', () => {
  const source = 'https://example.com/，页面标题为';
  const file = { value: source };
  const explicit = { type: 'link', url: source, children: [{ type: 'text', value: source }] };
  const tree = { type: 'root', children: [explicit] };
  remarkReadableAutolinks()(tree, file);
  assert.equal(file.value, source);
  assert.equal(tree.children[0], explicit);
});
