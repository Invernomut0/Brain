import { useMemo } from 'react'
import * as THREE from 'three'
import { Canvas } from '@react-three/fiber'
import { OrbitControls, Stars } from '@react-three/drei'
import { Bloom, ChromaticAberration, EffectComposer, Noise, Vignette } from '@react-three/postprocessing'
import { roleColor, useBrain } from '../store'
import { useNow } from '../hooks/useNow'
import { usePageVisible } from '../hooks/usePageVisible'
import type { AgentView } from '../types'
import { Dust, ParticleLayer, PulseSystem, Shockwaves } from './neural/Effects'
import { Edge, RadarFloor } from './neural/Edges'
import { flashOf, glowOf, setBase } from './neural/fx'
import { Halo } from './neural/Glow'
import { AgentNode, CoreNode, Satellite, type SatKind } from './neural/Nodes'

const SATELLITES: { id: string; kind: SatKind; label: string; color: string; pos: [number, number, number] }[] = [
  { id: 'user', kind: 'user', label: 'Lorenzo', color: '#ffe9b0', pos: [-13, 3, 2] },
  { id: 'internet', kind: 'internet', label: 'Internet', color: '#22d3ee', pos: [13, 3, -2] },
  { id: 'sandbox', kind: 'sandbox', label: 'Sandbox · Podman', color: '#ffb020', pos: [3, -4.5, 11] },
  { id: 'memory', kind: 'memory', label: 'Memoria', color: '#60a5fa', pos: [-3, 6.5, -11] },
]

function depthOf(a: AgentView, all: Record<string, AgentView>): number {
  let d = 0
  let cur: AgentView | undefined = a
  while (cur?.parent && all[cur.parent] && d < 4) { d++; cur = all[cur.parent] }
  return d
}

function layout(agents: AgentView[], all: Record<string, AgentView>, tools: string[]) {
  for (const s of SATELLITES) setBase(s.id, s.pos)
  setBase('core', [0, 0, 0])
  const byDepth: Record<number, AgentView[]> = {}
  for (const a of agents) (byDepth[depthOf(a, all)] ??= []).push(a)
  for (const [depth, list] of Object.entries(byDepth)) {
    const d = Number(depth)
    list.forEach((a, i) => {
      const ang = (i / Math.max(list.length, 3)) * Math.PI * 2 + d * 0.9
      const r = 5.6 + d * 2.6
      setBase(a.id, [Math.cos(ang) * r, Math.sin(i * 1.7 + d) * 1.6, Math.sin(ang) * r])
    })
  }
  tools.forEach((t, i) => {
    const ang = (i / Math.max(tools.length, 1)) * Math.PI * 2 + 0.4
    setBase(`tool:${t}`, [Math.cos(ang) * 16, -2 + Math.sin(i * 2) * 1.8, Math.sin(ang) * 16])
  })
}

function Scene() {
  const agentsMap = useBrain((s) => s.agents)
  const customTools = useBrain((s) => s.customTools)
  const now = useNow(1000)
  const agents = Object.values(agentsMap).filter((a) => !a.endedAt || now - a.endedAt < 12000)
  const toolNames = customTools.filter((t) => t.status === 'active').map((t) => t.name)
  layout(agents, agentsMap, toolNames)

  const agentBoost = (id: string) => () => {
    const st = useBrain.getState().agents[id]?.state
    return (st === 'acting' ? 1 : st === 'thinking' ? 0.7 : 0.15) + flashOf(id) * 0.6
  }
  const nodeBoost = (id: string) => () => Math.min(1.5, glowOf(useBrain.getState().activity, id) + flashOf(id))
  const busy = () => (useBrain.getState().sys.llm_busy > 0 ? 1 : 0)

  return (
    <>
      <Halo color="#3b2a9a" scale={90} opacity={0.12} />
      <group position={[-34, 12, -44]}><Halo color="#4b3bd0" scale={80} opacity={0.1} /></group>
      <group position={[38, -6, -48]}><Halo color="#0e7490" scale={80} opacity={0.08} /></group>
      <group position={[4, 26, -52]}><Halo color="#9d2a85" scale={70} opacity={0.08} /></group>

      <RadarFloor busy={busy} />
      <CoreNode />

      {SATELLITES.map((s) => (
        <group key={s.id}>
          <Satellite id={s.id} kind={s.kind} label={s.label} color={s.color} />
          <Edge from="core" to={s.id} color={s.color} boost={nodeBoost(s.id)} />
        </group>
      ))}
      {toolNames.map((n) => (
        <group key={n}>
          <Satellite id={`tool:${n}`} kind="tool" label={n} color="#ffb020" />
          <Edge from="core" to={`tool:${n}`} color="#ffb020" boost={nodeBoost(`tool:${n}`)} />
        </group>
      ))}
      {agents.map((a) => (
        <group key={a.id}>
          <AgentNode a={a} now={now} />
          <Edge from={a.parent && agentsMap[a.parent] ? a.parent : 'core'} to={a.id} color={roleColor(a.role)} boost={agentBoost(a.id)} />
        </group>
      ))}

      <PulseSystem />
      <ParticleLayer />
      <Shockwaves />
      <Dust />
    </>
  )
}

export function Neural3D() {
  const visible = usePageVisible()  // no frames are rendered while the tab is hidden
  const aberration = useMemo(() => new THREE.Vector2(0.0007, 0.0009), [])
  return (
    <Canvas camera={{ position: [0, 12, 29], fov: 50 }} dpr={[1, 2]} gl={{ antialias: true }} frameloop={visible ? 'always' : 'never'}>
      <color attach="background" args={['#03040b']} />
      <ambientLight intensity={0.3} />
      <Stars radius={110} depth={60} count={2200} factor={3} fade speed={0.5} />
      <Scene />
      <OrbitControls enableDamping autoRotate autoRotateSpeed={0.3} maxDistance={55} minDistance={9} maxPolarAngle={Math.PI * 0.62} />
      <EffectComposer>
        <Bloom intensity={1.0} luminanceThreshold={0.25} luminanceSmoothing={0.5} mipmapBlur radius={0.7} />
        <ChromaticAberration offset={aberration} radialModulation={false} modulationOffset={0} />
        <Noise opacity={0.025} />
        <Vignette eskil={false} offset={0.2} darkness={0.85} />
      </EffectComposer>
    </Canvas>
  )
}
