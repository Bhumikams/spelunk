import { useEffect, useMemo, useState } from 'react'
import { api, dirName, fileName } from '../api'

export default function FilesView({ repo, onOpenPath }) {
  const [files, setFiles] = useState([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    api.files(repo.repo_id).then((f) => live && setFiles(f)).catch((e) => live && setError(e.message))
    return () => { live = false }
  }, [repo.repo_id])

  const shown = useMemo(() => {
    const terms = filter.toLowerCase().split(/\s+/).filter(Boolean)
    const hits = terms.length ? files.filter((f) => terms.every((t) => f.path.toLowerCase().includes(t))) : files
    return hits.slice(0, 300)
  }, [files, filter])

  return (
    <section className="files">
      <div className="searchbar">
        <label htmlFor="ff" className="sr-only">Filter files</label>
        <input id="ff" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by path, e.g. ghidra launcher" />
      </div>
      {error && <p className="error">{error}</p>}
      <p className="muted small">
        {filter ? `${shown.length}${shown.length === 300 ? '+' : ''} of ` : ''}{files.length.toLocaleString()} files
      </p>
      <ul className="file-list">
        {shown.map((f) => (
          <li key={f.path}>
            <button type="button" onClick={() => onOpenPath(f.path)}>
              <span className="path-dir">{dirName(f.path)}</span>
              <span className="path-file">{fileName(f.path)}</span>
              <span className="file-lines">{f.lines.toLocaleString()} lines</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
