import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import SpriteText from 'three-spritetext'
import * as THREE from 'three'
import { useBrain } from '../store'

const ForceGraph3D = lazy(() => import('react-force-graph-3d'))

export interface GNode {
  id: string
  label: string
  color: string  // #rrggbb
  size: number
  hub?: boolean  // labelled even when zoomed out
  tip?: string
  arc?: number  // 0..1 ring around the node (e.g. predicted success)
  fixed?: [number, number, number] | null  // pinned position (semantic map)
}
export interface GLink { source: string; target: string; color?: string; weight?: number }

interface Props {
  nodes: GNode[]
  links: GLink[]
  selected?: string | null
  highlight?: Set<string>
  dag?: boolean  // hierarchy: every label shown at a fixed size and links animate; positions come from `fixed`
  fitKey?: string
  empty?: ReactNode
  onSelect: (id: string | null) => void
}

const TAU = Math.PI * 2
const SPHERE = new THREE.SphereGeometry(1, 18, 14)
const RING = new THREE.TorusGeometry(1.5, 0.07, 8, 40)
const alpha = (hex: string, a: number) => hex + Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0')
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
const cut = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
const endId = (e: any) => (typeof e === 'object' ? e.id : e) as string

function useSize(ref: React.RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setSize({ w: Math.floor(r.width), h: Math.floor(r.height) })
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.floor(e.contentRect.width), h: Math.floor(e.contentRect.height) }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return size
}

