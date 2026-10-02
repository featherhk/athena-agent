// HTTP is a versioned boundary: old backends and partial usage must remain renderable.
export type Usage = { id: string; model: string; status: string; input_tokens: number | null; output_tokens: number | null; cache_rate: number | null; cost_usd_low: number | null; cost_usd_high: number | null; error_code?: string; response_model?: string; context_epoch?: number };
export type Totals = { input_tokens: number | null; output_tokens: number | null; cache_rate: number | null; cost_usd_low: number | null; cost_usd_high: number | null; request_count: number | null; unreported_requests: number | null; unpriced_requests: number | null; cache_unreported_requests: number | null; cache_hit_tokens: number | null; cache_miss_tokens: number | null };
export type Metrics = { service?: { configured: boolean; code: string; model?: string; base_model?: string; context_epoch?: number }; total: Totals; requests: Usage[] };
const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const number = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const text = (value: unknown): string | undefined => typeof value === 'string' ? value : undefined;
const rate = (value: unknown) => { const n = number(value); return n !== null && n <= 1 ? n : null; };
export function normalizeMetrics(value: unknown): Metrics {
  const data = record(value), total = record(data.total), service = record(data.service);
  return {
    service: typeof service.configured === 'boolean' ? { configured: service.configured, code: text(service.code) || 'unknown', model: text(service.model), base_model: text(service.base_model), context_epoch: number(service.context_epoch) ?? undefined } : undefined,
    total: { input_tokens: number(total.input_tokens), output_tokens: number(total.output_tokens), cache_rate: rate(total.cache_rate), cost_usd_low: number(total.cost_usd_low), cost_usd_high: number(total.cost_usd_high), request_count: number(total.request_count), unreported_requests: number(total.unreported_requests), unpriced_requests: number(total.unpriced_requests), cache_unreported_requests: number(total.cache_unreported_requests), cache_hit_tokens: number(total.cache_hit_tokens), cache_miss_tokens: number(total.cache_miss_tokens) },
    requests: (Array.isArray(data.requests) ? data.requests : []).flatMap(value => {
      const row = record(value), id = text(row.id);
      if (!id) return [];
      return [{ id, model: text(row.model) || '—', status: text(row.status) || 'unknown', input_tokens: number(row.input_tokens), output_tokens: number(row.output_tokens), cache_rate: rate(row.cache_rate), cost_usd_low: number(row.cost_usd_low), cost_usd_high: number(row.cost_usd_high), error_code: text(row.error_code), response_model: text(row.response_model), context_epoch: number(row.context_epoch) ?? undefined }];
    }),
  };
}
