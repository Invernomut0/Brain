import { useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, Line, OrbitControls, Stars } from '@react-three/drei'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import * as THREE from 'three'
import { roleColor, useBrain } from '../store'
import { useNow } from '../hooks/useNow'
import type { AgentView } from '../types'

type Vec = [number, number, number]

const FIXED: Record<string, { pos: Vec; color: string; label: string }> = {
  core: { pos: [0, 0, 0], color: '#8b7bff', label: 'MIND' },
  user: { pos: [-11, 3, 0], color: '#ffffff', label: 'Lorenzo' },
  internet: { pos: [11, 3, 0], color: '#22d3ee', label: 'Internet' },
  sandbox: { pos: [0, -5, 9], color: '#ffb020', label: 'Sandbox · Podman' },
  memory: { pos: [0, 6, -9], color: '#60a5fa', label: 'Memoria' },
}

const positions = new Map<string, THREE.Vector3>()
const setPos = (id: string, v: Vec) => positions.set(id, new THREE.Vector3(...v))

function agentDepth(a: AgentView, all: Record<string, AgentView>): number {
  let d = 0
  let cur: AgentView | undefined = a
  while (cur?.parent && all[cur.parent] && d < 4) { d++; cur = all[cur.parent] }
  return d
}

function layout(agents: AgentView[], all: Record<string, AgentView>, customTools: string[]) {
  positions.clear()
  for (const [id, f] of Object.entries(FIXED)) setPos(id, f.pos)
  const byDepth: Record<number, AgentView[]> = {}
  for (const a of agents) (byDepth[agentDepth(a, all)] ??= []).push(a)
  for (const [depth, list] of Object.entries(byDepth)) {
    const d = Number(depth)
    list.forEach((a, i) => {
      const ang = (i / Math.max(list.length, 3)) * Math.PI * 2 + d * 0.9
      const r = 4.6 + d * 2.3
      setPos(a.id, [Math.cos(ang) * r, Math.sin(i * 1.7 + d) * 1.5, Math.sin(ang) * r])
    })
  }
  customTools.forEach((t, i) => {
    const ang = (i / Math.max(customTools.length, 1)) * Math.PI * 2
    setPos(`tool:${t}`, [Math.cos(ang) * 14, -1.5 + Math.sin(i) * 1.5, Math.sin(ang) * 14])
  })
}

function glow(activity: Record<string, number>, id: string): number {
  const t = activity[id]
  return t ? Math.exp(-(Date.now() - t) / 1400) : 0
}

function CoreNode() {
  const wire = useRef<THREE.Mesh>(null)
  const inner = useRef<THREE.Mesh>(null)
  const ring = useRef<THREE.Mesh>(null)
  useFrame((s, dt) => {
    const st = useBrain.getState()
    const aw = st.metrics.awareness_index
    const busy = st.sys.llm_busy > 0 ? 1 : 0
    const running = st.control.state === 'running'
    if (wire.current) { wire.current.rotation.y += dt * (0.25 + busy); wire.current.rotation.x += dt * 0.12 }
    if (ring.current) { ring.current.rotation.z += dt * 0.6; ring.current.rotation.x = Math.PI / 2.4 }
    if (inner.current) {
      const pulse = 1 + Math.sin(s.clock.elapsedTime * (running ? 3 : 1)) * 0.06 + busy * 0.12 + glow(st.activity, 'core') * 0.25
      inner.current.scale.setScalar((0.9 + aw * 0.9) * pulse)
      const mat = inner.current.material as THREE.MeshStandardMaterial
      mat.emissiveIntensity = 1.2 + busy * 2 + glow(st.activity, 'core') * 2
      mat.color.set(st.control.state === 'killed' ? '#ff4d6d' : st.control.state === 'paused' ? '#ffb020' : '#8b7bff')
      mat.emissive.copy(mat.color)
    }
  })
  return (
    <group>
      <mesh ref={inner}><sphereGeometry args={[1.3, 48, 48]} /><meshStandardMaterial color="#8b7bff" emissive="#8b7bff" emissiveIntensity={1.4} roughness={0.3} /></mesh>
      <mesh ref={wire}><icosahedronGeometry args={[2.2, 1]} /><meshBasicMaterial color="#a5b4fc" wireframe transparent opacity={0.35} /></mesh>
      <mesh ref={ring}><torusGeometry args={[2.9, 0.02, 8, 120]} /><meshBasicMaterial color="#22d3ee" transparent opacity={0.7} /></mesh>
      <Html center distanceFactor={14} position={[0, -3.4, 0]} style={{ pointerEvents: 'none' }}>
        <div style={{ color: '#a5b4fc', fontSize: 12, letterSpacing: '0.3em', whiteSpace: 'nowrap' }}>MIND</div>
      </Html>
    </group>
  )
}

