import { useMemo, useState } from 'react'
import { roleColor, shortName, useBrain } from '../store'
import { GraphCanvas, type GLink, type GNode } from './Graph'

/** Which agents use which tools (link weight = call count), on the shared 2D/3D graph canvas, updating live. */
export function ToolGalaxy() {
  const agents = useBrain((s) => s.agents)
  const tools = useBrain((s) => s.tools)
  const calls = useBrain((s) => s.toolCalls)
  const names = useBrain((s) => s.names)
  const [selected, setSelected] = useState<string | null>(null)

  const { nodes, links } = useMemo(() => {
    const used = new Set(Object.keys(calls).map((k) => k.split('|')[1]))
    const nodes: GNode[] = [
      ...tools.filter((t) => t.custom || used.has(t.name)).map((t): GNode => ({
        id: `t:${t.name}`, label: t.name, color: t.custom ? '#ffb020' : '#60a5fa', size: t.custom ? 9 : 7, hub: true, tip: `${t.name}\n${t.description}`,
      })),
      ...Object.values(agents).map((a): GNode => ({
        id: `a:${a.id}`, label: shortName(names[a.id] ?? `${a.role} ${a.id.split('-')[1] ?? ''}`.trim()), color: roleColor(a.role), size: 8, hub: true,
        tip: `${a.role} (${a.state})\n${a.task}`,
      })),
    ]
    const ids = new Set(nodes.map((n) => n.id))
    const links: GLink[] = Object.entries(calls)
      .map(([k, weight]) => { const [a, t] = k.split('|'); return { source: `a:${a}`, target: `t:${t}`, weight, color: '#8b7bff' } })
      .filter((l) => ids.has(l.source) && ids.has(l.target))
    return { nodes, links }
  }, [agents, tools, calls, names])

  return (
    <GraphCanvas nodes={nodes} links={links} selected={selected} onSelect={setSelected} fitKey={String(nodes.length)}
      empty={<div className="empty">Waiting for the first tools…</div>} />
  )
}
