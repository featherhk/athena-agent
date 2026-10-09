import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { measureSurface, parseTextStyleEffect } from '../src/effects.ts';
import { widgetDocument } from '../src/widget-document.ts';

test('text effects preserve intended styles and reject escape hatches or unbounded work', () => {
  const effect = { message_id: 'message-one', unit: 'character', styles: [
    { fontFamily: 'Georgia, serif', color: '#f3b', transform: 'translateY(-2px) rotate(8deg)', fontSize: 22 },
    { fontFamily: '苹方', fontWeight: 600, fontStyle: 'italic', letterSpacing: 1, opacity: .9 },
  ] };
  assert.deepEqual(parseTextStyleEffect(effect), effect);
  assert.equal(parseTextStyleEffect(null), null);
  for (const style of [{ position: 'fixed' }, { backgroundImage: 'url(https://example.test/image)' },
    { fontFamily: 'url(secret)' }, { color: 'red;display:none' }, { transform: 'translateY(10000px)' },
    { transform: 'scale(100)' }, { transform: 'matrix(1,0,0,1,0,0)' }, { fontSize: Infinity }]) {
    assert.equal(parseTextStyleEffect({ ...effect, styles: [style] }), undefined);
  }
  assert.equal(parseTextStyleEffect({ ...effect, styles: Array(257).fill({ color: 'red' }) }), undefined);
  assert.equal(parseTextStyleEffect({ ...effect, unit: 'html' }), undefined);
  assert.equal(parseTextStyleEffect({ ...effect, message_id: '* [data-password]' }), undefined);
  assert.equal(parseTextStyleEffect({ ...effect, styles: [JSON.parse('{"__proto__":{}}')] }), undefined);
});

test('layout exports only visible message geometry in iframe coordinates', () => {
  const element = (id, role, rect) => ({ dataset: { messageId: id, messageRole: role }, getBoundingClientRect: () => rect });
  const app = { querySelectorAll: () => [
    element('visible', 'assistant', { left: 60, right: 160, top: 100, bottom: 140, width: 100, height: 40 }),
    element('offscreen', 'user', { left: 60, right: 160, top: 500, bottom: 540, width: 100, height: 40 }),
    element('not-message', 'account', { left: 60, right: 160, top: 100, bottom: 140, width: 100, height: 40 }),
  ], querySelector: () => null, getAttribute: () => 'thinking' };
  const frame = { clientWidth: 400, clientHeight: 600, getBoundingClientRect: () => ({ left: 50, right: 250, top: 80, bottom: 380, width: 200, height: 300 }) };
  assert.deepEqual(measureSurface(app, frame, 'en', true), {
    viewport: { width: 400, height: 600 }, reduced_motion: true, locale: 'en', anchors: {}, phase: 'thinking', text_targets: [],
    messages: [{ id: 'visible', role: 'assistant', rect: { x: 20, y: 40, width: 200, height: 80 } }],
  });
});

