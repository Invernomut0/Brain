import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import * as d3 from 'd3'
import { api } from '../api'
import { useBrain } from '../store'

interface WNode { id: string; title: string; type: string; summary: string; updated: number; managed: string; degree: number; embedded: boolean; sx: number | null; sy: number | null }
interface WEdge { source: string; target: string }
interface WGraph { nodes: WNode[]; edges: WEdge[] }
interface WStats {
  pages: number; links: number; embedded: number; pending_items: number; last_ingest: number | null; dir: string
  memories: { total: number; embedded: number }
  embeddings: { mode: string; model: string; available: boolean; error: string }
  lint: { total: number } | null
}
interface WLink { target: string; id: string | null; title: string | null }
interface WPage {
  id: string; title: string; type: string; summary: string; body: string; updated: number; managed: string
  sources: string[]; tags: string[]; links: WLink[]; backlinks: { id: string; title: string }[]
}
interface Placed extends WNode { x: number; y: number }

const TYPE_COLOR: Record<string, string> = {
  meta: '#a5b4fc', phase: '#fde047', decision: '#ff9f6b', concept: '#22d3ee', entity: '#34f5a0',
  insight: '#f472d0', episode: '#7f89b8', tool: '#ffb020', note: '#60a5fa',
}
const TYPE_LABEL: Record<string, string> = {
  meta: 'stato', phase: 'fasi', decision: 'decisioni', concept: 'concetti', entity: 'entità', insight: 'intuizioni', episode: 'episodi', tool: 'tool', note: 'note',
}
const SPECIALS = [['index', 'Indice'], ['log', 'Log'], ['lint', 'Salute'], ['SCHEMA', 'Schema']] as const
const color = (t: string) => TYPE_COLOR[t] ?? '#94a3b8'
const radius = (n: WNode) => 4 + Math.min(n.degree, 9) * 1.1
const when = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-')

type Mode = 'links' | 'semantic'

/** Positions: force-directed by links, or anchored to the PCA of the page vectors (semantic map). */
function computeLayout(graph: WGraph, mode: Mode, previous: Map<string, { x: number; y: number }>): Placed[] {
  const R = 320
  const nodes: (Placed & d3.SimulationNodeDatum)[] = graph.nodes.map((n, i) => {
    const prev = previous.get(n.id)
    const angle = (i / Math.max(graph.nodes.length, 1)) * 2 * Math.PI
    const anchored = mode === 'semantic' && n.sx != null && n.sy != null
    return {
      ...n, x: anchored ? n.sx! * R : prev?.x ?? Math.cos(angle) * 120, y: anchored ? n.sy! * R : prev?.y ?? Math.sin(angle) * 120,
      ...(anchored ? { fx: n.sx! * R, fy: n.sy! * R } : {}),
    }
  })
  const links = graph.edges.map((e) => ({ source: e.source, target: e.target }))
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink<any, any>(links).id((d: any) => d.id).distance(mode === 'semantic' ? 40 : 70).strength(mode === 'semantic' ? 0.15 : 0.55))
    .force('charge', d3.forceManyBody().strength(mode === 'semantic' ? -25 : -140))
    .force('collide', d3.forceCollide<any>().radius((d: any) => radius(d) + 3))
    .stop()
  if (mode === 'links') sim.force('center', d3.forceCenter(0, 0)).force('x', d3.forceX(0).strength(0.03)).force('y', d3.forceY(0).strength(0.03))
  for (let i = 0; i < 320; i++) sim.tick()
  return nodes.map((n) => ({ ...n, x: n.x ?? 0, y: n.y ?? 0 }))
}

// ---------------------------------------------------------------- markdown
const INLINE = /\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]|\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(?<![\w])_([^_\n]+)_(?![\w])/g

