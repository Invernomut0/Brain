import { useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'

/** Confirmation dialog for the factory reset; the user must type RESET. */
export function ResetDialog({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setBusy(true); setErr(null)
    try { await api.reset(); onClose() } catch (e) { setErr(String(e)); setBusy(false) }
  }

  return createPortal(
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Reset everything</h2>
        <p>Return Brain to a brand-new, empty installation. The following will be permanently deleted:</p>
        <ul>
          <li>database: goals, memory, journal, lessons, self-model, metrics, events</li>
          <li>created tools and all the files of the sandbox workspace (the container is recreated)</li>
          <li>evolved prompts and hooks: they return to the factory values (the git history stays)</li>
        </ul>
        <p>Running agents are interrupted. After the reset Brain stays stopped: press ▶ Start to run it again. The main goal returns to the factory one.</p>
        <label>Type <b>RESET</b> to confirm
          <input autoFocus value={text} disabled={busy} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && text === 'RESET' && run()} />
        </label>
        {err && <div className="modal-err">{err}</div>}
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="btn danger" disabled={busy || text !== 'RESET'} onClick={run}>{busy ? <><span className="spin" /> Resetting…</> : 'Reset everything'}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
