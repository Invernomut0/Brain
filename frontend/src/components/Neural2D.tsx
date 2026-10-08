import { useMemo } from 'react'
import { roleColor, useBrain } from '../store'
import { useNow } from '../hooks/useNow'
import { glowOf } from './neural/fx'
import type { AgentView } from '../types'

interface P { x: number; y: number }

const FIXED: Record<string, { p: P; color: string; label: string }> = {
  core: { p: { x: 0, y: 0 }, color: '#8b7bff', label: 'MIND' },
  user: { p: { x: -390, y: -50 }, color: '#ffe9b0', label: 'Lorenzo' },
  internet: { p: { x: 390, y: -50 }, color: '#22d3ee', label: 'Internet' },
  sandbox: { p: { x: 70, y: 235 }, color: '#ffb020', label: 'Sandbox · Podman' },
  memory: { p: { x: -70, y: -235 }, color: '#60a5fa', label: 'Memoria' },
}

const arc = (a: P, b: P) => {
  const lift = Math.hypot(a.x - b.x, a.y - b.y) * 0.22
  return `M${a.x},${a.y} Q${(a.x + b.x) / 2},${(a.y + b.y) / 2 - lift} ${b.x},${b.y}`
}

function depthOf(a: AgentView, all: Record<string, AgentView>): number {
  let d = 0
  let cur: AgentView | undefined = a
  while (cur?.parent && all[cur.parent] && d < 3) { d++; cur = all[cur.parent] }
  return d
}

function Satellite({ id, p, color, label, glow }: { id: string; p: P; color: string; label: string; glow: number }) {
  const w = 1.5 + glow * 3
  return (
    <g transform={`translate(${p.x},${p.y})`}>
      <circle r={30 + glow * 10} fill={color} opacity={0.08 + glow * 0.2} />
      {id === 'internet' && <><circle r={17} fill="none" stroke={color} strokeWidth={w} /><ellipse rx={7} ry={17} fill="none" stroke={color} strokeWidth={1} opacity={0.7} /><line x1={-17} x2={17} stroke={color} strokeWidth={1} opacity={0.7} /></>}
      {id === 'sandbox' && <><rect x={-15} y={-15} width={30} height={30} fill="none" stroke={color} strokeWidth={w} /><rect x={-7} y={-7} width={14} height={14} fill={color} opacity={0.8} /></>}
      {id === 'memory' && <polygon points="0,-19 14,0 0,19 -14,0" fill="none" stroke={color} strokeWidth={w} />}
      {id === 'user' && <><circle r={13} fill={color} opacity={0.9} /><circle r={21} fill="none" stroke={color} strokeWidth={1} opacity={0.6} /></>}
      {id.startsWith('tool:') && <polygon points="0,-11 9,0 0,11 -9,0" fill={color} opacity={0.85} />}
      <text y={id.startsWith('tool:') ? 26 : 38} textAnchor="middle" fontSize={id.startsWith('tool:') ? 10 : 12} fill={color} fontWeight={600}>{label}</text>
    </g>
  )
}

