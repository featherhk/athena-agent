import assert from 'node:assert/strict';
import test from 'node:test';
import { desktopWidgetRequest, loadDesktopWidget, changeDesktopWidgetView } from '../src/desktop-widget-loader.ts';

const search = '?desktop=widget&session=chat&component=collector&version=2&scope=owner:chat';
const target = desktopWidgetRequest(search, true);
const original = { id: 'collector', version: 2, title: 'Collector', html: '<button>Collect</button>', placement: 'desktop', view_revision: 3, view: { width: 64 } };
function service({ user = { id: 'owner' }, component = original, failure, save } = {}) {
  const calls = [];
  async function request(path, body, signal) {
    calls.push({ path, body, signal });
    if (failure?.path === path) throw failure.error;
    if (path === '/auth/me') return { user, preferences: { ui_language: 'en' } };
    if (path === '/sessions/chat') return { components: component ? [component] : [], preferences: { ui_language: 'fr' } };
    if (path === '/sessions/chat/components/collector/view') return save || {
      version: 2, view_revision: component.view_revision + 1, view: { ...component.view, ...body.view }, view_updated_at: '2026-10-09T00:00:00Z',
    };
    throw new Error(`Unexpected path ${path}`);
  }
  return { request, calls };
}

test('desktop widget URLs require a native role, bounded identity and exact source version', () => {
  assert.deepEqual(target, { owner: 'owner', session: 'chat', component: 'collector', version: 2 });
  assert.equal(desktopWidgetRequest(search, false), null, 'A normal web query cannot activate the native renderer');
  for (const query of [
    search.replace('owner:chat', 'owner:other'), search.replace('owner:chat', 'owner:chat:extra'),
    search.replace('collector', '../escape'), search.replace('version=2', 'version=0'),
    search.replace('version=2', 'version=1.5'), search.replace('version=2', 'version=2147483648'),
    search.replace('version=2', 'version=2e1'), search.replace('desktop=widget', 'desktop=companion'),
    `${search}&component=other`, `${search}&scope=other:chat`, search.replace('session=chat&', ''),
  ]) assert.equal(desktopWidgetRequest(query, true), null, query);
});

test('authenticated widget loading uses only the exact registered desktop source and current preferences', async () => {
  const f = service();
  const value = await loadDesktopWidget(target, f.request);
  assert.equal(value.component, original);
  assert.equal(value.preferences.ui_language, 'fr');
  assert.deepEqual(f.calls.map(item => item.path), ['/auth/me', '/sessions/chat']);
  assert.ok(f.calls.every(item => item.body === undefined), 'Loading never sends a user message or changes state');
});

test('an unauthenticated or switched account cannot even request the component snapshot', async () => {
  for (const user of [null, { id: 'other' }]) {
    const f = service({ user });
    await assert.rejects(loadDesktopWidget(target, f.request), /desktop_widget_unavailable/);
    assert.deepEqual(f.calls.map(item => item.path), ['/auth/me']);
  }
});

test('removed, replaced, non-desktop or malformed sources never become an unrelated widget', async () => {
  for (const component of [null, { ...original, id: 'other' }, { ...original, version: 3 },
    { ...original, placement: 'inline' }, { ...original, view: { placement: 'surface' } }, { ...original, html: undefined }]) {
    const f = service({ component });
    await assert.rejects(loadDesktopWidget(target, f.request), /desktop_widget_unavailable/);
    assert.equal(f.calls.length, 2);
  }
  const override = { ...original, placement: 'inline', view: { placement: 'desktop' } };
  assert.equal((await loadDesktopWidget(target, service({ component: override }).request)).component, override);
});

test('a cancelled load cannot mount late source even if the request implementation resolves', async () => {
  const controller = new AbortController();
  const f = service();
  const request = async (...args) => { const value = await f.request(...args); controller.abort(); return value; };
  await assert.rejects(loadDesktopWidget(target, request, controller.signal), /desktop_widget_unavailable/);
  assert.equal(f.calls.length, 1);
});

