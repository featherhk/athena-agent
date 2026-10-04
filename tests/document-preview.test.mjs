import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import React, { act, createElement as h } from 'react';

const window = new Window({ url: 'https://athena.invalid', settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true } });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/FilePreview.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const module = { exports: {} };
vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: 'FilePreview.tsx' })(
  name => name.endsWith('.css') ? {} : name === './i18n' ? { useI18n: () => ({ t: key => key }) }
    : name === './MessageBody' ? { MarkdownContent: props => h('div', null, props.content) } : require(name), module, module.exports,
);
const { FilePreview } = module.exports;
const file = { id: 'doc_1', name: 'Renovation estimate.docx', content_url: '/api/files/doc_1/content', preview_url: '/api/files/doc_1/preview' };
const p = (text, heading = 0) => ({ kind: 'paragraph', text, heading });
const preview = {
  kind: 'document', truncated: false,
  blocks: [p('Renovation estimate', 1), p('Two lines\nwith a tab\there'), { kind: 'table', rows: [
    { header: true, cells: [{ colspan: 2, blocks: [p('Budget')] }] },
    { header: false, cells: [{ colspan: 1, blocks: [p('Paint')] }, { colspan: 1, blocks: [p('180'), { kind: 'table', rows: [{ header: false, cells: [{ colspan: 1, blocks: [p('Included labor')] }] }] }] }] },
  ] }],
};
async function fixture() {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  return { host, render: async props => { await act(async () => root.render(h(FilePreview, props))); },
    close: async () => { await act(async () => root.unmount()); host.remove(); } };
}
after(() => window.happyDOM.abort());

test('a DOCX renders inline as readable headings, paragraphs and nested tables, then reports ready', async () => {
  const observations = [], requests = [];
  globalThis.fetch = async url => { requests.push(url); return { ok: true, json: async () => preview }; };
  const page = await fixture();
  try {
    await page.render({ file, onObservation: value => observations.push(value) });
    assert.deepEqual(requests, [file.preview_url]);
    assert.equal(page.host.querySelector('article h1').textContent, 'Renovation estimate');
    assert.match(page.host.querySelector('article').textContent, /Two lines\nwith a tab\there/);
    assert.equal(page.host.querySelectorAll('table').length, 2);
    assert.equal(page.host.querySelector('th').colSpan, 2);
    assert.equal(page.host.querySelector('th').getAttribute('scope'), 'col');
    assert.match(page.host.querySelector('article').textContent, /Included labor/);
    assert.equal(page.host.querySelector('a').getAttribute('href'), file.content_url + '?download=1');
    assert.match(page.host.textContent, /files.documentPreview/);
    assert.equal(page.host.querySelector('button'), null, 'an inline preview does not add an unused Close button');
    assert.deepEqual(observations.at(-1), { file_id: file.id, status: 'ready' });
  } finally { await page.close(); }
});

test('document text never becomes HTML, a hyperlink, script, image or embedded resource', async () => {
  const dangerous = '<script>window.pwned=1</script><img src="https://example.invalid/track"><a href="javascript:alert(1)">link</a>';
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ kind: 'document', blocks: [p(dangerous), p('https://example.invalid/plain')], truncated: true }) });
  const page = await fixture();
  try {
    await page.render({ file });
    const body = page.host.querySelector('article');
    assert.equal(body.querySelectorAll('script,img,a,iframe,form,style,link').length, 0);
    assert.match(body.textContent, /<script>window.pwned=1<\/script>/);
    assert.equal(window.pwned, undefined);
    assert.match(page.host.textContent, /files.previewTruncated/);
    assert.ok(page.host.querySelector('a[download]'), 'clipped text must retain the original download');
  } finally { await page.close(); }
});

