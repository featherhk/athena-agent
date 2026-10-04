import { memo, useEffect, useRef, useState } from 'react';
import { FilePreview, type FileEntry } from './FilePreview';
import { ImageResult, isImageFile } from './ImageResult';
import { useI18n } from './i18n';
import type { PreviewObservation } from './frontend-observation';
import './artifact-results.css';

export function canDisplayArtifact(file: FileEntry) {
  return isImageFile(file) || /\.(docx|xlsx|md|markdown|html?|txt|csv|json|log)$/i.test(file.name);
}

/** Files are part of the conversation. Only approaching the viewport loads a preview. */
export const ArtifactResult = memo(function ArtifactResult({ file, selected = false, close, observe }: {
  file: FileEntry; selected?: boolean; close?: () => void; observe?: (value: PreviewObservation) => void;
}) {
  const { t } = useI18n();
  const root = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); } }, { rootMargin: '300px' });
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  return <section className="artifact-result" ref={root} data-inline-preview-id={selected ? file.id : undefined} tabIndex={-1}>
    {visible || selected ? isImageFile(file)
      ? <ImageResult file={file} onObservation={observe} />
      : <FilePreview file={file} onClose={close} onObservation={observe} />
      : <div className="artifact-placeholder"><strong>{file.name}</strong><span>{t('files.loading')}</span></div>}
  </section>;
});
