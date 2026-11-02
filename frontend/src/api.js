const BASE = import.meta.env.VITE_API_URL || '/api'

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(BASE + path, options)
  } catch {
    throw new Error("Can't reach the server. Start the backend with `uvicorn app.main:app --reload`.")
  }
  if (!res.ok) throw new Error(await errorText(res))
  return res.json()
}

async function errorText(res) {
  try {
    const body = await res.json()
    if (body.detail) return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  } catch { /* not JSON */ }
  return `Request failed (${res.status})`
}

const post = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

const repoPath = (id) => `/repos/${id}`

export const api = {
  health: () => request('/health'),
  repos: () => request('/repos'),
  repo: (id) => request(repoPath(id)),
  index: (url) => request('/repos', post({ url })),
  remove: (id) => request(repoPath(id), { method: 'DELETE' }),
  files: (id) => request(`${repoPath(id)}/files`),
  file: (id, path) => request(`${repoPath(id)}/file?path=${encodeURIComponent(path)}`),
  overview: (id, refresh = false) => request(`${repoPath(id)}/overview${refresh ? '?refresh=true' : ''}`),
  search: (body) => request('/search', post(body)),
  compare: (body) => request('/compare', post(body)),

  /** Streams /ask as server-sent events. Calls onEvent for each {type, ...} object. */
  async ask(body, onEvent, signal) {
    let res
    try {
      res = await fetch(BASE + '/ask', { ...post(body), signal })
    } catch (e) {
      if (e.name === 'AbortError') return
      throw new Error("Can't reach the server.")
    }
    if (!res.ok) throw new Error(await errorText(res))
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let cut
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, cut)
        buffer = buffer.slice(cut + 2)
        const line = frame.split('\n').find((l) => l.startsWith('data: '))
        if (line) onEvent(JSON.parse(line.slice(6)))
      }
    }
  },
}

export const lineLabel = (s) =>
  s.start_line === s.end_line ? `L${s.start_line}` : `L${s.start_line}–${s.end_line}`

export const shortSha = (sha) => (sha && sha !== 'local' ? sha.slice(0, 7) : sha || '')

export function fileName(path) {
  const i = path.lastIndexOf('/')
  return i < 0 ? path : path.slice(i + 1)
}

export function dirName(path) {
  const i = path.lastIndexOf('/')
  return i < 0 ? '' : path.slice(0, i + 1)
}
