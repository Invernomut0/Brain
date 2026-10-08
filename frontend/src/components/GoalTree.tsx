import { useMemo, useState } from 'react'
import * as d3 from 'd3'
import { useBrain } from '../store'
import type { Goal } from '../types'
import { GraphCanvas, type GLink, type GNode } from './Graph'

const STATUS_COLOR: Record<string, string> = {
  pending: '#7f89b8', active: '#22d3ee', done: '#34f5a0', failed: '#ff4d6d', cancelled: '#4b5578',
}
const STATUS_LABEL: Record<string, string> = { pending: 'in coda', active: 'in corso', done: 'riuscito', failed: 'fallito', cancelled: 'annullato' }
const ROW = 38  // px between siblings (2D)
const COL = 310  // px between levels (2D): wide enough for a full label
const RING = 190  // radius step (3D)

interface T { id: number; children: T[] }

/** Tidy-tree positions: left-to-right in 2D, radial in 3D. Orphans (missing parent) become extra roots. */
function layout(goals: Goal[], mode: '2d' | '3d'): Map<string, [number, number, number]> {
  const ids = new Set(goals.map((g) => g.id))
  const kids = new Map<number, Goal[]>()
  for (const g of [...goals].sort((a, b) => a.id - b.id)) {
    const key = g.parent_id != null && ids.has(g.parent_id) ? g.parent_id : -1
    kids.set(key, [...(kids.get(key) ?? []), g])
  }
  const build = (id: number): T => ({ id, children: (kids.get(id) ?? []).map((g) => build(g.id)) })
  const root = d3.hierarchy<T>({ id: -1, children: (kids.get(-1) ?? []).map((g) => build(g.id)) }, (n) => n.children)
  const out = new Map<string, [number, number, number]>()
  if (mode === '2d') {
    d3.tree<T>().nodeSize([ROW, COL])(root)
    root.each((n) => { if (n.depth > 0) out.set(String(n.data.id), [(n.depth - 1) * COL, n.x ?? 0, 0]) })
  } else {
    d3.tree<T>().size([2 * Math.PI, 1]).separation((a, b) => (a.parent === b.parent ? 1 : 2) / Math.max(a.depth, 1))(root)
    root.each((n) => {
      if (n.depth === 0) return
      const r = (n.depth - 1) * RING, a = n.x ?? 0
      out.set(String(n.data.id), [r * Math.cos(a), r * Math.sin(a), (n.depth - 1) * 45])
    })
  }
  return out
}

/** Goal tree on the shared graph canvas (2D or 3D); colour = status, ring = predicted success. */
export function GoalTree() {
  const goalsMap = useBrain((s) => s.goals)
  const mode = useBrain((s) => s.graphMode)
  const [selected, setSelected] = useState<string | null>(null)

  const { nodes, links } = useMemo(() => {
    const goals = Object.values(goalsMap)
    const pos = layout(goals, mode)
    const nodes: GNode[] = goals.map((g) => ({
      id: String(g.id), label: g.title, color: STATUS_COLOR[g.status] ?? '#7f89b8',
      size: g.parent_id == null ? 11 : 5 + (g.priority ?? 0.5) * 5, hub: g.parent_id == null,
      arc: g.expected_success ?? undefined, tip: `${g.title}\n[${STATUS_LABEL[g.status] ?? g.status}] ${g.description ?? ''}\n${g.result ?? ''}`.trim(),
      fixed: pos.get(String(g.id)) ?? null,
    }))
    const links: GLink[] = goals
      .filter((g) => g.parent_id != null && goalsMap[g.parent_id])
      .map((g) => ({ source: String(g.parent_id), target: String(g.id), color: STATUS_COLOR[g.status] }))
    return { nodes, links }
  }, [goalsMap, mode])

  const g = selected ? goalsMap[Number(selected)] : null
  return (
    <>
      <GraphCanvas nodes={nodes} links={links} dag selected={selected} onSelect={setSelected} fitKey={String(nodes.length)}
        empty={<div className="empty">Nessun obiettivo ancora.</div>} />
      {g && (
        <div className="goal-card">
          <button className="x" onClick={() => setSelected(null)} aria-label="Chiudi">×</button>
          <b>#{g.id} {g.title}</b>
          <small style={{ color: STATUS_COLOR[g.status] }}>{STATUS_LABEL[g.status] ?? g.status} · priorità {(g.priority ?? 0).toFixed(1)}{g.expected_success != null && <> · successo atteso {Math.round(g.expected_success * 100)}%</>}</small>
          {g.description && <p>{g.description}</p>}
          {g.result && <p className="res">{g.result}</p>}
        </div>
      )}
    </>
  )
}
