import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type ProjectInfo, type ProjectMeta } from '../api'

const mb = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1e3)) + ' KB')
const when = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })

/** Save the current state, load a saved project, or start a brand-new one. */
export function ProjectsDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState<ProjectInfo | null>(null)
  const [items, setItems] = useState<ProjectMeta[]>([])
  const [saveName, setSaveName] = useState('')
  const [newName, setNewName] = useState('')
  const [newGoal, setNewGoal] = useState('')
  const [saveFirst, setSaveFirst] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const r = await api.projects()
    setCurrent(r.current); setItems(r.items)
    setSaveName((v) => v || r.current.name)
  }, [])
  useEffect(() => { refresh().catch((e) => setErr(String(e))) }, [refresh])

  const run = async (label: string, fn: () => Promise<unknown>, done?: string, close = false) => {
    setBusy(label); setErr(null); setNote(null)
    try {
      await fn()
      if (close) { onClose(); return }
      await refresh()
      if (done) setNote(done)
    } catch (e) { setErr(String(e)) } finally { setBusy(null) }
  }

  const load = (p: ProjectMeta) => {
    if (!confirm(`Load "${p.name}"? Running agents are interrupted${saveFirst ? ' and the current project is saved first' : ' and the current state is discarded'}.`)) return
    run('load:' + p.id, () => api.loadProject(p.id, saveFirst), undefined, true)
  }
  const remove = (p: ProjectMeta) => {
    if (!confirm(`Delete the saved project "${p.name}"? This cannot be undone.`)) return
    run('del:' + p.id, () => api.deleteProject(p.id), `Deleted "${p.name}".`)
  }
  const create = () => run('new', () => api.newProject(newName.trim(), newGoal, saveFirst), undefined, true)

  const locked = busy !== null
  return createPortal(
    <div className="modal-back" onClick={() => !locked && onClose()}>
      <div className="modal projects" onClick={(e) => e.stopPropagation()}>
        <h2>Projects</h2>
        <p>A project is a full snapshot of Brain: goals, memory, journal, lessons, wiki, workspace files and evolved prompts/hooks.</p>

        <h3>Current: {current?.name ?? '…'}{current && !current.saved ? ' (never saved)' : ''}</h3>
        <div className="proj-save">
          <label>Save as
            <input value={saveName} disabled={locked} onChange={(e) => setSaveName(e.target.value)} placeholder="Project name" maxLength={60} />
          </label>
          <button className="btn go" disabled={locked || !saveName.trim()} onClick={() => run('save', () => api.saveProject(saveName.trim()), `Saved "${saveName.trim()}".`)}>
            {busy === 'save' ? <><span className="spin" /> Saving…</> : 'Save'}
          </button>
        </div>

        <h3>Saved projects ({items.length})</h3>
        {items.length === 0 && <p style={{ color: 'var(--dim)' }}>No saved project yet.</p>}
        {items.map((p) => (
          <div key={p.id} className={`proj-row ${p.current ? 'cur' : ''}`}>
            <div className="info">
              <div className="nm">{p.name}{p.current ? ' · active' : ''}</div>
              <div className="st">
                {when(p.saved_at)} · cycle {p.cycle} · {p.goals.done}/{p.goals.total} goals · {p.memories} memories · {p.tools} tools · {p.wiki_pages} wiki pages · {mb(p.size_bytes)}
              </div>
              {p.main_goal && <div className="mg" title={p.main_goal}>{p.main_goal}</div>}
            </div>
            <button className="btn" disabled={locked} onClick={() => load(p)}>{busy === 'load:' + p.id ? <><span className="spin" /> Loading…</> : p.current ? 'Reload' : 'Load'}</button>
            <button className="btn danger" disabled={locked} onClick={() => remove(p)} title="Delete this saved project">✕</button>
          </div>
        ))}

        <h3>New project</h3>
        <label>Name
          <input value={newName} disabled={locked} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Risotto research" maxLength={60} />
        </label>
        <label>Main goal (optional, 10+ characters; the factory goal is used if empty)
          <textarea rows={3} value={newGoal} disabled={locked} onChange={(e) => setNewGoal(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={saveFirst} disabled={locked} onChange={(e) => setSaveFirst(e.target.checked)} />
          Save the current project first (applies to Load and New)
        </label>
        <div className="modal-actions">
          <button className="btn" disabled={locked} onClick={onClose}>Close</button>
          <button className="btn go" disabled={locked || !newName.trim()} onClick={create}>
            {busy === 'new' ? <><span className="spin" /> Creating…</> : 'Create new project'}
          </button>
        </div>
        {note && <div className="modal-err" style={{ color: 'var(--green)' }}>{note}</div>}
        {err && <div className="modal-err">{err}</div>}
      </div>
    </div>,
    document.body,
  )
}
