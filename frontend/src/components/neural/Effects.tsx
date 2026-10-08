import { useMemo, useRef } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { useBrain } from '../../store'
import { arcPoint, burst, nodeFlash, particles, pos, pushShock, SHOCK_POOL, shocks } from './fx'
import { dustFragment, dustVertex, particleFragment, particleVertex } from './shaders'

/** Renders the shared particle pool (sparks, trails, bursts) in a single draw call. */
export function ParticleLayer() {
  const gl = useThree((s) => s.gl)
  const { geo, mat } = useMemo(() => {
    const g = new THREE.BufferGeometry()
    const mk = (arr: Float32Array, n: number) => { const a = new THREE.BufferAttribute(arr, n); a.setUsage(THREE.DynamicDrawUsage); return a }
    g.setAttribute('position', mk(particles.position, 3))
    g.setAttribute('aColor', mk(particles.color, 3))
    g.setAttribute('aSize', mk(particles.size, 1))
    g.setAttribute('aLife', mk(particles.life, 1))
    const m = new THREE.ShaderMaterial({
      uniforms: { uPx: { value: 1 } }, vertexShader: particleVertex, fragmentShader: particleFragment,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    })
    return { geo: g, mat: m }
  }, [])
  useFrame((_, dt) => {
    particles.update(Math.min(dt, 0.05))
    mat.uniforms.uPx.value = gl.getPixelRatio()
    for (const k of ['position', 'aColor', 'aSize', 'aLife']) geo.getAttribute(k).needsUpdate = true
  })
  return <points geometry={geo} material={mat} frustumCulled={false} />
}

/** Slowly drifting, twinkling dust that gives the space depth. */
export function Dust({ count = 700 }: { count?: number }) {
  const gl = useThree((s) => s.gl)
  const { geo, mat } = useMemo(() => {
    const p = new Float32Array(count * 3), seed = new Float32Array(count)
    for (let i = 0; i < count; i++) {
      const r = 8 + Math.random() * 42, th = Math.random() * Math.PI * 2, u = Math.random() * 2 - 1, s = Math.sqrt(1 - u * u)
      p.set([r * s * Math.cos(th), u * r * 0.6, r * s * Math.sin(th)], i * 3)
      seed[i] = Math.random()
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(p, 3))
    g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1))
    const m = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uPx: { value: 1 } }, vertexShader: dustVertex, fragmentShader: dustFragment,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    })
    return { geo: g, mat: m }
  }, [count])
  useFrame((s) => { mat.uniforms.uTime.value = s.clock.elapsedTime; mat.uniforms.uPx.value = gl.getPixelRatio() })
  return <points geometry={geo} material={mat} frustumCulled={false} />
}

/** Expanding camera-facing rings fired when a message lands on a node. */
export function Shockwaves() {
  const refs = useRef<(THREE.Mesh | null)[]>([])
  useFrame((s) => {
    const now = Date.now()
    for (let i = 0; i < SHOCK_POOL; i++) {
      const m = refs.current[i]
      if (!m) continue
      const sh = shocks[i]
      const age = sh ? (now - sh.t0) / 1100 : 2
      if (!sh || age > 1) { m.visible = false; continue }
      m.visible = true
      m.position.copy(sh.pos)
      m.quaternion.copy(s.camera.quaternion)
      m.scale.setScalar(0.5 + age * sh.size * 2.2)
      const mat = m.material as THREE.MeshBasicMaterial
      mat.opacity = (1 - age) ** 2 * 0.9
      mat.color.copy(sh.color).multiplyScalar(2.2)
    }
  })
  return (
    <>
      {Array.from({ length: SHOCK_POOL }, (_, i) => (
        <mesh key={i} ref={(el) => { refs.current[i] = el }} visible={false}>
          <ringGeometry args={[0.92, 1, 64]} />
          <meshBasicMaterial transparent depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </>
  )
}

interface Comet { from: string; to: string; color: THREE.Color; t0: number; dur: number }

/** Turns store pulses (messages, tool calls) into comets that fly along arcs and burst on arrival. */
export function PulseSystem() {
  const comets = useRef<Comet[]>([])
  const seen = useRef(new Set<number>())
  const tmp = useMemo(() => new THREE.Vector3(), [])
  const hot = useMemo(() => new THREE.Color(), [])

  useFrame(() => {
    const now = Date.now()
    const st = useBrain.getState()
    for (const p of st.pulses) {
      if (seen.current.has(p.id)) continue
      seen.current.add(p.id)
      if (comets.current.length < 80) comets.current.push({ from: p.from, to: p.to, color: new THREE.Color(p.color), t0: now, dur: 1000 })
    }
    if (seen.current.size > 600) seen.current = new Set(st.pulses.map((p) => p.id))

    comets.current = comets.current.filter((c) => {
      const a = pos(c.from), b = pos(c.to)
      if (!a || !b) return false
      const t = (now - c.t0) / c.dur
      if (t >= 1) {
        burst(b, c.color, 26, 3.2, 0.8)
        nodeFlash.set(c.to, now)
        pushShock(b, c.color, c.to === 'core' ? 3.2 : 1.5)
        return false
      }
      const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      arcPoint(a, b, e, tmp)
      hot.copy(c.color).lerp(new THREE.Color('#ffffff'), 0.5).multiplyScalar(2.4)
      particles.spawn(tmp.x, tmp.y, tmp.z, 0, 0, 0, hot, 1.9, 0.07)
      for (let k = 0; k < 3; k++) {
        particles.spawn(
          tmp.x, tmp.y, tmp.z, (Math.random() - 0.5) * 0.8, (Math.random() - 0.5) * 0.8, (Math.random() - 0.5) * 0.8,
          hot.copy(c.color).multiplyScalar(1.6), 0.55, 0.5 + Math.random() * 0.4,
        )
      }
      return true
    })
  })
  return null
}
