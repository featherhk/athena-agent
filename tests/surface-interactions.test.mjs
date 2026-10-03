import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSurfaceRegions, visibleSurfaceRegions } from '../src/surface-interactions.ts';

const region = { id: 'margin-note', label: 'Expand note', rect: { x: 120, y: 80, width: 44, height: 44 }, pressed: false };

test('free canvas accepts arbitrary bounded positions without fixed slots', () => {
  assert.deepEqual(parseSurfaceRegions([region]), [region]);
  assert.deepEqual(parseSurfaceRegions(null), []);
  assert.deepEqual(parseSurfaceRegions([]), []);
  assert.equal(parseSurfaceRegions([{...region, label:'   '}]), undefined);
  assert.equal(parseSurfaceRegions([region, region]), undefined);
  assert.equal(parseSurfaceRegions(Array.from({length:65},(_,n)=>({...region,id:`note-${n}`}))),undefined);
  for (const rect of [{...region.rect, x:Infinity}, {...region.rect, width:0}, {...region.rect, y:NaN}, {...region.rect, height:-1}, {...region.rect, x:'4'}, {...region.rect, target:'.composer'}]) {
    assert.equal(parseSurfaceRegions([{...region,rect}]),undefined);
  }
  assert.equal(parseSurfaceRegions([{...region,style:'position:fixed'}]),undefined);
  assert.equal(parseSurfaceRegions([{...region,pressed:1}]),undefined);
});

test('canvas clips both pixels and keyboard targets while retaining partial controls', () => {
  const clipped = {...region,rect:{x:-10,y:90,width:44,height:44}};
  assert.deepEqual(visibleSurfaceRegions([clipped],200,100),[{...region,rect:{x:0,y:90,width:34,height:10}}]);
  assert.deepEqual(visibleSurfaceRegions([region],0,0),[]);
  assert.deepEqual(visibleSurfaceRegions([{...region,rect:{x:201,y:0,width:44,height:44}}],200,100),[]);
  assert.deepEqual(visibleSurfaceRegions([region],390,844),[region]);
});
