import { useMemo } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { auraFragment, auraVertex } from './shaders'

/**
 * Orbiting "thought cloud" around an agent. Pure GPU animation: its density, speed and flicker follow `level()`
 * (0..1, driven by the token rate) and its colour blends from reasoning (`color`) to answering (green-white) with `mix()`.
 */
export function ThoughtAura({ color, level, mix, count = 64 }: { color: string; level: () => number; mix: () => number; count?: number }) {
  const gl = useThree((s) => s.gl)
  const { geo, mat } = useMemo(() => {
    const phase = new Float32Array(count), radius = new Float32Array(count), speed = new Float32Array(count), tilt = new Float32Array(count)
    for (let i = 0; i < count; i++) {
      phase[i] = Math.random() * Math.PI * 2
      radius[i] = 0.95 + Math.random() * 0.9
      speed[i] = (0.5 + Math.random() * 1.1) * (Math.random() < 0.5 ? -1 : 1)
      tilt[i] = Math.random() * Math.PI
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3))
    g.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1))
    g.setAttribute('aRadius', new THREE.BufferAttribute(radius, 1))
    g.setAttribute('aSpeed', new THREE.BufferAttribute(speed, 1))
    g.setAttribute('aTilt', new THREE.BufferAttribute(tilt, 1))
    const m = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 }, uLevel: { value: 0 }, uMix: { value: 0 }, uPx: { value: 1 },
        uColorA: { value: new THREE.Color(color).lerp(new THREE.Color('#a5b4fc'), 0.45) }, uColorB: { value: new THREE.Color('#7dffc4') },
      },
      vertexShader: auraVertex, fragmentShader: auraFragment, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    })
    return { geo: g, mat: m }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [count])

  useFrame((s) => {
    const u = mat.uniforms
    u.uTime.value = s.clock.elapsedTime
    u.uPx.value = gl.getPixelRatio()
    u.uLevel.value += (level() - u.uLevel.value) * 0.08
    u.uMix.value += (mix() - u.uMix.value) * 0.1
    u.uColorA.value.set(color).lerp(new THREE.Color('#a5b4fc'), 0.45)
  })
  return <points geometry={geo} material={mat} frustumCulled={false} />
}
