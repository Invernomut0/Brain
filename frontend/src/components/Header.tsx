import { useState } from 'react'
import { api } from '../api'
import { useBrain } from '../store'

const fmt = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n))

export function Header() {
  const { control, connected, health, sys } = useBrain()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const st = control.state

  const act = async (a: 'start' | 'pause' | 'resume' | 'stop' | 'kill') => {
    setBusy(true); setErr(null)
    try { await api.control(a) } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const budget = async (k: 'max_cycles' | 'max_tokens', v: string) => {
    try { await api.budget({ [k]: Number(v) || 0 }) } catch (e) { setErr(String(e)) }
  }
  const active = st === 'running' || st === 'paused'

  return (
    <header className="panel header">
      <span className="logo">BRAIN</span>
      <span className="chip"><i className={`dot ${st === 'running' ? 'run' : st === 'killed' ? 'bad' : st === 'paused' ? '' : 'ok'}`} />{st.toUpperCase()} · ciclo {control.cycle}</span>
      <span className="chip"><i className={`dot ${health.llm?.ok ? 'ok' : 'bad'}`} />LM Studio{health.llm?.model ? ` · ${health.llm.model}` : ''}</span>
      <span className="chip"><i className={`dot ${health.sandbox?.ok ? 'ok' : 'bad'}`} />Podman{health.sandbox?.ok && !health.sandbox.image ? ' (immagine da costruire)' : ''}</span>
      <span className="chip"><i className={`dot ${connected ? 'ok' : 'bad'}`} />{connected ? 'live' : 'offline'}</span>
      <span className="chip">{sys.tps.toFixed(1)} tok/s · {fmt(sys.tokens)} tok</span>
      <span className="spacer" />
      <label className="budget">cicli max <input defaultValue={control.max_cycles} key={'c' + control.max_cycles} onBlur={(e) => budget('max_cycles', e.target.value)} /></label>
      <label className="budget">token max <input defaultValue={control.max_tokens} key={'t' + control.max_tokens} onBlur={(e) => budget('max_tokens', e.target.value)} /></label>
      {!active && <button className="btn go" disabled={busy || !connected} onClick={() => act('start')}>▶ Avvia</button>}
      {st === 'running' && <button className="btn" disabled={busy} onClick={() => act('pause')}>⏸ Pausa</button>}
      {st === 'paused' && <button className="btn go" disabled={busy} onClick={() => act('resume')}>▶ Riprendi</button>}
      <button className="btn" disabled={busy || !active} onClick={() => act('stop')}>■ Stop</button>
      <button className="btn danger" disabled={busy || st === 'idle'} onClick={() => act('kill')}>☠ Kill</button>
      {err && <div className="toast" onClick={() => setErr(null)}>{err}</div>}
    </header>
  )
}
