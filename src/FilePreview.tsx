import { useEffect, useState } from 'react';
import { useI18n } from './i18n';
import { MarkdownContent } from './MessageBody';
import './files.css';

export type FileEntry = {
  id: string; session_id: string; name: string; path: string; size: number;
  media_type: string; source: 'upload' | 'output'; created_at: string;
  content_url: string; preview_url: string;
};
type Sheet = { name: string; rows: string[][]; row_numbers: number[]; truncated: boolean };
type Preview = { kind: 'text' | 'markdown' | 'html'; content: string; truncated: boolean }
  | { kind: 'image'; url: string }
  | { kind: 'spreadsheet'; sheets: Sheet[]; truncated: boolean }
  | { kind: 'unsupported' };
const framePolicy = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'";

export function FilePreview({ file, onClose }: { file: FileEntry; onClose?: () => void }) {
  const { t } = useI18n();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [failed, setFailed] = useState(false);
  const [sheetIndex, setSheetIndex] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPreview(null); setFailed(false); setSheetIndex(0);
    void fetch(file.preview_url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('preview_unavailable');
      const value: Preview = await response.json();
      if (!controller.signal.aborted) setPreview(value);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [file.id, file.preview_url]);
  const sheet = preview?.kind === 'spreadsheet' ? preview.sheets[sheetIndex] : undefined;
  const columns = sheet ? Math.max(0, ...sheet.rows.map(row => row.length)) : 0;
  function columnName(index: number) {
    let name = '';
    for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) name = String.fromCharCode(65 + (value - 1) % 26) + name;
    return name;
  }
  return <section className="file-preview" aria-label={file.name}>
    <header className="file-preview-header"><strong>{file.name}</strong><div>
      <a className="file-download" href={`${file.content_url}?download=1`} download={file.name}>{t('files.download')}</a>
      {onClose && <button className="icon-button" type="button" onClick={onClose} aria-label={t('files.close')}>×</button>}
    </div></header>
    {!preview && !failed && <p role="status" className="empty-note">{t('files.loading')}</p>}
    {(failed || preview?.kind === 'unsupported') && <p className="empty-note">{t('files.unavailable')}</p>}
    {preview?.kind === 'markdown' && <MarkdownContent content={preview.content} />}
    {preview?.kind === 'text' && <pre className="file-text-preview">{preview.content}</pre>}
    {preview?.kind === 'html' && <iframe className="file-html-preview" sandbox="" referrerPolicy="no-referrer" title={file.name}
      srcDoc={`<!doctype html><meta http-equiv="Content-Security-Policy" content="${framePolicy}"><meta name="referrer" content="no-referrer">${preview.content}`} />}
    {preview?.kind === 'image' && <img className="file-image-preview" src={preview.url} alt={file.name} />}
    {preview?.kind === 'spreadsheet' && <div className="spreadsheet-preview">
      {preview.sheets.length > 1 && <div className="spreadsheet-tabs" role="tablist" aria-label={t('files.sheet')}>
        {preview.sheets.map((item, index) => <button key={index} type="button" role="tab" aria-selected={index === sheetIndex} onClick={() => setSheetIndex(index)}>{item.name || `${index + 1}`}</button>)}
      </div>}
      {sheet && <div className="spreadsheet-table-scroll"><table aria-label={sheet.name || file.name}>
        <thead><tr><th aria-hidden="true" />{Array.from({ length: columns }, (_, index) => <th key={index} scope="col">{columnName(index)}</th>)}</tr></thead>
        <tbody>{sheet.rows.map((row, index) => <tr key={index}><th scope="row">{sheet.row_numbers[index] ?? index + 1}</th>{Array.from({ length: columns }, (_, column) => <td key={column}>{row[column] || ''}</td>)}</tr>)}</tbody>
      </table></div>}
      {(!sheet || sheet.rows.length === 0) && <p className="empty-note">{t('files.empty')}</p>}
      {(sheet?.truncated || preview.truncated) && <p className="file-preview-note">{t('files.rowsTruncated')}</p>}
    </div>}
    {preview && 'content' in preview && preview.truncated && <p className="file-preview-note">{t('files.previewTruncated')}</p>}
  </section>;
}
