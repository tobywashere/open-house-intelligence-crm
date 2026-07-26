import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'

// Shared renderer for AI-written text: chat replies, briefing fields, daily
// summary narrative. Preserves the [Name](lead:12) profile-link convention
// (docs/BRIEFING-UI.md); every other link opens in a new tab. `inline` renders
// paragraphs inline so short fields can sit after a label on the same line.
const Unwrap = ({ children }: { children?: React.ReactNode }) => <>{children}</>

export function Markdown({ children, inline = false }: { children: string; inline?: boolean }) {
  const Wrap = inline ? 'span' : 'div'
  return (
    <Wrap className={inline ? 'md md-inline' : 'md'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // react-markdown's default transform strips unknown protocols, turning
        // [Name](lead:12) into href="" — pass lead: through untouched
        urlTransform={(url) => (url.startsWith('lead:') ? url : defaultUrlTransform(url))}
        components={{
          // inline mode is used inside <p>/<Link> hosts — unwrap every block
          // element so the DOM stays valid (no p/ul/pre nested in a <p>)
          ...(inline
            ? {
                p: Unwrap,
                ul: Unwrap,
                ol: Unwrap,
                li: Unwrap,
                blockquote: Unwrap,
                pre: Unwrap,
              }
            : {}),
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
