import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber'
import gsap from 'gsap'
import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode } from 'react'
import * as THREE from 'three'
import { MOTION, prefersReducedMotion } from '../lib/motion'

const R = 1

/** Geographic → Cartesian on a sphere whose texture u=0 is at lon -180. */
export function latLonToVec3(lat: number, lon: number, r = R): THREE.Vector3 {
  const phi = THREE.MathUtils.degToRad(90 - lat)
  const theta = THREE.MathUtils.degToRad(lon + 180)
  return new THREE.Vector3(-r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta))
}

const earthVertex = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  void main() {
    vUv = uv;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`
// Mid-tone grade: partial desaturation toward warm stone, sage-tinted land, slate water.
const earthFragment = /* glsl */ `
  uniform sampler2D map;
  uniform vec3 sunDir;
  varying vec2 vUv;
  varying vec3 vNormalW;
  void main() {
    vec3 c = texture2D(map, vUv).rgb;
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    float water = smoothstep(0.02, 0.10, c.b - c.r);
    vec3 graded = mix(vec3(l), c, 0.42);
    graded = mix(graded * vec3(1.06, 1.02, 0.92), graded * vec3(0.78, 0.90, 0.95), water);
    graded = mix(graded, vec3(0.83, 0.81, 0.76), 0.10);
    float ndl = dot(normalize(vNormalW), normalize(sunDir));
    float day = smoothstep(-0.25, 0.35, ndl);
    vec3 night = graded * 0.16 + vec3(0.03, 0.045, 0.05);
    vec3 col = mix(night, graded * (0.62 + 0.55 * max(ndl, 0.0)), day);
    gl_FragColor = vec4(col, 1.0);
  }
`
const atmoVertex = /* glsl */ `
  varying vec3 vNormalV;
  void main() {
    vNormalV = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`
const atmoFragment = /* glsl */ `
  varying vec3 vNormalV;
  void main() {
    float i = pow(0.68 - dot(vNormalV, vec3(0.0, 0.0, 1.0)), 3.6) * 0.75;
    gl_FragColor = vec4(vec3(0.80, 0.84, 0.80), 1.0) * i;
  }
`

function Earth({ points, focus }: { points: [number, number][]; focus: { lat: number; lon: number; distance: number; key: number } }) {
  const tex = useLoader(THREE.TextureLoader, '/textures/earth-blue-marble.jpg')
  tex.colorSpace = THREE.SRGBColorSpace
  tex.anisotropy = 8
  const group = useRef<THREE.Group>(null)
  const pulse = useRef<THREE.InstancedMesh>(null)
  const core = useRef<THREE.InstancedMesh>(null)
  const { camera } = useThree()
  const reduce = prefersReducedMotion()
  const idle = useRef(true)

  const earthMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: { map: { value: tex }, sunDir: { value: new THREE.Vector3(-0.6, 0.35, 0.9).normalize() } },
        vertexShader: earthVertex,
        fragmentShader: earthFragment,
      }),
    [tex],
  )
  const atmoMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: atmoVertex,
        fragmentShader: atmoFragment,
        blending: THREE.AdditiveBlending,
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
      }),
    [],
  )

  const base = useRef<{ p: THREE.Vector3; q: THREE.Quaternion }[]>([])

  // Real detection coordinates → instanced markers.
  useEffect(() => {
    const m = new THREE.Matrix4()
    const up = new THREE.Vector3(0, 0, 1)
    const one = new THREE.Vector3(1, 1, 1)
    base.current = points.map(([lat, lon]) => {
      const p = latLonToVec3(lat, lon, R * 1.002)
      const q = new THREE.Quaternion().setFromUnitVectors(up, p.clone().normalize())
      return { p, q }
    })
    base.current.forEach(({ p, q }, i) => {
      m.compose(p, q, one)
      core.current?.setMatrixAt(i, m)
      pulse.current?.setMatrixAt(i, m)
    })
    if (core.current) {
      core.current.count = points.length
      core.current.instanceMatrix.needsUpdate = true
    }
    if (pulse.current) {
      pulse.current.count = points.length
      pulse.current.instanceMatrix.needsUpdate = true
    }
  }, [points])

  // Camera focus: rotate the globe so the target faces the camera, then dolly.
  useEffect(() => {
    const g = group.current
    if (!g) return
    idle.current = focus.key === 0
    const targetY = -THREE.MathUtils.degToRad(focus.lon + 90)
    const targetX = THREE.MathUtils.degToRad(focus.lat) * 0.85
    // choose the shortest rotation path
    const cur = g.rotation.y
    const twoPi = Math.PI * 2
    const delta = ((((targetY - cur) % twoPi) + twoPi * 1.5) % twoPi) - Math.PI
    const dur = reduce ? 0 : MOTION.camera.long
    gsap.to(g.rotation, { y: cur + delta, x: targetX, duration: dur, ease: MOTION.gsapEaseInOut })
    gsap.to(camera.position, { z: focus.distance, duration: dur, ease: MOTION.gsapEaseInOut, onUpdate: () => camera.updateProjectionMatrix() })
  }, [focus.key, focus.lat, focus.lon, focus.distance, camera, reduce])

  useFrame((_, dt) => {
    if (!reduce && idle.current && group.current) group.current.rotation.y += dt * 0.035
    if (pulse.current && !reduce && base.current.length) {
      const t = (performance.now() % 2400) / 2400
      ;(pulse.current.material as THREE.MeshBasicMaterial).opacity = 0.55 * (1 - t)
      tmpS.setScalar(1 + t * 2.2)
      base.current.forEach(({ p, q }, i) => {
        tmpM.compose(p, q, tmpS)
        pulse.current!.setMatrixAt(i, tmpM)
      })
      pulse.current.instanceMatrix.needsUpdate = true
    }
  })

  const max = Math.max(points.length, 1)
  return (
    <group ref={group} rotation={[THREE.MathUtils.degToRad(16) * 0.85, -THREE.MathUtils.degToRad(70 + 90), 0]}>
      <mesh material={earthMat}>
        <sphereGeometry args={[R, 128, 128]} />
      </mesh>
      <mesh material={atmoMat} scale={1.16}>
        <sphereGeometry args={[R, 64, 64]} />
      </mesh>
      <instancedMesh ref={core} args={[undefined, undefined, max]} frustumCulled={false}>
        <circleGeometry args={[0.0021, 8]} />
        <meshBasicMaterial color="#f2a57d" toneMapped={false} />
      </instancedMesh>
      <instancedMesh ref={pulse} args={[undefined, undefined, max]} frustumCulled={false}>
        <ringGeometry args={[0.0028, 0.0038, 16]} />
        <meshBasicMaterial color="#e77943" transparent opacity={0.5} depthWrite={false} toneMapped={false} />
      </instancedMesh>
    </group>
  )
}

const tmpM = new THREE.Matrix4()
const tmpS = new THREE.Vector3()

class GlobeBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

export default function Globe({
  points,
  focus,
  className,
}: {
  points: [number, number][]
  focus: { lat: number; lon: number; distance: number; key: number }
  className?: string
}) {
  const fallback = (
    <div className="grid h-full place-items-center text-sm text-ivory/50" role="img" aria-label="3D globe unavailable">
      3D globe unavailable on this device.
    </div>
  )
  return (
    <div className={className} role="img" aria-label={`Globe showing ${points.length} satellite thermal-anomaly detections from the last 24 hours at their reported coordinates`}>
      <GlobeBoundary fallback={fallback}>
        <Canvas
          dpr={[1, 1.75]}
          camera={{ position: [0, 0, 4.6], fov: 32, near: 0.1, far: 50 }}
          gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
        >
          <Suspense fallback={null}>
            <Earth points={points} focus={focus} />
          </Suspense>
        </Canvas>
      </GlobeBoundary>
    </div>
  )
}
