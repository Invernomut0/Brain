import * as THREE from 'three'

/** Rest positions (layout) and live positions (animated by nodes) of every entity in the scene. */
export const basePos = new Map<string, THREE.Vector3>()
export const livePos = new Map<string, THREE.Vector3>()
/** Timestamp (ms) of the last message that arrived at a node: drives its flash. */
export const nodeFlash = new Map<string, number>()

export function setBase(id: string, v: [number, number, number]) {
  basePos.set(id, new THREE.Vector3(...v))
  if (!livePos.has(id)) livePos.set(id, new THREE.Vector3(...v))
}
export const pos = (id: string) => livePos.get(id) ?? basePos.get(id)

/** Point on a quadratic arc lifted above the chord, so links read as flowing beams instead of flat lines. */
export function arcPoint(a: THREE.Vector3, b: THREE.Vector3, t: number, out: THREE.Vector3) {
  const lift = a.distanceTo(b) * 0.28
  const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2 + lift, cz = (a.z + b.z) / 2
  const u = 1 - t
  return out.set(
    u * u * a.x + 2 * u * t * cx + t * t * b.x,
    u * u * a.y + 2 * u * t * cy + t * t * b.y,
    u * u * a.z + 2 * u * t * cz + t * t * b.z,
  )
}

export const MAXP = 4000

/** Ring-buffer particle system: one draw call for sparks, trails and bursts. */
export class ParticlePool {
  readonly position = new Float32Array(MAXP * 3)
  readonly color = new Float32Array(MAXP * 3)
  readonly size = new Float32Array(MAXP)
  readonly life = new Float32Array(MAXP)
  private vel = new Float32Array(MAXP * 3)
  private age = new Float32Array(MAXP)
  private ttl = new Float32Array(MAXP)
  private drag = new Float32Array(MAXP)
  private head = 0

  spawn(x: number, y: number, z: number, vx: number, vy: number, vz: number, c: THREE.Color, size: number, ttl: number, drag = 1.2) {
    const i = this.head
    this.head = (i + 1) % MAXP
    this.position.set([x, y, z], i * 3)
    this.vel.set([vx, vy, vz], i * 3)
    this.color.set([c.r, c.g, c.b], i * 3)
    this.size[i] = size
    this.ttl[i] = ttl
    this.age[i] = 0
    this.drag[i] = drag
    this.life[i] = 1
  }

  update(dt: number) {
    for (let i = 0; i < MAXP; i++) {
      if (this.ttl[i] <= 0) continue
      this.age[i] += dt
      if (this.age[i] >= this.ttl[i]) { this.ttl[i] = 0; this.life[i] = 0; continue }
      const k = Math.max(0, 1 - this.drag[i] * dt), j = i * 3
      this.vel[j] *= k; this.vel[j + 1] *= k; this.vel[j + 2] *= k
      this.position[j] += this.vel[j] * dt
      this.position[j + 1] += this.vel[j + 1] * dt
      this.position[j + 2] += this.vel[j + 2] * dt
      this.life[i] = 1 - this.age[i] / this.ttl[i]
    }
  }
}

export const particles = new ParticlePool()

const HDR = new THREE.Color()
export function burst(p: THREE.Vector3, color: THREE.Color, n: number, speed: number, size: number) {
  HDR.copy(color).multiplyScalar(1.8)
  for (let i = 0; i < n; i++) {
    const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, r = Math.sqrt(1 - u * u)
    const s = speed * (0.4 + Math.random() * 0.9)
    particles.spawn(p.x, p.y, p.z, r * Math.cos(th) * s, u * s, r * Math.sin(th) * s, HDR, size * (0.6 + Math.random() * 0.8), 0.7 + Math.random() * 0.7)
  }
}

export interface Shock { pos: THREE.Vector3; color: THREE.Color; t0: number; size: number; alpha: number }
export const SHOCK_POOL = 24
export const shocks: (Shock | null)[] = Array(SHOCK_POOL).fill(null)
let shockHead = 0
export function pushShock(p: THREE.Vector3, color: THREE.Color, size: number, alpha = 0.5) {
  shocks[shockHead++ % SHOCK_POOL] = { pos: p.clone(), color: color.clone(), t0: Date.now(), size, alpha }
}

export const glowOf = (activity: Record<string, number>, id: string) => {
  const t = activity[id]
  return t ? Math.exp(-(Date.now() - t) / 1400) : 0
}
export const flashOf = (id: string) => {
  const t = nodeFlash.get(id)
  return t ? Math.exp(-(Date.now() - t) / 650) : 0
}
