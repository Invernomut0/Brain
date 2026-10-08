import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useBrain } from '../store'

const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString('it-IT')

function summary(e: { type: string; data: Record<string, any> }): string {
  const d = e.data
  switch (e.type) {
    case 'agent.thought': return `${d.action} — ${d.thought}`
    case 'tool.call': return `${d.tool} ${d.args}`
    case 'tool.result': return `${d.ok ? '✓' : '✗'} ${d.tool}: ${d.preview}`
    case 'agent.end': return `${d.success ? 'OK' : 'KO'} ${d.summary}`
    case 'agent.spawn': return `${d.role}: ${d.task}`
    case 'goal.update': return `#${d.id} [${d.status}] ${d.title}`
    case 'chat.message': return `${d.role}: ${d.text}`
    case 'system.log': return d.text
    case 'tool.created': return `${d.name} (${d.passed ? 'test OK' : 'test falliti'})`
    case 'evolution': return `${d.kind} ${d.target}: ${d.status} — ${d.reason}`
    case 'introspection.probe': return `punteggio ${d.score}: ${d.answer}`
    case 'lesson.learned': return `${d.new ? 'NUOVA' : `x${d.count}`} [${d.kind}] ${d.text}`
    case 'cycle.start': return `ciclo ${d.cycle}`
    default: return JSON.stringify(d).slice(0, 160)
  }
}

export function EventFeed() {
  const events = useBrain((s) => s.events)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight }, [events.length])
  return (
    <div className="scroll feed" ref={ref}>
      {events.map((e, i) => (
        <div className="ev" key={`${e.seq}-${i}`}>
          <span className="t">{time(e.ts)}</span><span className="ty">{e.type}</span><span className="ag">{e.agent ?? ''}</span><span className="tx" title={summary(e)}>{summary(e)}</span>
        </div>
      ))}
    </div>
  )
}

export function Chat() {
  const chat = useBrain((s) => s.chat)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight; if (chat.at(-1)?.role === 'brain') setWaiting(false) }, [chat])

  const send = async () => {
    const t = text.trim()
    if (!t || sending) return
    setSending(true)
    try { await api.chat(t); setText(''); setWaiting(true) } finally { setSending(false) }
  }
  return (
    <div className="chat">
      <div className="msgs" ref={ref}>
        {!chat.length && <div className="empty">Scrivi a Brain: ti risponde e può trasformare le tue richieste in obiettivi.</div>}
        {chat.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.text}</div>)}
        {waiting && <div className="msg brain"><span className="spin" /> sta pensando…</div>}
      </div>
      <div className="composer">
        <input value={text} placeholder="Parla con Brain…" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} />
        <button className="btn go" disabled={sending || !text.trim()} onClick={send}>Invia</button>
      </div>
    </div>
  )
}

export function Journal() {
  const journal = useBrain((s) => s.journal)
  return (
    <div className="scroll">
      {!journal.length && <div className="empty">Il giornale si riempie con le riflessioni di Brain.</div>}
      {[...journal].reverse().map((j) => <div className="entry" key={j.id}><small>{j.kind} · {time(j.ts)}</small>{j.text}</div>)}
    </div>
  )
}

export function ToolsView() {
  const tools = useBrain((s) => s.customTools)
  const builtin = useBrain((s) => s.tools.filter((t) => !t.custom))
  return (
    <div className="scroll">
      <div className="cols">
        {tools.map((t) => (
          <div className="card" key={t.name}><b>{t.name}</b> <small style={{ display: 'inline' }}>[{t.status}] chiamate {t.calls} · errori {t.failures}</small><small>{t.description}</small></div>
        ))}
        {builtin.map((t) => <div className="card" key={t.name}><b style={{ color: '#60a5fa' }}>{t.name}</b><small>{t.description}</small></div>)}
      </div>
    </div>
  )
}

export function LessonsView() {
  const lessons = useBrain((s) => s.lessons)
  return (
    <div className="scroll">
      {!lessons.length && <div className="empty">Quando un tool fallisce e poi viene corretto, o un obiettivo fallisce, Brain ne ricava una lezione e la usa nei prompt successivi.</div>}
      {lessons.map((l) => (
        <div className="entry" key={l.text}>
          <small>{l.kind} · visto {l.count}× · {time(l.ts)}</small>{l.text}
        </div>
      ))}
    </div>
  )
}

export function EvolutionView() {
  const evs = useBrain((s) => s.evolutions)
  return (
    <div className="scroll">
      {!evs.length && <div className="empty">Nessuna evoluzione ancora: prompt e hook vengono modificati solo con test e rollback.</div>}
      {evs.map((e) => (
        <div className="entry" key={e.id}>
          <small>{e.kind} · {e.target} · {time(e.ts)}</small>
          <b style={{ color: e.status === 'applied' ? '#34f5a0' : e.status === 'rolled_back' ? '#ffb020' : '#ff4d6d' }}>{e.status}</b> — {e.reason}
        </div>
      ))}
    </div>
  )
}
