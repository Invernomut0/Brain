import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api'
import { useBrain } from '../store'
import { GraphCanvas, type GLink, type GNode } from './Graph'

interface WNode { id: string; title: string; type: string; summary: string; updated: number; managed: string; degree: number; embedded: boolean; sx: number | null; sy: number | null; sz: number | null }
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
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<{ id: string; title: string; summary: string; score: number }[]>([])
  const [busy, setBusy] = useState<'' | 'ingest' | 'embed'>('')
  const [notice, setNotice] = useState('')

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

  const R = 380
  const nodes = useMemo<GNode[]>(() => (graph?.nodes ?? []).filter((n) => !hidden.has(n.type)).map((n) => ({
    id: n.id, label: n.title, color: color(n.type), size: radius(n), hub: n.degree >= 4 || n.type === 'meta',
    tip: `${n.title}\n${n.summary}${n.embedded ? '\n(vettore)' : ''}`,
    fixed: effectiveMode === 'semantic' && n.sx != null && n.sy != null ? ([n.sx * R, n.sy * R, (n.sz ?? 0) * R] as [number, number, number]) : null,
  })), [graph, hidden, effectiveMode])
  const links = useMemo<GLink[]>(() => {
    const visible = new Set(nodes.map((n) => n.id))
    return (graph?.edges ?? []).filter((e) => visible.has(e.source) && visible.has(e.target))
  }, [graph, nodes])

  const resolve = useMemo(() => {
    const m = new Map((page?.links ?? []).map((l) => [l.target.toLowerCase(), l.id]))
    return (t: string) => m.get(t.trim().toLowerCase()) ?? null
  }, [page])

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

  return (
    <div className="wiki">
      <GraphCanvas nodes={nodes} links={links} selected={selected} highlight={hitIds} onSelect={setSelected} fitKey={effectiveMode}
        empty={<div className="empty wiki-empty">La wiki si riempie man mano che Brain lavora: stato, episodi, tool, lezioni e conoscenza compilata dai ricordi.</div>} />

      <div className="wiki-bar">
        <input value={q} placeholder="Cerca nella wiki…" onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          <button className={effectiveMode === 'links' ? 'on' : ''} onClick={() => setMode('links')} title="Layout a forze: i link avvicinano le pagine">Collegamenti</button>
          <button className={effectiveMode === 'semantic' ? 'on' : ''} disabled={!semanticReady} onClick={() => setMode('semantic')}
            title={semanticReady ? 'Mappa semantica: pagine vicine = significato simile (PCA dei vettori)' : 'Servono almeno 3 pagine con vettore: premi "Vettori"'}>Mappa semantica</button>
        </div>
        {SPECIALS.map(([id, label]) => <button key={id} className={`btn mini ${selected === id ? 'on' : ''}`} onClick={() => setSelected(id)}>{label}</button>)}
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