test('a layout change reads the current revision and persists once without creating another stream', async () => {
  const f = service({ component: { ...original, view_revision: 7 } });
  const updated = await changeDesktopWidgetView(target, { hidden: true }, f.request);
  assert.equal(updated.version, 2); assert.equal(updated.view_revision, 8);
  assert.deepEqual(updated.view, { width: 64, hidden: true });
  assert.deepEqual(f.calls.map(item => item.path), ['/auth/me', '/sessions/chat', '/sessions/chat/components/collector/view']);
  assert.deepEqual(f.calls[2].body, { version: 2, view_revision: 7, view: { hidden: true } });
});

test('layout conflicts and failed writes are not automatically replayed or called successful', async () => {
  const conflict = Object.assign(new Error('component_changed'), { code: 'component_changed' });
  const f = service({ failure: { path: '/sessions/chat/components/collector/view', error: conflict } });
  await assert.rejects(changeDesktopWidgetView(target, { hidden: true }, f.request), error => error === conflict);
  assert.equal(f.calls.filter(item => item.body !== undefined).length, 1);
  for (const save of [{ version: 3, view_revision: 4 }, { version: 2, view_revision: 3 }]) {
    await assert.rejects(changeDesktopWidgetView(target, { hidden: true }, service({ save }).request), /desktop_widget_unavailable/);
  }
});

test('account or source changes before a user action prevent any layout write', async () => {
  for (const f of [service({ user: { id: 'other' } }), service({ component: { ...original, version: 3 } })]) {
    await assert.rejects(changeDesktopWidgetView(target, { hidden: true }, f.request), /desktop_widget_unavailable/);
    assert.ok(f.calls.every(item => item.body === undefined));
  }
});

const file = { id: 'budget', session_id: 'chat', name: 'Budget.xlsx', size: 1200, media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  content_url: '/api/files/budget/content', preview_url: '/api/files/budget/preview', content_revision: 's1-current' };
const fileComponent = { ...original, html: '', file_id: 'budget', file };
const fileSearch = `${search}&file=budget&content_revision=s1-current`;

test('native file URL binds to exact registered bytes as well as component version', async () => {
  const bound = desktopWidgetRequest(fileSearch, true);
  assert.deepEqual(bound, { ...target, file: 'budget', contentRevision: 's1-current' });
  assert.equal((await loadDesktopWidget(bound, service({ component: fileComponent }).request)).component.file, file);
  for (const query of [fileSearch + '&file=another', fileSearch.replace('file=budget', 'file=../secret'),
    fileSearch.replace('&content_revision=s1-current', ''), `${search}&content_revision=s1-current`,
    fileSearch.replace('s1-current', 'x'.repeat(129))]) assert.equal(desktopWidgetRequest(query, true), null);
  for (const changed of [{ ...fileComponent, file: { ...file, content_revision: 's1-next' } }, original,
    { ...fileComponent, file_id: 'different', file: { ...file, id: 'different' } }]) {
    await assert.rejects(loadDesktopWidget(bound, service({ component: changed }).request), /desktop_widget_unavailable/);
  }
});

test('file rendering uses the authenticated registry projection with only canonical owned URLs', async () => {
  for (const component of [{ ...fileComponent, file: null }, { ...fileComponent, file: { ...file, id: 'another' } },
    { ...fileComponent, file: { ...file, content_url: 'https://remote.example/secret' } },
    { ...fileComponent, file: { ...file, preview_url: '/api/files/another/preview' } },
    { ...fileComponent, file: { ...file, size: -1 } }, { ...fileComponent, file: { ...file, session_id: '../escape' } }]) {
    await assert.rejects(loadDesktopWidget(target, service({ component }).request), /desktop_widget_unavailable/);
  }
  for (const name of ['Budget.xlsx', 'Proposal.docx', 'Chart.html', 'Photo.png', 'Notes.md']) {
    const source = { ...fileComponent, file: { ...file, name } };
    const f = service({ component: source });
    assert.equal((await loadDesktopWidget(target, f.request)).component.file.name, name);
    assert.deepEqual(f.calls.map(call => call.path), ['/auth/me', '/sessions/chat']);
  }
});
