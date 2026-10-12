import assert from 'node:assert/strict';
import test from 'node:test';
import { artifactLinkFile, deliveredArtifact, desktopFilePreview, isDesktopArtifact, outputFilePresentation } from '../src/desktop-artifacts.ts';

const origin = 'http://127.0.0.1:64417';
const file = { id: 'budget', session_id: 'chat', name: 'Budget.xlsx', path: 'Budget.xlsx', size: 1200,
  source: 'output', media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  content_url: '/api/files/budget/content', preview_url: '/api/files/budget/preview', content_revision: 's1-current' };
const message = { id: 'reply', role: 'assistant', status: 'complete', run_id: 'task', content: 'The budget is ready.', attachments: [file] };
const snapshot = change => ({ messages: [message], files: [file], runs: [{ id: 'task', status: 'complete' }], ...change });
const component = { id: 'athena_file_budget', version: 2, active: 1, title: 'Budget.xlsx', html: '', placement: 'desktop',
  file_id: file.id, file, view_revision: 3, view: { hidden: false, width: 860, height: 560 } };
const receipt = { id: component.id, version: component.version, view_revision: component.view_revision,
  file_id: file.id, content_revision: file.content_revision, state: 'mounted', visible: true,
  screen_id: '1', rect: { x: 40, y: 50, width: 860, height: 560 } };
const native = change => ({ platform: 'macos', supported: true, presented: true, screens: [], components: [receipt], ...change });


test('desktop artifact formats include documents and charts without opening implementation scripts', () => {
  for (const name of ['Budget.XLSX', 'Proposal.docx', 'chart.png', 'photo.jpeg', 'photo.avif', 'report.md', 'report.markdown',
    'report.html', 'report.htm', 'notes.txt', 'ledger.csv', 'data.json', 'output.log']) {
    assert.equal(isDesktopArtifact({ ...file, name }), true, name);
  }
  for (const name of ['generate.py', 'package.zip', 'script.js', 'Budget.xlsx.py', 'table.xlsx.bak']) {
    assert.equal(isDesktopArtifact({ ...file, name }), false, name);
  }
});

test('automatic delivery waits for a completed run and a completed final assistant result', () => {
  assert.equal(deliveredArtifact(snapshot()), file);
  for (const status of ['queued', 'running', 'waiting_user', 'waiting_agents', 'failed', 'interrupted', 'cancelled']) {
    assert.equal(deliveredArtifact(snapshot({ runs: [{ id: 'task', status }] })), null, status);
  }
  assert.equal(deliveredArtifact(snapshot({ runs: [] })), null, 'An unknown run is not proof of completion');
  for (const status of ['streaming', 'queued', 'failed', 'interrupted']) {
    assert.equal(deliveredArtifact(snapshot({ messages: [{ ...message, status }] })), null, status);
  }
  assert.equal(deliveredArtifact(snapshot({ messages: [{ ...message, role: 'user' }] })), null);
  assert.equal(deliveredArtifact(snapshot({ messages: [] })), null);
});

test('final delivery selects a declared output and prefers canonical file metadata to stale attachments', () => {
  const current = { ...file, content_revision: 's1-updated', size: 1800 };
  assert.equal(deliveredArtifact(snapshot({ files: [current] })), current);
  const script = { ...file, id: 'generator', name: 'generate.py', content_url: '/api/files/generator/content' };
  const uploaded = { ...file, id: 'input', source: 'upload', content_url: '/api/files/input/content' };
  const final = { ...file, id: 'final', name: 'Final.docx', content_url: '/api/files/final/content' };
  const files = [script, uploaded, file, final];
  assert.equal(deliveredArtifact(snapshot({ files, messages: [{ ...message, attachments: files }] })), final);
  assert.equal(deliveredArtifact(snapshot({ files, messages: [{ ...message, attachments: [] }] })), null,
    'Indexing intermediate output files alone must not open them');
  assert.equal(deliveredArtifact(snapshot({ files: [], messages: [message] })), null,
    'An attachment absent from the canonical owned-file list is not a desktop capability');
  assert.equal(deliveredArtifact(snapshot({ files: [uploaded], messages: [{ ...message, attachments: [uploaded] }] })), null);
  assert.equal(deliveredArtifact(snapshot({ files: [script], messages: [{ ...message, attachments: [script] }] })), null);
  assert.equal(deliveredArtifact(snapshot({ messages: [message, { ...message, id: 'final-reply', attachments: [], content: 'No result to open.' }] })), null,
    'An earlier intermediate message must not override the final response');
});

test('linked delivery requires a real owned content link, not a substring or a download', () => {
  const delivered = content => deliveredArtifact(snapshot({ messages: [{ ...message, attachments: [], content }] }), origin);
  assert.equal(delivered('[Budget.xlsx](/api/files/budget/content)'), file);
  assert.equal(delivered('[Budget.xlsx](/api/files/budget/content?revision=s1-current)'), file);
  assert.equal(delivered(`[Budget.xlsx](${origin}/api/files/budget/content)`), file);
  assert.equal(delivered(`${origin}/api/files/budget/content`), file);
  for (const content of [
    '[Unknown](/api/files/unknown/content)',
    '[Other website](https://elsewhere.example/api/files/budget/content)',
    '[Other website](//elsewhere.example/api/files/budget/content)',
    '[Download](/api/files/budget/content?download=1)',
    '[Download](/api/files/budget/content?download=false)',
    '[Different route](/api/files/budget/content-evil)',
    '[Nested route](/api/files/budget/content/extra)',
    '`/api/files/budget/content` is an example API path.',
  ]) assert.equal(delivered(content), null, content);
});

