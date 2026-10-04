// Production preview, translations and CSS; synthetic bytes/transport only.
import React, { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { FilePreview, type FileEntry } from '../../src/FilePreview';
import { I18nProvider, useI18n } from '../../src/i18n';
import en from '../../src/locales/en.json';
import ar from '../../src/locales/ar.json';

const memory = new Map([['athena-language', 'en']]);
Object.defineProperty(window, 'localStorage', { value: {
  getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, String(value)), removeItem: key => memory.delete(key),
} });
const report = value => window.webkit.messageHandlers.probe.postMessage(value);
const results = {}, requests = [], observations = [], downloads = [];
const delay = (ms = 40) => new Promise(resolve => setTimeout(resolve, ms));
const until = async (read, name) => {
  const deadline = performance.now() + 6000;
  do { if (read()) return; await delay(); } while (performance.now() < deadline);
  throw new Error(`timeout:${name}`);
};
const check = (name, pass, detail = undefined) => {
  results[name] = { pass: Boolean(pass), detail }; report({ progress: name });
  if (!pass) throw new Error(name);
};
const capture = name => new Promise((resolve, reject) => {
  window.addEventListener('probe-captured', event => event.detail?.error ? reject(new Error(event.detail.error)) : resolve(), { once: true });
  report({ capture: name });
});
const resize = async (width, height) => {
  report({ resize: { width, height } });
  await until(() => innerWidth === width && innerHeight === height, 'native resize'); await delay(150);
};
const textCell = text => ({ raw: text, display: text, kind: 'text' });
const formula = "SUM('Household invoices with a long worksheet name'!A1:A500)+SUM(B1:B500)";
const workbook = {
  kind: 'spreadsheet', truncated: false, formula_results: 'not_recalculated', calculation_warning: true,
  sheets: [
    { name: 'Summary', truncated: false, row_numbers: [1, 2, 3, 4, 5, 6, 7], rows: [
      ['Item', 'Value', 'Context'], ['Saved formula', '¥1,234.50', 'Existing workbook result'],
      ['Missing formula', '', 'No saved result'], ['Empty formula', '', 'An explicitly saved empty result'],
      ['Custom number format', '0.125', 'Stored value preserved'],
      ['Hidden zero', '', 'Value hidden by formatting'], ['Hidden formula result', '', 'A saved numeric zero'],
    ], cells: [
      ['Item', 'Value', 'Context'].map(textCell),
      [textCell('Saved formula'), { kind: 'formula', raw: '1234.5', display: '¥1,234.50', formula,
        formula_status: 'saved', number_format: '"¥"#,##0.00', format_status: 'formatted' }, textCell('Existing workbook result')],
      [textCell('Missing formula'), { kind: 'formula', raw: '', display: '', formula: 'SUM(C1:C3)', formula_status: 'missing' }, textCell('No saved result')],
      [textCell('Empty formula'), { kind: 'formula', raw: '', display: '', formula: 'IF(FALSE,"ready","")', formula_status: 'saved' }, textCell('An explicitly saved empty result')],
      [textCell('Custom number format'), { kind: 'number', raw: '0.125', display: '0.125', number_format: '[DBNum1][$-804]G/通用格式', format_status: 'unsupported' }, textCell('Stored value preserved')],
      [textCell('Hidden zero'), { kind: 'number', raw: '0', display: '', number_format: '0;-0;;@', format_status: 'formatted' }, textCell('Value hidden by formatting')],
      [textCell('Hidden formula result'), { kind: 'formula', raw: '0', display: '', formula: 'SUM(0,0)', formula_status: 'saved', number_format: '0;-0;;@', format_status: 'formatted' }, textCell('A saved numeric zero')],
    ] },
    { name: 'Dates & balances', truncated: false, row_numbers: [1, 2, 3, 4], rows: [
      ['Label', 'Value'], ['Payment date', '2026-10-05'], ['Tax rate', '12.5%'], ['Remaining', '1,234.50'],
    ], cells: [
      ['Label', 'Value'].map(textCell), [textCell('Payment date'), { kind: 'date', raw: '2026-10-05T00:00:00', display: '2026-10-05', number_format: 'yyyy-mm-dd', format_status: 'formatted' }],
      [textCell('Tax rate'), { kind: 'number', raw: '0.125', display: '12.5%', number_format: '0.0%', format_status: 'formatted' }],
      [textCell('Remaining'), { kind: 'number', raw: '1234.5', display: '1,234.50', number_format: '#,##0.00', format_status: 'formatted' }],
    ] },
    { name: 'Legacy rows without cell metadata', truncated: true, row_numbers: [1, 9], rows: [
      ['Item', 'Amount', 'Month', 'Owner', 'Unit', 'Notes'], ['A retained legacy value', '420.00', 'October', 'Household', 'HKD', 'Saved'],
    ] },
  ],
};
const file: FileEntry = { id: 'budget', session_id: 'fixture', name: 'Household budget.xlsx', path: 'synthetic.xlsx', size: 2048,
  source: 'output', media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', created_at: '2026-10-05T00:00:00Z',
  content_url: '/api/files/budget/content', preview_url: '/api/files/budget/preview', content_revision: 'fixture-r1' };
