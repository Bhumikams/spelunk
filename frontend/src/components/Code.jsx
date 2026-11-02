import { useEffect, useMemo, useRef } from 'react'
import hljs from 'highlight.js/lib/core'
import typescript from 'highlight.js/lib/languages/typescript'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import java from 'highlight.js/lib/languages/java'
import markdown from 'highlight.js/lib/languages/markdown'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import cpp from 'highlight.js/lib/languages/cpp'
import swift from 'highlight.js/lib/languages/swift'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'

hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('tsx', typescript)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('java', java)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('cc', cpp)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('c', cpp)
hljs.registerLanguage('h', cpp)
hljs.registerLanguage('hpp', cpp)
hljs.registerLanguage('swift', swift)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rs', rust)

/** Highlight a whole file once, then split into lines so each line keeps its gutter.
 *  Spans that cross a newline are closed and reopened so every line is valid HTML. */
function highlightLines(code, language) {
  let html
  try {
    html = hljs.getLanguage(language)
      ? hljs.highlight(code, { language, ignoreIllegals: true }).value
      : escapeHtml(code)
  } catch {
    html = escapeHtml(code)
  }
  const lines = []
  let open = []
  for (const raw of html.split('\n')) {
    const line = open.join('') + raw
    const tags = raw.match(/<span[^>]*>|<\/span>/g) || []
    for (const t of tags) {
      if (t === '</span>') open.pop()
      else open.push(t)
    }
    lines.push(line + '</span>'.repeat(open.length))
  }
  return lines
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** A listing: line numbers in the gutter, an optional highlighted range. */
export default function Code({ code, language, firstLine = 1, from, to, maxLines, scrollToRange = false }) {
  const lines = useMemo(() => {
    const all = highlightLines(code, language)
    return maxLines ? all.slice(0, maxLines) : all
  }, [code, language, maxLines])
  const rangeRef = useRef(null)

  useEffect(() => {
    if (scrollToRange && rangeRef.current) {
      rangeRef.current.scrollIntoView({ block: 'center' })
    }
  }, [scrollToRange, from, to, code])

  return (
    <div className="listing" role="region" aria-label="Source code">
      <pre>
        <code>
          {lines.map((html, i) => {
            const n = firstLine + i
            const hit = from != null && n >= from && n <= to
            return (
              <span
                key={n}
                ref={hit && n === from ? rangeRef : undefined}
                className={hit ? 'ln hit' : 'ln'}
              >
                <span className="gutter" aria-hidden="true">{n}</span>
                <span className="src" dangerouslySetInnerHTML={{ __html: html || ' ' }} />
                {'\n'}
              </span>
            )
          })}
        </code>
      </pre>
    </div>
  )
}
