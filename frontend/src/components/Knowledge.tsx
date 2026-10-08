import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useBrain } from '../store'

const when = (ts: number) => new Date(ts * 1000).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

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
        <input value={q} placeholder="Cerca nei ricordi…" onChange={(e) => setQ(e.target.value)} />
        <button className={`pill ${kind === null ? 'on' : ''}`} onClick={() => setKind(null)}>tutti {stored}</button>
        {Object.entries(kinds).map(([k, n]) => (
          <button key={k} className={`pill ${kind === k ? 'on' : ''}`} style={{ ['--c' as string]: kindColor(k) }} onClick={() => setKind(kind === k ? null : k)}>{k} {n}</button>
        ))}
        {loading && <span className="spin" />}
      </div>
      <div className="scroll">
        {error && <div className="empty" style={{ color: 'var(--red)' }}>{error}</div>}
        {!error && !items.length && !loading && (
          <div className="empty">{debounced || kind ? 'Nessun ricordo corrisponde alla ricerca.' : 'Nessun ricordo ancora: Brain li salva con "remember", dalle riflessioni e da ciò che gli dici in chat.'}</div>
        )}
        {items.map((m) => (
          <div className="entry" key={m.id}>
            <small>
              <span className="kind" style={{ ['--c' as string]: kindColor(m.kind) }}>{m.kind}</span> · {when(m.ts)} · importanza {Math.round(m.importance * 100)}%
              {m.score !== undefined && <> · rilevanza {m.score.toFixed(2)}</>}
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
        <div className="status-pct"><b>{pct}%</b><span>verso l'obiettivo</span></div>
        <div className="status-main">
          <div className="status-goal" title={f?.goal}>{f?.goal ?? '…'}</div>
          <div className="bar big"><i style={{ width: `${pct}%` }} /></div>
          <small>
            {r ? (r.source === 'llm' ? 'Stima di Brain basata sui dati qui sotto' : 'Indicatore misurabile (indice di consapevolezza): il modello non era disponibile') : 'Report non ancora generato'}
            {r && <> · aggiornato {when(r.ts)}</>}{data?.stale && r && ' · i dati sono cambiati'}
          </small>
        </div>
        <button className="btn" disabled={busy} onClick={refresh}>{busy ? <><span className="spin" /> Sto riassumendo…</> : '↻ Aggiorna'}</button>
      </div>
      {error && <div className="empty" style={{ color: 'var(--red)' }}>{error}</div>}

      {r ? (
        <ol className="status-lines">{r.lines.map((l, i) => <li key={i}>{l}</li>)}</ol>
      ) : (
        <div className="empty">{busy ? 'Brain sta riassumendo il progetto…' : 'Premi Aggiorna per far raccontare a Brain il suo stato.'}</div>
      )}

      {f && (
        <div className="status-stats">
          <div><b style={{ color: 'var(--green)' }}>{f.goals.done}</b>riusciti</div>
          <div><b style={{ color: 'var(--red)' }}>{f.goals.failed}</b>falliti</div>
          <div><b style={{ color: 'var(--amber)' }}>{f.goals.pending + f.goals.active}</b>in coda</div>
          <div><b>{f.metrics.tools}</b>tool</div>
          <div><b>{f.metrics.memories}</b>ricordi</div>
          <div><b>{f.metrics.lessons}</b>lezioni</div>
          <div><b>{f.metrics.awareness_index.toFixed(2)}</b>indice</div>
        </div>
      )}

      {r && (
        <div className="cols status-cols">
          <div className="card"><b style={{ color: 'var(--green)' }}>Fatto</b>
            <ul>{r.done.length ? r.done.map((d, i) => <li key={i}>{d}</li>) : <li className="dim">nulla di concreto ancora</li>}</ul>
          </div>
          <div className="card"><b style={{ color: 'var(--amber)' }}>Manca</b>
            <ul>{r.missing.length ? r.missing.map((d, i) => <li key={i}>{d}</li>) : <li className="dim">non indicato</li>}</ul>
          </div>
        </div>
      )}
    </div>
  )
}
