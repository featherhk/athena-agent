import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeMetrics } from '../src/telemetry.ts';

test('legacy metrics without service metadata remain usable', () => {
  const metrics = normalizeMetrics({ total: { request_count: 2, input_tokens: 32, output_tokens: 4 }, requests: [{ id: 'old', model: 'deepseek-flash', status: 'complete' }] });
  assert.equal(metrics.service?.model, undefined);
  assert.equal(metrics.total.input_tokens, 32);
  assert.equal(metrics.total.cost_usd_low, null);
  assert.equal(metrics.requests[0].id, 'old');
});

test('partial or malformed telemetry never invents zero cost or supplies unsafe numeric values', () => {
  for (const payload of [null, [], {}, { total: null, requests: null }, { service: null, total: { cost_usd_low: '0.5', output_tokens: NaN, input_tokens: Infinity, cache_rate: 4 }, requests: [null, {}, { id: 42 }, { id: 'one', model: null, cost_usd_low: {} }] }]) {
    const normalized = normalizeMetrics(payload);
    assert.equal(normalized.total.cost_usd_low, null);
    assert.equal(normalized.total.input_tokens, null);
    assert.equal(normalized.total.cache_rate, null);
    assert.ok(Array.isArray(normalized.requests));
    for (const row of normalized.requests) assert.equal(typeof row.id, 'string');
  }
});
