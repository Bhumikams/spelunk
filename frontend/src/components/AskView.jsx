import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import Answer, { citedIn } from './Answer'
import ModePicker from './ModePicker'
import SourceCard from './SourceCard'

const STARTERS = {
  'morluto/rea': [
    'What happens on SIGINT or SIGTERM: how does the server process shut down?',
    'How is a headless Ghidra process started, and what arguments are passed to analyzeHeadless?',
    'How does the Hopper bridge talk to the TypeScript side?',
    'Where are evidence records kept, and how are unknowns tracked?',
  ],
  'pallets/flask': [
    'How does the route decorator register a URL rule?',
    'Where is the request context pushed and popped?',
    'How does Flask load config from environment variables?',
    'How are blueprints registered on the app?',
  ],
  'psf/requests': [
    'How does a Session send a request and follow redirects?',
    'How does requests guess the response encoding?',
    'How are cookies kept between requests?',
    'Where are retries configured for connections?',
  ],
}
const GENERIC = [
  'What is the entry point, and what happens on startup?',
  'How is configuration loaded?',
  'Where are errors handled and reported?',
]

export default function AskView({ repo, onOpenSource }) {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState('hybrid')
  const [turns, setTurns] = useState([])     // {q, mode, sources, text, cited, error, done}
  const [busy, setBusy] = useState(false)
  const [activeSource, setActiveSource] = useState(null)
  const abortRef = useRef(null)
  const endRef = useRef(null)

  useEffect(() => {
    setTurns([])
    setActiveSource(null)
    abortRef.current?.abort()
  }, [repo.repo_id])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [turns.length])

  const update = (fn) =>
    setTurns((ts) => {
      const copy = ts.slice()
      copy[copy.length - 1] = fn(copy[copy.length - 1])
      return copy
    })

  async function ask(q) {
    const text = q.trim()
    if (text.length < 2 || busy) return
    const history = turns
      .filter((t) => t.done && !t.error)
      .slice(-2)
      .flatMap((t) => [
        { role: 'user', content: t.q },
        { role: 'assistant', content: t.text },
      ])
    setTurns((ts) => [...ts, { q: text, mode, sources: [], text: '', cited: [], error: '', done: false }])
    setQuestion('')
    setBusy(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await api.ask(
        { repo: repo.repo_id, question: text, mode, history },
        (ev) => {
          if (ev.type === 'sources') update((t) => ({ ...t, sources: ev.sources }))
          else if (ev.type === 'token') update((t) => ({ ...t, text: t.text + ev.text }))
          else if (ev.type === 'done')
            update((t) => ({ ...t, cited: ev.cited.length ? ev.cited : citedIn(t.text, t.sources.length), done: true }))
          else if (ev.type === 'error') update((t) => ({ ...t, error: ev.message, done: true }))
        },
        ctrl.signal,
      )
    } catch (e) {
      update((t) => ({ ...t, error: e.message, done: true }))
    } finally {
      update((t) => ({ ...t, done: true }))
      setBusy(false)
    }
  }

  function cite(turnIndex, n) {
    const s = turns[turnIndex].sources[n - 1]
    if (!s) return
    setActiveSource(`${turnIndex}:${n}`)
    document.getElementById(`t${turnIndex}-src-${n}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }

  const starters = STARTERS[repo.repo_id] || GENERIC

  return (
    <section className="ask">
      {turns.length === 0 && (
        <div className="ask-empty">
          <h2>Ask how {repo.name} works</h2>
          <p className="muted">
            Answers come only from the indexed code and docs, and every claim cites the
            function or section it came from.
          </p>
          <div className="starters">
            {starters.map((s) => (
              <button key={s} type="button" className="starter" onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="thread">
        {turns.map((t, i) => (
          <article key={i} className="turn">
            <h3 className="turn-q">{t.q}</h3>
            <div className="turn-grid">
              <div className="turn-answer">
                {t.error && <p className="error">{t.error}</p>}
                {!t.text && !t.error && !t.done && (
                  <p className="muted thinking">
                    {t.sources.length ? `Reading ${t.sources.length} sources…` : 'Searching the code…'}
                  </p>
                )}
                {t.text && (
                  <Answer text={t.text} sources={t.sources} onCite={(n) => cite(i, n)} />
                )}
              </div>
              {t.sources.length > 0 && (
                <aside className="turn-sources" aria-label="Sources">
                  <p className="sources-head">
                    {t.sources.length} sources, {t.mode} search
                  </p>
                  {t.sources.map((s) => (
                    <div key={s.chunk_id} id={`t${i}-src-${s.n}`}>
                      <SourceCard
                        source={s}
                        active={activeSource === `${i}:${s.n}`}
                        onOpen={onOpenSource}
                        showTracks={t.mode === 'hybrid'}
                      />
                    </div>
                  ))}
                  {t.done && t.cited.length > 0 && (
                    <p className="muted small">Cited: {t.cited.join(', ')} of {t.sources.length}</p>
                  )}
                </aside>
              )}
            </div>
          </article>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          ask(question)
        }}
      >
        <label htmlFor="q" className="sr-only">Question about {repo.repo_id}</label>
        <textarea
          id="q"
          rows={2}
          value={question}
          placeholder={turns.length ? 'Ask a follow-up…' : `Ask about ${repo.repo_id}…`}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              ask(question)
            }
          }}
        />
        <div className="composer-row">
          <ModePicker value={mode} onChange={setMode} />
          {busy ? (
            <button type="button" className="btn" onClick={() => abortRef.current?.abort()}>Stop</button>
          ) : (
            <button type="submit" className="btn btn-primary" disabled={question.trim().length < 2}>Ask</button>
          )}
        </div>
      </form>
    </section>
  )
}
