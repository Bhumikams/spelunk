import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** Some models (gpt-oss) cite as 【1】 or 【1†source】 instead of [1]. */
export function normaliseCitations(text) {
  return text.replace(/【\s*(\d{1,2})(?:[†:][^】]*)?\s*】/g, '[$1]')
}

export function citedIn(text, count) {
  const nums = new Set()
  for (const m of normaliseCitations(text).matchAll(/\[(\d{1,2})\]/g)) {
    const n = Number(m[1])
    if (n >= 1 && n <= count) nums.add(n)
  }
  return [...nums].sort((a, b) => a - b)
}

/** Markdown answer where [n] citations become buttons that focus source n. */
export default function Answer({ text, sources, onCite }) {
  const linked = normaliseCitations(text).replace(/\[(\d{1,2})\](?!\()/g, (m, n) =>
    Number(n) >= 1 && Number(n) <= sources.length ? `[${n}](#cite-${n})` : m,
  )
  return (
    <div className="answer prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith('#cite-')) {
              const n = Number(href.slice(6))
              const s = sources[n - 1]
              return (
                <button
                  type="button"
                  className="cite"
                  title={s ? `${s.path} ${s.symbol}` : undefined}
                  onClick={() => onCite(n)}
                >
                  {n}
                </button>
              )
            }
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>
          },
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  )
}
