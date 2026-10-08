import { useMemo } from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { arcPoint, pos } from './fx'
import { edgeFragment, edgeVertex, floorFragment, floorVertex } from './shaders'

const N = 56

/** Persistent link between two entities: faint beam + travelling light packets whose strength follows `boost()`. */
export function Edge({ from, to, color, boost }: { from: string; to: string; color: string; boost: () => number }) {
  const { geo, lineObj, line, dots } = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3))
    g.setAttribute('aT', new THREE.BufferAttribute(Float32Array.from({ length: N }, (_, i) => i / (N - 1)), 1))
    const mk = (point: number, size: number, alpha: number) => new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 }, uSize: { value: size }, uBoost: { value: 0.3 }, uSpeed: { value: 0.35 },
        uColor: { value: new THREE.Color(color) }, uPoint: { value: point }, uAlpha: { value: alpha },
      },
      vertexShader: edgeVertex, fragmentShader: edgeFragment, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    })
    const l = new THREE.Line(g, mk(0, 0, 0.1))
    l.frustumCulled = false
    return { geo: g, lineObj: l, line: l.material as THREE.ShaderMaterial, dots: mk(1, 3, 0.0) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const tmp = useMemo(() => new THREE.Vector3(), [])

  useFrame((s) => {
    const a = pos(from), b = pos(to)
    if (!a || !b) return
    const arr = geo.attributes.position.array as Float32Array
    for (let i = 0; i < N; i++) {
      arcPoint(a, b, i / (N - 1), tmp)
      arr[i * 3] = tmp.x; arr[i * 3 + 1] = tmp.y; arr[i * 3 + 2] = tmp.z
    }
    geo.attributes.position.needsUpdate = true
    const k = boost()
    for (const m of [line, dots]) {
      m.uniforms.uTime.value = s.clock.elapsedTime
      m.uniforms.uBoost.value = 0.2 + k * 0.7
      m.uniforms.uSpeed.value = 0.25 + k * 0.5
    }
    line.uniforms.uAlpha.value = 0.05 + k * 0.1
  })

  return (
    <>
      <primitive object={lineObj} />
      <points geometry={geo} material={dots} frustumCulled={false} />
    </>
  )
}

/** Radar-like polar grid under the scene; the sweep speeds up while the LLM is busy. */
export function RadarFloor({ busy }: { busy: () => number }) {
  const mat = useMemo(
    () => new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uBusy: { value: 0 }, uColor: { value: new THREE.Color('#5b6cff') } },
      vertexShader: floorVertex, fragmentShader: floorFragment, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    }),
    [],
  )
  useFrame((s) => { mat.uniforms.uTime.value = s.clock.elapsedTime; mat.uniforms.uBusy.value += (busy() - mat.uniforms.uBusy.value) * 0.05 })
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -8, 0]}>
      <planeGeometry args={[120, 120]} />
      <primitive object={mat} attach="material" />
    </mesh>
  )
}
