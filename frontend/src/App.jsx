import { useCallback, useEffect, useRef, useState } from 'react'
import { api, shortSha } from './api'
import RepoRail from './components/RepoRail'
import AskView from './components/AskView'
import CompareView from './components/CompareView'
import MapView from './components/MapView'
import FilesView from './components/FilesView'
import CodePanel from './components/CodePanel'

const TABS = [
  { id: 'ask', label: 'Ask' },
  { id: 'compare', label: 'Compare search' },
  { id: 'map', label: 'Architecture map' },
  { id: 'files', label: 'Files' },
]

function readHash() {
  const m = window.location.hash.match(/^#\/([\w.-]+\/[\w.-]+)(?:\/(\w+))?/)
  return m ? { repo: m[1], tab: m[2] } : {}
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [repos, setRepos] = useState([])
  const [selected, setSelected] = useState(readHash().repo || null)
  const [tab, setTab] = useState(readHash().tab || 'ask')
  const [panel, setPanel] = useState(null)   // {path, start_line?, end_line?}
  const [loadError, setLoadError] = useState('')
  const pollRef = useRef(null)

  const refresh = useCallback(async () => {
    try {
      const list = await api.repos()
      setRepos(list)
      setLoadError('')
      return list
    } catch (e) {
      setLoadError(e.message)
      return []
    }
  }, [])

  useEffect(() => {
    api.health().then(setHealth).catch(() => {})
    refresh().then((list) => {
      setSelected((cur) => cur || list.find((r) => r.status === 'ready')?.repo_id || list[0]?.repo_id || null)
    })
  }, [refresh])

  // poll while anything is being indexed
  useEffect(() => {
    const indexing = repos.some((r) => !['ready', 'error'].includes(r.status))
    clearInterval(pollRef.current)
    if (indexing) pollRef.current = setInterval(refresh, 2000)
    return () => clearInterval(pollRef.current)
  }, [repos, refresh])

  useEffect(() => {
    if (selected) window.history.replaceState(null, '', `#/${selected}/${tab}`)
  }, [selected, tab])

  const repo = repos.find((r) => r.repo_id === selected)
  const openSource = useCallback((s) => setPanel({ path: s.path, start_line: s.start_line, end_line: s.end_line }), [])
  const openPath = useCallback((path) => setPanel({ path }), [])

  return (
    <div className="app">
      <RepoRail
        repos={repos}
        selected={selected}
        onSelect={(id) => { setSelected(id); setPanel(null) }}
        readOnly={health?.read_only}
        defaultRepo={health?.default_repo}
        onIndexed={(id) => { setSelected(id); refresh() }}
      />

      <main className="main">
        {loadError && <p className="error pad">{loadError}</p>}

        {!repo && !loadError && (
          <div className="welcome">
            <h1>Read any codebase by asking it questions</h1>
            <p>
              Spelunk parses a GitHub repository into functions, classes and doc sections, indexes them
              for both keyword and semantic search, and answers questions with citations to exact lines.
            </p>
            {!health?.read_only && (
              <p className="muted">Start by indexing a repository in the left column. {health?.default_repo && <>Try <code>{health.default_repo}</code>.</>}</p>
            )}
          </div>
        )}

        {repo && (
          <>
            <header className="repo-head">
              <div>
                <h1>
                  <span className="owner">{repo.owner}/</span>{repo.name}
                </h1>
                {repo.description && <p className="muted">{repo.description}</p>}
              </div>
              {repo.commit && (
                <p className="commit" title={repo.commit}>
                  {repo.url && repo.commit !== 'local'
                    ? <a href={`${repo.url}/tree/${repo.commit}`} target="_blank" rel="noreferrer">at {shortSha(repo.commit)}</a>
                    : `at ${shortSha(repo.commit)}`}
                  {repo.indexed_at && <span className="muted"> indexed {repo.indexed_at.slice(0, 10)}</span>}
                </p>
              )}
            </header>

            {repo.status !== 'ready' ? (
              <div className="pending">
                {repo.status === 'error' ? (
                  <p className="error">Indexing failed: {repo.error}</p>
                ) : (
                  <p className="muted">Indexing is in progress. This page updates on its own.</p>
                )}
              </div>
            ) : (
              <>
                <div className="tabs" role="tablist" aria-label="Views">
                  {TABS.map((t) => (
                    <button
                      key={t.id}
                      role="tab"
                      type="button"
                      aria-selected={tab === t.id}
                      className={tab === t.id ? 'tab is-on' : 'tab'}
                      onClick={() => setTab(t.id)}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
                <div className="view" role="tabpanel">
                  {tab === 'ask' && <AskView repo={repo} onOpenSource={openSource} />}
                  {tab === 'compare' && <CompareView repo={repo} onOpenSource={openSource} />}
                  {tab === 'map' && <MapView repo={repo} readOnly={health?.read_only} onOpenPath={openPath} />}
                  {tab === 'files' && <FilesView repo={repo} onOpenPath={openPath} />}
                </div>
              </>
            )}
          </>
        )}
      </main>

      {repo && panel && (
        <CodePanel repo={repo} target={panel} onClose={() => setPanel(null)} onJump={setPanel} />
      )}
    </div>
  )
}
