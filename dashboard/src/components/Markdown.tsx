import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'

// Shared renderer for AI-written text: chat replies, briefing fields, daily
// summary narrative. Preserves the [Name](lead:12) profile-link convention
// (docs/BRIEFING-UI.md); every other link opens in a new tab. `inline` renders
// paragraphs inline so short fields can sit after a label on the same line.
export function Markdown({ children, inline = false }: { children: string; inline?: boolean }) {
  const Wrap = inline ? 'span' : 'div'
  return (
    <Wrap className={inline ? 'md md-inline' : 'md'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // inline mode is used inside <p> elements — unwrap paragraphs so the
          // DOM stays valid (no <p>/<div> nested in <p>)
          ...(inline ? { p: ({ children: kids }: { children?: React.ReactNode }) => <>{kids}</> } : {}),
          a: ({ href, children: kids }) => {
            const lead = href?.match(/^lead:(\d+)$/)
            if (lead) {
              return (
                <Link to={`/lead/${lead[1]}`} className="text-accent underline hover:opacity-80">
                  {kids}
                </Link>
              )
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-accent underline hover:opacity-80">
                {kids}
              </a>
            )
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </Wrap>
  )
}