/** Lightweight SVG rendering of the same mesh: no WebGL, near-zero GPU cost. */
export function Neural2D() {
  const agentsMap = useBrain((s) => s.agents)
  const customTools = useBrain((s) => s.customTools)
  const pulses = useBrain((s) => s.pulses)
  const activity = useBrain((s) => s.activity)
  const streamTps = useBrain((s) => s.streamTps)
  const cycle = useBrain((s) => s.control.cycle)
  const aw = useBrain((s) => s.metrics.awareness_index)
  const busy = useBrain((s) => s.sys.llm_busy)
  const now = useNow(500)

  const agents = useMemo(() => Object.values(agentsMap).filter((a) => !a.endedAt || now - a.endedAt < 12000), [agentsMap, now])
  const tools = useMemo(() => customTools.filter((t) => t.status === 'active').map((t) => t.name), [customTools])

  const pos = useMemo(() => {
    const m = new Map<string, P>()
    for (const [id, f] of Object.entries(FIXED)) m.set(id, f.p)
    const byDepth: Record<number, AgentView[]> = {}
    for (const a of agents) (byDepth[depthOf(a, agentsMap)] ??= []).push(a)
    for (const [depth, list] of Object.entries(byDepth)) {
      const d = Number(depth)
      list.forEach((a, i) => {
        const ang = (i / Math.max(list.length, 3)) * Math.PI * 2 + d * 0.9 - Math.PI / 2
        const r = 125 + d * 65
        m.set(a.id, { x: Math.cos(ang) * r, y: Math.sin(ang) * r * 0.8 })
      })
    }
    tools.forEach((t, i) => {
      const ang = (i / Math.max(tools.length, 1)) * Math.PI * 2 + 0.4
      m.set(`tool:${t}`, { x: Math.cos(ang) * 300, y: Math.sin(ang) * 215 })
    })
    return m
  }, [agents, agentsMap, tools])

  const core = pos.get('core') as P
  const links: { id: string; a: P; b: P; color: string; hot: boolean }[] = []
  for (const [id, f] of Object.entries(FIXED)) if (id !== 'core') links.push({ id, a: core, b: f.p, color: f.color, hot: glowOf(activity, id) > 0.25 })
  for (const t of tools) links.push({ id: `tool:${t}`, a: core, b: pos.get(`tool:${t}`) as P, color: '#ffb020', hot: glowOf(activity, `tool:${t}`) > 0.25 })
  for (const a of agents) {
    const from = a.parent && pos.get(a.parent) ? pos.get(a.parent) as P : core
    links.push({ id: a.id, a: from, b: pos.get(a.id) as P, color: roleColor(a.role), hot: a.state === 'thinking' || a.state === 'acting' })
  }

  return (
    <svg viewBox="-500 -320 1000 640" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" className="n2d">
      <defs>
        <radialGradient id="n2core"><stop offset="0" stopColor="#fff" stopOpacity="0.95" /><stop offset="0.35" stopColor="#8b7bff" stopOpacity="0.9" /><stop offset="1" stopColor="#8b7bff" stopOpacity="0" /></radialGradient>
      </defs>
      {[90, 170, 250, 330].map((r) => <ellipse key={r} rx={r * 1.25} ry={r} fill="none" stroke="#5b6cff" strokeOpacity={0.1} />)}

      {links.map((l) => (
        <path key={l.id} d={arc(l.a, l.b)} fill="none" stroke={l.color} strokeOpacity={l.hot ? 0.7 : 0.22} strokeWidth={l.hot ? 1.6 : 1} className={l.hot ? 'n2d-flow hot' : 'n2d-flow'} />
      ))}

      <g>
        <circle r={74} fill="url(#n2core)" opacity={0.55 + (busy ? 0.25 : 0)} className={busy ? 'n2d-breathe' : ''} />
        <circle r={34 + aw * 20} fill="#c7d0ff" opacity={0.9} />
        <circle r={52} fill="none" stroke="#22d3ee" strokeWidth={1.5} strokeDasharray="6 10" className="n2d-spin" />
        <circle r={64} fill="none" stroke="#f472d0" strokeWidth={1} strokeDasharray="2 12" className="n2d-spin rev" />
        <text y={96} textAnchor="middle" fontSize={15} letterSpacing={6} fill="#c7d0ff" fontWeight={700}>MIND</text>
        <text y={113} textAnchor="middle" fontSize={10} letterSpacing={2} fill="#7f89b8">CICLO {cycle} · AWARENESS {(aw * 100).toFixed(0)}</text>
      </g>

      {Object.entries(FIXED).filter(([id]) => id !== 'core').map(([id, f]) => (
        <Satellite key={id} id={id} p={f.p} color={f.color} label={f.label} glow={glowOf(activity, id)} />
      ))}
      {tools.map((t) => <Satellite key={t} id={`tool:${t}`} p={pos.get(`tool:${t}`) as P} color="#ffb020" label={t} glow={glowOf(activity, `tool:${t}`)} />)}

      {agents.map((a) => {
        const p = pos.get(a.id) as P
        const c = a.state === 'failed' ? '#ff4d6d' : a.state === 'done' ? '#34f5a0' : roleColor(a.role)
        const tps = streamTps[a.id] ?? 0
        const fade = a.endedAt ? Math.max(0.25, 1 - (now - a.endedAt) / 12000) : 1
        return (
          <g key={a.id} transform={`translate(${p.x},${p.y})`} opacity={fade}>
            <circle r={30} fill={c} opacity={a.state === 'idle' ? 0.08 : 0.18} />
            {a.state === 'thinking' && <circle r={14} fill="none" stroke={c} strokeWidth={2} className="n2d-ripple" />}
            {a.state === 'acting' && <circle r={22} fill="none" stroke={c} strokeWidth={1.5} strokeDasharray="4 6" className="n2d-spin" />}
            {a.state === 'queued' && <circle r={20} fill="none" stroke="#ffb020" strokeWidth={1.5} strokeDasharray="2 5" className="n2d-spin rev" />}
            <circle r={13} fill={c} />
            <text y={-24} textAnchor="middle" fontSize={12} fontWeight={700} fill={c}>{a.role}</text>
            <text y={34} textAnchor="middle" fontSize={10} fill="#aab4e6">{a.state === 'acting' ? `▸ ${a.detail}` : a.state === 'thinking' && tps > 0 ? `${a.state} · ${tps.toFixed(0)} tok/s` : a.state}</text>
          </g>
        )
      })}

      {pulses.filter((p) => now - p.t0 < 1100 && pos.get(p.from) && pos.get(p.to)).map((p) => (
        <circle key={p.id} r={3.5} fill={p.color} opacity={0.95}>
          <animateMotion dur="1s" path={arc(pos.get(p.from) as P, pos.get(p.to) as P)} fill="freeze" />
        </circle>
      ))}
    </svg>
  )
}

/** Placeholder when the graphical view is switched off: only plain-text live status, no rendering cost. */
export function ViewOff({ onPick }: { onPick: (m: '3d' | '2d') => void }) {
  const agents = useBrain((s) => s.agents)
  const sys = useBrain((s) => s.sys)
  const active = Object.values(agents).filter((a) => !a.endedAt)
  return (
    <div className="view-off">
      <div className="view-off-title">Vista grafica disattivata</div>
      <div className="view-off-sub">Nessun utilizzo di GPU. Brain continua a lavorare normalmente.</div>
      <div className="view-off-stats">{active.length} agenti attivi · {sys.tps.toFixed(1)} tok/s · {sys.llm_busy} richieste LLM{sys.llm_queued ? ` (+${sys.llm_queued} in coda)` : ''}</div>
      <div className="view-off-list">
        {active.map((a) => <span key={a.id} style={{ color: roleColor(a.role) }}>{a.role} <small>{a.state === 'acting' ? a.detail : a.state}</small></span>)}
      </div>
      <div className="view-off-actions">
        <button className="btn" onClick={() => onPick('2d')}>Mostra 2D</button>
        <button className="btn go" onClick={() => onPick('3d')}>Mostra 3D</button>
      </div>
    </div>
  )
}
