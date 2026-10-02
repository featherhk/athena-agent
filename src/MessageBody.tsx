import Markdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useI18n } from './i18n';
import './files.css';

const ownedImage = /^\/api\/files\/[a-zA-Z0-9_-]+\/content$/;

export function MarkdownContent({ content }: { content: string }) {
  return <div className="markdown-body"><Markdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children, title }) => <a href={href} title={title} target={href?.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>,
    img: ({ src, alt }) => typeof src === 'string' && ownedImage.test(src)
      ? <img src={src} alt={alt || ''} loading="lazy" />
      : <a href={typeof src === 'string' ? defaultUrlTransform(src) : undefined} target="_blank" rel="noopener noreferrer">{alt || src}</a>,
    table: ({ children }) => <div className="markdown-table-scroll"><table>{children}</table></div>,
  }}>{content}</Markdown></div>;
}

export function MessageBody({ content, reasoning, streaming = false }: { content: string; reasoning?: string; streaming?: boolean }) {
  const { t } = useI18n();
  return <div className="message-body">
    {reasoning && <details className="message-reasoning">
      <summary>{t(streaming ? 'message.reasoningStreaming' : 'message.reasoning')}</summary>
      <MarkdownContent content={reasoning} />
    </details>}
    {content && <MarkdownContent content={content} />}
  </div>;
}
