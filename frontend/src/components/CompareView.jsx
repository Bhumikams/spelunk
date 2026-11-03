import { useState } from 'react'
import { api } from '../api'
import SourceCard from './SourceCard'

const SAMPLES = {
  'morluto/rea': [
    'parseMcpHttpEndpoint',
    'how does the server shut down on SIGTERM',
    'Ghidra Java version check',
    'read Electron fuses',
  ],
  'pallets/flask': ['add_url_rule', 'how does the route decorator work', 'from_prefixed_env', 'where is the app context pushed'],
  'psf/requests': ['HTTPAdapter', 'how are redirects followed', 'apparent_encoding', 'how are cookies stored'],
}

const COLUMNS = [
  { key: 'keyword', title: 'Keyword', note: 'BM25: exact words and identifiers' },
  { key: 'semantic', title: 'Semantic', note: 'Embeddings: meaning, not spelling' },
  { key: 'hybrid', title: 'Hybrid', note: 'Reciprocal rank fusion of both' },
]

export default function CompareView({ repo, onOpenSource }) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function run(q) {
    const text = q.trim()
    if (text.length < 2) return
    setQuery(text)
    setBusy(true)
    setError('')
    try {
      setResult({ query: text, ...(await api.compare({ repo: repo.repo_id, query: text, k: 8 })) })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // which chunks appear in more than one column
  const seen = {}
  if (result) for (const c of COLUMNS) for (const s of result[c.key]) seen[s.chunk_id] = (seen[s.chunk_id] || 0) + 1

  return (
    <section className="compare">
      <div className="compare-intro">
        <h2>Compare retrievers</h2>
        <p className="muted">
          Run one query through keyword search, semantic search and the fused ranking. Identifiers
          tend to win on keyword; plain-English questions on semantic. Hybrid should hold up on both.
        </p>
      </div>
      <form className="searchbar" onSubmit={(e) => { e.preventDefault(); run(query) }}>
        <label htmlFor="cq" className="sr-only">Search query</label>
        <input id="cq" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="An identifier or a question" />
        <button className="btn btn-primary" disabled={busy || query.trim().length < 2}>{busy ? 'Searching…' : 'Compare'}</button>
      </form>
      <div className="chips">
        {(SAMPLES[repo.repo_id] || []).map((s) => (
          <button key={s} type="button" className="chip" onClick={() => run(s)}>{s}</button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="columns">
          {COLUMNS.map((c) => (
            <div key={c.key} className={`column column-${c.key}`}>
              <header>
                <h3>{c.title}</h3>
                <p className="muted small">{c.note}</p>
              </header>
              {result[c.key].length === 0 && <p className="muted small">No results.</p>}
              {result[c.key].map((s) => (
                <div key={s.chunk_id} className={seen[s.chunk_id] > 1 ? 'shared' : undefined}>
                  <SourceCard source={s} compact onOpen={onOpenSource} showTracks={c.key === 'hybrid'} />
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
