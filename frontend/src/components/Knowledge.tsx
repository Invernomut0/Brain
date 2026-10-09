import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useBrain } from '../store'

const when = (ts: number) => new Date(ts * 1000).toLocaleString('en-GB', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

const KIND_COLOR: Record<string, string> = { fact: '#22d3ee', insight: '#fde047', user: '#a5b4fc', outcome: '#34f5a0' }
const kindColor = (k: string) => KIND_COLOR[k] ?? '#94a3b8'

interface MemoryItem { id: number; ts: number; kind: string; text: string; tags: string[]; importance: number; score?: number }

export function MemoriesView() {
  const stored = useBrain((s) => s.metrics.memories)
  const [items, setItems] = useState<MemoryItem[]>([])
  const [kinds, setKinds] = useState<Record<string, number>>({})
  const [kind, setKind] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 350); return () => clearTimeout(t) }, [q])

  // Refetch when filters change or Brain stores a new memory (the metrics counter changes).
  useEffect(() => {
    let live = true
    setLoading(true)
    const params = new URLSearchParams({ limit: '150' })
    if (kind) params.set('kind', kind)
    if (debounced) params.set('q', debounced)
    api.get<{ items: MemoryItem[]; kinds: Record<string, number> }>(`/memories?${params}`)
      .then((r) => { if (live) { setItems(r.items); setKinds(r.kinds); setError('') } })
      .catch((e) => live && setError(String(e.message ?? e)))
      .finally(() => live && setLoading(false))
    return () => { live = false }
  }, [kind, debounced, stored])

  return (
    <div className="mem">
      <div className="mem-bar">
        <input value={q} placeholder="Search memories…" onChange={(e) => setQ(e.target.value)} />
        <button className={`pill ${kind === null ? 'on' : ''}`} onClick={() => setKind(null)}>all {stored}</button>
        {Object.entries(kinds).map(([k, n]) => (
          <button key={k} className={`pill ${kind === k ? 'on' : ''}`} style={{ ['--c' as string]: kindColor(k) }} onClick={() => setKind(kind === k ? null : k)}>{k} {n}</button>
        ))}
        {loading && <span className="spin" />}
      </div>
      <div className="scroll">
        {error && <div className="empty" style={{ color: 'var(--red)' }}>{error}</div>}
        {!error && !items.length && !loading && (
          <div className="empty">{debounced || kind ? 'No memory matches the search.' : 'No memories yet: Brain saves them with "remember", from its reflections and from what you tell it in chat.'}</div>
        )}
        {items.map((m) => (
          <div className="entry" key={m.id}>
            <small>
              <span className="kind" style={{ ['--c' as string]: kindColor(m.kind) }}>{m.kind}</span> · {when(m.ts)} · importance {Math.round(m.importance * 100)}%
              {m.score !== undefined && <> · relevance {m.score.toFixed(2)}</>}
              {m.tags.length > 0 && <> · {m.tags.join(', ')}</>}
            </small>
            {m.text}
          </div>
        ))}
      </div>
    </div>
  )
}

interface Facts {
  goal: string; state: string; cycle: number
  goals: { done: number; failed: number; pending: number; active: number; cancelled: number; total: number }
  metrics: { awareness_index: number; success_rate: number; tools: number; memories: number; lessons: number; evolutions_applied: number }
  next: string[]
}
interface Report { lines: string[]; progress: number; done: string[]; missing: string[]; source: 'llm' | 'proxy'; ts: number }
interface StatusPayload { facts: Facts; report: Report | null; stale: boolean }

export function StatusView() {
  const [data, setData] = useState<StatusPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const auto = useRef(false)

  const refresh = useCallback(async () => {
    setBusy(true)
    try { setData(await api.post<StatusPayload>('/status/refresh')); setError('') } catch (e: any) { setError(String(e.message ?? e)) } finally { setBusy(false) }
  }, [])

  useEffect(() => {
    api.get<StatusPayload>('/status').then((d) => {
      setData(d)
      if (d.stale && !auto.current) { auto.current = true; void refresh() }
    }).catch((e) => setError(String(e.message ?? e)))
  }, [refresh])

  const f = data?.facts, r = data?.report
  const pct = Math.round(r?.progress ?? (f ? f.metrics.awareness_index * 100 : 0))
  return (
    <div className="scroll status">
      <div className="status-head">
        <div className="status-pct"><b>{pct}%</b><span>toward the goal</span></div>
        <div className="status-main">
          <div className="status-goal" title={f?.goal}>{f?.goal ?? '…'}</div>
          <div className="bar big"><i style={{ width: `${pct}%` }} /></div>
          <small>
            {r ? (r.source === 'llm' ? "Brain's estimate based on the data below" : 'Measurable indicator (awareness index): the model was not available') : 'Report not generated yet'}
            {r && <> · updated {when(r.ts)}</>}{data?.stale && r && ' · the data has changed'}
          </small>
        </div>
        <button className="btn" disabled={busy} onClick={refresh}>{busy ? <><span className="spin" /> Summarising…</> : '↻ Refresh'}</button>
      </div>
      {error && <div className="empty" style={{ color: 'var(--red)' }}>{error}</div>}

      {r ? (
        <ol className="status-lines">{r.lines.map((l, i) => <li key={i}>{l}</li>)}</ol>
      ) : (
        <div className="empty">{busy ? 'Brain is summarising the project…' : 'Press Refresh to have Brain tell its status.'}</div>
      )}

      {f && (
        <div className="status-stats">
          <div><b style={{ color: 'var(--green)' }}>{f.goals.done}</b>succeeded</div>
          <div><b style={{ color: 'var(--red)' }}>{f.goals.failed}</b>failed</div>
          <div><b style={{ color: 'var(--amber)' }}>{f.goals.pending + f.goals.active}</b>queued</div>
          <div><b>{f.metrics.tools}</b>tools</div>
          <div><b>{f.metrics.memories}</b>memories</div>
          <div><b>{f.metrics.lessons}</b>lessons</div>
          <div><b>{f.metrics.awareness_index.toFixed(2)}</b>index</div>
        </div>
      )}

      {r && (
        <div className="cols status-cols">
          <div className="card"><b style={{ color: 'var(--green)' }}>Done</b>
            <ul>{r.done.length ? r.done.map((d, i) => <li key={i}>{d}</li>) : <li className="dim">nothing concrete yet</li>}</ul>
          </div>
          <div className="card"><b style={{ color: 'var(--amber)' }}>Missing</b>
            <ul>{r.missing.length ? r.missing.map((d, i) => <li key={i}>{d}</li>) : <li className="dim">not specified</li>}</ul>
          </div>
        </div>
      )}
    </div>
  )
}
