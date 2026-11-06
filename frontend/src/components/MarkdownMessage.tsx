import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

type Props = { text: string }

export function MarkdownMessage({ text }: Props) {
  return <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    rehypePlugins={[rehypeHighlight]}
    components={{
      a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener">{children}</a>,
      pre: ({ children, ...props }) => <div className="code-shell"><pre {...props}>{children}</pre></div>,
    }}
  >{text}</ReactMarkdown>
}