function inline(text: string, resolve: (t: string) => string | null, open: (id: string) => void): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const k = m.index
    if (m[1] !== undefined) {
      const id = resolve(m[1])
      out.push(id
        ? <a key={k} className="wl" onClick={() => open(id)}>{m[2] || m[1]}</a>
        : <span key={k} className="wl broken" title="pagina mancante">{m[2] || m[1]}</span>)
    } else if (m[3] !== undefined) out.push(<b key={k}>{m[3]}</b>)
    else if (m[4] !== undefined) out.push(<code key={k}>{m[4]}</code>)
    else if (m[5] !== undefined) out.push(<a key={k} href={m[6]} target="_blank" rel="noopener noreferrer">{m[5]}</a>)
    else out.push(<i key={k}>{m[7]}</i>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function Markdown({ text, resolve, open }: { text: string; resolve: (t: string) => string | null; open: (id: string) => void }) {
  const inl = (s: string) => inline(s, resolve, open)
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
  while (i < lines.length) {
    const l = lines[i]
    const key = i
    if (!l.trim()) { i++; continue }
    if (l.startsWith('```')) {
      const code: string[] = []
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) code.push(lines[i])
      i++
      blocks.push(<pre key={key}>{code.join('\n')}</pre>)
    } else if (/^#{1,4}\s/.test(l)) {
      const level = l.match(/^#+/)![0].length
      const Tag = `h${Math.min(level + 1, 5)}` as 'h2'
      blocks.push(<Tag key={key}>{inl(l.replace(/^#+\s+/, ''))}</Tag>)
      i++
    } else if (/^\|.*\|\s*$/.test(l) && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1] ?? '')) {
      const head = cells(l)
      const rows: string[][] = []
      for (i += 2; i < lines.length && /^\|.*\|\s*$/.test(lines[i]); i++) rows.push(cells(lines[i]))
      blocks.push(
        <table key={key}><thead><tr>{head.map((c, j) => <th key={j}>{inl(c)}</th>)}</tr></thead>
          <tbody>{rows.map((r, a) => <tr key={a}>{r.map((c, j) => <td key={j}>{inl(c)}</td>)}</tr>)}</tbody></table>,
      )
    } else if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const ordered = /^\s*\d+\./.test(l)
      const items: string[] = []
      for (; i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''))
      const Tag = ordered ? 'ol' : 'ul'
      blocks.push(<Tag key={key}>{items.map((t, j) => <li key={j}>{inl(t)}</li>)}</Tag>)
    } else if (l.startsWith('>')) {
      const q: string[] = []
      for (; i < lines.length && lines[i].startsWith('>'); i++) q.push(lines[i].replace(/^>\s?/, ''))
      blocks.push(<blockquote key={key}>{inl(q.join(' '))}</blockquote>)
    } else {
      const p: string[] = []
      for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|\s*([-*]|\d+\.)\s)/.test(lines[i]); i++) p.push(lines[i])
      blocks.push(<p key={key}>{inl(p.join(' '))}</p>)
    }
  }
  return <div className="md">{blocks}</div>
}

