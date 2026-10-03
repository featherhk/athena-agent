type Point = { line: number; column: number; offset?: number };
type Position = { start: Point; end: Point };
type MarkdownNode = { type: string; value?: string; url?: string; position?: Position; children?: MarkdownNode[] };

// These delimit prose in unmarked CJK links. Explicit Markdown/angle links and
// percent-encoded URLs remain the way to express punctuation in a URL itself.
const proseBoundary = /[、。，；：！？（）【】《》〈〉「」『』“”‘’]/;

/** GFM includes CJK sentence punctuation and following prose in bare URLs.
 * Work on the display AST only, using source offsets to distinguish literal
 * autolinks from intentional destinations; never decode or rewrite stored text. */
export function remarkReadableAutolinks() {
  return (tree: MarkdownNode, file: { value: unknown }) => {
    if (typeof file.value !== 'string') return;
    const source = file.value;
    function split(node: MarkdownNode): MarkdownNode[] | undefined {
      if (node.type !== 'link' || node.children?.length !== 1 || node.children[0].type !== 'text') return;
      const position = node.position, start = position?.start.offset, end = position?.end.offset;
      if (!position || start === undefined || end === undefined) return;
      const raw = source.slice(start, end);
      if (!/^(?:https?:\/\/|www\.)/i.test(raw) || raw !== node.children[0].value || /[\r\n]/.test(raw)) return;
      const prefix = /^www\./i.test(raw) ? 'http://' : '';
      if (node.url !== prefix + raw) return;
      const boundary = raw.search(proseBoundary);
      if (boundary < 0) return;
      const label = raw.slice(0, boundary);
      const middle = { line: position.start.line, column: position.start.column + boundary, offset: start + boundary };
      const linkPosition = { start: position.start, end: middle };
      return [
        { ...node, url: prefix + label, position: linkPosition, children: [{ ...node.children[0], value: label, position: linkPosition }] },
        { type: 'text', value: raw.slice(boundary), position: { start: middle, end: position.end } },
      ];
    }
    function visit(node: MarkdownNode) {
      if (!node.children) return;
      node.children = node.children.flatMap(child => {
        const replacement = split(child);
        if (replacement) return replacement;
        visit(child);
        return [child];
      });
    }
    visit(tree);
  };
}
