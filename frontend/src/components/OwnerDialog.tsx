import { useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import { useBrain } from '../store'

/** Asks who Brain works for; blocking (no close) while the name is still unknown. */
export function OwnerDialog({ current, onClose }: { current: string; onClose: () => void }) {
  const [name, setName] = useState(current)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const required = !current

  const save = async () => {
    setBusy(true); setErr(null)
    try { const r = await api.setOwner(name.trim()); useBrain.setState({ owner: r.name }); onClose() } catch (e) { setErr(String(e)); setBusy(false) }
  }

  return createPortal(
    <div className="modal-back" onClick={() => !required && !busy && onClose()}>
      <div className="modal goal" onClick={(e) => e.stopPropagation()}>
        <h2>{required ? 'Who are you?' : 'Owner'}</h2>
        <p>Brain works for one person and talks to them in the chat. Tell it the name to use in its prompts, questions and wiki.</p>
        <label>Owner name
          <input autoFocus value={name} maxLength={60} disabled={busy} style={{ letterSpacing: 0, fontSize: 14 }}
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name.trim() && save()} />
        </label>
        {err && <div className="modal-err">{err}</div>}
        <div className="modal-actions">
          {!required && <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>}
          <button className="btn go" disabled={busy || !name.trim()} onClick={save}>{busy ? <><span className="spin" /> Saving…</> : 'Save'}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
