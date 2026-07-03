// frontend/src/components/Orb/JarvisOrb.tsx

import { memo, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { ORB_STATE_CONFIG, type OrbState } from "./OrbStates";

interface JarvisOrbProps {
  state: OrbState;
}

// ── Motion gate ──────────────────────────────────────────────────────────────
function useMotionGate() {
  const [reducedMotion, setReducedMotion] = useState(false);
  const [paused, setPaused] = useState(
    typeof document !== "undefined" && document.visibilityState === "hidden"
  );

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const onMotionChange = () => setReducedMotion(mq.matches);
    mq.addEventListener("change", onMotionChange);

    const onVisibility = () => setPaused(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      mq.removeEventListener("change", onMotionChange);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return { reducedMotion, paused };
}

// ── Procedural organic ring boundary ───────────────────────────────────────
type Point = [number, number];

function roughRingPoints(
  cx: number,
  cy: number,
  baseR: number,
  amp: number,
  t: number,
  seedOffset: number,
  points = 48
): Point[] {
  const pts: Point[] = [];
  for (let i = 0; i <= points; i++) {
    const a = (i / points) * Math.PI * 2;
    const wobble =
      Math.sin(a * 3 + t * 0.6 + seedOffset) * amp * 0.5 +
      Math.sin(a * 5 - t * 0.4 + seedOffset * 1.7) * amp * 0.3 +
      Math.sin(a * 9 + t * 0.9 + seedOffset * 0.5) * amp * 0.2;
    const r = baseR + wobble;
    pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
  }
  return pts;
}

function pointsToPath(pts: Point[]): string {
  return "M " + pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" L ") + " Z";
}

// ── Organic rings layer ─────────────────────────────────────────────────────
interface OrbRingsProps {
  color: string;
  intensity: number;
  speed: number;
  reducedMotion: boolean;
  paused: boolean;
}

function OrbRings({ color, intensity, speed, reducedMotion, paused }: OrbRingsProps) {
  const [t, setT] = useState(0);

  useEffect(() => {
    if (paused) return;
    let raf: number;
    let last = performance.now();
    const loop = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setT((prev) => prev + dt * (reducedMotion ? 0.12 : speed));
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [paused, reducedMotion, speed]);

  const cx = 100;
  const cy = 100;
  const amp = reducedMotion ? 1.5 : 5;
  const outerA = pointsToPath(roughRingPoints(cx, cy, 86, amp, t, 0));
  const outerB = pointsToPath(roughRingPoints(cx, cy, 80, amp * 0.8, t * 1.1, 2.3));

  return (
    <svg className="orb-rings" viewBox="0 0 200 200" style={{ opacity: intensity }}>
      <defs>
        <filter id="orbRingGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.6" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <path d={outerA} fill="none" stroke={color} strokeWidth={1.4} filter="url(#orbRingGlow)" />
      <path d={outerB} fill="none" stroke={color} strokeWidth={0.9} opacity={0.7} filter="url(#orbRingGlow)" />
      {[64, 52].map((r, idx) => (
        <circle
          key={r}
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={0.6}
          strokeDasharray={idx === 0 ? "2 6" : "1 4"}
          opacity={0.4}
          style={{
            transformOrigin: "100px 100px",
            transform: `rotate(${t * (idx === 0 ? 8 : -11)}deg)`,
          }}
        />
      ))}
    </svg>
  );
}

// ── Rotating wireframe geodesic core (Three.js) ─────────────────────────────
interface OrbWireframeProps {
  color: string;
  intensity: number;
  speed: number;
  reducedMotion: boolean;
  paused: boolean;
}

function OrbWireframe({ color, intensity, speed, reducedMotion, paused }: OrbWireframeProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const speedRef = useRef(speed);
  const pausedRef = useRef(paused);
  const colorRef = useRef(color);
  const materialsRef = useRef<{
    outer: THREE.MeshBasicMaterial;
    inner: THREE.MeshBasicMaterial;
  } | null>(null);

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    colorRef.current = color;
    if (materialsRef.current) {
      const c = new THREE.Color(color);
      materialsRef.current.outer.color = c;
      materialsRef.current.inner.color = c;
    }
  }, [color]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 3.1;

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    mount.appendChild(renderer.domElement);

    const outerGeometry = new THREE.IcosahedronGeometry(1.15, 2);
    const outerMaterial = new THREE.MeshBasicMaterial({
      color: new THREE.Color(colorRef.current),
      wireframe: true,
      transparent: true,
      opacity: 0.85,
    });
    const outerMesh = new THREE.Mesh(outerGeometry, outerMaterial);
    scene.add(outerMesh);

    const innerGeometry = new THREE.IcosahedronGeometry(0.7, 1);
    const innerMaterial = new THREE.MeshBasicMaterial({
      color: new THREE.Color(colorRef.current),
      wireframe: true,
      transparent: true,
      opacity: 0.45,
    });
    const innerMesh = new THREE.Mesh(innerGeometry, innerMaterial);
    scene.add(innerMesh);

    materialsRef.current = { outer: outerMaterial, inner: innerMaterial };

    const resize = () => {
      const size = mount.clientWidth || 120;
      renderer.setSize(size, size);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    let raf: number;
    const base = reducedMotion ? 0.03 : 0.18;
    const tick = () => {
      if (!pausedRef.current) {
        const s = speedRef.current;
        outerMesh.rotation.y += base * 0.016 * s;
        outerMesh.rotation.x += base * 0.008 * s;
        innerMesh.rotation.y -= base * 0.012 * s;
        innerMesh.rotation.x += base * 0.01 * s;
        renderer.render(scene, camera);
      }
      raf = requestAnimationFrame(tick);
    };
    tick();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      outerGeometry.dispose();
      outerMaterial.dispose();
      innerGeometry.dispose();
      innerMaterial.dispose();
      renderer.dispose();
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
      materialsRef.current = null;
    };
  }, [reducedMotion]);

  return (
    <div
      ref={mountRef}
      className="orb-wireframe-mount"
      style={{ opacity: intensity, background: "transparent" }}
    />
  );
}

