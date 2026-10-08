import { forwardRef, useMemo } from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { orbFragment, orbVertex } from './shaders'

let tex: THREE.CanvasTexture | null = null
function haloTexture() {
  if (!tex) {
    const c = document.createElement('canvas')
    c.width = c.height = 128
    const g = c.getContext('2d')!
    const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64)
    gr.addColorStop(0, 'rgba(255,255,255,1)')
    gr.addColorStop(0.25, 'rgba(255,255,255,0.35)')
    gr.addColorStop(0.6, 'rgba(255,255,255,0.08)')
    gr.addColorStop(1, 'rgba(255,255,255,0)')
    g.fillStyle = gr
    g.fillRect(0, 0, 128, 128)
    tex = new THREE.CanvasTexture(c)
  }
  return tex
}

/** Soft additive glow billboard. */
export const Halo = forwardRef<THREE.Sprite, { color: string; scale: number; opacity?: number }>(
  ({ color, scale, opacity = 0.5 }, ref) => (
    <sprite ref={ref} scale={[scale, scale, 1]}>
      <spriteMaterial map={haloTexture()} color={color} blending={THREE.AdditiveBlending} depthWrite={false} transparent opacity={opacity} toneMapped={false} />
    </sprite>
  ),
)

export const hdr = (color: string, k: number) => new THREE.Color(color).multiplyScalar(k)

/** Fresnel-rim glowing body; `energy()` (0..~2) is polled every frame. */
export function Orb({ radius, color, energy, plasma = 0, shape = 'sphere' }: {
  radius: number; color: string; energy: () => number; plasma?: number; shape?: 'sphere' | 'octa' | 'ico'
}) {
  const mat = useMemo(
    () => new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) }, uTime: { value: 0 }, uIntensity: { value: 0.5 }, uPlasma: { value: plasma } },
      vertexShader: orbVertex, fragmentShader: orbFragment,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )
  mat.uniforms.uColor.value.set(color)
  useFrame((s) => { mat.uniforms.uTime.value = s.clock.elapsedTime; mat.uniforms.uIntensity.value = energy() })
  return (
    <mesh>
      {shape === 'sphere' && <sphereGeometry args={[radius, 48, 48]} />}
      {shape === 'octa' && <octahedronGeometry args={[radius, 0]} />}
      {shape === 'ico' && <icosahedronGeometry args={[radius, 1]} />}
      <primitive object={mat} attach="material" />
    </mesh>
  )
}
