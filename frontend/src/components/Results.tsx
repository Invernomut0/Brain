import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useBrain } from '../store'
import type { Artifact, Milestone, ProgressEntry, ToolRun } from '../types'
import { Markdown } from './Wiki'

const when = (ts: number, secs = false) => new Date(ts * 1000).toLocaleString('en-GB', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', ...(secs ? { second: '2-digit' } : {}) })
const bytes = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n >= 1e3 ? `${Math.round(n / 1e3)} KB` : `${n} B`)
const SOURCE_COLOR: Record<string, string> = { agent: '#8b7bff', status: '#22d3ee', system: '#7f89b8' }

/** Fetch `path` now and again (debounced) whenever tool results, artifacts or progress reports arrive. */
function useLive<T>(path: string): [T | null, string, () => void] {
  const rev = useBrain((s) => s.resultsRev)
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let live = true
    const t = setTimeout(() => {
      api.get<T>(path).then((d) => { if (live) { setData(d); setErr('') } }).catch((e) => live && setErr(String(e.message ?? e)))
    }, data ? 600 : 0)
    return () => { live = false; clearTimeout(t) }
  }, [path, rev, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  return [data, err, () => setTick((n) => n + 1)]
}

function useWho() {
  const names = useBrain((s) => s.names)
  return (agent: string | null, source?: string) => (agent ? names[agent] ?? agent : source === 'status' ? 'Status report' : 'System')
}

// ---------------------------------------------------------------- progress
interface ProgressPayload { latest: ProgressEntry | null; milestones: Milestone[]; history: ProgressEntry[]; goals: Record<string, number> }

function Ring({ pct, color }: { pct: number; color: string }) {
  const r = 52, c = 2 * Math.PI * r
  return (
    <svg className="res-ring" viewBox="0 0 132 132" aria-label={`${Math.round(pct)}% toward the goal`}>
      <circle cx="66" cy="66" r={r} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="10" />
      <circle cx="66" cy="66" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)}
        transform="rotate(-90 66 66)" style={{ transition: 'stroke-dashoffset 0.8s ease', filter: `drop-shadow(0 0 6px ${color})` }} />
      <text x="66" y="68" textAnchor="middle" fontSize="30" fontWeight="800" fill="#e8ecff">{Math.round(pct)}%</text>
      <text x="66" y="88" textAnchor="middle" fontSize="8" letterSpacing="2.5" fill="#7f89b8">PROGRESS</text>
    </svg>
  )
}

function Timeline({ history, who }: { history: ProgressEntry[]; who: (a: string | null, s?: string) => string }) {
  const W = 700, H = 170, L = 34, R = 12, T = 10, B = 22
  if (!history.length) return null
  const t0 = history[0].ts, t1 = history[history.length - 1].ts
  const x = (ts: number) => L + (t1 === t0 ? (W - L - R) / 2 : ((ts - t0) / (t1 - t0)) * (W - L - R))
  const y = (p: number) => T + (1 - p / 100) * (H - T - B)
  const line = history.map((h, i) => `${i ? 'L' : 'M'}${x(h.ts).toFixed(1)},${y(h.percent).toFixed(1)}`).join(' ')
  const area = `${line} L${x(t1).toFixed(1)},${y(0)} L${x(t0).toFixed(1)},${y(0)} Z`
  return (
    <svg className="res-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <defs><linearGradient id="resarea" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#8b7bff" stopOpacity="0.35" /><stop offset="1" stopColor="#8b7bff" stopOpacity="0" /></linearGradient></defs>
      {[0, 25, 50, 75, 100].map((g) => (
        <g key={g}><line x1={L} x2={W - R} y1={y(g)} y2={y(g)} stroke="rgba(255,255,255,0.07)" /><text x={L - 6} y={y(g) + 3} textAnchor="end" fontSize="9" fill="#7f89b8">{g}</text></g>
      ))}
      <path d={area} fill="url(#resarea)" />
      <path d={line} fill="none" stroke="#8b7bff" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {history.map((h) => (
        <circle key={h.id} cx={x(h.ts)} cy={y(h.percent)} r={4} fill={SOURCE_COLOR[h.source]} stroke="#0b0f26" strokeWidth="1.5">
          <title>{`${Math.round(h.percent)}% - ${who(h.agent, h.source)} - ${when(h.ts)}\n${h.summary}`}</title>
        </circle>
      ))}
      <text x={L} y={H - 6} fontSize="9" fill="#7f89b8">{when(t0)}</text>
      <text x={W - R} y={H - 6} fontSize="9" textAnchor="end" fill="#7f89b8">{when(t1)}</text>
    </svg>
  )
}

