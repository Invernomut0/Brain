import { useMemo, useRef } from 'react'
import * as THREE from 'three'
import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { roleColor, useBrain } from '../../store'
import type { AgentView } from '../../types'
import { basePos, flashOf, glowOf, livePos, particles } from './fx'
import { Halo, Orb, hdr } from './Glow'

const energyOf = (id: string) => Math.min(2, glowOf(useBrain.getState().activity, id) + flashOf(id))
const rnd = (k = 1) => (Math.random() - 0.5) * k

/** Emits `rate` particles/second from a point; used to make working entities visibly "alive". */
function useEmitter() {
  const acc = useRef(0)
  return (dt: number, rate: number, emit: () => void) => {
    acc.current += dt * rate
    let guard = 0
    while (acc.current >= 1 && guard++ < 8) { acc.current -= 1; emit() }
    if (acc.current > 8) acc.current = 0
  }
}

export function CoreNode() {
  const wire = useRef<THREE.Mesh>(null)
  const halo = useRef<THREE.Sprite>(null)
  const orb = useRef<THREE.Group>(null)
  const rings = useRef<(THREE.Mesh | null)[]>([])
  const swirl = useRef<THREE.Points>(null)
  const tint = useMemo(() => new THREE.Color(), [])
  const emit = useEmitter()

  const swirlGeo = useMemo(() => {
    const n = 420, p = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) {
      const r = 1.9 + Math.random() * 1.6, th = Math.random() * Math.PI * 2, u = (Math.random() * 2 - 1) * 0.8, s = Math.sqrt(1 - u * u)
      p.set([r * s * Math.cos(th), u * r, r * s * Math.sin(th)], i * 3)
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(p, 3))
    return g
  }, [])

  const coreColor = () => {
    const st = useBrain.getState().control.state
    return st === 'killed' ? '#ff4d6d' : st === 'paused' ? '#ffb020' : st === 'stopped' ? '#7f89b8' : '#8b7bff'
  }
  const energy = () => {
    const st = useBrain.getState()
    return 0.7 + (st.sys.llm_busy > 0 ? 1 : 0) + energyOf('core') * 0.6
  }

  useFrame((s, dt) => {
    const st = useBrain.getState()
    const t = s.clock.elapsedTime
    const busy = st.sys.llm_busy > 0 ? 1 : 0
    const running = st.control.state === 'running'
    const aw = st.metrics.awareness_index
    const e = energy()
    const c = coreColor()
    tint.set(c)
    if (wire.current) { wire.current.rotation.y += dt * (0.2 + busy * 0.9); wire.current.rotation.x += dt * 0.1; (wire.current.material as THREE.MeshBasicMaterial).color.copy(tint).multiplyScalar(1.5) }
    if (orb.current) orb.current.scale.setScalar((0.95 + aw * 0.8) * (1 + Math.sin(t * (running ? 3 : 1)) * 0.04 + busy * 0.08 + energyOf('core') * 0.12))
    if (halo.current) {
      const sp = 11 + e * 3 + Math.sin(t * 2) * 0.5
      halo.current.scale.set(sp, sp, 1)
      ;(halo.current.material as THREE.SpriteMaterial).color.copy(tint)
      ;(halo.current.material as THREE.SpriteMaterial).opacity = 0.3 + e * 0.22
    }
    rings.current.forEach((r, i) => {
      if (!r) return
      r.rotation.x += dt * (0.5 + i * 0.35 + busy * 1.2) * (i % 2 ? -1 : 1)
      r.rotation.y += dt * (0.3 + i * 0.2)
      ;(r.material as THREE.MeshBasicMaterial).opacity = 0.35 + e * 0.3
    })
    if (swirl.current) { swirl.current.rotation.y += dt * (0.25 + busy * 1.1); swirl.current.rotation.z += dt * 0.07 }
    // thinking energy radiating out of the core
    emit(dt, busy ? 36 : running ? 6 : 0, () => {
      const u = rnd(2), th = Math.random() * 6.283, r = Math.sqrt(Math.max(0, 1 - u * u)), k = 2.2 + Math.random() * 1.6
      particles.spawn(r * Math.cos(th) * 2, u * 2, r * Math.sin(th) * 2, r * Math.cos(th) * k, u * k, r * Math.sin(th) * k, tint.clone().multiplyScalar(1.7), 0.6, 1.3, 0.8)
    })
  })

  const ringColors = ['#22d3ee', '#8b7bff', '#f472d0']
  return (
    <group>
      <Halo ref={halo} color="#8b7bff" scale={12} opacity={0.5} />
      <group ref={orb}>
        <Orb radius={1.3} color="#8b7bff" energy={energy} plasma={1} />
      </group>
      <mesh ref={wire}><icosahedronGeometry args={[2.25, 1]} /><meshBasicMaterial wireframe transparent opacity={0.35} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh>
      {[2.7, 3.2, 3.75].map((r, i) => (
        <mesh key={r} ref={(el) => { rings.current[i] = el }} rotation={[i * 1.1, i * 0.7, 0]}>
          <torusGeometry args={[r, 0.018, 8, 160]} />
          <meshBasicMaterial color={hdr(ringColors[i], 2)} transparent opacity={0.5} blending={THREE.AdditiveBlending} toneMapped={false} />
        </mesh>
      ))}
      <points ref={swirl} geometry={swirlGeo}>
        <pointsMaterial size={0.07} color={hdr('#a5b4fc', 2)} transparent opacity={0.85} blending={THREE.AdditiveBlending} depthWrite={false} sizeAttenuation toneMapped={false} />
      </points>
      <Html center distanceFactor={16} position={[0, -4.6, 0]} style={{ pointerEvents: 'none' }} zIndexRange={[5, 0]}>
        <CoreLabel />
      </Html>
    </group>
  )
}

