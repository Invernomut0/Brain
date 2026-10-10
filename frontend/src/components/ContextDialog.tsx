import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'

interface Section { name: string; chars: number; tokens: number }
interface Ctx {
  agent: string; role: string; step: number; max_steps: number; ts: number; chars: number; tokens: number; budget_chars: number
  sections: Section[]; messages: { section: string; chars: number; text: string }[]
}

const COLORS = ['#8b7bff', '#22d3ee', '#34f5a0', '#ffb020', '#f472d0']

/** What the agent sees at its latest step: size of every section and the text itself (to understand why it drifts). */
export function ContextDialog({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const [ctx, setCtx] = useState<Ctx | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    const load = () => api.get<Ctx>(`/agents/${encodeURIComponent(id)}/context`).then((c) => { if (live) { setCtx(c); setErr('') } }).catch((e) => live && setErr(String(e.message ?? e).replace(/^\d+ /, '')))
    load()
    const t = setInterval(load, 2500)
    return () => { live = false; clearInterval(t) }
  }, [id])

  return createPortal(
    <div className="modal-back" onClick={onClose}>
      <div className="modal projects ctx" onClick={(e) => e.stopPropagation()}>
        <h2>Context · {name}</h2>
        {err && <div className="modal-err">{err}</div>}
        {ctx && (
          <>
            <p>
              Step {ctx.step}/{ctx.max_steps} · about <b>{ctx.tokens.toLocaleString()} tokens</b> ({ctx.chars.toLocaleString()} characters) sent with the last request.
              Raw recent steps are capped at {ctx.budget_chars.toLocaleString()} characters; everything older lives in the working memory.
            </p>
            <div className="ctx-bar">
              {ctx.sections.map((s, i) => s.chars > 0 && <i key={s.name} style={{ flex: s.chars, background: COLORS[i % COLORS.length] }} title={`${s.name}: ${s.tokens} tokens`} />)}
            </div>
            <table className="ctx-table">
              <tbody>
                {ctx.sections.map((s, i) => (
                  <tr key={s.name}>
                    <td><i className="res-dot" style={{ background: COLORS[i % COLORS.length] }} /> {s.name}</td>
                    <td>{s.tokens.toLocaleString()} tok</td>
                    <td>{ctx.chars ? Math.round((100 * s.chars) / ctx.chars) : 0}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ctx.messages.map((m, i) => (
              <details key={i}>
                <summary>{m.section} <small>{m.chars.toLocaleString()} chars</small></summary>
                <pre className="res-pre ctx-pre">{m.text}{m.chars > m.text.length ? '\n… [cut for display]' : ''}</pre>
              </details>
            ))}
          </>
        )}
        <div className="modal-actions"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>,
    document.body,
  )
}