test('only canonical same-origin content links are taken over; browser downloads remain downloads', () => {
  for (const href of ['/api/files/budget/content', `${origin}/api/files/budget/content`, '/api/files/budget/content?revision=s1-current#preview']) {
    assert.equal(artifactLinkFile(href, [file], origin), file, href);
  }
  for (const href of ['/api/files/unknown/content', '/api/files/budget/preview', '/api/files/budget/content/extra',
    '/api/files/budget/content?download=1', '/api/files/budget/content?download=0',
    'https://elsewhere.example/api/files/budget/content', '//elsewhere.example/api/files/budget/content',
    'javascript:alert(1)', 'data:text/html,hi', 'http://[invalid']) {
    assert.equal(artifactLinkFile(href, [file], origin), null, href);
  }
  assert.equal(artifactLinkFile('/api/files/budget/content', [], origin), null);
});

test('a file becomes ready only from the exact native registration, file bytes and visible surface', () => {
  assert.deepEqual(desktopFilePreview(file, [component], native()), { file_id: file.id, status: 'ready' });
  for (const change of [{ id: 'different' }, { version: 1 }, { view_revision: 2 }, { file_id: 'other' },
    { content_revision: 's1-stale' }, { content_revision: null }, { visible: false }, { state: 'hidden' }]) {
    assert.equal(desktopFilePreview(file, [component], native({ components: [{ ...receipt, ...change }] })), null,
      JSON.stringify(change));
  }
  assert.equal(desktopFilePreview(file, [], native()), null);
  assert.equal(desktopFilePreview(file, [component], native({ components: [] })), null);
  assert.equal(desktopFilePreview(null, [component], native()), null);
  assert.equal(desktopFilePreview(file, [component]), null);
});

test('hidden, removed, unsupported and stale file registrations never claim readiness', () => {
  for (const change of [{ view: { ...component.view, hidden: true } }, { placement: 'inline' },
    { view: { ...component.view, placement: 'workspace' } }, { active: 0 }, { active: false },
    { file: { ...file, id: 'other' } }, { file: { ...file, content_revision: 's1-older' } }, { file: null }]) {
    assert.equal(desktopFilePreview(file, [{ ...component, ...change }], native()), null, JSON.stringify(change));
  }
  for (const change of [{ presented: false }, { supported: false }, { platform: 'web' }]) {
    assert.equal(desktopFilePreview(file, [component], native(change)), null, JSON.stringify(change));
  }
  const unknown = { ...file, content_revision: null };
  assert.equal(desktopFilePreview(unknown, [{ ...component, file: unknown }], native({ components: [{ ...receipt, content_revision: null }] })), null,
    'Two missing revision fields are not proof of matching file bytes');
});

test('loading and preview failure stay explicit instead of becoming a success receipt', () => {
  assert.deepEqual(desktopFilePreview(file, [component], native({ components: [{ ...receipt, state: 'loading' }] })),
    { file_id: file.id, status: 'loading' });
  assert.deepEqual(desktopFilePreview(file, [component], native({ components: [{ ...receipt, state: 'failed', error_code: 'preview_unavailable' }] })),
    { file_id: file.id, status: 'failed', error_code: 'preview_unavailable' });
  assert.equal(desktopFilePreview(file, [component], native({ presented: false, components: [{ ...receipt, state: 'failed', error_code: 'preview_unavailable' }] })), null);
});


test('results follow delivery intent while generator files remain available as working files', () => {
  const script = { ...file, id: 'generator', name: 'generate.py' };
  const current = { ...file, content_revision: 'new-bytes' };
  const uploaded = { ...file, id: 'input', source: 'upload' };
  const state = snapshot({ files: [script, current, uploaded] });
  assert.deepEqual(outputFilePresentation(state, origin), { delivered: [current], working: [script] });
  const codeDelivery = { ...message, attachments: [script], content: 'Here is the script you requested.' };
  assert.deepEqual(outputFilePresentation(snapshot({ files: [script, current], messages: [codeDelivery] }), origin),
    { delivered: [script], working: [current] });
});

test('owned links and registered closed results survive presentation without publishing unrelated files', () => {
  const state = snapshot({ messages: [{ ...message, attachments: [], content: `[Budget](${origin}/api/files/budget/content)` }] });
  assert.deepEqual(outputFilePresentation(state, origin), { delivered: [file], working: [] });
  state.messages[0].content = '[Budget](https://unrelated.example/api/files/budget/content)';
  assert.deepEqual(outputFilePresentation(state, origin), { delivered: [], working: [file] });
  state.components = [{ ...component, view: { hidden: true } }];
  assert.deepEqual(outputFilePresentation(state, origin), { delivered: [file], working: [] });
  state.components[0].active = 0;
  assert.deepEqual(outputFilePresentation(state, origin), { delivered: [], working: [file] });
});


test('focused exchange does not inherit an earlier task result even when its panel is retained', () => {
  const question = { id: 'next', role: 'user', content: 'What time is it?', run_id: 'next' };
  const state = snapshot({ messages: [message, question], components: [component] });
  assert.deepEqual(outputFilePresentation(state, origin).delivered, [file]);
  assert.deepEqual(outputFilePresentation({ ...state, messages: [question], components: [] }, origin).delivered, []);
  assert.deepEqual(outputFilePresentation(state, origin).delivered, [file], 'the original result remains available in history');
});