function CoreLabel() {
  const cycle = useBrain((s) => s.control.cycle)
  const aw = useBrain((s) => s.metrics.awareness_index)
  return (
    <div style={{ textAlign: 'center', whiteSpace: 'nowrap', textShadow: '0 0 12px #8b7bff' }}>
      <div style={{ color: '#c7d0ff', fontSize: 15, letterSpacing: '0.45em', fontWeight: 700 }}>MIND</div>
      <div style={{ color: '#7f89b8', fontSize: 10, letterSpacing: '0.2em' }}>CICLO {cycle} · AWARENESS {(aw * 100).toFixed(0)}</div>
    </div>
  )
}

const easeOutBack = (x: number) => 1 + 2.70158 * (x - 1) ** 3 + 1.70158 * (x - 1) ** 2

export function AgentNode({ a, now }: { a: AgentView; now: number }) {
  const g = useRef<THREE.Group>(null)
  const halo = useRef<THREE.Sprite>(null)
  const ringA = useRef<THREE.Mesh>(null)
  const ringB = useRef<THREE.Mesh>(null)
  const emit = useEmitter()
  const col = useMemo(() => new THREE.Color(), [])
  const stream = useBrain((s) => s.streams[a.id])
  const base = a.state === 'failed' ? '#ff4d6d' : a.state === 'done' ? '#34f5a0' : roleColor(a.role)

  const energy = () => {
    const st = useBrain.getState().agents[a.id]?.state ?? a.state
    const t = Date.now() / 1000
    const e = st === 'acting' ? 1.6 : st === 'thinking' ? 1.0 + Math.sin(t * 7) * 0.35 : st === 'idle' ? 0.45 : 0.8
    return e + flashOf(a.id) * 0.8
  }

  useFrame((s, dt) => {
    const b = basePos.get(a.id), live = livePos.get(a.id)
    if (!g.current || !b || !live) return
    const t = s.clock.elapsedTime
    const st = useBrain.getState().agents[a.id]?.state ?? a.state
    live.set(b.x + Math.sin(t * 0.7 + a.id.length) * 0.3, b.y + Math.sin(t * 1.2 + a.id.length) * 0.35, b.z + Math.cos(t * 0.6 + a.id.length) * 0.3)
    g.current.position.copy(live)
    const born = Math.min((Date.now() - a.bornAt) / 700, 1)
    const fade = a.endedAt ? Math.max(0, 1 - (Date.now() - a.endedAt) / 12000) : 1
    g.current.scale.setScalar(Math.max(0.001, easeOutBack(born) * (0.55 + fade * 0.45)))
    const speed = st === 'acting' ? 3 : st === 'thinking' ? 1.6 : 0.4
    if (ringA.current) { ringA.current.rotation.x += dt * speed; ringA.current.rotation.y += dt * speed * 0.6 }
    if (ringB.current) { ringB.current.rotation.z -= dt * speed * 1.2; ringB.current.rotation.x += dt * speed * 0.4 }
    const e = energy()
    if (halo.current) {
      const sp = 3.2 + e * 1.8
      halo.current.scale.set(sp, sp, 1)
      ;(halo.current.material as THREE.SpriteMaterial).opacity = (0.25 + e * 0.3) * fade
    }
    col.set(base).multiplyScalar(1.8)
    const rate = st === 'acting' ? 46 : st === 'thinking' ? 16 : 0
    emit(dt, rate, () => {
      const th = Math.random() * 6.283, k = st === 'acting' ? 2.4 : 1.0
      particles.spawn(
        live.x + Math.cos(th) * 0.8, live.y + rnd(0.8), live.z + Math.sin(th) * 0.8,
        -Math.sin(th) * k + rnd(0.6), 0.3 + Math.random() * 0.6, Math.cos(th) * k + rnd(0.6), col, 0.42, 1.1,
      )
    })
  })

  const detail = a.state === 'acting' ? `▸ ${a.detail}` : a.state
  return (
    <group ref={g} scale={0.001}>
      <Halo ref={halo} color={base} scale={4} opacity={0.4} />
      <Orb radius={0.55} color={base} energy={energy} plasma={0.5} />
      <mesh ref={ringA}><torusGeometry args={[0.95, 0.012, 8, 72]} /><meshBasicMaterial color={hdr(base, 2.2)} transparent opacity={0.75} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh>
      <mesh ref={ringB} rotation={[1.2, 0.4, 0]}><torusGeometry args={[1.15, 0.01, 8, 72]} /><meshBasicMaterial color={hdr(base, 1.6)} transparent opacity={0.45} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh>
      <Html center distanceFactor={15} position={[0, 1.7, 0]} style={{ pointerEvents: 'none' }} zIndexRange={[10, 0]}>
        <div style={{
          minWidth: 96, maxWidth: 190, padding: '4px 9px', borderRadius: 8, textAlign: 'center', whiteSpace: 'nowrap',
          background: 'rgba(6,8,22,0.72)', border: `1px solid ${base}66`, boxShadow: `0 0 14px ${base}55`, backdropFilter: 'blur(4px)',
        }}>
          <div style={{ color: base, fontSize: 12, fontWeight: 700, letterSpacing: '0.06em' }}>{a.role}</div>
          <div style={{ color: '#aab4e6', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis' }}>{detail}</div>
          {a.state === 'thinking' && stream && (
            <div style={{ color: '#7f89b8', fontSize: 9, fontFamily: 'ui-monospace, monospace', overflow: 'hidden', textOverflow: 'ellipsis' }}>{stream.slice(-34)}</div>
          )}
        </div>
      </Html>
    </group>
  )
}

export type SatKind = 'user' | 'internet' | 'sandbox' | 'memory' | 'tool'

export function Satellite({ id, color, label, kind }: { id: string; color: string; label: string; kind: SatKind }) {
  const g = useRef<THREE.Group>(null)
  const spin = useRef<THREE.Group>(null)
  const spin2 = useRef<THREE.Group>(null)
  const halo = useRef<THREE.Sprite>(null)
  const emit = useEmitter()
  const col = useMemo(() => new THREE.Color(color).multiplyScalar(1.8), [color])
  const energy = () => 0.45 + energyOf(id)

  useFrame((s, dt) => {
    const b = basePos.get(id), live = livePos.get(id)
    if (!g.current || !b || !live) return
    const t = s.clock.elapsedTime
    const e = energyOf(id)
    live.set(b.x, b.y + Math.sin(t * 0.8 + b.x) * 0.35, b.z)
    g.current.position.copy(live)
    g.current.scale.setScalar(1 + e * 0.18)
    if (spin.current) spin.current.rotation.y += dt * (0.35 + e * 2.2)
    if (spin2.current) { spin2.current.rotation.y -= dt * (0.6 + e * 2.5); spin2.current.rotation.x += dt * 0.3 }
    if (halo.current) {
      const sp = (kind === 'tool' ? 3.4 : 5.2) + e * 2.4
      halo.current.scale.set(sp, sp, 1)
      ;(halo.current.material as THREE.SpriteMaterial).opacity = 0.22 + e * 0.5
    }
    emit(dt, e > 0.35 ? 30 * e : 0, () => {
      const u = rnd(2), th = Math.random() * 6.283, r = Math.sqrt(Math.max(0, 1 - u * u)), k = 1.5 + Math.random()
      particles.spawn(live.x, live.y, live.z, r * Math.cos(th) * k, u * k, r * Math.sin(th) * k, col, 0.45, 0.9)
    })
  })

  const orbiters = useMemo(() => Array.from({ length: 6 }, (_, i) => (i / 6) * Math.PI * 2), [])

  return (
    <group ref={g}>
      <Halo ref={halo} color={color} scale={5} opacity={0.3} />
      {kind === 'user' && (
        <>
          <Orb radius={0.95} color="#ffe9b0" energy={energy} plasma={0.6} />
          <group ref={spin}><mesh rotation={[1.3, 0, 0]}><torusGeometry args={[1.5, 0.02, 8, 90]} /><meshBasicMaterial color={hdr('#ffe9b0', 2.2)} transparent opacity={0.8} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh></group>
        </>
      )}
      {kind === 'internet' && (
        <>
          <Orb radius={1.15} color={color} energy={energy} plasma={0.35} />
          <group ref={spin}>
            <mesh><icosahedronGeometry args={[1.5, 2]} /><meshBasicMaterial color={hdr(color, 1.8)} wireframe transparent opacity={0.4} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh>
          </group>
          <group ref={spin2} rotation={[0.5, 0, 0.3]}>
            {orbiters.map((a, i) => (
              <mesh key={i} position={[Math.cos(a) * 2.1, 0, Math.sin(a) * 2.1]}><sphereGeometry args={[0.07, 8, 8]} /><meshBasicMaterial color={hdr('#ffffff', 2.5)} toneMapped={false} /></mesh>
            ))}
          </group>
        </>
      )}
      {kind === 'sandbox' && (
        <>
          <group ref={spin}><mesh><boxGeometry args={[2.2, 2.2, 2.2]} /><meshBasicMaterial color={hdr(color, 1.8)} wireframe transparent opacity={0.65} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh></group>
          <group ref={spin2}><mesh><boxGeometry args={[1.0, 1.0, 1.0]} /><meshBasicMaterial color={hdr(color, 2.4)} transparent opacity={0.85} toneMapped={false} /></mesh></group>
        </>
      )}
      {kind === 'memory' && (
        <group ref={spin}>
          <Orb radius={0.9} color={color} energy={energy} shape="octa" />
          <mesh scale={[1, 1.9, 1]}><octahedronGeometry args={[1.25, 0]} /><meshBasicMaterial color={hdr(color, 1.8)} wireframe transparent opacity={0.45} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh>
          <mesh rotation={[0, 0.8, 0]} scale={[1.5, 0.6, 1.5]}><octahedronGeometry args={[1.1, 0]} /><meshBasicMaterial color={hdr(color, 1.4)} wireframe transparent opacity={0.35} blending={THREE.AdditiveBlending} toneMapped={false} /></mesh>
        </group>
      )}
      {kind === 'tool' && (
        <group ref={spin}><Orb radius={0.7} color={color} energy={energy} shape="octa" /></group>
      )}
      <Html center distanceFactor={15} position={[0, kind === 'tool' ? -1.5 : -2.3, 0]} style={{ pointerEvents: 'none' }} zIndexRange={[5, 0]}>
        <div style={{ color, fontSize: kind === 'tool' ? 10 : 12, fontWeight: 600, letterSpacing: '0.1em', whiteSpace: 'nowrap', textShadow: `0 0 10px ${color}` }}>{label}</div>
      </Html>
    </group>
  )
}