function Satellite({ id, color, label, shape }: { id: string; color: string; label: string; shape: 'box' | 'globe' | 'octa' | 'person' }) {
  const ref = useRef<THREE.Mesh>(null)
  useFrame((_, dt) => {
    const m = ref.current
    if (!m) return
    m.rotation.y += dt * 0.4
    const g = glow(useBrain.getState().activity, id)
    m.scale.setScalar(1 + g * 0.5)
    ;(m.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.5 + g * 3
  })
  const p = positions.get(id)
  if (!p) return null
  return (
    <group position={p}>
      <mesh ref={ref}>
        {shape === 'box' && <boxGeometry args={[1.5, 1.5, 1.5]} />}
        {shape === 'globe' && <icosahedronGeometry args={[1.3, 2]} />}
        {shape === 'octa' && <octahedronGeometry args={[0.8]} />}
        {shape === 'person' && <dodecahedronGeometry args={[1.1]} />}
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} wireframe={shape === 'globe' || shape === 'box'} />
      </mesh>
      <Html center distanceFactor={14} position={[0, -1.7, 0]} style={{ pointerEvents: 'none' }}>
        <div style={{ color, fontSize: 11, whiteSpace: 'nowrap', opacity: 0.9 }}>{label}</div>
      </Html>
    </group>
  )
}

function AgentNode({ a, now }: { a: AgentView; now: number }) {
  const g = useRef<THREE.Group>(null)
  const mat = useRef<THREE.MeshStandardMaterial>(null)
  const color = roleColor(a.role)
  const stream = useBrain((s) => s.streams[a.id])
  useFrame((s) => {
    const base = positions.get(a.id)
    if (!g.current || !base) return
    g.current.position.set(base.x, base.y + Math.sin(s.clock.elapsedTime * 1.2 + a.id.length) * 0.25, base.z)
    if (mat.current) {
      const hot = a.state === 'thinking' ? 2.2 + Math.sin(s.clock.elapsedTime * 8) * 0.8 : a.state === 'acting' ? 3 : 0.7
      mat.current.emissiveIntensity = hot
    }
  })
  const fade = a.endedAt ? Math.max(0, 1 - (now - a.endedAt) / 12000) : 1
  const c = a.state === 'failed' ? '#ff4d6d' : a.state === 'done' ? '#34f5a0' : color
  return (
    <group ref={g} scale={0.55 + fade * 0.45}>
      <mesh>
        <sphereGeometry args={[0.6, 28, 28]} />
        <meshStandardMaterial ref={mat} color={c} emissive={c} emissiveIntensity={0.7} transparent opacity={0.3 + fade * 0.7} />
      </mesh>
      <mesh><sphereGeometry args={[0.95, 16, 16]} /><meshBasicMaterial color={c} wireframe transparent opacity={0.18 * fade} /></mesh>
      <Html center distanceFactor={14} position={[0, 1.3, 0]} style={{ pointerEvents: 'none' }}>
        <div style={{ textAlign: 'center', whiteSpace: 'nowrap', opacity: 0.4 + fade * 0.6 }}>
          <div style={{ color: c, fontSize: 12, fontWeight: 700 }}>{a.role}</div>
          <div style={{ color: '#9aa4d8', fontSize: 10 }}>{a.state === 'acting' ? `▸ ${a.detail}` : a.state}</div>
          {a.state === 'thinking' && stream && <div style={{ color: '#c9d3ff', fontSize: 9, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }}>{stream.slice(-40)}</div>}
        </div>
      </Html>
    </group>
  )
}

