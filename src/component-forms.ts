// Transient browser memory only; never put generated-form input in model logs.
export type FormField = { index: number; value: string; checked: boolean; selected?: number[] };
export type FormDraft = FormField[];

export function parseFormDraft(value: unknown): FormDraft | undefined {
  if (!Array.isArray(value) || value.length > 128) return;
  let size = 0;
  const seen = new Set<number>();
  for (const field of value) {
    if (!field || !Number.isInteger(field.index) || field.index < 0 || field.index >= 128 || seen.has(field.index) || typeof field.value !== 'string' || typeof field.checked !== 'boolean') return;
    if (field.selected !== undefined && (!Array.isArray(field.selected) || field.selected.length > 256 || field.selected.some((index: unknown) => typeof index !== 'number' || !Number.isInteger(index) || index < 0 || index > 10000))) return;
    size += field.value.length;
    if (size > 32000) return;
    seen.add(field.index);
  }
  return value.map(field => ({ index: field.index, value: field.value, checked: field.checked, ...(field.selected ? { selected: [...field.selected] } : {}) }));
}
