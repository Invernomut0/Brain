export const orbVertex = /* glsl */ `
varying vec3 vN; varying vec3 vV; varying vec3 vP;
void main() {
  vP = position;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalize(normalMatrix * normal);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`

export const orbFragment = /* glsl */ `
uniform vec3 uColor; uniform float uTime; uniform float uIntensity; uniform float uPlasma;
varying vec3 vN; varying vec3 vV; varying vec3 vP;
void main() {
  float f = pow(1.0 - clamp(dot(normalize(vN), normalize(vV)), 0.0, 1.0), 2.2);
  float n = 0.5 + 0.5 * sin(vP.x * 3.0 + uTime * 1.3) * sin(vP.y * 3.5 - uTime * 0.9) * sin(vP.z * 3.0 + uTime * 1.1);
  float plasma = mix(1.0, 0.55 + 0.9 * n, uPlasma);
  vec3 core = uColor * (0.35 + 0.9 * uIntensity) * plasma;
  vec3 rim = mix(uColor, vec3(1.0), 0.45) * f * (1.2 + 2.2 * uIntensity);
  gl_FragColor = vec4(core + rim, 1.0);
}`

export const edgeVertex = /* glsl */ `
attribute float aT; uniform float uTime; uniform float uSize; uniform float uBoost; uniform float uSpeed;
varying float vW;
void main() {
  float w = pow(max(0.0, sin((aT * 3.0 - uTime * uSpeed) * 6.2831)), 8.0);
  vW = w;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = uSize * (0.35 + w * 1.8 * uBoost) * (300.0 / -mv.z);
}`

export const edgeFragment = /* glsl */ `
uniform vec3 uColor; uniform float uBoost; uniform float uPoint; uniform float uAlpha;
varying float vW;
void main() {
  float a = 1.0;
  if (uPoint > 0.5) { a = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5)); }
  gl_FragColor = vec4(uColor * (1.0 + vW * uBoost * 2.0), a * (uAlpha + vW * uBoost));
}`

export const particleVertex = /* glsl */ `
attribute vec3 aColor; attribute float aSize; attribute float aLife; uniform float uPx;
varying vec3 vC; varying float vL;
void main() {
  vC = aColor; vL = aLife;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aSize * aLife * uPx * (320.0 / -mv.z);
}`

export const particleFragment = /* glsl */ `
varying vec3 vC; varying float vL;
void main() {
  if (vL <= 0.0) discard;
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.0, d);
  gl_FragColor = vec4(vC, a * a * 1.6 * vL);
}`

export const dustVertex = /* glsl */ `
attribute float aSeed; uniform float uTime; uniform float uPx;
varying float vTw;
void main() {
  vec3 p = position + vec3(sin(uTime * 0.1 + aSeed * 10.0), cos(uTime * 0.13 + aSeed * 7.0), sin(uTime * 0.09 + aSeed * 13.0)) * 2.2;
  vTw = 0.5 + 0.5 * sin(uTime * 1.5 + aSeed * 40.0);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = (1.2 + aSeed * 2.2) * uPx * (120.0 / -mv.z);
}`

export const dustFragment = /* glsl */ `
varying float vTw;
void main() {
  float a = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5));
  gl_FragColor = vec4(vec3(0.55, 0.7, 1.0), a * (0.15 + 0.35 * vTw));
}`

export const floorVertex = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`

export const floorFragment = /* glsl */ `
uniform float uTime; uniform float uBusy; uniform vec3 uColor;
varying vec2 vUv;
void main() {
  vec2 p = (vUv - 0.5) * 120.0;
  float r = length(p);
  float ang = atan(p.y, p.x);
  float ring = smoothstep(0.08, 0.0, abs(fract(r / 6.0 + 0.5) - 0.5) * 6.0);
  float tick = smoothstep(0.05, 0.0, abs(fract(ang / 6.2831 * 48.0 + 0.5) - 0.5) * r * 6.2831 / 48.0) * step(r, 36.0);
  float s = mod(ang - uTime * (0.5 + uBusy * 0.7), 6.2831);
  float sweep = pow(1.0 - s / 6.2831, 6.0) * step(r, 42.0);
  float fade = smoothstep(46.0, 6.0, r);
  vec3 col = uColor * (ring * 0.55 + tick * 0.3 + sweep * (0.3 + 0.5 * uBusy));
  gl_FragColor = vec4(col, length(col) * fade);
}`
