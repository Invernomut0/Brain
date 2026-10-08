import { useEffect, useRef, useState } from 'react'
import { roleColor, useBrain } from '../store'
import type { AgentView } from '../types'

const REASONING_PREFIX = '(ragiona) '
const HIDE_KEY = 'brain.thoughts.hidden'

function ThoughtCard({ a }: { a: AgentView }) {
  const raw = useBrain((s) => s.streams[a.id]) ?? ''
  const tps = useBrain((s) => s.streamTps[a.id]) ?? 0
  const [open, setOpen] = useState(false)
  const reasoning = raw.startsWith(REASONING_PREFIX)
  const text = reasoning ? raw.slice(REASONING_PREFIX.length) : raw
  const body = useRef<HTMLDivElement>(null)
  const color = roleColor(a.role)
  useEffect(() => { const el = body.current; if (el) el.scrollTop = el.scrollHeight }, [text, open])

  const thinking = a.state === 'thinking'
  const queued = a.state === 'queued'
  const shown = thinking ? text : a.thought
  return (
    <div className={`live ${open ? 'open' : ''}`} style={{ ['--c' as string]: color }}>
      <div className="live-head" onClick={() => setOpen(!open)} title="Clicca per espandere/comprimere">
        <i className="live-dot" />
        <b>{a.role}</b>
        <span className="live-id">{a.id}</span>
        <span className="live-mode">{thinking ? (reasoning ? 'ragionamento' : 'risposta') : queued ? 'in coda' : a.state === 'acting' ? `tool \u00b7 ${a.detail}` : a.state}</span>
        {thinking && tps > 0 && <span className="live-tps">{tps.toFixed(1)} tok/s</span>}
        <span className="live-chev">{open ? '\u25be' : '\u25b8'}</span>
      </div>
      <div className="live-body" ref={body}>
        {shown || <span className="live-wait">{queued ? 'in attesa di uno slot su LM Studio\u2026' : 'in attesa del modello\u2026'}</span>}
        {thinking && <span className="live-caret" />}
      </div>
    </div>
  )
}

/** Compact live view of working agents; cards expand on click and the whole stack can be hidden. */
export function LiveThoughts() {
  const agents = useBrain((s) => s.agents)
  const [hidden, setHidden] = useState(() => localStorage.getItem(HIDE_KEY) === '1')
  const toggle = () => { localStorage.setItem(HIDE_KEY, hidden ? '0' : '1'); setHidden(!hidden) }
  const active = Object.values(agents)
    .filter((a) => !a.endedAt && (a.state === 'thinking' || a.state === 'acting' || a.state === 'queued'))
    .sort((a, b) => b.bornAt - a.bornAt)
  if (!active.length) return null
  return (
    <div className="live-stack">
      <button className="live-toggle" onClick={toggle}>{hidden ? `\u25b8 ${active.length} agenti al lavoro` : '\u25be nascondi pensieri'}</button>
      {!hidden && active.slice(0, 3).map((a) => <ThoughtCard key={a.id} a={a} />)}
    </div>
  )
}
