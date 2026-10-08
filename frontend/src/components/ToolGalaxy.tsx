import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { roleColor, useBrain } from '../store'

interface N extends d3.SimulationNodeDatum { id: string; kind: 'agent' | 'tool'; label: string; color: string; r: number }
interface L extends d3.SimulationLinkDatum<N> { w: number }

/** Force-directed map of which agents use which tools (edge weight = call count), updating live. */
export function ToolGalaxy() {
  const agents = useBrain((s) => s.agents)
  const tools = useBrain((s) => s.tools)
  const calls = useBrain((s) => s.toolCalls)
  const ref = useRef<SVGSVGElement>(null)
  const [, force] = useState(0)
  const sim = useRef<d3.Simulation<N, L> | null>(null)
  const nodes = useRef<N[]>([])
  const links = useRef<L[]>([])

  const graph = useMemo(() => {
    const used = new Set(Object.keys(calls).map((k) => k.split('|')[1]))
    const ns: N[] = [
      ...tools.filter((t) => t.custom || used.has(t.name)).map((t): N => ({ id: `t:${t.name}`, kind: 'tool', label: t.name, color: t.custom ? '#ffb020' : '#60a5fa', r: t.custom ? 9 : 7 })),
      ...Object.values(agents).map((a): N => ({ id: `a:${a.id}`, kind: 'agent', label: a.role, color: roleColor(a.role), r: 8 })),
    ]
    const ids = new Set(ns.map((n) => n.id))
    const ls: L[] = Object.entries(calls)
      .map(([k, w]) => { const [a, t] = k.split('|'); return { source: `a:${a}`, target: `t:${t}`, w } })
      .filter((l) => ids.has(l.source as string) && ids.has(l.target as string))
    return { ns, ls }
  }, [agents, tools, calls])

  useEffect(() => {
    const prev = new Map(nodes.current.map((n) => [n.id, n]))
    nodes.current = graph.ns.map((n) => Object.assign(prev.get(n.id) ?? { x: 300 + Math.random() * 40, y: 200 + Math.random() * 40 }, n))
    links.current = graph.ls.map((l) => ({ ...l }))
    const el = ref.current
    const w = el?.clientWidth ?? 600, h = el?.clientHeight ?? 400
    sim.current?.stop()
    sim.current = d3.forceSimulation(nodes.current)
      .force('link', d3.forceLink<N, L>(links.current).id((d) => d.id).distance(90).strength(0.4))
      .force('charge', d3.forceManyBody().strength(-160))
      .force('center', d3.forceCenter(w / 2, h / 2))
      .force('collide', d3.forceCollide<N>().radius((d) => d.r + 14))
      .on('tick', () => force((x) => x + 1))
    return () => { sim.current?.stop() }
  }, [graph])

  return (
    <svg ref={ref} width="100%" height="100%">
      {links.current.map((l, i) => {
        const s = l.source as N, t = l.target as N
        return <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="#8b7bff" strokeOpacity={0.4} strokeWidth={Math.min(1 + l.w * 0.4, 6)} />
      })}
      {nodes.current.map((n) => (
        <g key={n.id} transform={`translate(${n.x},${n.y})`}>
          <circle r={n.r} fill={n.color} fillOpacity={0.85} stroke={n.color} strokeWidth={n.kind === 'tool' ? 0 : 2} />
          <text y={n.r + 12} textAnchor="middle" className="node-label">{n.label}</text>
        </g>
      ))}
      {!nodes.current.length && <text x="50%" y="50%" textAnchor="middle" className="node-label">In attesa dei primi tool…</text>}
    </svg>
  )
}
