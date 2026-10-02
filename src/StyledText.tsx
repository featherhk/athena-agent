import type { CSSProperties } from 'react';
import type { TextStyleEffect } from './effects';

export function textUnits(text: string, unit: TextStyleEffect['unit']): string[] {
  return [...new Intl.Segmenter(undefined, { granularity: unit === 'word' ? 'word' : 'grapheme' }).segment(text)].map(item => item.segment);
}

export function StyledText({ text, effect }: { text: string; effect?: TextStyleEffect }) {
  if (!effect) return <>{text}</>;
  const units = textUnits(text, effect.unit);
  return <>{units.slice(0, 8000).map((unit, index) => <span key={index} style={{ ...effect.styles[index % effect.styles.length], display: 'inline-block', whiteSpace: 'pre-wrap' } as CSSProperties}>{unit}</span>)}{units.slice(8000).join('')}</>;
}

type Node = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: Node[] };

/** Decorate text leaves while leaving Markdown links, lists and tables semantic.
 * The stored message is untouched; unload/reset removes the presentation layer. */
export function textEffectPlugin(effect?: TextStyleEffect) {
  return () => (tree: Node) => {
    if (!effect) return;
    let index = 0;
    const visit = (node: Node) => {
      if (!node.children) return;
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value || index >= 8000) { visit(child); return [child]; }
        return textUnits(child.value, effect.unit).map(unit => {
          if (index >= 8000) return { type: 'text', value: unit };
          const style = effect.styles[index++ % effect.styles.length];
          const css = Object.entries(style).map(([key, value]) => {
            const property = key.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase());
            return `${property}:${value}${typeof value === 'number' && ['fontSize', 'letterSpacing'].includes(key) ? 'px' : ''}`;
          }).join(';');
          return { type: 'element', tagName: 'span', properties: { style: `display:inline-block;white-space:pre-wrap;${css}` }, children: [{ type: 'text', value: unit }] };
        });
      });
    };
    visit(tree);
  };
}
