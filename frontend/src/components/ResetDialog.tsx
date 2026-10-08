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
        <h2>Azzera tutto</h2>
        <p>Riporta Brain a un'installazione nuova e vuota. Verranno eliminati in modo definitivo:</p>
        <ul>
          <li>database: obiettivi, memoria, giornale, lezioni, self-model, metriche, eventi</li>
          <li>tool creati e tutti i file del workspace della sandbox (il container viene ricreato)</li>
          <li>prompt e hook evoluti: tornano ai valori di fabbrica (la cronologia git resta)</li>
        </ul>
        <p>Gli agenti in corso vengono interrotti. Il sistema riparte da solo se l'avvio automatico è attivo.</p>
        <label>Scrivi <b>RESET</b> per confermare
          <input autoFocus value={text} disabled={busy} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && text === 'RESET' && run()} />
        </label>
        {err && <div className="modal-err">{err}</div>}
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onClose}>Annulla</button>
          <button className="btn danger" disabled={busy || text !== 'RESET'} onClick={run}>{busy ? <><span className="spin" /> Azzeramento…</> : 'Azzera tutto'}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