window.fetch = async (path, init) => {
  requests.push({ path: String(path), method: init?.method || 'GET' });
  if (String(path) !== '/api/files/budget/preview?revision=fixture-r1') throw new Error('Unexpected fixture request');
  return new Response(JSON.stringify(workbook), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
document.addEventListener('click', event => {
  const link = event.target.closest?.('a.file-download');
  if (link) { event.preventDefault(); downloads.push({ href: link.getAttribute('href'), name: link.getAttribute('download') }); }
});
function Fixture() {
  const { setChoice } = useI18n();
  useEffect(() => { window.changePreviewLocale = setChoice; });
  return <div className="app"><header className="topbar"><strong>ATHENA</strong></header><div className="scroll-area"><main className="conversation">
    <section className="artifact-result"><FilePreview file={file} onObservation={value => observations.push(value)} /></section>
  </main></div></div>;
}
createRoot(document.getElementById('root'), { onUncaughtError: error => report({ react_error: String(error) }) })
  .render(<I18nProvider><Fixture /></I18nProvider>);
const cell = (row, column = 1) => document.querySelectorAll('tbody tr')[row].querySelectorAll('td')[column];
const cellText = (row, column = 1) => cell(row, column).innerText.trim();
const tab = index => document.querySelectorAll('[role="tab"]')[index];
const visible = element => {
  const rect = element.getBoundingClientRect(), hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
  return rect.width > 0 && rect.height > 0 && rect.left >= -1 && rect.right <= innerWidth + 1
    && rect.top >= -1 && rect.bottom <= innerHeight + 1 && element.contains(hit);
};
function geometry(name) {
  const table = document.querySelector('.spreadsheet-table-scroll'), preview = document.querySelector('.file-preview');
  check(`${name}_preview_stays_in_viewport`, document.documentElement.scrollWidth === innerWidth && document.body.scrollWidth === innerWidth
    && preview.getBoundingClientRect().left >= 0 && preview.getBoundingClientRect().right <= innerWidth + 1
    && visible(document.querySelector('.file-download')),
  { viewport: innerWidth, document: document.documentElement.scrollWidth, preview: preview.getBoundingClientRect().width,
    tableViewport: table.clientWidth, tableContent: table.scrollWidth });
}
async function run() {
  try {
    await until(() => !document.hidden && document.querySelector('tbody') && observations.some(item => item.status === 'ready'), 'visible preview ready');
    check('typed_cells_render_saved_values_without_reformatting', cellText(1) === '¥1,234.50' && cellText(4) === '0.125');
    check('missing_and_empty_formula_results_are_distinct', cellText(2) === en['files.formulaMissing'] && cellText(3) === en['files.formulaEmpty']);
    check('hidden_zero_is_not_a_missing_or_empty_saved_result', cellText(5) === en['files.formatHidden'] && cellText(6) === en['files.formatHidden']);
    cell(5).querySelector('summary').click(); cell(6).querySelector('summary').click(); await delay();
    check('hidden_values_remain_inspectable', cell(5).querySelector('dd bdi').textContent === '0'
      && cell(6).querySelector('dd bdi').textContent === '0' && cell(6).innerText.includes('SUM(0,0)'));
    cell(5).querySelector('summary').click(); cell(6).querySelector('summary').click();
    check('formula_and_format_limits_are_visible', document.body.innerText.includes(en['files.formulaSaved'])
      && document.body.innerText.includes(en['files.formulaRecalculate']) && document.body.innerText.includes(en['files.formatUnsupported']));
    cell(1).querySelector('summary').click(); await delay();
    check('formula_disclosure_preserves_raw_value_formula_and_format', cell(1).querySelector('details').open
      && cell(1).innerText.includes('1234.5') && cell(1).innerText.includes(formula) && cell(1).innerText.includes('"¥"#,##0.00'));
    check('formula_disclosure_has_cell_address', cell(1).querySelector('summary').getAttribute('aria-label').includes('B2'));
    geometry('desktop'); await capture('spreadsheet-desktop');
    cell(1).querySelector('summary').click(); cell(4).querySelector('summary').click(); await delay();
    check('unsupported_format_can_be_inspected_without_losing_raw_value', cell(4).innerText.includes('0.125') && cell(4).innerText.includes('[DBNum1][$-804]G/通用格式'));
    tab(1).click(); await delay();
    check('sheet_switch_uses_its_own_metadata', tab(1).getAttribute('aria-selected') === 'true' && tab(0).getAttribute('aria-selected') === 'false'
      && cellText(1) === '2026-10-05' && cellText(2) === '12.5%' && !document.body.innerText.includes(en['files.formatUnsupported']));
    tab(2).click(); await delay();
    check('legacy_rows_still_render_with_original_row_numbers', cellText(1) === '420.00'
      && document.querySelectorAll('tbody tr')[1].querySelector('th').innerText === '9' && document.body.innerText.includes(en['files.rowsTruncated']));
    await resize(390, 844);
    const scroller = document.querySelector('.spreadsheet-table-scroll');
    check('narrow_wide_sheet_scrolls_inside_preview', scroller.scrollWidth > scroller.clientWidth);
    scroller.scrollLeft = scroller.scrollWidth; await delay();
    check('narrow_sheet_horizontal_scroll_is_operable', scroller.scrollLeft > 0 && window.scrollX === 0);
    scroller.scrollLeft = 0; tab(0).click(); await delay();
    cell(1).querySelector('summary').click(); await delay();
    geometry('narrow'); await capture('spreadsheet-narrow');
    window.changePreviewLocale('ar'); await until(() => document.documentElement.dir === 'rtl', 'Arabic RTL'); await delay(100);
    check('rtl_uses_catalog_and_isolates_formula_text', document.querySelector('.file-download').innerText === ar['files.download']
      && document.body.innerText.includes(ar['files.formulaSaved']) && cell(1).querySelector('dd bdi').textContent === '1234.5'
      && getComputedStyle(cell(1).querySelector('dd bdi')).unicodeBidi === 'isolate');
    geometry('rtl_narrow'); await capture('spreadsheet-rtl');
    document.querySelector('.file-download').click();
    check('download_keeps_original_file_and_does_not_open_a_workspace', downloads.length === 1
      && downloads[0].href === '/api/files/budget/content?download=1' && downloads[0].name === file.name
      && !document.querySelector('[role="dialog"], .workspace-panel') && requests.length === 1);
  } catch (error) { results.error = String(error); results.diagnostic = { requests, text: document.body.innerText, width: innerWidth, height: innerHeight }; }
  report(results);
}
void run();
