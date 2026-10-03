export type TracePage = { content: string; offset: number; total_chars: number };
export const charCount = (value: string) => Array.from(value).length;

/** A streamed preview can grow over pages already fetched by the reader. */
export function traceContent(preview: string, loaded: string): string {
  return loaded.startsWith(preview) ? loaded : preview;
}

/** Join by Unicode offsets, checking any overlap instead of duplicating it. */
export function appendTracePage(current: string, page: TracePage): string | undefined {
  const before = Array.from(current), incoming = Array.from(page.content);
  if (!Number.isInteger(page.offset) || page.offset < 0 || page.offset > before.length
    || !Number.isInteger(page.total_chars) || page.total_chars < page.offset + incoming.length) return undefined;
  const overlap = Math.min(before.length - page.offset, incoming.length);
  if (before.slice(page.offset, page.offset + overlap).join('') !== incoming.slice(0, overlap).join('')) return undefined;
  return current + incoming.slice(overlap).join('');
}

type ObjectValue = Record<string, unknown>;
function object(value: unknown): value is ObjectValue { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function parse(value: string): unknown { try { return JSON.parse(value); } catch { return undefined; } }
const text = (value: unknown) => typeof value === 'string' && value.trim() ? value.trim() : undefined;

export type ToolResult = {
  outcome: 'failed' | 'uncertain' | 'cancelled' | 'received';
  exitCode?: number; message?: string; output?: string;
};

/** A completed message is not proof that its operation succeeded. */
export function toolResult(content: string, status: string): ToolResult {
  const value = parse(content);
  const result = object(value) ? value : {};
  const exitCode = typeof result.exit_code === 'number' && Number.isInteger(result.exit_code) ? result.exit_code : undefined;
  const error = text(result.error) || (object(result.error) ? text(result.error.message) : undefined);
  const cancelled = result.cancelled === true || result.status === 'cancelled' || status === 'cancelled' || status === 'interrupted';
  const failed = result.ok === false || result.success === false || Boolean(result.error) || Boolean(text(result.error_code))
    || (exitCode !== undefined && exitCode !== 0) || ['failed', 'error', 'timed_out'].includes(String(result.status)) || status === 'failed';
  const output = typeof result.output === 'string' ? result.output : typeof result.stdout === 'string' ? result.stdout : undefined;
  return {
    outcome: result.effect_known === false ? 'uncertain' : cancelled ? 'cancelled' : failed ? 'failed' : 'received',
    ...(exitCode !== undefined ? { exitCode } : {}),
    ...((error || text(result.message)) ? { message: (error || text(result.message))!.slice(0, 320) } : {}),
    ...(output?.trim() ? { output: Array.from(output).slice(0, 600).join('') } : {}),
  };
}

/** Keep tool names useful to inspect without opening a JSON protocol envelope. */
export function toolRequests(content: string): string[] {
  const calls = parse(content);
  if (!Array.isArray(calls)) return [];
  return calls.flatMap(call => {
    if (!object(call) || !object(call.function) || !text(call.function.name)) return [];
    const name = String(call.function.name), args = typeof call.function.arguments === 'string' ? parse(call.function.arguments) : undefined;
    const operation = object(args) ? text(args.op) : undefined;
    const action = object(args) && object(args.args) ? text(args.args.action) : undefined;
    return [[name, operation, action].filter(Boolean).join(' · ').slice(0, 140)];
  }).slice(0, 20);
}
