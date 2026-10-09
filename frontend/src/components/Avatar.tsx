import { useEffect, useRef, useState } from 'react'
import { useBrain } from '../store'

/** Avatar sheet per agent role (see frontend/scripts/build_avatars.py); service roles share the "reasoner". */
const SHEET: Record<string, string> = {
  planner: 'planner', executor: 'executor', researcher: 'researcher', engineer: 'engineer',
  critic: 'critic', reflector: 'reflector', evolver: 'evolver',
}

/** Sheet rows: 0 waiting, 1 work in progress, 2 reasoning, 3 queued. */
export function avatarRow(state: string, streaming: boolean): 0 | 1 | 2 | 3 {
  if (state === 'queued') return 3
  if (state === 'acting') return 1
  if (state === 'thinking') return streaming ? 2 : 0  // no token yet: the model has not started, the agent is just waiting
  return 0
}

const FADE_MS = 650
const QUEUED_COLOR = '#ffb020'

/** Full-body animated sprite for the top-right corner of an active card; state changes cross-fade instead of jumping. */
export function Avatar({ id, role, state, color, size = 112 }: { id: string; role: string; state: string; color: string; size?: number }) {
  const streaming = useBrain((s) => !!s.streams[id])
  const row = avatarRow(state, streaming)
  const [layers, setLayers] = useState<{ key: number; row: number }[]>([{ key: 0, row }])
  const seq = useRef(0)

  useEffect(() => {
    setLayers((l) => (l[l.length - 1].row === row ? l : [...l.slice(-1), { key: ++seq.current, row }]))
    const t = setTimeout(() => setLayers((l) => l.slice(-1)), FADE_MS + 50)
    return () => clearTimeout(t)
  }, [row])

  const ring = state === 'done' ? '#34f5a0' : state === 'failed' ? '#ff4d6d' : row === 3 ? QUEUED_COLOR : color
  const sheet = `url(/avatars/${SHEET[role] ?? 'reasoner'}.png)`
  return (
    <div className={`avatar r${row}`} style={{ ['--sz' as string]: `${size}px`, ['--c' as string]: ring }} aria-hidden>
      {layers.map((l, i) => (
        <i key={l.key} className={i === layers.length - 1 ? 'in' : 'out'} style={{ ['--row' as string]: l.row, backgroundImage: sheet }} />
      ))}
    </div>
  )
}
