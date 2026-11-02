import { useState } from 'react'
import { api } from '../api'

const STATE_TEXT = {
  queued: 'Queued',
  cloning: 'Cloning',
  scanning: 'Scanning files',
  chunking: 'Parsing code',
  embedding: 'Embedding',
  ready: 'Ready',
  error: 'Failed',
}

function progressText(r) {
  const p = r.progress || {}
  if (r.status === 'embedding' && p.total) return `${Math.round((100 * p.done) / p.total)}% of ${p.total.toLocaleString()} chunks`
  if (r.status === 'ready' && r.stats) return `${r.stats.files.toLocaleString()} files, ${r.stats.chunks.toLocaleString()} chunks`
  if (r.status === 'error') return r.error
  return ''
}

export default function RepoRail({ repos, selected, onSelect, readOnly, defaultRepo, onIndexed }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const res = await api.index(url.trim())
      setUrl('')
      onIndexed(res.repo_id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <nav className="rail" aria-label="Indexed repositories">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
          <circle cx="13" cy="14" r="7" fill="none" stroke="var(--kw)" strokeWidth="3" />
          <path d="M18 19l7 7" stroke="var(--sem)" strokeWidth="3.4" strokeLinecap="round" />
        </svg>
        <span>Spelunk</span>
      </div>

      <ul className="repo-list">
        {repos.map((r) => (
          <li key={r.repo_id}>
            <button
              type="button"
              className={`repo${selected === r.repo_id ? ' is-on' : ''} state-${r.status}`}
              onClick={() => onSelect(r.repo_id)}
              aria-current={selected === r.repo_id ? 'page' : undefined}
            >
              <span className="repo-owner">{r.owner || r.repo_id.split('/')[0]}/</span>
              <span className="repo-name">{r.name || r.repo_id.split('/')[1]}</span>
              <span className="repo-state">
                {r.status !== 'ready' && <span className="state-word">{STATE_TEXT[r.status] || r.status} </span>}
                {progressText(r)}
              </span>
              {r.status === 'embedding' && r.progress?.total > 0 && (
                <span className="repo-progress"><span style={{ width: `${(100 * r.progress.done) / r.progress.total}%` }} /></span>
              )}
            </button>
          </li>
        ))}
        {repos.length === 0 && <li className="muted small pad-x">Nothing indexed yet.</li>}
      </ul>

      {readOnly ? (
        <p className="muted small rail-note">
          This demo is read-only. Clone the project and run it locally to index any GitHub repository.
        </p>
      ) : (
        <form className="index-form" onSubmit={submit}>
          <label htmlFor="repo-url">Index a GitHub repository</label>
          <input
            id="repo-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={defaultRepo || 'https://github.com/owner/repo'}
          />
          <button className="btn btn-primary" disabled={busy || url.trim().length < 3}>
            {busy ? 'Starting…' : 'Index'}
          </button>
          {error && <p className="error small">{error}</p>}
          <p className="muted small">Large repos take a few minutes. The CLI is faster: <code>python -m scripts.index_repo</code></p>
        </form>
      )}
    </nav>
  )
}
