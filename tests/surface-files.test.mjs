import test from 'node:test';
import assert from 'node:assert/strict';
import { readSurfaceFile, findSurfaceFile, SURFACE_IMAGE_LIMIT } from '../src/surface-files.ts';
import { SurfaceError } from '../src/surface-runtime.ts';

const file = (patch = {}) => ({ id: 'owned', session_id: 'session', name: 'Result.png', path: 'Result.png', size: 4,
  media_type: 'image/png', source: 'output', created_at: '2026-10-04T00:00:00Z', content_revision: 's1-test',
  content_url: 'https://must-not-use.invalid/content', preview_url: 'https://must-not-use.invalid/preview', ...patch });
const snapshot = files => ({ files, messages: [] });
const json = value => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
const rejects = (promise, code) => assert.rejects(promise, error => error instanceof SurfaceError && error.message === code);
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };

test('file capability resolves only current files and canonical visible message attachments', async () => {
  const state = snapshot([file()]);
  state.messages = [
    { role: 'user', attachments: [file({ id: 'upload', session_id: 'fork-origin' })] },
    { role: 'assistant', attachments: [file({ id: 'answer' })] },
    { role: 'tool', attachments: [file({ id: 'private-tool' })] },
    { role: 'system', attachments: [file({ id: 'private-system' })] },
  ];
  for (const id of ['owned', 'upload', 'answer']) assert.equal(findSurfaceFile(state, id).id, id);
  for (const id of ['private-tool', 'private-system', 'other', 'https://foreign.invalid/file', '..', '/', null, 7]) {
    assert.equal(findSurfaceFile(state, id), undefined);
    await rejects(readSurfaceFile(() => state, id, () => true, async () => { assert.fail('No request for an unowned file'); }), 'file_not_found');
  }
});

test('image reads use only fixed owned endpoints and transfer a bounded raster data URL', async () => {
  const state = snapshot([file()]), requests = [];
  const result = await readSurfaceFile(() => state, 'owned', () => true, async (url, options) => {
    requests.push({ url, options });
    return url.includes('/preview') ? json({ kind: 'image', url: 'https://must-not-use.invalid/image' })
      : new Response(new Uint8Array([1, 2, 3, 4]), { headers: { 'content-type': 'image/png', 'content-length': '4' } });
  });
  assert.deepEqual(requests.map(item => item.url), ['/api/files/owned/preview?revision=s1-test', '/api/files/owned/content?revision=s1-test']);
  for (const request of requests) assert.deepEqual(request.options, { credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
  assert.deepEqual(result, { id: 'owned', name: 'Result.png', size: 4, media_type: 'image/png', content_revision: 's1-test', kind: 'image', data_url: 'data:image/png;base64,AQIDBA==' });
  for (const key of ['url', 'path', 'content_url', 'preview_url']) assert.equal(key in result, false);
});

test('Word and Excel retain typed content for arbitrary rendering without active markup or hidden response fields', async () => {
  const state = snapshot([file()]);
  for (const preview of [
    { kind: 'document', blocks: [{ kind: 'paragraph', text: '<script>not code</script>', heading: 1 }], truncated: true },
    { kind: 'spreadsheet', sheets: [{ name: 'Budget', rows: [['Paint', '180']], row_numbers: [1], truncated: false }], truncated: false },
  ]) {
    const result = await readSurfaceFile(() => state, 'owned', () => true, async () => json({ ...preview, provider_secret: 'must not cross' }));
    assert.equal(result.kind, preview.kind); assert.equal(result.truncated, preview.truncated);
    assert.deepEqual(result[preview.kind === 'document' ? 'blocks' : 'sheets'], preview[preview.kind === 'document' ? 'blocks' : 'sheets']);
    assert.equal('provider_secret' in result, false);
  }
});

test('text, markdown and HTML are data strings; no parser, script execution or remote resource is invoked', async () => {
  const state = snapshot([file()]), source = '<script>globalThis.surfaceFileInjected = true</script><img src="https://foreign.invalid/pixel">';
  for (const kind of ['text', 'markdown', 'html']) {
    let reads = 0;
    const result = await readSurfaceFile(() => state, 'owned', () => true, async () => { reads++; return json({ kind, content: source, truncated: true }); });
    assert.equal(result.kind, kind); assert.equal(result.content, source); assert.equal(result.truncated, true); assert.equal(reads, 1);
    assert.equal(globalThis.surfaceFileInjected, undefined);
  }
});

test('spreadsheet surfaces receive saved-value provenance and calculation limits with typed cells', async () => {
  const state = snapshot([file({ name: 'Quotation.xlsx' })]);
  const sheets = [{ name: 'Quote', rows: [['8%', '120']], row_numbers: [2], truncated: false,
    cells: [[{ raw: '0.08', display: '8%', kind: 'number', number_format: '0%', format_status: 'formatted' },
      { raw: '120', display: '120', kind: 'formula', formula: 'SUM(A1:A2)', formula_status: 'saved' }]] }];
  const result = await readSurfaceFile(() => state, 'owned', () => true, async () => json({
    kind: 'spreadsheet', sheets, truncated: false, formula_results: 'not_recalculated', calculation_warning: true,
  }));
  assert.deepEqual(result.sheets, sheets);
  assert.equal(result.formula_results, 'not_recalculated');
  assert.equal(result.calculation_warning, true);
});

test('unsupported preview is explicit and malformed or failed preview never returns success', async () => {
  const state = snapshot([file()]);
  assert.equal((await readSurfaceFile(() => state, 'owned', () => true, async () => json({ kind: 'unsupported' }))).kind, 'unsupported');
  for (const response of [json({ kind: 'document', blocks: null }), json({ kind: 'unknown', private: true }), new Response('bad json'), new Response('', { status: 404 })])
    await rejects(readSurfaceFile(() => state, 'owned', () => true, async () => response), 'preview_unavailable');
  await rejects(readSurfaceFile(() => state, 'owned', () => true, async () => { throw new TypeError('redirect rejected'); }), 'preview_unavailable');
});

test('oversized image metadata avoids reading bytes and declared content sizes are checked', async () => {
  for (const larger of ['metadata', 'header']) {
    const state = snapshot([file({ size: larger === 'metadata' ? SURFACE_IMAGE_LIMIT + 1 : 1 })]); let reads = 0, canceled = false;
    await rejects(readSurfaceFile(() => state, 'owned', () => true, async url => {
      reads++;
      return url.includes('/preview') ? json({ kind: 'image' }) : new Response(new ReadableStream({ cancel() { canceled = true; } }),
        { headers: { 'content-type': 'image/png', 'content-length': String(SURFACE_IMAGE_LIMIT + 1) } });
    }), 'image_too_large');
    assert.equal(reads, larger === 'metadata' ? 1 : 2);
    if (larger === 'header') { await Promise.resolve(); assert.equal(canceled, true); }
  }
});

test('streamed image size is bounded even when the server omits or understates Content-Length', async () => {
  for (const header of [undefined, '1']) {
    const state = snapshot([file({ size: 1 })]); let canceled = false;
    const headers = { 'content-type': 'image/png' }; if (header) headers['content-length'] = header;
    await rejects(readSurfaceFile(() => state, 'owned', () => true, async url => url.includes('/preview') ? json({ kind: 'image' })
      : new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(SURFACE_IMAGE_LIMIT)); controller.enqueue(new Uint8Array([1])); }, cancel() { canceled = true; } }), { headers })), 'image_too_large');
    await Promise.resolve(); assert.equal(canceled, true);
  }
});