// ------------------------------------------------------------------- view
export function WikiView() {
  const rev = useBrain((s) => s.wikiRev)
  const [graph, setGraph] = useState<WGraph | null>(null)
  const [stats, setStats] = useState<WStats | null>(null)
  const [mode, setMode] = useState<Mode>('links')
  const [selected, setSelected] = useState<string | null>(null)
  const [page, setPage] = useState<WPage | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<{ id: string; title: string; summary: string; score: number }[]>([])
  const [busy, setBusy] = useState<'' | 'ingest' | 'embed'>('')
  const [notice, setNotice] = useState('')
  const [zoomedIn, setZoomedIn] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)
  const gRef = useRef<SVGGElement>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const positions = useRef(new Map<string, { x: number; y: number }>())
  const fitted = useRef('')

  const refresh = useCallback(() => {
    api.get<WGraph>('/wiki/graph').then(setGraph).catch((e) => setNotice(String(e.message ?? e)))
    api.get<WStats>('/wiki').then(setStats).catch(() => undefined)
  }, [])
  useEffect(() => { const t = setTimeout(refresh, 300); return () => clearTimeout(t) }, [rev, refresh])

  useEffect(() => {
    if (!selected) { setPage(null); return }
    let live = true
    api.get<WPage>(`/wiki/page?id=${encodeURIComponent(selected)}`).then((p) => live && setPage(p)).catch(() => live && setPage(null))
    return () => { live = false }
  }, [selected, rev])

  useEffect(() => {
    const t = setTimeout(() => {
      if (!q.trim()) { setHits([]); return }
      api.get<typeof hits>(`/wiki/search?q=${encodeURIComponent(q.trim())}&k=8`).then(setHits).catch(() => setHits([]))
    }, 300)
    return () => clearTimeout(t)
  }, [q, rev])

  const semanticReady = (graph?.nodes.filter((n) => n.sx != null).length ?? 0) >= 3
  const effectiveMode: Mode = mode === 'semantic' && semanticReady ? 'semantic' : 'links'

  const placed = useMemo(() => {
    if (!graph?.nodes.length) return []
    const out = computeLayout(graph, effectiveMode, positions.current)
    positions.current = new Map(out.map((n) => [n.id, { x: n.x, y: n.y }]))
    return out
  }, [graph, effectiveMode])
  const byId = useMemo(() => new Map(placed.map((n) => [n.id, n])), [placed])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg || zoomRef.current) return
    const zoom = d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.15, 4]).on('zoom', (e) => {
      d3.select(gRef.current).attr('transform', e.transform.toString())
      setZoomedIn((z) => (z === e.transform.k > 1.4 ? z : e.transform.k > 1.4))
    })
    zoomRef.current = zoom
    d3.select(svg).call(zoom)
  }, [])

  const fit = useCallback((nodes: Placed[]) => {
    const svg = svgRef.current
    if (!svg || !zoomRef.current || !nodes.length) return
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y)
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    const w = svg.clientWidth, h = svg.clientHeight
    const k = Math.min(w / (x1 - x0 + 160), h / (y1 - y0 + 160), 1.6)
    const t = d3.zoomIdentity.translate(w / 2 - ((x0 + x1) / 2) * k, h / 2 - ((y0 + y1) / 2) * k).scale(k)
    d3.select(svg).transition().duration(400).call(zoomRef.current.transform, t)
  }, [])
  useEffect(() => {
    const sig = `${effectiveMode}:${placed.length > 0}`
    if (placed.length && fitted.current !== sig) { fitted.current = sig; fit(placed) }
  }, [placed, effectiveMode, fit])

  const resolve = useMemo(() => {
    const m = new Map((page?.links ?? []).map((l) => [l.target.toLowerCase(), l.id]))
    return (t: string) => m.get(t.trim().toLowerCase()) ?? null
  }, [page])

  const neighbours = useMemo(() => {
    const focus = hover ?? selected
    const s = new Set<string>()
    if (focus) for (const e of graph?.edges ?? []) { if (e.source === focus) s.add(e.target); if (e.target === focus) s.add(e.source) }
    return s
  }, [graph, hover, selected])
  const hitIds = useMemo(() => new Set(hits.map((h) => h.id)), [hits])

  const run = async (what: 'ingest' | 'embed') => {
    setBusy(what)
    setNotice('')
    try {
      const r = await api.post<any>(what === 'ingest' ? '/wiki/ingest' : '/wiki/embed')
      setNotice(what === 'ingest'
        ? (r.error ? `Ingest: ${r.error}` : r.busy ? 'Un ingest è già in corso.' : `Wiki aggiornata: ${r.pages} pagine da ${r.items} nuove informazioni.`)
        : (r.available ? `Vettori calcolati: ${r.embedded} pagine, ${r.memories ?? 0} ricordi.` : `Modello di embedding non disponibile: ${r.error ?? 'caricalo in LM Studio'}`))
      refresh()
    } catch (e: any) { setNotice(String(e.message ?? e)) } finally { setBusy('') }
  }

  const types = useMemo(() => {
    const c = new Map<string, number>()
    graph?.nodes.forEach((n) => c.set(n.type, (c.get(n.type) ?? 0) + 1))
    return [...c.entries()]
  }, [graph])
  const emb = stats?.embeddings
  const focus = hover ?? selected

  return (
    <div className="wiki">
      <svg ref={svgRef} width="100%" height="100%" style={{ cursor: 'grab' }} onClick={(e) => { if (e.target === svgRef.current) setSelected(null) }}>
        <g ref={gRef}>
          {graph?.edges.map((e) => {
            const a = byId.get(e.source), b = byId.get(e.target)
            if (!a || !b || hidden.has(a.type) || hidden.has(b.type)) return null
            const on = focus && (e.source === focus || e.target === focus)
            return <line key={`${e.source}>${e.target}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={on ? '#e6e9ff' : '#7f89b8'} strokeOpacity={on ? 0.8 : focus ? 0.07 : 0.22} strokeWidth={on ? 1.6 : 1} />
          })}
          {placed.map((n) => {
            if (hidden.has(n.type)) return null
            const dim = (focus && n.id !== focus && !neighbours.has(n.id)) || (hits.length > 0 && !hitIds.has(n.id))
            const r = radius(n)
            const showLabel = n.id === focus || n.id === selected || hitIds.has(n.id) || neighbours.has(n.id) || zoomedIn || n.degree >= 4
            return (
              <g key={n.id} transform={`translate(${n.x},${n.y})`} style={{ cursor: 'pointer', opacity: dim ? 0.18 : 1 }}
                onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onClick={(e) => { e.stopPropagation(); setSelected(n.id) }}>
                <title>{`${n.title}\n${n.summary}${n.embedded ? '\n(vettore)' : ''}`}</title>
                {n.id === selected && <circle r={r + 6} fill="none" stroke="#fff" strokeOpacity={0.8} strokeDasharray="3 3" />}
                {hitIds.has(n.id) && <circle r={r + 5} fill="none" stroke="#34f5a0" strokeWidth={2} />}
                <circle r={r} fill={color(n.type)} fillOpacity={n.managed === 'auto' ? 0.55 : 0.92} stroke={color(n.type)} strokeWidth={n.embedded ? 2 : 0} strokeOpacity={0.35} />
                {showLabel && <text x={r + 5} y={3.5} className="node-label" fill={color(n.type)}>{n.title.length > 34 ? `${n.title.slice(0, 33)}…` : n.title}</text>}
              </g>
            )
          })}
        </g>
      </svg>

      {!placed.length && <div className="empty wiki-empty">La wiki si riempie man mano che Brain lavora: stato, episodi, tool, lezioni e conoscenza compilata dai ricordi.</div>}

      <div className="wiki-bar">
        <input value={q} placeholder="Cerca nella wiki…" onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          <button className={effectiveMode === 'links' ? 'on' : ''} onClick={() => setMode('links')} title="Layout a forze: i link avvicinano le pagine">Collegamenti</button>
          <button className={effectiveMode === 'semantic' ? 'on' : ''} disabled={!semanticReady} onClick={() => setMode('semantic')}
            title={semanticReady ? 'Mappa semantica: pagine vicine = significato simile (PCA dei vettori)' : 'Servono almeno 3 pagine con vettore: premi "Vettori"'}>Mappa semantica</button>
        </div>
        {SPECIALS.map(([id, label]) => <button key={id} className={`btn mini ${selected === id ? 'on' : ''}`} onClick={() => setSelected(id)}>{label}</button>)}
        <button className="btn mini" onClick={() => fit(placed)} title="Inquadra tutto">⤢</button>
        <button className="btn mini go" disabled={!!busy} onClick={() => run('ingest')} title="Rigenera le pagine dal database e integra i nuovi ricordi con il modello">
          {busy === 'ingest' ? <><span className="spin" /> Integro…</> : '↻ Aggiorna wiki'}
        </button>
        <button className="btn mini" disabled={!!busy} onClick={() => run('embed')} title="Calcola i vettori con il modello di embedding (LM Studio può doverlo caricare)">
          {busy === 'embed' ? <><span className="spin" /> Vettori…</> : '◈ Vettori'}
        </button>
      </div>
      {hits.length > 0 && (
        <div className="wiki-hits">
          {hits.map((h) => <div key={h.id} onClick={() => setSelected(h.id)}><b>{h.title}</b><small>{h.summary}</small></div>)}
        </div>
      )}
      <div className="wiki-foot">
        <div className="wiki-legend">
          {types.map(([t, n]) => (
            <button key={t} className={hidden.has(t) ? 'off' : ''} onClick={() => setHidden((h) => { const x = new Set(h); x.has(t) ? x.delete(t) : x.add(t); return x })}>
              <i style={{ background: color(t) }} />{TYPE_LABEL[t] ?? t} {n}
            </button>
          ))}
        </div>
        <div className="wiki-stats">
          {stats ? <>
            {stats.pages} pagine · {stats.links} link · vettori {stats.embedded}/{stats.pages} · ricordi vettorizzati {stats.memories.embedded}/{stats.memories.total}
            {stats.pending_items > 0 && <> · {stats.pending_items} da integrare</>}{stats.lint && stats.lint.total > 0 && <> · {stats.lint.total} segnalazioni</>}
            {emb && !emb.available && <span className="warn"> · modello embedding ({emb.model}) non caricato in LM Studio: ricerca per parole chiave</span>}
          </> : '…'}
          {notice && <span className="note"> — {notice}</span>}
        </div>
      </div>

      {selected && (
        <aside className="wiki-reader">
          <button className="x" onClick={() => setSelected(null)} aria-label="Chiudi">×</button>
          {!page ? <div className="empty">Caricamento…</div> : (
            <>
              <div className="wr-head">
                <span className="kind" style={{ ['--c' as string]: color(page.type) }}>{page.type}</span>
                {page.managed !== 'auto' || page.type === 'special' ? null : <span className="tag">generata dal database</span>}
                <small> {when(page.updated)}</small>
              </div>
              {page.type !== 'special' && <h3>{page.title}</h3>}
              <Markdown text={page.body} resolve={resolve} open={setSelected} />
              {page.sources.length > 0 && <div className="wr-meta">Fonti: {page.sources.join(', ')}</div>}
              {page.tags.length > 0 && <div className="wr-meta">Tag: {page.tags.join(', ')}</div>}
              {page.backlinks.length > 0 && (
                <div className="wr-meta">Citata da: {page.backlinks.map((b, i) => <Fragment key={b.id}>{i > 0 && ', '}<a className="wl" onClick={() => setSelected(b.id)}>{b.title}</a></Fragment>)}</div>
              )}
            </>
          )}
        </aside>
      )}
    </div>
  )
}
