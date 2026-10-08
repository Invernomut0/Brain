import { useEffect, useMemo, useRef } from 'react'
import * as d3 from 'd3'
import { useBrain } from '../store'
import type { Goal } from '../types'

const STATUS_COLOR: Record<string, string> = {
  pending: '#7f89b8', active: '#22d3ee', done: '#34f5a0', failed: '#ff4d6d', cancelled: '#4b5578',
}

interface TNode { goal: Goal; children: TNode[] }

/** 2D goal tree (d3 tree layout) with zoom/pan; colours show status, ring shows predicted success. */
export function GoalTree() {
  const goalsMap = useBrain((s) => s.goals)
  const svgRef = useRef<SVGSVGElement>(null)
  const gRef = useRef<SVGGElement>(null)
  const goals = useMemo(() => Object.values(goalsMap), [goalsMap])

  const layout = useMemo(() => {
    if (!goals.length) return null
    const byParent = new Map<number | null, Goal[]>()
    goals.forEach((g) => byParent.set(g.parent_id, [...(byParent.get(g.parent_id) ?? []), g]))
    const build = (g: Goal): TNode => ({ goal: g, children: (byParent.get(g.id) ?? []).map(build) })
    const roots = byParent.get(null) ?? []
    if (!roots.length) return null
    const root = d3.hierarchy<TNode>(build(roots[0]), (n) => n.children)
    d3.tree<TNode>().nodeSize([26, 190])(root)
    return root
  }, [goals])

  useEffect(() => {
    if (!svgRef.current || !gRef.current) return
    const svg = d3.select(svgRef.current)
    const zoom = d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.3, 2.5]).on('zoom', (e) => {
      d3.select(gRef.current).attr('transform', e.transform.toString())
    })
    svg.call(zoom)
    svg.call(zoom.transform, d3.zoomIdentity.translate(40, 200).scale(0.9))
  }, [])

  if (!layout) return <div className="empty">Nessun obiettivo ancora.</div>
  const nodes = layout.descendants()
  const links = layout.links()
  const link = d3.linkHorizontal<any, any>().x((d) => d.y).y((d) => d.x)

  return (
    <svg ref={svgRef} width="100%" height="100%" style={{ cursor: 'grab' }}>
      <g ref={gRef}>
        {links.map((l) => (
          <path key={l.target.data.goal.id} d={link(l) ?? ''} fill="none" stroke={STATUS_COLOR[l.target.data.goal.status]} strokeOpacity={0.5} strokeWidth={1.4} />
        ))}
        {nodes.map((n) => {
          const g = n.data.goal
          const c = STATUS_COLOR[g.status] ?? '#7f89b8'
          const r = 5 + (g.priority ?? 0.5) * 5
          return (
            <g key={g.id} transform={`translate(${n.y},${n.x})`}>
              <title>{`${g.title}\n${g.description}\n${g.result ?? ''}`}</title>
              {g.expected_success != null && <circle r={r + 4} fill="none" stroke={c} strokeOpacity={0.35} strokeDasharray={`${g.expected_success * 2 * Math.PI * (r + 4)} 999`} />}
              <circle r={r} fill={c} fillOpacity={0.85}>
                {g.status === 'active' && <animate attributeName="r" values={`${r};${r + 3};${r}`} dur="1.2s" repeatCount="indefinite" />}
              </circle>
              <text x={r + 8} y={4} className="node-label" fill={c}>{g.title.length > 38 ? g.title.slice(0, 37) + '…' : g.title}</text>
            </g>
          )
        })}
      </g>
    </svg>
  )
}
