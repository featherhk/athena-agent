import { memo, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useI18n } from './i18n';
import type { FileEntry } from './FilePreview';
import type { PreviewObservation } from './frontend-observation';
import './files.css';

const imageTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif']);
export const ownedImageSource = (source: unknown): source is string => typeof source === 'string' && /^\/api\/files\/[a-zA-Z0-9_-]+\/content$/.test(source);
export const isImageFile = (file: Pick<FileEntry, 'id' | 'media_type' | 'content_url'>) =>
  imageTypes.has(file.media_type) && ownedImageSource(file.content_url) && file.content_url === `/api/files/${file.id}/content`;

function ImageZoom({ src, name, downloadUrl, onClose }: { src: string; name: string; downloadUrl: string; onClose: () => void }) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const element = dialog.current!, previous = document.activeElement;
    element.showModal();
    return () => {
      if (element.open) element.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(<dialog ref={dialog} className="image-lightbox" aria-label={name}
    onCancel={event => { event.preventDefault(); onClose(); }} onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="image-lightbox-header"><span>{name}</span><a className="file-download" href={downloadUrl} download={name}>{t('files.download')}</a>
      <button type="button" className="icon-button" aria-label={t('files.close')} onClick={onClose}>×</button></div>
    {failed ? <p role="status">{t('files.unavailable')}</p> : <img src={src} alt={name} decoding="async" onError={() => setFailed(true)} />}
  </dialog>, document.body);
}

/** Images are results in the conversation; opening a larger view is explicit. */
export const InlineImage = memo(function InlineImage({ src, name, fileId, revision, onOpen, onObservation }: {
  src: string; name: string; fileId?: string; revision?: string | number; onOpen?: () => void;
  onObservation?: (value: PreviewObservation) => void;
}) {
  const { t } = useI18n();
  const [attempt, setAttempt] = useState(0);
  const identity = `${src}:${revision ?? ''}:${attempt}`;
  const current = useRef(identity); current.current = identity;
  const observe = useRef(onObservation); observe.current = onObservation;
  const [state, setState] = useState<{ identity: string; status: 'loading' | 'ready' | 'failed' }>({ identity, status: 'loading' });
  const [expanded, setExpanded] = useState<string | null>(null);
  const valid = ownedImageSource(src);
  const status = !valid ? 'failed' : state.identity === identity ? state.status : 'loading';
  const query = new URLSearchParams();
  if (revision !== undefined) query.set('revision', String(revision));
  if (attempt) query.set('retry', String(attempt));
  const imageSource = query.size ? `${src}?${query}` : src;
  const downloadUrl = `${src}?download=1`;
  useEffect(() => {
    if (fileId) observe.current?.({ file_id: fileId, status, ...(status === 'failed' ? { error_code: 'preview_unavailable' as const } : {}) });
  }, [fileId, identity, status]);
  function loaded(success: boolean) {
    if (current.current === identity) setState({ identity, status: success ? 'ready' : 'failed' });
  }
  return <span className={`inline-image-result is-${status}`} data-file-result-id={fileId} data-image-state={status}>
    {valid && <button className="inline-image-open" type="button" disabled={status !== 'ready'}
      aria-label={t('files.preview', { name })} onClick={() => { if (onOpen) onOpen(); else setExpanded(identity); }}>
      <img key={identity} src={imageSource} alt={name} loading="lazy" decoding="async"
        onLoad={() => loaded(true)} onError={() => loaded(false)} />
    </button>}
    {status === 'loading' && <span className="inline-image-status" role="status">{t('files.loading')}</span>}
    {status === 'failed' && <span className="inline-image-status" role="status">{t('files.unavailable')}
      {valid && <button className="quiet" type="button" onClick={() => setAttempt(value => value + 1)}>{t('retry')}</button>}</span>}
    <span className="inline-image-caption"><span title={name}>{name}</span>
      {valid && <a className="file-download" href={downloadUrl} download={name}>{t('files.download')}</a>}</span>
    {expanded === identity && status === 'ready' && <ImageZoom src={imageSource} name={name} downloadUrl={downloadUrl} onClose={() => setExpanded(null)} />}
  </span>;
});

export const ImageResult = memo(function ImageResult({ file, onOpen, onObservation }: {
  file: FileEntry; onOpen?: () => void; onObservation?: (value: PreviewObservation) => void;
}) {
  return <InlineImage src={isImageFile(file) ? file.content_url : ''} name={file.name} fileId={file.id}
    revision={file.content_revision ?? file.size} onOpen={onOpen} onObservation={onObservation} />;
});
