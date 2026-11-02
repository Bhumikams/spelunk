import { lineLabel, fileName, dirName } from '../api'

const KIND_LABEL = {
  function: 'function', method: 'method', class: 'class', outline: 'class outline',
  interface: 'interface', type: 'type', enum: 'enum', const: 'constant', members: 'members',
  definitions: 'definitions', module: 'module code', imports: 'imports', section: 'doc section',
  block: 'block', field: 'field', namespace: 'namespace',
}

/** How a result was found: one track per retriever, filled by rank (1 = full).
 *  This is the one place the two accent colours appear together. */
export function RankTracks({ ranks, candidates = 40 }) {
  const tracks = [
    ['keyword', 'Keyword'],
    ['semantic', 'Semantic'],
  ]
  return (
    <div className="tracks">
      {tracks.map(([key, label]) => {
        const r = ranks?.[key]
        const fill = r ? Math.max(0.08, 1 - (r - 1) / candidates) : 0
        return (
          <div key={key} className={`track track-${key}`} title={r ? `${label} rank #${r}` : `Not in ${label.toLowerCase()} results`}>
            <span className="track-label">{label}</span>
            <span className="track-bar"><span style={{ width: `${fill * 100}%` }} /></span>
            <span className="track-rank">{r ? `#${r}` : '—'}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function SourceCard({ source, active, onOpen, showTracks = true, compact = false }) {
  const s = source
  return (
    <article
      id={`src-${s.n}`}
      className={`xref${active ? ' is-active' : ''}${compact ? ' is-compact' : ''}`}
    >
      <button type="button" className="xref-main" onClick={() => onOpen(s)}>
        <span className="xref-n" aria-label={`Source ${s.n}`}>{s.n}</span>
        <span className="xref-body">
          <span className="xref-symbol">{s.symbol}</span>
          <span className="xref-where">
            <span className="path-dir">{dirName(s.path)}</span>
            <span className="path-file">{fileName(s.path)}</span>
            <span className="xref-lines">{lineLabel(s)}</span>
          </span>
          {!compact && s.doc && <span className="xref-doc">{s.doc}</span>}
        </span>
        <span className="xref-kind">{KIND_LABEL[s.kind] || s.kind}</span>
      </button>
      {showTracks && <RankTracks ranks={s.ranks} />}
    </article>
  )
}