test('sandbox bridge receives layout only from its host and cleans up subscriptions', () => {
  const html = widgetDocument('<canvas></canvas>', 'en', 'instance-one', undefined, true);
  assert.match(html, /background:transparent/);
  assert.match(html, /connect-src 'none'/);
  const handlers = new Map(), sent = [], css = new Map();
  const parent = { postMessage: value => sent.push(value) };
  const context = {
    innerWidth: 400, innerHeight: 240,
    parent, addEventListener: (name, callback) => handlers.set(name, callback),
    document: { addEventListener() {}, body: { innerText: 'Widget', querySelectorAll: () => [] }, documentElement: { style: { setProperty: (key, value) => css.set(key, value) } } },
    navigator: { userActivation: { isActive: false } },
    ErrorEvent: class {}, MutationObserver: class { observe() {} },
    setTimeout: () => {}, getComputedStyle: () => ({}),
  };
  context.window = context;
  vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  const observed = [], stop = context.Athena.observe(layout => observed.push(layout));
  assert.equal(sent.at(-1).type, 'athena:observe');
  const layout = { viewport: { width: 100, height: 100 }, messages: [] };
  handlers.get('message')({ source: {}, data: { type: 'athena:layout', instance: 'instance-one', layout } });
  handlers.get('message')({ source: parent, data: { type: 'athena:layout', instance: 'old-instance', layout } });
  assert.equal(observed.length, 0);
  handlers.get('message')({ source: parent, data: { type: 'athena:layout', instance: 'instance-one', layout } });
  assert.deepEqual(observed, [layout]);
  const target = { id: 'deadline', message_id: 'reply', quote: 'before Friday' };
  context.Athena.track([target]);
  assert.equal(sent.at(-1).type, 'athena:track');
  assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1).targets)), [target]);
  const trackedCount = sent.length;
  context.Athena.track([target]);
  assert.equal(sent.length, trackedCount, 'unchanged tracking cannot cause a layout loop');
  target.quote = 'a later edit';
  handlers.get('message')({ source: parent, data: { type: 'athena:inspect', instance: 'instance-one' } });
  assert.equal(sent.findLast(item => item.type === 'athena:track').targets[0].quote, 'before Friday', 'host resubscription uses the saved target');
  context.Athena.track(null);
  assert.equal(sent.at(-1).targets.length, 0);
  context.Athena.styleText(null);
  assert.equal(sent.at(-1).type, 'athena:style-text');
  assert.equal(sent.at(-1).effect, null);
  assert.equal(context.Athena.navigate('owned-session'), false);
  context.navigator.userActivation.isActive = true;
  assert.equal(context.Athena.navigate('owned-session'), true);
  assert.equal(sent.at(-1).session_id, 'owned-session');
  assert.equal(context.Athena.navigate('../../account'), false);
  const activated = [], regions = [{id:'pin',label:'Pin this note',rect:{x:5,y:10,width:44,height:44}}];
  context.Athena.interact(regions, action => activated.push(action));
  assert.equal(sent.at(-1).type, 'athena:interactions');
  const action = {id:'pin',kind:'activate',pointer:'keyboard',point:{x:27,y:32}};
  handlers.get('message')({source:{},data:{type:'athena:interaction',instance:'instance-one',action}});
  handlers.get('message')({source:parent,data:{type:'athena:interaction',instance:'previous-version',action}});
  handlers.get('message')({source:parent,data:{type:'athena:interaction',instance:'instance-one',action:{...action,id:'another-widget'}}});
  assert.equal(activated.length,0);
  handlers.get('message')({source:parent,data:{type:'athena:interaction',instance:'instance-one',action}});
  assert.deepEqual(activated,[action]);
  context.Athena.interact(null);
  handlers.get('message')({source:parent,data:{type:'athena:interaction',instance:'instance-one',action}});
  assert.equal(activated.length,1);
  const theme = { tokens: { '--ink': '#dfeee0', '--surface': '#102a18' }, font_family: 'Georgia', scheme: 'dark' };
  handlers.get('message')({ source: parent, data: { type: 'athena:theme', instance: 'instance-one', theme } });
  assert.equal(context.Athena.theme, theme);
  assert.equal(css.get('--ink'), '#dfeee0');
  assert.equal(css.get('--font'), 'Georgia');
  context.document.body.innerText = '';
  context.document.body.firstElementChild = { getBoundingClientRect: () => ({ width: 30, height: 30 }) };
  handlers.get('message')({ source: parent, data: { type: 'athena:inspect', instance: 'instance-one' } });
  assert.equal(sent.findLast(item => item.type === 'athena:health').has_content, true, 'CSS-only shapes are content');
  stop();
  assert.equal(sent.at(-1).type, 'athena:unobserve');
  handlers.get('message')({ source: parent, data: { type: 'athena:layout', instance: 'instance-one', layout } });
  assert.equal(observed.length, 1);
  handlers.get('error')(new context.ErrorEvent());
  assert.equal(sent.at(-1).error_code, 'script_error');
});
