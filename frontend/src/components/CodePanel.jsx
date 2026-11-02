import { useEffect, useRef, useState } from 'react'
import { api, lineLabel, shortSha } from '../api'
import Code from './Code'

/** Slide-over that shows a whole file, scrolled to the cited range. */
export default function CodePanel({ repo, target, onClose, onJump }) {
  const [file, setFile] = useState(null)
  const [error, setError] = useState('')
  const closeRef = useRef(null)

  useEffect(() => {
    if (!target) return
    let live = true
    setError('')
    if (!file || file.path !== target.path) setFile(null)
    api.file(repo.repo_id, target.path)
      .then((f) => live && setFile(f))
      .catch((e) => live && setError(e.message))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo.repo_id, target?.path])

  useEffect(() => {
    if (!target) return
    closeRef.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [target, onClose])

  if (!target) return null
  const ghUrl = repo.url && repo.commit && repo.commit !== 'local'
    ? `${repo.url}/blob/${repo.commit}/${target.path}${target.start_line ? `#L${target.start_line}-L${target.end_line}` : ''}`
    : null

  return (
    <div className="panel-scrim" onClick={onClose}>
      <aside
        className="code-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Source of ${target.path}`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="panel-head">
          <div className="panel-title">
            <code className="panel-path">{target.path}</code>
            <span className="panel-meta">
              {target.start_line ? lineLabel(target) : 'Whole file'}
              {file && ` of ${file.lines}`}
              {repo.commit && ` at ${shortSha(repo.commit)}`}
            </span>
          </div>
          <div className="panel-actions">
            {ghUrl && <a className="btn btn-quiet" href={ghUrl} target="_blank" rel="noreferrer">Open on GitHub</a>}
            <button ref={closeRef} type="button" className="btn btn-quiet" onClick={onClose}>Close</button>
          </div>
        </header>

        {file?.symbols?.length > 0 && (
          <nav className="symbol-strip" aria-label="Definitions in this file">
            {file.symbols.slice(0, 60).map((s) => (
              <button
                key={`${s.symbol}-${s.start_line}`}
                type="button"
                className={target.start_line === s.start_line ? 'sym is-on' : 'sym'}
                onClick={() => onJump({ path: target.path, start_line: s.start_line, end_line: s.end_line })}
              >
                {s.symbol}
              </button>
            ))}
          </nav>
        )}

        <div className="panel-body">
          {error && <p className="error">{error}</p>}
          {!file && !error && <p className="muted pad">Loading {target.path}…</p>}
          {file && (
            <Code
              code={file.content}
              language={file.language}
              from={target.start_line}
              to={target.end_line}
              scrollToRange
            />
          )}
        </div>
      </aside>
    </div>
  )
}