test('image transport rejects SVG, HTML and unknown MIME types regardless of preview claims', async () => {
  const state = snapshot([file()]);
  for (const type of ['image/svg+xml', 'text/html', 'application/octet-stream']) {
    await rejects(readSurfaceFile(() => state, 'owned', () => true, async url => url.includes('/preview') ? json({ kind: 'image' })
      : new Response('<svg/>', { headers: { 'content-type': type } })), 'preview_unavailable');
  }
  let reads = 0;
  await rejects(readSurfaceFile(() => snapshot([file({ media_type: 'image/svg+xml' })]), 'owned', () => true, async () => { reads++; return json({ kind: 'image' }); }), 'preview_unavailable');
  assert.equal(reads, 1);
});

test('a stale scope before or after preview resolution returns no file data or image fetch', async () => {
  const state = snapshot([file()]); let reads = 0, current = false;
  const transport = async () => { reads++; return json({ kind: 'image' }); };
  await rejects(readSurfaceFile(() => state, 'owned', () => current, transport), 'surface_closed'); assert.equal(reads, 0);
  current = true; const gate = deferred();
  const pending = readSurfaceFile(() => state, 'owned', () => current, async () => { reads++; return gate.promise; });
  current = false; gate.resolve(json({ kind: 'image' }));
  await rejects(pending, 'surface_closed'); assert.equal(reads, 1);
});

test('a scope change during image streaming cancels the read and returns no partial data', async () => {
  const state = snapshot([file()]); let current = true, canceled = false;
  await rejects(readSurfaceFile(() => state, 'owned', () => current, async url => url.includes('/preview') ? json({ kind: 'image' })
    : new Response(new ReadableStream({ pull(controller) { current = false; controller.enqueue(new Uint8Array([1])); }, cancel() { canceled = true; } }),
      { headers: { 'content-type': 'image/png' } })), 'surface_closed');
  await Promise.resolve(); assert.equal(canceled, true);
});

test('changed or removed file revisions reject in-flight reads, including server read-start conflicts', async () => {
  for (const change of [state => { state.files[0] = file({ content_revision: 's1-new' }); }, state => { state.files = []; }]) {
    const state = snapshot([file()]), gate = deferred();
    const pending = readSurfaceFile(() => state, 'owned', () => true, async () => gate.promise);
    change(state); gate.resolve(json({ kind: 'text', content: 'Old bytes', truncated: false }));
    await rejects(pending, 'file_changed');
  }
  await rejects(readSurfaceFile(() => snapshot([file()]), 'owned', () => true, async () => new Response('', { status: 409 })), 'file_changed');
});
