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

const MOTION = ['r0', 'r1', 'r2', 'r3'] as const

/** Animated sprite showing what the agent is doing right now; meant for the top-right corner of its card. */
export function Avatar({ id, role, state, color, size = 44 }: { id: string; role: string; state: string; color: string; size?: number }) {
  const streaming = useBrain((s) => !!s.streams[id])
  const row = avatarRow(state, streaming)
  const ring = state === 'done' ? '#34f5a0' : state === 'failed' ? '#ff4d6d' : color
  return (
    <div className={`avatar ${MOTION[row]}`} style={{ ['--sz' as string]: `${size}px`, ['--c' as string]: ring, ['--row' as string]: row }} aria-hidden>
      <i style={{ backgroundImage: `url(/avatars/${SHEET[role] ?? 'reasoner'}.png)` }} />
    </div>
  )
}
