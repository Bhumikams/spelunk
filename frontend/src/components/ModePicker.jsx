const MODES = [
  { id: 'hybrid', label: 'Hybrid', hint: 'Keyword and semantic results fused with RRF' },
  { id: 'keyword', label: 'Keyword', hint: 'BM25 over code, identifiers and paths' },
  { id: 'semantic', label: 'Semantic', hint: 'Embedding similarity (MiniLM)' },
]

export default function ModePicker({ value, onChange }) {
  return (
    <div className="modes" role="radiogroup" aria-label="Search mode">
      {MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          role="radio"
          aria-checked={value === m.id}
          title={m.hint}
          className={`mode mode-${m.id}${value === m.id ? ' is-on' : ''}`}
          onClick={() => onChange(m.id)}
        >
          {m.label}
        </button>
      ))}
    </div>
  )
}