// ── Ambient drifting particles ───────────────────────────────────────────────
interface OrbParticlesProps {
  color: string;
  intensity: number;
}

function OrbParticles({ color, intensity }: OrbParticlesProps) {
  const particles = useMemo(
    () =>
      Array.from({ length: 14 }, (_, i) => ({
        id: i,
        left: 50 + (Math.random() - 0.5) * 80,
        top: 50 + (Math.random() - 0.5) * 80,
        size: 1.5 + Math.random() * 2,
        delay: Math.random() * 6,
        duration: 5 + Math.random() * 5,
      })),
    []
  );

  return (
    <div className="orb-particles" style={{ opacity: intensity }}>
      {particles.map((p) => (
        <span
          key={p.id}
          style={{
            left: `${p.left}%`,
            top: `${p.top}%`,
            width: p.size,
            height: p.size,
            background: color,
            animationDelay: `${p.delay}s`,
            animationDuration: `${p.duration}s`,
          }}
        />
      ))}
    </div>
  );
}

// ── Main orb ─────────────────────────────────────────────────────────────────
export const JarvisOrb = memo(function JarvisOrb({ state }: JarvisOrbProps) {
  const config = ORB_STATE_CONFIG[state] ?? ORB_STATE_CONFIG.idle;
  const { reducedMotion, paused } = useMotionGate();

  return (
    <div className="orb-backdrop" role="img" aria-label={config.ariaLabel}>
      {/* F16: Added config.cssClass here so CSS can target states like .orb-wrap.speaking */}
      <div className={`orb-wrap ${config.cssClass}`}>
        <OrbRings
          color={config.ringColor}
          intensity={config.intensity}
          speed={config.speed}
          reducedMotion={reducedMotion}
          paused={paused}
        />
        <OrbWireframe
          color={config.ringColor}
          intensity={config.intensity}
          speed={config.speed}
          reducedMotion={reducedMotion}
          paused={paused}
        />
        
        <OrbParticles color={config.ringColor} intensity={config.intensity * 0.8} />
      </div>
    </div>
  );
});