import { useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import { useBrain } from '../store'

export function useMainGoal(): string {
  return useBrain((s) => Object.values(s.goals).find((g) => g.parent_id === null)?.description ?? '')
}

/** Editor for the main goal; optionally cancels the queue that was planned for the old one. */
function MainGoalDialog({ initial, onClose }: { initial: string; onClose: () => void }) {
  const [text, setText] = useState(initial)
  const [archive, setArchive] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const valid = text.trim().length >= 10 && text.length <= 2000

  const save = async () => {
    setBusy(true); setErr(null)
    try { await api.setMainGoal(text.trim(), archive); onClose() } catch (e) { setErr(String(e)); setBusy(false) }
  }

  return createPortal(
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal goal" onClick={(e) => e.stopPropagation()}>
        <h2>Main goal</h2>
        <p>What Brain is trying to achieve. The planner follows it from the next planning cycle.</p>
        <textarea autoFocus rows={8} value={text} disabled={busy} onChange={(e) => setText(e.target.value)} />
        <div className="goal-count">{text.trim().length}/2000</div>
        <label className="check">
          <input type="checkbox" checked={archive} disabled={busy} onChange={(e) => setArchive(e.target.checked)} />
          Cancel the queued goals planned for the old goal (recommended)
        </label>
        {err && <div className="modal-err">{err}</div>}
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="btn go" disabled={busy || !valid} onClick={save}>{busy ? <><span className="spin" /> Saving…</> : 'Save goal'}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

export function MainGoalCard() {
  const goal = useMainGoal()
  const [open, setOpen] = useState(false)
  return (
    <section className="panel goalcard">
      <h3>Main goal <button className="btn mini" onClick={() => setOpen(true)} disabled={!goal} title="Edit the main goal">✎ Edit</button></h3>
      <div className="goalcard-text" title={goal}>{goal || '—'}</div>
      {open && <MainGoalDialog initial={goal} onClose={() => setOpen(false)} />}
    </section>
  )
}

/** Shown over the 3D stage while Brain is not running: nothing starts until the user says so. */
export function StartOverlay() {
  const state = useBrain((s) => s.control.state)
  const connected = useBrain((s) => s.connected)
  const goal = useMainGoal()
  const [busy, setBusy] = useState(false)
  if (!connected || !['idle', 'stopped', 'killed'].includes(state)) return null
  const start = async () => { setBusy(true); try { await api.control('start') } finally { setBusy(false) } }
  return (
    <div className="start-overlay">
      <div className="start-card">
        <div className="start-title">{state === 'idle' ? 'Brain is ready' : 'Brain is stopped'}</div>
        <div className="start-goal">{goal}</div>
        <button className="btn go big" disabled={busy} onClick={start}>{busy ? <span className="spin" /> : '▶ Start'}</button>
      </div>
    </div>
  )
}
