import type { Snapshot } from './api';
import type { FileEntry } from './FilePreview';
import { SurfaceError } from './surface-runtime.ts';

export const SURFACE_IMAGE_LIMIT = 8 * 1024 * 1024;
const PREVIEW_LIMIT = 4 * 1024 * 1024;
const imageTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif']);
type FileSnapshot = Pick<Snapshot, 'files' | 'messages'>;
type VersionedFile = FileEntry & { content_revision?: string | null };

export function findSurfaceFile(snapshot: FileSnapshot, identifier: unknown): VersionedFile | undefined {
  if (typeof identifier !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(identifier)) return undefined;
  return snapshot.files.find(file => file.id === identifier) || snapshot.messages
    .filter(message => message.role === 'user' || message.role === 'assistant')
    .flatMap(message => message.attachments || []).find(file => file.id === identifier);
}

async function bytes(response: Response, limit: number, error: string, check: () => void): Promise<Uint8Array> {
  try { check(); } catch (error) { void response.body?.cancel().catch(() => {}); throw error; }
  const declared = response.headers.get('content-length');
  if (declared && Number(declared) > limit) { void response.body?.cancel().catch(() => {}); throw new SurfaceError(error); }
  if (!response.body) throw new SurfaceError('preview_unavailable');
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let total = 0;
  try {
    while (true) {
      const next = await reader.read(); check();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > limit) throw new SurfaceError(error);
      chunks.push(next.value);
    }
  } catch (error) { void reader.cancel().catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  const result = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

function base64(value: Uint8Array) {
  const chunks: string[] = [];
  for (let start = 0; start < value.length; start += 32768) chunks.push(String.fromCharCode(...value.subarray(start, start + 32768)));
  return btoa(chunks.join(''));
}

/** File bytes only cross the frame RPC. They are never an agent tool result. */
export async function readSurfaceFile(snapshot: () => FileSnapshot, identifier: unknown, isCurrent: () => boolean, transport: typeof fetch = fetch) {
  if (!isCurrent()) throw new SurfaceError('surface_closed');
  const file = findSurfaceFile(snapshot(), identifier);
  if (!file) throw new SurfaceError('file_not_found');
  const revision = file.content_revision || null;
  const check = () => {
    if (!isCurrent()) throw new SurfaceError('surface_closed');
    const current = findSurfaceFile(snapshot(), file.id);
    if (!current || (current.content_revision || null) !== revision) throw new SurfaceError('file_changed');
  };
  const prefix = `/api/files/${encodeURIComponent(file.id)}`;
  const query = revision ? `?revision=${encodeURIComponent(revision)}` : '';
  const get = async (endpoint: 'preview' | 'content') => {
    check();
    const response = await transport(`${prefix}/${endpoint}${query}`, { credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
    try {
      check();
      if (response.status === 409) throw new SurfaceError('file_changed');
      if (!response.ok || response.redirected) throw new SurfaceError('preview_unavailable');
    } catch (error) { void response.body?.cancel().catch(() => {}); throw error; }
    return response;
  };
  try {
    const raw = await bytes(await get('preview'), PREVIEW_LIMIT, 'preview_too_large', check);
    const preview = JSON.parse(new TextDecoder().decode(raw)); check();
    const metadata = { id: file.id, name: file.name, size: file.size, media_type: file.media_type, content_revision: revision };
    if (preview?.kind === 'image') {
      if (!imageTypes.has(file.media_type)) throw new SurfaceError('preview_unavailable');
      if (file.size > SURFACE_IMAGE_LIMIT) throw new SurfaceError('image_too_large');
      const response = await get('content');
      const mediaType = response.headers.get('content-type')?.split(';')[0].trim().toLowerCase();
      if (!mediaType || !imageTypes.has(mediaType)) { void response.body?.cancel().catch(() => {}); throw new SurfaceError('preview_unavailable'); }
      const content = await bytes(response, SURFACE_IMAGE_LIMIT, 'image_too_large', check);
      const data_url = `data:${mediaType};base64,${base64(content)}`; check();
      return { ...metadata, kind: 'image' as const, data_url };
    }
    // These fields are data. In particular, HTML remains inert source text;
    // the host's scriptless file preview is the rendering path for raw HTML.
    if (['text', 'markdown', 'html'].includes(preview?.kind) && typeof preview.content === 'string')
      return { ...metadata, kind: preview.kind as 'text' | 'markdown' | 'html', content: preview.content, truncated: preview.truncated === true };
    if (preview?.kind === 'document' && Array.isArray(preview.blocks))
      return { ...metadata, kind: 'document' as const, blocks: preview.blocks, truncated: preview.truncated === true };
    if (preview?.kind === 'spreadsheet' && Array.isArray(preview.sheets))
      return { ...metadata, kind: 'spreadsheet' as const, sheets: preview.sheets, truncated: preview.truncated === true,
        ...(preview.formula_results === 'not_recalculated' ? { formula_results: 'not_recalculated' as const } : {}),
        ...(preview.calculation_warning === true ? { calculation_warning: true } : {}) };
    if (preview?.kind === 'unsupported') return { ...metadata, kind: 'unsupported' as const };
    throw new SurfaceError('preview_unavailable');
  } catch (error) {
    check();
    if (error instanceof SurfaceError) throw error;
    throw new SurfaceError('preview_unavailable');
  }
}