test('unsupported or failed previews keep the download and accurately report failure', async () => {
  const page = await fixture(), observations = [];
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ kind: 'unsupported' }) });
  try {
    await page.render({ file, onObservation: value => observations.push(value) });
    assert.match(page.host.textContent, /files.unavailable/);
    assert.equal(page.host.querySelector('article'), null);
    assert.ok(page.host.querySelector('a[download]'));
    assert.deepEqual(observations.at(-1), { file_id: file.id, status: 'failed', error_code: 'preview_unavailable' });
    globalThis.fetch = async () => { throw new Error('offline'); };
    await page.render({ file: { ...file, id: 'doc_2', preview_url: '/api/files/doc_2/preview' }, onObservation: value => observations.push(value) });
    assert.match(page.host.textContent, /files.unavailable/);
    assert.ok(page.host.querySelector('a[download]'));
    assert.equal(observations.at(-1).file_id, 'doc_2');
    assert.equal(observations.at(-1).status, 'failed');
  } finally { await page.close(); }
});

test('a delayed old document cannot overwrite a newly selected file', async () => {
  let release;
  globalThis.fetch = url => url === file.preview_url ? new Promise(resolve => { release = resolve; })
    : Promise.resolve({ ok: true, json: async () => ({ kind: 'document', blocks: [p('Current document')], truncated: false }) });
  const page = await fixture();
  try {
    await page.render({ file });
    await page.render({ file: { ...file, id: 'current', preview_url: '/api/files/current/preview' } });
    assert.match(page.host.querySelector('article').textContent, /Current document/);
    await act(async () => { release({ ok: true, json: async () => preview }); });
    assert.equal(page.host.querySelector('article').textContent, 'Current document');
  } finally { await page.close(); }
});

test('unrelated chat rerenders do not traverse a long unchanged document again', async () => {
  let reads = 0, requests = 0;
  const blocks = Array.from({ length: 200 }, (_, index) => ({ kind: 'paragraph', heading: 0, get text() { reads++; return `Paragraph ${index}`; } }));
  globalThis.fetch = async () => { requests++; return { ok: true, json: async () => ({ kind: 'document', blocks, truncated: false }) }; };
  const page = await fixture();
  try {
    await page.render({ file });
    assert.equal(page.host.querySelectorAll('article p').length, 200);
    const initial = reads;
    assert.ok(initial >= 200);
    for (let index = 0; index < 20; index++) await page.render({ file: { ...file }, onObservation() {} });
    assert.equal(requests, 1);
    assert.equal(reads, initial, 'typing elsewhere must not repeatedly walk every document block');
  } finally { await page.close(); }
});

test('a quotation preview preserves displayed amounts and distinguishes saved, missing and empty formula results', async () => {
  const cells = [[
    { kind: 'number', raw: '1234.5', display: '¥1,234.50', number_format: '"¥"#,##0.00', format_status: 'formatted' },
    { kind: 'number', raw: '0.08', display: '8%', number_format: '0%', format_status: 'formatted' },
    { kind: 'date', raw: '46299', display: '2026-10-04', number_format: 'yyyy-mm-dd', format_status: 'formatted' },
    { kind: 'formula', raw: '120', display: '120.00', formula: 'SUM(A2:A3)', formula_status: 'saved' },
    { kind: 'formula', raw: '', display: '=SUM(A2:A3)', formula: 'SUM(A2:A3)', formula_status: 'missing' },
    { kind: 'formula', raw: '', display: '', formula: 'IF(A2=0,"",A2)', formula_status: 'saved' },
    { kind: 'formula', raw: '', display: '', formula: '', formula_status: 'missing' },
    { kind: 'formula', raw: '0', display: '', formula: 'SUM(A2:A3)', formula_status: 'saved', number_format: '0;-0;;@', format_status: 'formatted' },
    { kind: 'number', raw: '0', display: '', number_format: '0;-0;;@', format_status: 'formatted' },
  ]];
  const workbook = { kind: 'spreadsheet', sheets: [{ name: 'Quote', rows: cells.map(row => row.map(cell => cell.display)),
    cells, row_numbers: [2], truncated: false }], truncated: false, formula_results: 'not_recalculated', calculation_warning: true };
  globalThis.fetch = async () => ({ ok: true, json: async () => workbook });
  const page = await fixture();
  try {
    await page.render({ file: { ...file, name: 'Quotation.xlsx' } });
    const values = [...page.host.querySelectorAll('tbody td')];
    assert.deepEqual(values.slice(0, 3).map(cell => cell.textContent), ['¥1,234.50', '8%', '2026-10-04']);
    assert.equal(values[3].querySelector('summary').textContent, '120.00');
    assert.equal(values[4].querySelector('summary').textContent, 'files.formulaMissing');
    assert.equal(values[5].querySelector('summary').textContent, 'files.formulaEmpty');
    assert.equal(values[6].querySelector('summary').textContent, 'files.formulaMissing');
    assert.equal(values[7].querySelector('summary').textContent, 'files.formatHidden');
    assert.equal(values[8].querySelector('summary').textContent, 'files.formatHidden');
    assert.equal(values[7].querySelector('dd').textContent, '0', 'a saved zero hidden by formatting is not an empty saved result');
    assert.match(values[3].textContent, /SUM\(A2:A3\)/);
    assert.equal(values[3].querySelector('details').open, false, 'inspection is optional');
    assert.match(page.host.textContent, /files.formulaSaved/);
    assert.match(page.host.textContent, /files.formulaRecalculate/);
    assert.doesNotMatch(page.host.textContent, /files.formatUnsupported/);
    assert.ok(page.host.querySelector('a[download]'));
    assert.equal(page.host.querySelector('dialog'), null, 'results remain in the conversation');
  } finally { await page.close(); }
});

