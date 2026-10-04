import { createElement, memo, useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from './i18n';
import { MarkdownContent } from './MessageBody';
import type { PreviewObservation } from './frontend-observation';
import './files.css';

export type FileEntry = {
  id: string; session_id: string; name: string; path: string; size: number;
  media_type: string; source: 'upload' | 'output'; created_at: string;
  content_url: string; preview_url: string;
  content_revision?: string | null;
};
type SheetCell = {
  raw: string; display: string; kind: 'text' | 'number' | 'boolean' | 'date' | 'error' | 'formula';
  number_format?: string; format_status?: 'formatted' | 'raw' | 'unsupported';
  formula?: string; formula_status?: 'saved' | 'missing';
};
type Sheet = { name: string; rows: string[][]; cells?: SheetCell[][]; row_numbers: number[]; truncated: boolean };
type DocumentBlock = { kind: 'paragraph'; text: string; heading: number }
  | { kind: 'table'; rows: { header: boolean; cells: { colspan: number; blocks: DocumentBlock[] }[] }[] };
type Preview = { kind: 'text' | 'markdown' | 'html'; content: string; truncated: boolean }
  | { kind: 'image'; url: string }
  | { kind: 'spreadsheet'; sheets: Sheet[]; truncated: boolean; formula_results?: 'not_recalculated'; calculation_warning?: boolean }
  | { kind: 'document'; blocks: DocumentBlock[]; truncated: boolean }
  | { kind: 'unsupported' };
const framePolicy = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'";

const DocumentBlocks = memo(function Blocks({ blocks }: { blocks: DocumentBlock[] }) {
  return <>{blocks.map((block, index) => block.kind === 'paragraph'
    ? createElement(block.heading >= 1 && block.heading <= 6 ? `h${block.heading}` : 'p', { key: index }, block.text || '\u00a0')
    : <div key={index} className="document-table-scroll"><table><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>
      {row.cells.map((cell, column) => createElement(row.header ? 'th' : 'td', { key: column, colSpan: cell.colspan, scope: row.header ? 'col' : undefined }, <DocumentBlocks blocks={cell.blocks} />))}
    </tr>)}</tbody></table></div>)}</>;
});

const SpreadsheetCell = memo(function Cell({ value, cell, address }: { value: string; cell?: SheetCell; address: string }) {
  const { t } = useI18n();
  const hidden = value === '' && Boolean(cell?.raw);
  const inspectable = cell?.kind === 'formula' || cell?.format_status === 'unsupported' || hidden;
  if (!cell || !inspectable) return <bdi>{value}</bdi>;
  const label = cell.formula_status === 'missing' ? t('files.formulaMissing')
    : hidden ? t('files.formatHidden')
    : cell.formula_status === 'saved' && cell.raw === '' ? t('files.formulaEmpty') : value;
  return <details className="spreadsheet-cell-detail">
    <summary aria-label={`${t('files.cellDetails', { cell: address })}: ${label}`}>{label}</summary>
    <dl>
      <dt>{t('files.rawValue')}</dt><dd><bdi>{cell.raw === '' ? '""' : cell.raw}</bdi></dd>
      {cell.formula !== undefined && <><dt>{t('files.formula')}</dt><dd><bdi>{cell.formula || t('files.formulaMissing')}</bdi></dd></>}
      {cell.number_format && <><dt>{t('files.numberFormat')}</dt><dd><bdi>{cell.number_format}</bdi></dd></>}
    </dl>
  </details>;
});

function columnName(index: number) {
  let name = '';
  for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) name = String.fromCharCode(65 + (value - 1) % 26) + name;
  return name;
}

const SpreadsheetTable = memo(function Table({ sheet, fallbackName }: { sheet: Sheet; fallbackName: string }) {
  const columns = Math.max(0, ...sheet.rows.map(row => row.length));
  return <div className="spreadsheet-table-scroll"><table aria-label={sheet.name || fallbackName}>
    <thead><tr><th aria-hidden="true" />{Array.from({ length: columns }, (_, index) => <th key={index} scope="col">{columnName(index)}</th>)}</tr></thead>
    <tbody>{sheet.rows.map((row, index) => <tr key={index}><th scope="row">{sheet.row_numbers[index] ?? index + 1}</th>{Array.from({ length: columns }, (_, column) => {
      const cell = sheet.cells?.[index]?.[column];
      return <td key={column} data-kind={cell?.kind}><SpreadsheetCell value={row[column] ?? ''} cell={cell} address={`${columnName(column)}${sheet.row_numbers[index] ?? index + 1}`} /></td>;
    })}</tr>)}</tbody>
  </table></div>;
});

type FilePreviewProps = { file: FileEntry; onClose?: () => void; onObservation?: (value: PreviewObservation) => void };

export function FilePreview(props: FilePreviewProps) {
  const { file } = props;
  // A fresh load owns its state and receipts. Stable snapshots keep this same
  // child; changed bytes cannot inherit an old iframe/image's late load event.
  const identity = JSON.stringify([file.id, file.preview_url, file.content_revision ?? file.size ?? null]);
  return <FilePreviewContent key={identity} {...props} />;
}

