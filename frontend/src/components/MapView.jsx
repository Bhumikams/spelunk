import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'

export default function MapView({ repo, readOnly, onOpenPath }) {
  const [md, setMd] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function load(refresh = false) {
    setBusy(true)
    setError('')
    try {
      setMd((await api.overview(repo.repo_id, refresh)).markdown)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    setMd('')
    load(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo.repo_id])

  const stats = repo.stats || {}
  const langs = Object.entries(stats.languages || {}).slice(0, 6)
  const maxLang = Math.max(1, ...langs.map(([, n]) => n))

  return (
    <section className="map">
      <div className="map-main">
        {busy && !md && <p className="muted">Reading the README, architecture notes and largest classes…</p>}
        {error && <p className="error">{error}</p>}
        {md && (
          <div className="prose">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code({ children, className }) {
                  const text = String(children)
                  const looksLikePath = !className && /^[\w.@-]+(\/[\w.@-]+)+\/?$/.test(text)
                  if (looksLikePath && !text.endsWith('/')) {
                    return <button type="button" className="path-link" onClick={() => onOpenPath(text)}><code>{text}</code></button>
                  }
                  return <code className={className}>{children}</code>
                },
              }}
            >
              {md}
            </ReactMarkdown>
          </div>
        )}
        {md && !readOnly && (
          <button type="button" className="btn btn-quiet" onClick={() => load(true)} disabled={busy}>
            {busy ? 'Rewriting…' : 'Rewrite this map'}
          </button>
        )}
      </div>

      <aside className="map-side" aria-label="Index statistics">
        <dl className="facts">
          <div><dt>Files indexed</dt><dd>{stats.files?.toLocaleString()}</dd></div>
          <div><dt>Chunks</dt><dd>{stats.chunks?.toLocaleString()}</dd></div>
          <div><dt>Lines of code and docs</dt><dd>{stats.lines?.toLocaleString()}</dd></div>
          <div><dt>Indexing took</dt><dd>{stats.seconds != null ? `${stats.seconds}s` : '—'}</dd></div>
        </dl>
        {langs.length > 0 && (
          <div className="langs">
            <h3>Files by language</h3>
            {langs.map(([lang, n]) => (
              <div key={lang} className="lang">
                <span>{lang}</span>
                <span className="lang-bar"><span style={{ width: `${(100 * n) / maxLang}%` }} /></span>
                <span className="lang-n">{n}</span>
              </div>
            ))}
          </div>
        )}
        {stats.skipped && (
          <p className="muted small">
            Skipped {stats.skipped.excluded?.toLocaleString()} excluded files (tests, translations,
            generated data), {stats.skipped.too_large} too large, {stats.skipped.unsupported} unsupported.
          </p>
        )}
      </aside>
    </section>
  )
}