function ProgressView() {
  const [p, err] = useLive<ProgressPayload>('/progress')
  const who = useWho()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const latest = p?.latest
  const done = p?.milestones.filter((m) => m.done).length ?? 0
  const g = p?.goals ?? {}

  const estimate = async () => {
    setBusy(true); setNote('')
    try { await api.post('/status/refresh') } catch (e: any) { setNote(String(e.message ?? e)) } finally { setBusy(false) }
  }

  return (
    <div className="scroll res-progress">
      {err && <div className="empty" style={{ color: 'var(--red)' }}>{err}</div>}
      <div className="res-head">
        <Ring pct={latest?.percent ?? 0} color={latest ? SOURCE_COLOR[latest.source] : '#7f89b8'} />
        <div className="res-head-text">
          {latest ? (
            <>
              <div className="res-summary">{latest.summary}</div>
              <small>
                <i className="res-dot" style={{ background: SOURCE_COLOR[latest.source] }} />{who(latest.agent, latest.source)} · {when(latest.ts)}
                {latest.goal_id !== null && <> · goal #{latest.goal_id}</>}
              </small>
            </>
          ) : (
            <div className="empty" style={{ textAlign: 'left' }}>No progress reported yet. Agents report it with <code>report_progress</code>; every finished goal and every status estimate adds a point here.</div>
          )}
          <div className="res-chips">
            <span className="res-chip ok">{g.done ?? 0} goals done</span>
            <span className="res-chip bad">{g.failed ?? 0} failed</span>
            <span className="res-chip">{(g.pending ?? 0) + (g.active ?? 0)} queued</span>
            <button className="btn mini" disabled={busy} onClick={estimate} title="Ask the model for a fresh estimate toward the main goal">{busy ? <><span className="spin" /> Estimating…</> : '↻ Estimate now'}</button>
          </div>
          {note && <div className="modal-err">{note}</div>}
        </div>
      </div>

      {p && p.history.length > 0 && (
        <section className="res-card">
          <h4>Timeline <span>{p.history.length} reports</span></h4>
          <Timeline history={p.history} who={who} />
          <div className="res-legend">
            {Object.entries(SOURCE_COLOR).map(([s, c]) => <span key={s}><i className="res-dot" style={{ background: c }} />{s === 'agent' ? 'reported by an agent' : s === 'status' ? 'status estimate' : 'goal finished'}</span>)}
          </div>
        </section>
      )}

      {p && p.milestones.length > 0 && (
        <section className="res-card">
          <h4>Milestones <span>{done}/{p.milestones.length} reached</span></h4>
          <ul className="res-miles">
            {p.milestones.map((m, i) => <li key={i} className={m.done ? 'done' : ''}><i>{m.done ? '✓' : ''}</i>{m.title}</li>)}
          </ul>
        </section>
      )}

      {p && p.history.length > 0 && (
        <section className="res-card">
          <h4>Updates</h4>
          {[...p.history].reverse().slice(0, 60).map((h) => (
            <div className="res-upd" key={h.id}>
              <b style={{ color: SOURCE_COLOR[h.source] }}>{Math.round(h.percent)}%</b>
              <div><div>{h.summary}</div><small>{who(h.agent, h.source)} · {when(h.ts)}{h.goal_id !== null && <> · goal #{h.goal_id}</>}</small></div>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}

// --------------------------------------------------------------- artifacts
function parseCsv(text: string, delim: string): string[][] {
  const rows: string[][] = []
  let row: string[] = [], cell = '', quoted = false
  for (let i = 0; i < text.length && rows.length < 300; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++ } else if (ch === '"') quoted = false
      else cell += ch
    } else if (ch === '"') quoted = true
    else if (ch === delim) { row.push(cell); cell = '' }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell); cell = ''
      if (row.some((c) => c !== '')) rows.push(row)
      row = []
    } else cell += ch
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row) }
  return rows
}

const rawUrl = (a: Artifact, download = false) => `/api/v1/artifacts/${a.id}/raw${download ? '?download=1' : ''}`
const TEXT_KINDS = new Set(['markdown', 'csv', 'json', 'text', 'code'])

function Preview({ a }: { a: Artifact }) {
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const asText = TEXT_KINDS.has(a.kind)
  useEffect(() => {
    setText(null); setErr('')
    if (!asText || !a.exists) return
    let live = true
    fetch(rawUrl(a)).then(async (r) => { if (!r.ok) throw new Error(`${r.status}`); const t = await r.text(); if (live) setText(t.slice(0, 200_000)) }).catch((e) => live && setErr(String(e.message ?? e)))
    return () => { live = false }
  }, [a.id, a.ts, a.exists, asText]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!a.exists) return <div className="empty">The file is no longer in the workspace.</div>
  if (a.kind === 'html') return <iframe key={`${a.id}-${a.ts}`} className="res-frame" title={a.title} sandbox="allow-scripts" referrerPolicy="no-referrer" src={rawUrl(a)} />
  if (a.kind === 'image') return <div className="res-img"><img key={`${a.id}-${a.ts}`} src={rawUrl(a)} alt={a.title} /></div>
  if (!asText) return <div className="empty">No preview for this file type: use Open or Download.</div>
  if (err) return <div className="empty" style={{ color: 'var(--red)' }}>{err}</div>
  if (text === null) return <div className="empty"><span className="spin" /></div>
  if (a.kind === 'markdown') return <div className="scroll res-doc"><Markdown text={text} resolve={() => null} open={() => {}} /></div>
  if (a.kind === 'csv') {
    const rows = parseCsv(text, a.path.toLowerCase().endsWith('.tsv') ? '\t' : ',')
    return (
      <div className="scroll res-doc">
        <table className="res-table"><thead><tr>{(rows[0] ?? []).map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
          <tbody>{rows.slice(1).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table>
        {rows.length >= 300 && <div className="empty">Showing the first 300 rows.</div>}
      </div>
    )
  }
  let body = text
  if (a.kind === 'json') { try { body = JSON.stringify(JSON.parse(text), null, 2) } catch { /* shown as it is */ } }
  return <div className="scroll res-doc"><pre className="res-pre">{body}</pre></div>
}

const KIND_LABEL: Record<string, string> = { html: 'HTML', markdown: 'MD', image: 'IMG', json: 'JSON', csv: 'CSV', text: 'TXT', pdf: 'PDF', code: 'CODE', other: 'FILE' }

function ArtifactsView() {
  const [data, err, reload] = useLive<{ items: Artifact[] }>('/artifacts')
  const who = useWho()
  const [selected, setSelected] = useState<number | null>(null)
  const items = data?.items ?? []
  const current = useMemo(() => items.find((a) => a.id === selected) ?? items[0] ?? null, [items, selected])

  const remove = async (a: Artifact) => {
    if (!confirm(`Remove "${a.title}" from the list? The file stays in the workspace.`)) return
    await api.del(`/artifacts/${a.id}`)
    setSelected(null); reload()
  }

  return (
    <div className="res-body">
      <div className="res-list scroll">
        {err && <div className="empty" style={{ color: 'var(--red)' }}>{err}</div>}
        {!items.length && !err && <div className="empty">No artifacts yet.<br />Agents publish pages, reports and data with <code>publish_artifact</code>; files written while working on a goal are added when it ends.</div>}
        {items.map((a) => (
          <div key={a.id} className={`res-item ${current?.id === a.id ? 'on' : ''}`} onClick={() => setSelected(a.id)}>
            <span className="res-kind">{KIND_LABEL[a.kind] ?? 'FILE'}</span>
            <div className="res-item-text">
              <b>{a.title}</b>
              <small>{who(a.agent)} · {when(a.ts)} · {bytes(a.size)}{a.auto ? ' · auto' : ''}</small>
            </div>
          </div>
        ))}
      </div>
      <div className="res-main">
        {current ? (
          <>
            <div className="res-main-head">
              <div>
                <h3>{current.title}</h3>
                <small>{current.path} · {who(current.agent)}{current.goal_id !== null && <> · goal #{current.goal_id}</>} · {when(current.ts)} · {bytes(current.size)}</small>
                {current.description && <div className="res-desc">{current.description}</div>}
              </div>
              <div className="res-actions">
                {current.exists && <a className="btn mini" href={rawUrl(current)} target="_blank" rel="noopener noreferrer">↗ Open</a>}
                {current.exists && <a className="btn mini" href={rawUrl(current, true)}>⇩ Download</a>}
                <button className="btn mini danger" onClick={() => remove(current)}>✕ Remove</button>
              </div>
            </div>
            <div className="res-preview"><Preview a={current} /></div>
          </>
        ) : <div className="empty">Select an artifact.</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- tool runs
interface RunList { items: ToolRun[]; tools: string[] }
interface RunFull extends ToolRun { args: string; output: string }

function pretty(s: string) {
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s }
}

function ToolRunsView() {
  const [tool, setTool] = useState<string | null>(null)
  const [status, setStatus] = useState<'all' | 'ok' | 'failed'>('all')
  const params = new URLSearchParams({ limit: '150' })
  if (tool) params.set('tool', tool)
  if (status !== 'all') params.set('ok', String(status === 'ok'))
  const [data, err] = useLive<RunList>(`/tool-runs?${params}`)
  const who = useWho()
  const [selected, setSelected] = useState<number | null>(null)
  const [full, setFull] = useState<RunFull | null>(null)
  const [copied, setCopied] = useState(false)
  const items = data?.items ?? []
  const currentId = items.find((r) => r.id === selected)?.id ?? items[0]?.id ?? null

  useEffect(() => {
    if (currentId === null) { setFull(null); return }
    let live = true
    api.get<RunFull>(`/tool-runs/${currentId}`).then((r) => live && setFull(r)).catch(() => live && setFull(null))
    return () => { live = false }
  }, [currentId])

  const copy = async () => { if (full) { await navigator.clipboard.writeText(full.output); setCopied(true); setTimeout(() => setCopied(false), 1200) } }

  return (
    <div className="res-body">
      <div className="res-list scroll">
        <div className="mem-bar" style={{ padding: '0 0 8px' }}>
          {(['all', 'ok', 'failed'] as const).map((s) => <button key={s} className={`pill ${status === s ? 'on' : ''}`} onClick={() => setStatus(s)}>{s}</button>)}
        </div>
        <div className="mem-bar" style={{ padding: '0 0 8px' }}>
          <button className={`pill ${tool === null ? 'on' : ''}`} onClick={() => setTool(null)}>all tools</button>
          {(data?.tools ?? []).map((t) => <button key={t} className={`pill ${tool === t ? 'on' : ''}`} onClick={() => setTool(tool === t ? null : t)}>{t}</button>)}
        </div>
        {err && <div className="empty" style={{ color: 'var(--red)' }}>{err}</div>}
        {!items.length && !err && <div className="empty">No tool runs yet: every call an agent makes is recorded here with its full output.</div>}
        {items.map((r) => (
          <div key={r.id} className={`res-item ${currentId === r.id ? 'on' : ''}`} onClick={() => setSelected(r.id)}>
            <span className={`res-kind ${r.ok ? 'ok' : 'bad'}`}>{r.ok ? 'OK' : 'ERR'}</span>
            <div className="res-item-text">
              <b>{r.tool}</b>
              <small>{who(r.agent)} · {when(r.ts, true)} · {r.ms} ms</small>
              <small className="res-prev">{r.preview}</small>
            </div>
          </div>
        ))}
      </div>
      <div className="res-main">
        {full ? (
          <>
            <div className="res-main-head">
              <div>
                <h3>{full.tool} <span className={`res-kind ${full.ok ? 'ok' : 'bad'}`}>{full.ok ? 'OK' : 'ERROR'}</span></h3>
                <small>{who(full.agent)}{full.goal_id !== null && <> · goal #{full.goal_id}</>} · {when(full.ts, true)} · {full.ms} ms · {bytes(full.output.length)}</small>
              </div>
              <div className="res-actions"><button className="btn mini" onClick={copy}>{copied ? '✓ Copied' : '⧉ Copy output'}</button></div>
            </div>
            <h5 className="res-label">Arguments</h5>
            <pre className="res-pre res-args">{pretty(full.args)}</pre>
            <h5 className="res-label">Output</h5>
            <div className="scroll res-out"><pre className="res-pre">{full.output || '(empty)'}</pre></div>
          </>
        ) : <div className="empty">Select a tool run.</div>}
      </div>
    </div>
  )
}

// -------------------------------------------------------------------- view
const TABS = { progress: 'Progress', artifacts: 'Artifacts', runs: 'Tool runs' } as const
type Tab = keyof typeof TABS
const TAB_KEY = 'brain.results.tab'

export function ResultsView() {
  const [tab, setTabState] = useState<Tab>(() => (localStorage.getItem(TAB_KEY) as Tab) in TABS ? (localStorage.getItem(TAB_KEY) as Tab) : 'progress')
  const setTab = (t: Tab) => { localStorage.setItem(TAB_KEY, t); setTabState(t) }
  return (
    <div className="results">
      <div className="res-tabs">
        {(Object.keys(TABS) as Tab[]).map((t) => <button key={t} className={`pill ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>{TABS[t]}</button>)}
      </div>
      {tab === 'progress' && <ProgressView />}
      {tab === 'artifacts' && <ArtifactsView />}
      {tab === 'runs' && <ToolRunsView />}
    </div>
  )
}
