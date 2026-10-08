import { useEffect, useRef, useState } from 'react'
import { roleColor, useBrain } from '../store'
import type { AgentView } from '../types'

const REASONING_PREFIX = '(ragiona) '
const HIDE_KEY = 'brain.thoughts.hidden'
const SIZE_KEY = 'brain.thoughts.size'

interface Size { w: number; h: number }
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

function loadSize(): Size | null {
  try {
    const v = JSON.parse(localStorage.getItem(SIZE_KEY) ?? 'null')
    return v && v.w > 0 && v.h > 0 ? v : null
  } catch { return null }
}

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

/** Live view of working agents: resizable (drag the top-right corner, double-click to reset) and hideable. */
export function LiveThoughts() {
  const agents = useBrain((s) => s.agents)
  const [hidden, setHidden] = useState(() => localStorage.getItem(HIDE_KEY) === '1')
  const [size, setSize] = useState<Size | null>(loadSize)
  const sizeRef = useRef<Size | null>(size)
  const stack = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; w: number; h: number } | null>(null)

  const toggle = () => { localStorage.setItem(HIDE_KEY, hidden ? '0' : '1'); setHidden(!hidden) }
  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = stack.current?.getBoundingClientRect()
    if (!r) return
    drag.current = { x: e.clientX, y: e.clientY, w: r.width, h: r.height }
    e.currentTarget.setPointerCapture(e.pointerId)
    e.preventDefault()
  }
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    const parent = stack.current?.parentElement?.getBoundingClientRect()
    if (!d || !parent) return
    // The panel is anchored bottom-left, so dragging the top-right corner up/right grows it.
    const next = { w: clamp(d.w + (e.clientX - d.x), 260, parent.width - 24), h: clamp(d.h + (d.y - e.clientY), 90, parent.height - 70) }
    sizeRef.current = next
    setSize(next)
  }
  const onUp = () => {
    drag.current = null
    if (sizeRef.current) localStorage.setItem(SIZE_KEY, JSON.stringify(sizeRef.current))
  }
  const resetSize = () => { sizeRef.current = null; setSize(null); localStorage.removeItem(SIZE_KEY) }

  const active = Object.values(agents)
    .filter((a) => !a.endedAt && (a.state === 'thinking' || a.state === 'acting' || a.state === 'queued'))
    .sort((a, b) => b.bornAt - a.bornAt)

  // Tell 2D views how much of the left edge this panel covers, so they can shift right and stay fully visible.
  const showing = active.length > 0 && !hidden
  useEffect(() => {
    const el = stack.current
    if (!showing || !el) { useBrain.setState({ thoughtsInset: 0 }); return }
    const ro = new ResizeObserver(() => useBrain.setState({ thoughtsInset: Math.round(el.getBoundingClientRect().width + 24) }))
    ro.observe(el)
    return () => { ro.disconnect(); useBrain.setState({ thoughtsInset: 0 }) }
  }, [showing])

  if (!active.length) return null
  const sized = size && !hidden
  return (
    <div ref={stack} className={`live-stack ${sized ? 'sized' : ''}`} style={sized ? { width: size.w, height: size.h } : undefined}>
      {!hidden && (
        <div className="live-grip" title="Trascina per ridimensionare, doppio clic per ripristinare"
          onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onDoubleClick={resetSize} />
      )}
      <button className="live-toggle" onClick={toggle}>{hidden ? `\u25b8 ${active.length} agenti al lavoro` : '\u25be nascondi pensieri'}</button>
      {!hidden && active.slice(0, 3).map((a) => <ThoughtCard key={a.id} a={a} />)}
    </div>
  )
}
