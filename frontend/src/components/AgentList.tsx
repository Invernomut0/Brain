import { useState } from 'react'
import { api } from '../api'
import { roleColor, useBrain } from '../store'
import { useNow } from '../hooks/useNow'
import { ContextDialog } from './ContextDialog'

const STYLE_LABEL: Record<string, string> = {
  off: 'Off (plain ids)', all: 'All styles (by role)', classic: 'Classic', royal: 'Royal', corporate: 'Corporate', cyber: 'Cyber',
  fantasy: 'Fantasy', action: 'Action', nonsense: 'Nonsense', memes: 'Memes', heroic: 'Heroic', pirate: 'Pirate', scifi: 'Sci-fi',
  cozy: 'Cozy', office: 'Office',
}

export function AgentList() {
  const agents = useBrain((s) => s.agents)
  const names = useBrain((s) => s.names)
  const naming = useBrain((s) => s.naming)
  const now = useNow(1000)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [ctxFor, setCtxFor] = useState<string | null>(null)
  const working = (s: string) => s === 'thinking' || s === 'acting'
  const toggle = (id: string) => setExpanded((p) => { const n = new Set(p); if (!n.delete(id)) n.add(id); return n })
  const list = Object.values(agents).filter((a) => !a.endedAt || now - a.endedAt < 30000)
    .sort((a, b) => Number(working(b.state)) - Number(working(a.state)) || b.bornAt - a.bornAt)

  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); setEditing(null) } catch (e) { setErr(String(e).replace(/^Error: \d+ /, '')) } }
  const rename = (id: string) => draft.trim() && run(() => api.renameAgent(id, draft.trim()))

  return (
    <section className="panel" style={{ flex: 1 }}>
      <h3>Agents <span>{list.filter((a) => !a.endedAt).length} active</span></h3>
      <div className="naming-bar" title="Applies to agents created from now on">
        <label>Names
          <select value={naming.style} onChange={(e) => run(() => api.setNaming(e.target.value))}>
            {naming.styles.map((s) => <option key={s} value={s}>{STYLE_LABEL[s] ?? s}</option>)}
          </select>
        </label>
      </div>
      {err && <div className="modal-err" style={{ margin: '0 14px 6px' }} onClick={() => setErr(null)}>{err}</div>}
      <div className="scroll">
        {!list.length && <div className="empty">No active agents.<br />Press ▶ Start at the top to start the system.</div>}
        {list.map((a) => {
          const name = names[a.id]
          const collapsed = !working(a.state) && !expanded.has(a.id)
          return (
            <div className={`agent ${collapsed ? 'collapsed' : ''}`} key={a.id} onClick={() => toggle(a.id)} title={collapsed ? 'Click to expand' : undefined}
              style={{ borderLeft: `3px solid ${roleColor(a.role)}`, opacity: a.endedAt ? 0.6 : 1 }}>
              <div className="top">
                {editing === a.id ? (
                  <input className="name-input" autoFocus value={draft} maxLength={32} onClick={(e) => e.stopPropagation()} onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') rename(a.id); if (e.key === 'Escape') setEditing(null) }} onBlur={() => setEditing(null)} />
                ) : (
                  <span className="role" style={{ color: roleColor(a.role) }}>{name ?? a.role}</span>
                )}
                <small style={{ color: '#7f89b8' }}>{name ? `${a.role} · ${a.id}` : a.id}</small>
                <span className={`badge ${a.state}`}>{a.state === 'acting' ? a.detail : a.state === 'thinking' ? 'working' : a.state}</span>
                {editing !== a.id && (
                  <span className="name-tools" onClick={(e) => e.stopPropagation()}>
                    <button title="Choose a name" onClick={() => { setDraft(name ?? ''); setEditing(a.id) }}>✎</button>
                    <button title="Roll a new random name" onClick={() => run(() => api.renameAgent(a.id, null))}>⟳</button>
                    <button title="Show what this agent sees (context inspector)" onClick={() => setCtxFor(a.id)}>◧</button>
                  </span>
                )}
              </div>
              {!collapsed && <div className="sub">{a.summary || a.thought || a.task}</div>}
            </div>
          )
        })}
      </div>
      {ctxFor && <ContextDialog id={ctxFor} name={names[ctxFor] ?? ctxFor} onClose={() => setCtxFor(null)} />}
    </section>
  )
}