function FilePreviewContent({ file, onClose, onObservation }: FilePreviewProps) {
  const { t } = useI18n();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [failed, setFailed] = useState(false);
  const [sheetIndex, setSheetIndex] = useState(0);
  const observe = useRef(onObservation); observe.current = onObservation;
  const currentFile = useRef(file.id); currentFile.current = file.id;
  const loadedFile = useRef<string | null>(null);
  const [mediaReady, setMediaReady] = useState(false);
  const revisionQuery = file.content_revision ? `?revision=${encodeURIComponent(file.content_revision)}` : '';
  function mediaLoaded(id: string, success: boolean) {
    if (currentFile.current !== id || loadedFile.current !== id) return;
    if (success) setMediaReady(true); else setFailed(true);
  }
  useEffect(() => {
    const controller = new AbortController();
    loadedFile.current = null;
    setPreview(null); setFailed(false); setSheetIndex(0); setMediaReady(false);
    observe.current?.({ file_id: file.id, status: 'loading' });
    void fetch(file.preview_url + revisionQuery, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('preview_unavailable');
      const value: Preview = await response.json();
      if (!controller.signal.aborted) { loadedFile.current = file.id; setPreview(value); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [file.id, file.preview_url, revisionQuery]);
  useEffect(() => {
    if (failed || (loadedFile.current === file.id && preview?.kind === 'unsupported')) observe.current?.({ file_id: file.id, status: 'failed', error_code: 'preview_unavailable' });
    else if (loadedFile.current === file.id && preview && (!['html', 'image'].includes(preview.kind) || mediaReady)) observe.current?.({ file_id: file.id, status: 'ready' });
  }, [file.id, preview, failed, mediaReady]);
  const sheet = preview?.kind === 'spreadsheet' ? preview.sheets[sheetIndex] : undefined;
  const unsupportedFormats = useMemo(() => sheet?.cells?.some(row => row.some(cell => cell.format_status === 'unsupported')), [sheet]);
  return <section className="file-preview" aria-label={file.name}>
    <header className="file-preview-header"><strong>{file.name}</strong><div>
      <a className="file-download" href={`${file.content_url}?download=1`} download={file.name}>{t('files.download')}</a>
      {onClose && <button className="icon-button" type="button" onClick={onClose} aria-label={t('files.close')}>×</button>}
    </div></header>
    {!preview && !failed && <p role="status" className="empty-note">{t('files.loading')}</p>}
    {(failed || preview?.kind === 'unsupported') && <p className="empty-note">{t('files.unavailable')}</p>}
    {preview?.kind === 'markdown' && <MarkdownContent content={preview.content} />}
    {preview?.kind === 'text' && <pre className="file-text-preview">{preview.content}</pre>}
    {preview?.kind === 'html' && <iframe className="file-html-preview" sandbox="" referrerPolicy="no-referrer" title={file.name} onLoad={() => mediaLoaded(file.id, true)} onError={() => mediaLoaded(file.id, false)}
      srcDoc={`<!doctype html><meta http-equiv="Content-Security-Policy" content="${framePolicy}"><meta name="referrer" content="no-referrer">${preview.content}`} />}
    {preview?.kind === 'image' && <img className="file-image-preview" src={preview.url + revisionQuery} alt={file.name} decoding="async" onLoad={() => mediaLoaded(file.id, true)} onError={() => mediaLoaded(file.id, false)} />}
    {preview?.kind === 'document' && <>
      <p className="file-preview-note">{t('files.documentPreview')}</p>
      <article className="file-preview-document" aria-label={file.name}><DocumentBlocks blocks={preview.blocks} /></article>
      {preview.blocks.length === 0 && <p className="empty-note">{t('files.empty')}</p>}
      {preview.truncated && <p className="file-preview-note">{t('files.previewTruncated')}</p>}
    </>}
    {preview?.kind === 'spreadsheet' && <div className="spreadsheet-preview">
      {preview.sheets.length > 1 && <div className="spreadsheet-tabs" role="tablist" aria-label={t('files.sheet')}>
        {preview.sheets.map((item, index) => <button key={index} type="button" role="tab" aria-selected={index === sheetIndex} onClick={() => setSheetIndex(index)}>{item.name || `${index + 1}`}</button>)}
      </div>}
      {sheet && <SpreadsheetTable sheet={sheet} fallbackName={file.name} />}
      {(!sheet || sheet.rows.length === 0) && <p className="empty-note">{t('files.empty')}</p>}
      {preview.formula_results === 'not_recalculated' && <p className="file-preview-note">{t('files.formulaSaved')}{preview.calculation_warning ? ` ${t('files.formulaRecalculate')}` : ''}</p>}
      {unsupportedFormats && <p className="file-preview-note">{t('files.formatUnsupported')}</p>}
      {(sheet?.truncated || preview.truncated) && <p className="file-preview-note">{t('files.rowsTruncated')}</p>}
    </div>}
    {preview && 'content' in preview && preview.truncated && <p className="file-preview-note">{t('files.previewTruncated')}</p>}
  </section>;
}