/** Edges core<->agent (or parent<->child), redrawn each frame from live positions. */
function Links({ agents }: { agents: AgentView[] }) {
  const ref = useRef<THREE.LineSegments>(null)
  const geo = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3 * 2 * 120), 3))
    return g
  }, [])
  useFrame(() => {
    const arr = geo.attributes.position.array as Float32Array
    let i = 0
    for (const a of agents.slice(0, 118)) {
      const p = positions.get(a.id)
      const q = positions.get(a.parent && positions.has(a.parent) ? a.parent : 'core')
      if (!p || !q) continue
      arr.set([q.x, q.y, q.z, p.x, p.y, p.z], i)
      i += 6
    }
    arr.fill(0, i)
    geo.attributes.position.needsUpdate = true
    geo.setDrawRange(0, i / 3)
  })
  return <lineSegments ref={ref} geometry={geo}><lineBasicMaterial color="#6b78d6" transparent opacity={0.45} /></lineSegments>
}

function PulseDot({ from, to, color, t0 }: { from: string; to: string; color: string; t0: number }) {
  const m = useRef<THREE.Mesh>(null)
  useFrame(() => {
    const a = positions.get(from), b = positions.get(to)
    const t = (Date.now() - t0) / 1100
    if (!m.current || !a || !b || t > 1) { if (m.current) m.current.visible = false; return }
    m.current.visible = true
    m.current.position.lerpVectors(a, b, t)
    m.current.scale.setScalar(0.6 + Math.sin(t * Math.PI) * 0.8)
  })
  return <mesh ref={m}><sphereGeometry args={[0.18, 12, 12]} /><meshBasicMaterial color={color} toneMapped={false} /></mesh>
}

function Scene() {
  const agentsMap = useBrain((s) => s.agents)
  const customTools = useBrain((s) => s.customTools)
  const pulses = useBrain((s) => s.pulses)
  const now = useNow(1000)
  const agents = Object.values(agentsMap).filter((a) => !a.endedAt || now - a.endedAt < 12000)
  const toolNames = customTools.filter((t) => t.status === 'active').map((t) => t.name)
  layout(agents, agentsMap, toolNames)

  return (
    <>
      <CoreNode />
      {(['user', 'internet', 'sandbox', 'memory'] as const).map((id) => (
        <Satellite key={id} id={id} color={FIXED[id].color} label={FIXED[id].label} shape={id === 'internet' ? 'globe' : id === 'sandbox' ? 'box' : id === 'user' ? 'person' : 'octa'} />
      ))}
      {toolNames.map((n) => <Satellite key={n} id={`tool:${n}`} color="#ffb020" label={n} shape="octa" />)}
      {agents.map((a) => <AgentNode key={a.id} a={a} now={now} />)}
      <Links agents={agents} />
      {(['user', 'internet', 'sandbox', 'memory'] as const).map((id) => {
        const p = positions.get(id)
        return p ? <Line key={id} points={[[0, 0, 0], p.toArray() as Vec]} color={FIXED[id].color} transparent opacity={0.12} lineWidth={1} /> : null
      })}
      {pulses.map((p) => <PulseDot key={p.id} {...p} />)}
    </>
  )
}

export function Neural3D() {
  return (
    <Canvas camera={{ position: [0, 9, 24], fov: 52 }} dpr={[1, 2]}>
      <color attach="background" args={['#04050d']} />
      <fog attach="fog" args={['#04050d', 30, 80]} />
      <ambientLight intensity={0.35} />
      <pointLight position={[0, 0, 0]} intensity={60} color="#8b7bff" distance={30} />
      <Stars radius={90} depth={50} count={3500} factor={3.5} fade speed={0.6} />
      <Scene />
      <OrbitControls enableDamping autoRotate autoRotateSpeed={0.35} maxDistance={50} minDistance={8} />
      <EffectComposer>
        <Bloom intensity={1.3} luminanceThreshold={0.2} luminanceSmoothing={0.4} mipmapBlur />
      </EffectComposer>
    </Canvas>
  )
}
