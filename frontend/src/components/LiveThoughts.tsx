import { useEffect, useRef } from 'react'
import { roleColor, useBrain } from '../store'
import type { AgentView } from '../types'

const REASONING_PREFIX = '(ragiona) '

function ThoughtCard({ a }: { a: AgentView }) {
  const raw = useBrain((s) => s.streams[a.id]) ?? ''
  const tps = useBrain((s) => s.sys.tps)
  const reasoning = raw.startsWith(REASONING_PREFIX)
  const text = reasoning ? raw.slice(REASONING_PREFIX.length) : raw
  const body = useRef<HTMLDivElement>(null)
  const color = roleColor(a.role)
  useEffect(() => { const el = body.current; if (el) el.scrollTop = el.scrollHeight }, [text])

  const thinking = a.state === 'thinking'
  const shown = thinking ? text : a.thought
  return (
    <div className="live" style={{ ['--c' as string]: color }}>
      <div className="live-head">
        <i className="live-dot" />
        <b>{a.role}</b>
        <span className="live-id">{a.id}</span>
        <span className="live-mode">{thinking ? (reasoning ? 'ragionamento' : 'risposta') : a.state === 'acting' ? `tool · ${a.detail}` : a.state}</span>
        {thinking && <span className="live-tps">{tps.toFixed(1)} tok/s</span>}
      </div>
      <div className="live-body" ref={body}>
        {shown || <span className="live-wait">in attesa del modello…</span>}
        {thinking && <span className="live-caret" />}
      </div>
    </div>
  )
}

/** Live view of what the working agents are thinking/saying, readable and auto-scrolling. */
export function LiveThoughts() {
  const agents = useBrain((s) => s.agents)
  const active = Object.values(agents)
    .filter((a) => !a.endedAt && (a.state === 'thinking' || a.state === 'acting'))
    .sort((a, b) => b.bornAt - a.bornAt)
    .slice(0, 2)
  if (!active.length) return null
  return <div className="live-stack">{active.map((a) => <ThoughtCard key={a.id} a={a} />)}</div>
}
