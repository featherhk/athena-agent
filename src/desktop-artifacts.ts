import type { Component, Snapshot } from './api';
import type { FileEntry } from './FilePreview';
import type { DesktopState } from './desktop-bridge';
import type { PreviewObservation } from './frontend-observation';

export function isDesktopArtifact(file: FileEntry) {
  return /\.(png|jpe?g|gif|webp|avif|docx|xlsx|md|markdown|html?|txt|csv|json|log)$/i.test(file.name);
}

/** Deliver one completed result, not every intermediate file indexed by a run. */
export function deliveredArtifact(snapshot: Snapshot, origin = 'http://athena.invalid'): FileEntry | null {
  const last = snapshot.messages.findLast(message => message.role === 'assistant' && message.status === 'complete' && Boolean(message.content || message.attachments?.length));
  if (!last) return null;
  const run = snapshot.runs.find(run => run.id === last.run_id);
  if (!run || run.status !== 'complete') return null;
  const candidates = (last.attachments || []).flatMap(file => snapshot.files.filter(current => current.id === file.id));
  // A linked result is a delivery too; internal files without a link stay in Files.
  for (const match of last.content.matchAll(/\]\(\s*<?([^\s)>]+)>?(?:\s+["'][^)]*)?\)|(?<!\S)(https?:\/\/[^\s<>]+|\/api\/files\/[^\s<>]+)/g)) {
    const file = artifactLinkFile(match[1] || match[2], snapshot.files, origin);
    if (file && !candidates.some(item => item.id === file.id)) candidates.push(file);
  }
  return candidates.findLast(file => file.source === 'output' && isDesktopArtifact(file)) || null;
}

/** Read a real native receipt for the exact registered file bytes, never a postMessage acknowledgement. */
export function desktopFilePreview(file: FileEntry | null, components: Component[], state?: DesktopState): PreviewObservation | null {
  if (!file?.content_revision || !state?.presented || state.platform !== 'macos' || state.supported !== true) return null;
  const component = components.find(item => item.file_id === file.id && item.file?.id === file.id
    && item.file.content_revision === file.content_revision && item.active !== false && item.active !== 0 && !item.view?.hidden
    && (item.view?.placement || item.placement) === 'desktop');
  const observed = component && state.components.find(item => item.id === component.id && item.version === component.version
    && item.view_revision === (component.view_revision || 0) && item.file_id === file.id
    && (item.content_revision || null) === (file.content_revision || null) && item.visible);
  if (!observed || observed.state === 'hidden') return null;
  return { file_id: file.id, status: observed.state === 'mounted' ? 'ready' : observed.state === 'failed' ? 'failed' : 'loading',
    ...(observed.state === 'failed' ? { error_code: 'preview_unavailable' as const } : {}) };
}

/** Only owned same-origin content links become local work surfaces. Downloads stay downloads. */
export function artifactLinkFile(href: string, files: FileEntry[], origin: string): FileEntry | null {
  try {
    const url = new URL(href, origin);
    if (url.origin !== origin || url.searchParams.has('download')) return null;
    const match = /^\/api\/files\/([A-Za-z0-9_-]{1,64})\/content$/.exec(url.pathname);
    return match ? files.find(file => file.id === match[1]) || null : null;
  } catch { return null; }
}