test('unsupported number formats stay explicit, inert and scoped to the selected sheet', async () => {
  const dangerous = '<img src="https://example.invalid/track" onerror="window.pwned=1">';
  const workbook = { kind: 'spreadsheet', truncated: false, sheets: [
    { name: 'Custom units', rows: [['12.5']], row_numbers: [1], truncated: false,
      cells: [[{ kind: 'number', raw: '12.5', display: '12.5', number_format: dangerous, format_status: 'unsupported' }]] },
    { name: 'Legacy', rows: [['0', 'false']], row_numbers: [3], truncated: false },
  ] };
  globalThis.fetch = async () => ({ ok: true, json: async () => workbook });
  const page = await fixture();
  try {
    await page.render({ file });
    assert.equal(page.host.querySelector('summary').textContent, '12.5');
    assert.match(page.host.textContent, /files.formatUnsupported/);
    assert.match(page.host.textContent, /<img src=/);
    assert.equal(page.host.querySelectorAll('img,script,iframe').length, 0);
    assert.equal(window.pwned, undefined);
    await act(async () => page.host.querySelectorAll('[role="tab"]')[1].click());
    assert.deepEqual([...page.host.querySelectorAll('tbody td')].map(cell => cell.textContent), ['0', 'false']);
    assert.doesNotMatch(page.host.textContent, /files.formatUnsupported|files.formulaSaved/);
    assert.equal(page.host.querySelector('details'), null);
  } finally { await page.close(); }
});

test('typing or streaming around an unchanged worksheet does not walk its cells again', async () => {
  let reads = 0, requests = 0;
  const rows = Array.from({ length: 200 }, (_, index) => {
    const row = [String(index), '123.45'];
    Object.defineProperty(row, '0', { get() { reads++; return String(index); } });
    return row;
  });
  globalThis.fetch = async () => { requests++; return { ok: true, json: async () => ({ kind: 'spreadsheet', truncated: false,
    sheets: [{ name: 'Quote', rows, row_numbers: rows.map((_, index) => index + 1), truncated: false }] }) }; };
  const page = await fixture();
  try {
    await page.render({ file });
    assert.equal(page.host.querySelectorAll('tbody tr').length, 200);
    const initial = reads;
    assert.ok(initial >= 200);
    for (let index = 0; index < 20; index++) await page.render({ file: { ...file } });
    assert.equal(requests, 1);
    assert.equal(reads, initial);
  } finally { await page.close(); }
});