/** Force-directed graph in 2D (canvas) or 3D (WebGL) with shared styling, focus dimming and readable labels. */
export function GraphCanvas(p: Props) {
  const mode = useBrain((s) => s.graphMode)
  const setMode = useBrain((s) => s.setGraphMode)
  const wrap = useRef<HTMLDivElement>(null)
  const size = useSize(wrap)
  const fg = useRef<any>(null)
  const nodeStore = useRef(new Map<string, any>())  // node objects survive data refreshes so positions are kept
  const fitted = useRef('')
  const [hover, setHover] = useState<string | null>(null)

  const meta = useMemo(() => new Map(p.nodes.map((n) => [n.id, n])), [p.nodes])
  const linkMeta = useMemo(() => new Map(p.links.map((l) => [`${l.source}>${l.target}`, l])), [p.links])
  const adj = useMemo(() => {
    const m = new Map<string, Set<string>>()
    for (const l of p.links) {
      if (!m.has(l.source)) m.set(l.source, new Set())
      if (!m.has(l.target)) m.set(l.target, new Set())
      m.get(l.source)!.add(l.target)
      m.get(l.target)!.add(l.source)
    }
    return m
  }, [p.links])

  const structure = useMemo(
    () => `${p.nodes.map((n) => `${n.id}${n.fixed ? n.fixed.join(',') : ''}`).join('|')}#${p.links.map((l) => `${l.source}>${l.target}`).join('|')}`,
    [p.nodes, p.links],
  )
  const data = useMemo(() => {
    const store = nodeStore.current
    const keep = new Set(p.nodes.map((n) => n.id))
    for (const id of [...store.keys()]) if (!keep.has(id)) store.delete(id)
    const nodes = p.nodes.map((n) => {
      const o = store.get(n.id) ?? { id: n.id }
      store.set(n.id, o)
      o.fx = n.fixed?.[0]; o.fy = n.fixed?.[1]; o.fz = n.fixed?.[2]
      return o
    })
    return { nodes, links: p.links.map((l) => ({ source: l.source, target: l.target })) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure])

  // Latest values for the per-frame canvas callbacks (keeps their identity stable).
  const focus2d = hover ?? p.selected ?? null
  const live = useRef({ meta, linkMeta, adj, focus: focus2d, selected: p.selected ?? null, hover, hl: p.highlight, dag: !!p.dag })
  live.current = { meta, linkMeta, adj, focus: focus2d, selected: p.selected ?? null, hover, hl: p.highlight?.size ? p.highlight : undefined, dag: !!p.dag }
  const isDim = (id: string, focus: string | null) => {
    const s = live.current
    if (s.hl && !s.hl.has(id)) return true
    return !!focus && id !== focus && !s.adj.get(focus)?.has(id)
  }

  const paint = useCallback((node: any, ctx: CanvasRenderingContext2D, k: number) => {
    const s = live.current
    const m = s.meta.get(node.id)
    if (!m || node.x == null) return
    const r = m.size
    const dim = isDim(node.id, s.focus)
    ctx.save()
    ctx.globalAlpha = dim ? 0.13 : 1
    const glow = ctx.createRadialGradient(node.x, node.y, r * 0.3, node.x, node.y, r * 2.6)
    glow.addColorStop(0, alpha(m.color, 0.35)); glow.addColorStop(1, alpha(m.color, 0))
    ctx.fillStyle = glow
    ctx.beginPath(); ctx.arc(node.x, node.y, r * 2.6, 0, TAU); ctx.fill()
    ctx.fillStyle = m.color
    ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, TAU); ctx.fill()
    ctx.lineWidth = 1 / k; ctx.strokeStyle = 'rgba(255,255,255,0.45)'; ctx.stroke()
    if (m.arc != null) {
      ctx.strokeStyle = alpha(m.color, 0.8); ctx.lineWidth = 1.6 / k
      ctx.beginPath(); ctx.arc(node.x, node.y, r + 3.5, -Math.PI / 2, -Math.PI / 2 + TAU * Math.max(0.02, m.arc)); ctx.stroke()
    }
    if (node.id === s.selected) {
      ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.4 / k; ctx.setLineDash([3 / k, 3 / k])
      ctx.beginPath(); ctx.arc(node.x, node.y, r + 7, 0, TAU); ctx.stroke(); ctx.setLineDash([])
    }
    const show = !dim && (s.dag || m.hub || node.id === s.selected || node.id === s.hover || s.hl?.has(node.id) || s.adj.get(s.focus ?? '')?.has(node.id) || k > 1.3)
    if (show) {
      const fs = s.dag ? 12 : Math.max(11 / k, 2.5)
      ctx.font = `${node.id === s.selected || node.id === s.hover ? 600 : 400} ${fs}px -apple-system, system-ui, sans-serif`
      const text = cut(m.label, s.dag ? 42 : 34)
      const w = ctx.measureText(text).width
      const x = node.x + r + 5 / k, y = node.y
      ctx.fillStyle = 'rgba(4,5,13,0.78)'
      ctx.beginPath(); ctx.roundRect(x - 3 / k, y - fs * 0.75, w + 6 / k, fs * 1.5, 3 / k); ctx.fill()
      ctx.fillStyle = node.id === s.selected ? '#ffffff' : '#dfe3ff'
      ctx.textBaseline = 'middle'; ctx.fillText(text, x, y + 0.5 / k)
    }
    ctx.restore()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const pointer = useCallback((node: any, color: string, ctx: CanvasRenderingContext2D) => {
    ctx.fillStyle = color
    ctx.beginPath(); ctx.arc(node.x, node.y, (live.current.meta.get(node.id)?.size ?? 5) + 4, 0, TAU); ctx.fill()
  }, [])

  const linkColor2d = useCallback((l: any) => {
    const s = live.current
    const a = endId(l.source), b = endId(l.target)
    const base = s.linkMeta.get(`${a}>${b}`)?.color ?? '#8a94c8'
    const on = s.focus && (a === s.focus || b === s.focus)
    return alpha(base, on ? 0.9 : s.focus || s.hl ? 0.05 : 0.32)
  }, [])
  const linkWidth2d = useCallback((l: any) => {
    const s = live.current
    const w = s.linkMeta.get(`${endId(l.source)}>${endId(l.target)}`)?.weight
    const base = w ? Math.min(1 + w * 0.4, 6) : 1.1
    return s.focus && (endId(l.source) === s.focus || endId(l.target) === s.focus) ? base + 1 : base
  }, [])
  const tooltip = useCallback((n: any) => {
    const m = live.current.meta.get(n.id)
    return m ? `<div style="max-width:320px;white-space:pre-wrap;font-size:12px">${esc(cut(m.tip ?? m.label, 400))}</div>` : ''
  }, [])

  // 3D: node objects are rebuilt only when selection/highlight/data change, never on hover.
  const nodeObj3d = useCallback((node: any) => {
    const m = meta.get(node.id)
    const g = new THREE.Group()
    if (!m) return g
    const near = p.selected ? adj.get(p.selected) : undefined
    const hl = p.highlight?.size ? p.highlight : undefined
    const dim = (hl && !hl.has(node.id)) || (!!p.selected && node.id !== p.selected && !near?.has(node.id))
    const mat = new THREE.MeshLambertMaterial({ color: m.color, emissive: m.color, emissiveIntensity: 0.4, transparent: true, opacity: dim ? 0.12 : 0.95 })
    const mesh = new THREE.Mesh(SPHERE, mat)
    mesh.scale.setScalar(m.size * 0.8)
    g.add(mesh)
    if (node.id === p.selected) {
      const ring = new THREE.Mesh(RING, new THREE.MeshBasicMaterial({ color: '#ffffff' }))
      ring.scale.setScalar(m.size * 0.8)
      g.add(ring)
    }
    if (!dim && (p.dag || m.hub || node.id === p.selected || near?.has(node.id) || hl?.has(node.id))) {
      const th = p.dag ? 11 : 5
      const t = new SpriteText(cut(m.label, 36), th, '#e6e9ff')
      t.backgroundColor = 'rgba(4,5,13,0.7)'; t.padding = th * 0.3; t.borderRadius = 2
      t.position.set(0, m.size * 0.8 + th * 1.3, 0)
      ;(t.material as THREE.Material).depthWrite = false
      g.add(t)
    }
    return g
  }, [meta, adj, p.selected, p.highlight, p.dag])
  const linkColor3d = useCallback((l: any) => live.current.linkMeta.get(`${endId(l.source)}>${endId(l.target)}`)?.color ?? '#8a94c8', [])
  const linkWidth3d = useCallback((l: any) => {
    const w = live.current.linkMeta.get(`${endId(l.source)}>${endId(l.target)}`)?.weight
    return w ? Math.min(0.5 + w * 0.25, 3) : 0.6
  }, [])

  useEffect(() => {
    const f = fg.current
    if (!f?.d3Force) return
    f.d3Force('charge')?.strength(p.dag ? -160 : -230)
    f.d3Force('link')?.distance(p.dag ? 70 : 85)
  }, [mode, data, p.dag])

  const fit = useCallback(() => {
    const f = fg.current
    if (!f) return
    const ns = (data.nodes as any[]).filter((n) => n.x != null)
    if (!ns.length) return
    const ext = (k: 'x' | 'y' | 'z') => [Math.min(...ns.map((n) => n[k] ?? 0)), Math.max(...ns.map((n) => n[k] ?? 0))]
    const [x0, x1] = ext('x'), [y0, y1] = ext('y'), [z0, z1] = ext('z')
    if (mode === '2d') {
      // zoomToFit ignores label widths, so labels at the right edge would be cut: add their extent to the box
      const lx = p.dag ? 300 : 110
      const w = size.w || 800, h = size.h || 500
      const k = Math.min(w / (x1 - x0 + lx + 120), h / (y1 - y0 + 120), 2.2)
      f.zoom(k, 450)
      f.centerAt((x0 + x1 + lx) / 2, (y0 + y1) / 2, 450)
      return
    }
    const c = { x: (x0 + x1) / 2, y: (y0 + y1) / 2, z: (z0 + z1) / 2 }
    const r = Math.max(x1 - x0, y1 - y0, z1 - z0) / 2 + (p.dag ? 40 : 25)
    const fov = ((f.camera?.().fov ?? 40) * Math.PI) / 180
    f.cameraPosition({ x: c.x, y: c.y, z: c.z + (r / Math.tan(fov / 2)) * 0.9 }, c, 600)
  }, [mode, data, size.w, size.h, p.dag])
  useEffect(() => {
    const t = setTimeout(fit, 1500)  // the engine-stop fit can come late (or never) on large graphs
    return () => clearTimeout(t)
  }, [mode, p.fitKey, fit])
  const onStop = useCallback(() => {
    const key = `${p.fitKey ?? ''}:${mode}`
    if (fitted.current !== key) { fitted.current = key; fit() }
  }, [p.fitKey, mode, fit])

  const common = {
    width: size.w, height: size.h, graphData: data, backgroundColor: 'rgba(0,0,0,0)', nodeLabel: tooltip,
    linkDirectionalParticles: p.dag ? 2 : 0, linkDirectionalParticleWidth: 2, linkDirectionalParticleSpeed: 0.006,
    onNodeClick: (n: any) => p.onSelect(n.id), onBackgroundClick: () => p.onSelect(null), onEngineStop: onStop,
    cooldownTicks: 160, warmupTicks: 100,
  }

  return (
    <div className="graph-wrap" ref={wrap}>
      {size.w > 0 && p.nodes.length > 0 && (mode === '2d'
        ? (
          <ForceGraph2D
            ref={fg} {...common}
            autoPauseRedraw={false} d3VelocityDecay={0.3}
            nodeCanvasObject={paint} nodeCanvasObjectMode={() => 'replace'} nodePointerAreaPaint={pointer}
            linkColor={linkColor2d} linkWidth={linkWidth2d} linkDirectionalParticleColor={linkColor2d}
            onNodeHover={(n: any) => setHover(n?.id ?? null)}
          />
        ) : (
          <Suspense fallback={<div className="empty">Carico la vista 3D…</div>}>
            <ForceGraph3D
              ref={fg} {...common}
              showNavInfo={false} nodeThreeObject={nodeObj3d} nodeThreeObjectExtend={false}
              linkColor={linkColor3d} linkOpacity={0.4} linkWidth={linkWidth3d} linkDirectionalParticleColor={linkColor3d}
            />
          </Suspense>
        ))}
      {!p.nodes.length && p.empty}
      <div className="gmode">
        <button className={mode === '2d' ? 'on' : ''} onClick={() => setMode('2d')}>2D</button>
        <button className={mode === '3d' ? 'on' : ''} onClick={() => setMode('3d')}>3D</button>
        <button onClick={fit} title="Inquadra tutto">⤢</button>
      </div>
    </div>
  )
}
