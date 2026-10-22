import { useEffect, useMemo, useRef, type CSSProperties } from "react";
import * as THREE from "three";

import {
  ORB_STATE_CONFIG,
  type OrbState,
  type OrbStateConfig,
} from "./OrbStates";

interface JarvisOrbProps {
  state: OrbState;
  isSpeaking?: boolean;
}

interface OrbitRecord {
  group: THREE.Group;
  baseMaterial: THREE.MeshBasicMaterial;
  speed: number;
  direction: 1 | -1;
}

const Y_AXIS = new THREE.Vector3(0, 1, 0);

function damp(current: number, target: number, lambda: number, delta: number) {
  return THREE.MathUtils.lerp(
    current,
    target,
    1 - Math.exp(-lambda * delta),
  );
}

function createParticleTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;

  const context = canvas.getContext("2d");
  if (!context) return null;

  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 31);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.2, "rgba(255,255,255,0.95)");
  gradient.addColorStop(0.55, "rgba(255,255,255,0.3)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 64, 64);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

function fillParticleShell(
  count: number,
  minRadius: number,
  maxRadius: number,
) {
  const positions = new Float32Array(count * 3);

  for (let index = 0; index < count; index += 1) {
    const radius = THREE.MathUtils.lerp(
      minRadius,
      maxRadius,
      Math.pow(Math.random(), 0.72),
    );
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(THREE.MathUtils.randFloatSpread(2));

    positions[index * 3] = radius * Math.sin(phi) * Math.cos(theta);
    positions[index * 3 + 1] = radius * Math.cos(phi);
    positions[index * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta);
  }

  return positions;
}

function currentConfig(state: OrbState): OrbStateConfig {
  return ORB_STATE_CONFIG[state];
}

export function JarvisOrb({ state, isSpeaking = false }: JarvisOrbProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef(state);
  const speakingRef = useRef(isSpeaking);
  const visualState = currentConfig(state);

  const cssVariables = useMemo(
    () =>
      ({
        "--orb-primary": visualState.primary,
        "--orb-secondary": visualState.secondary,
        "--orb-core": visualState.core,
        "--orb-intensity": visualState.intensity,
      }) as CSSProperties,
    [visualState],
  );

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    speakingRef.current = isSpeaking;
  }, [isSpeaking]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 0, 6.8);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: "high-performance",
      });
    } catch (error) {
      console.error("[JarvisOrb] WebGL initialization failed", error);
      mount.dataset.webglUnavailable = "true";
      return;
    }

    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.28;
    mount.appendChild(renderer.domElement);

    const root = new THREE.Group();
    root.rotation.x = -0.06;
    scene.add(root);

    const coreGroup = new THREE.Group();
    const orbitSystem = new THREE.Group();
    const particleSystem = new THREE.Group();
    root.add(coreGroup, orbitSystem, particleSystem);

    const initial = currentConfig(stateRef.current);
    const primaryColor = new THREE.Color(initial.primary);
    const secondaryColor = new THREE.Color(initial.secondary);
    const coreColor = new THREE.Color(initial.core);
    const targetPrimary = new THREE.Color(initial.primary);
    const targetSecondary = new THREE.Color(initial.secondary);
    const targetCore = new THREE.Color(initial.core);

    let activity = initial.intensity;
    let rotationSpeed = initial.speed;
    let pulseAmount = initial.pulse;
    let particleOpacity = initial.particleOpacity;
    let ringOpacity = initial.ringOpacity;
    let distortion = initial.distortion;
    let jitter = initial.jitter;

    const shellGeometry = new THREE.IcosahedronGeometry(1.12, 5);
    const shellMaterial = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: { value: 0 },
        uPrimary: { value: primaryColor.clone() },
        uSecondary: { value: secondaryColor.clone() },
        uCore: { value: coreColor.clone() },
        uIntensity: { value: activity },
        uDistortion: { value: distortion },
      },
      vertexShader: `
        uniform float uTime;
        uniform float uDistortion;
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        varying float vWave;

        void main() {
          float waveA = sin(position.y * 8.0 + uTime * 1.85);
          float waveB = sin(position.x * 7.0 - uTime * 1.35);
          float waveC = sin(position.z * 10.0 + uTime * 2.2) * 0.55;
          float wave = (waveA * waveB + waveC) * uDistortion;
          vec3 displaced = position + normal * wave * 0.038;

          vWave = wave;
          vNormal = normalize(normalMatrix * normal);
          vec4 worldPosition = modelMatrix * vec4(displaced, 1.0);
          vWorldPosition = worldPosition.xyz;
          gl_Position = projectionMatrix * viewMatrix * worldPosition;
        }
      `,
      fragmentShader: `
        uniform float uTime;
        uniform float uIntensity;
        uniform vec3 uPrimary;
        uniform vec3 uSecondary;
        uniform vec3 uCore;
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        varying float vWave;

        void main() {
          vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
          float fresnel = pow(1.0 - max(dot(vNormal, viewDirection), 0.0), 2.15);
          float latitude = abs(sin(vWorldPosition.y * 17.0 + uTime * 2.35));
          float energyBand = smoothstep(0.78, 0.99, latitude);
          float scan = sin((vWorldPosition.x + vWorldPosition.z) * 13.0 - uTime * 2.0);
          float filament = pow(scan * 0.5 + 0.5, 7.0);
          float waveLight = smoothstep(0.18, 0.8, abs(vWave));

          vec3 color = mix(uSecondary, uPrimary, clamp(fresnel + 0.2, 0.0, 1.0));
          color = mix(color, uCore, energyBand * 0.5 + filament * 0.28);
          color += uCore * waveLight * 0.08;

          float alpha = (0.15 + fresnel * 0.62 + energyBand * 0.17 + filament * 0.12)
            * uIntensity;
          gl_FragColor = vec4(color, alpha);
        }
      `,
    });
    const energyShell = new THREE.Mesh(shellGeometry, shellMaterial);
    energyShell.renderOrder = 3;
    coreGroup.add(energyShell);

    const glassGeometry = new THREE.IcosahedronGeometry(1.18, 4);
    const glassMaterial = new THREE.MeshPhysicalMaterial({
      color: primaryColor,
      emissive: secondaryColor,
      emissiveIntensity: 0.42,
      metalness: 0.12,
      roughness: 0.18,
      clearcoat: 1,
      clearcoatRoughness: 0.08,
      transmission: 0.18,
      thickness: 0.7,
      transparent: true,
      opacity: 0.26,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    const glassShell = new THREE.Mesh(glassGeometry, glassMaterial);
    glassShell.renderOrder = 2;
    coreGroup.add(glassShell);

    const wireGeometry = new THREE.IcosahedronGeometry(1.22, 3);
    const wireMaterial = new THREE.MeshBasicMaterial({
      color: coreColor,
      wireframe: true,
      transparent: true,
      opacity: 0.34,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const wireframe = new THREE.Mesh(wireGeometry, wireMaterial);
    wireframe.renderOrder = 4;
    coreGroup.add(wireframe);

    const outerCageGeometry = new THREE.IcosahedronGeometry(2.16, 2);
    const outerCageMaterial = new THREE.MeshBasicMaterial({
      color: primaryColor,
      wireframe: true,
      transparent: true,
      opacity: 0.075,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const outerCage = new THREE.Mesh(outerCageGeometry, outerCageMaterial);
    outerCage.scale.set(1, 0.98, 1);
    root.add(outerCage);

    const auraGeometry = new THREE.SphereGeometry(1.52, 64, 64);
    const auraMaterial = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: { value: 0 },
        uColor: { value: primaryColor.clone() },
        uIntensity: { value: activity },
      },
      vertexShader: `
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        void main() {
          vNormal = normalize(normalMatrix * normal);
          vec4 worldPosition = modelMatrix * vec4(position, 1.0);
          vWorldPosition = worldPosition.xyz;
          gl_Position = projectionMatrix * viewMatrix * worldPosition;
        }
      `,
      fragmentShader: `
        uniform float uTime;
        uniform float uIntensity;
        uniform vec3 uColor;
        varying vec3 vNormal;
        varying vec3 vWorldPosition;

        void main() {
          vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
          float fresnel = pow(1.0 - max(dot(vNormal, viewDirection), 0.0), 2.6);
          float pulse = 0.88 + sin(uTime * 2.15) * 0.12;
          gl_FragColor = vec4(uColor, fresnel * uIntensity * pulse * 0.82);
        }
      `,
    });
    const aura = new THREE.Mesh(auraGeometry, auraMaterial);
    aura.renderOrder = 1;
    coreGroup.add(aura);

    const nucleusGeometry = new THREE.SphereGeometry(0.31, 48, 48);
    const nucleusMaterial = new THREE.MeshBasicMaterial({
      color: coreColor,
      transparent: true,
      opacity: 0.96,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const nucleus = new THREE.Mesh(nucleusGeometry, nucleusMaterial);
    nucleus.renderOrder = 7;
    coreGroup.add(nucleus);

    const nucleusGlowGeometry = new THREE.SphereGeometry(0.57, 48, 48);
    const nucleusGlowMaterial = new THREE.MeshBasicMaterial({
      color: primaryColor,
      transparent: true,
      opacity: 0.31,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const nucleusGlow = new THREE.Mesh(
      nucleusGlowGeometry,
      nucleusGlowMaterial,
    );
    nucleusGlow.renderOrder = 6;
    coreGroup.add(nucleusGlow);

    const innerBandMaterial = new THREE.MeshBasicMaterial({
      color: coreColor,
      transparent: true,
      opacity: 0.76,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const innerBands = [
      { radius: 0.73, tube: 0.018, rotation: [0.2, 0.55, 0.1] },
      { radius: 0.88, tube: 0.014, rotation: [1.15, -0.3, 0.5] },
      { radius: 1.02, tube: 0.012, rotation: [0.75, 0.2, -0.65] },
    ].map(({ radius, tube, rotation }) => {
      const band = new THREE.Mesh(
        new THREE.TorusGeometry(radius, tube, 10, 150),
        innerBandMaterial,
      );
      band.rotation.set(rotation[0], rotation[1], rotation[2]);
      band.renderOrder = 5;
      coreGroup.add(band);
      return band;
    });

    const spokeMaterial = new THREE.MeshBasicMaterial({
      color: secondaryColor,
      transparent: true,
      opacity: 0.18,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const spokeGeometry = new THREE.CylinderGeometry(
      0.006,
      0.002,
      1,
      5,
      1,
      true,
    );
    const spokeGroup = new THREE.Group();
    for (let index = 0; index < 22; index += 1) {
      const direction = new THREE.Vector3(
        THREE.MathUtils.randFloatSpread(2),
        THREE.MathUtils.randFloatSpread(2),
        THREE.MathUtils.randFloatSpread(2),
      ).normalize();
      const length = THREE.MathUtils.randFloat(0.72, 1.45);
      const spoke = new THREE.Mesh(spokeGeometry, spokeMaterial);
      spoke.position.copy(direction).multiplyScalar(length * 0.5);
      spoke.quaternion.setFromUnitVectors(Y_AXIS, direction);
      spoke.scale.y = length;
      spokeGroup.add(spoke);
    }
    coreGroup.add(spokeGroup);

    const orbitBaseMaterials: THREE.MeshBasicMaterial[] = [];
    const orbitAccentMaterial = new THREE.MeshBasicMaterial({
      color: coreColor,
      transparent: true,
      opacity: 0.92,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const nodeMaterial = new THREE.MeshBasicMaterial({
      color: coreColor,
      transparent: true,
      opacity: 0.98,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const nodeHaloMaterial = new THREE.MeshBasicMaterial({
      color: primaryColor,
      transparent: true,
      opacity: 0.24,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const shardMaterial = new THREE.MeshBasicMaterial({
      color: secondaryColor,
      transparent: true,
      opacity: 0.72,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      wireframe: true,
    });
    const nodeGeometry = new THREE.SphereGeometry(0.047, 12, 12);
    const nodeHaloGeometry = new THREE.SphereGeometry(0.092, 12, 12);
    const shardGeometry = new THREE.OctahedronGeometry(0.072, 0);
    const orbitRecords: OrbitRecord[] = [];

    const createOrbit = (
      radius: number,
      tube: number,
      rotation: [number, number, number],
      speed: number,
      direction: 1 | -1,
      opacityScale: number,
      nodeAngles: number[],
    ) => {
      const orbitGroup = new THREE.Group();
      orbitGroup.rotation.set(...rotation);

      const baseMaterial = new THREE.MeshBasicMaterial({
        color: primaryColor,
        transparent: true,
        opacity: initial.ringOpacity * opacityScale,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      baseMaterial.userData.opacityScale = opacityScale;
      orbitBaseMaterials.push(baseMaterial);

      const baseRing = new THREE.Mesh(
        new THREE.TorusGeometry(radius, tube, 12, 220),
        baseMaterial,
      );
      baseRing.renderOrder = 2;
      orbitGroup.add(baseRing);

      const arcSpecs = [
        [0.18, 1.05],
        [2.15, 0.72],
        [3.72, 1.28],
        [5.55, 0.42],
      ];
      arcSpecs.forEach(([start, length], arcIndex) => {
        const arc = new THREE.Mesh(
          new THREE.TorusGeometry(
            radius,
            tube * (arcIndex % 2 === 0 ? 2.1 : 1.65),
            12,
            70,
            length,
          ),
          orbitAccentMaterial,
        );
        arc.rotation.z = start;
        arc.renderOrder = 5;
        orbitGroup.add(arc);
      });

      nodeAngles.forEach((angle, nodeIndex) => {
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;

        const halo = new THREE.Mesh(nodeHaloGeometry, nodeHaloMaterial);
        halo.position.set(x, y, 0);
        halo.scale.setScalar(nodeIndex % 2 === 0 ? 1 : 0.72);
        orbitGroup.add(halo);

        const node = new THREE.Mesh(nodeGeometry, nodeMaterial);
        node.position.set(x, y, 0);
        node.scale.setScalar(nodeIndex % 3 === 0 ? 1.18 : 0.82);
        node.renderOrder = 8;
        orbitGroup.add(node);

        if (nodeIndex % 2 === 0) {
          const shard = new THREE.Mesh(shardGeometry, shardMaterial);
          shard.position.set(
            Math.cos(angle + 0.18) * radius,
            Math.sin(angle + 0.18) * radius,
            0,
          );
          shard.rotation.set(angle, angle * 0.7, angle * 0.45);
          orbitGroup.add(shard);
        }
      });

      orbitSystem.add(orbitGroup);
      orbitRecords.push({ group: orbitGroup, baseMaterial, speed, direction });
    };

    createOrbit(1.34, 0.018, [0.42, 0.2, 0.18], 0.82, 1, 0.82, [0.55, 2.6]);
    createOrbit(1.62, 0.021, [1.08, -0.18, -0.38], 0.58, -1, 0.72, [1.2, 3.7, 5.35]);
    createOrbit(1.92, 0.026, [0.78, 0.62, 0.12], 0.41, 1, 0.58, [0.25, 2.1, 4.55]);
    createOrbit(2.2, 0.018, [1.36, -0.48, 0.54], 0.3, -1, 0.42, [1.75, 4.9]);

    const particleTexture = createParticleTexture();
    const outerParticleGeometry = new THREE.BufferGeometry();
    outerParticleGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(fillParticleShell(480, 1.42, 2.58), 3),
    );
    const outerParticleMaterial = new THREE.PointsMaterial({
      color: primaryColor,
      size: 0.038,
      map: particleTexture ?? undefined,
      transparent: true,
      opacity: particleOpacity,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      alphaTest: 0.01,
      sizeAttenuation: true,
    });
    const outerParticles = new THREE.Points(
      outerParticleGeometry,
      outerParticleMaterial,
    );
    particleSystem.add(outerParticles);

    const innerParticleGeometry = new THREE.BufferGeometry();
    innerParticleGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(fillParticleShell(150, 0.38, 1.35), 3),
    );
    const innerParticleMaterial = new THREE.PointsMaterial({
      color: coreColor,
      size: 0.047,
      map: particleTexture ?? undefined,
      transparent: true,
      opacity: Math.min(1, particleOpacity * 1.15),
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      alphaTest: 0.01,
      sizeAttenuation: true,
    });
    const innerParticles = new THREE.Points(
      innerParticleGeometry,
      innerParticleMaterial,
    );
    particleSystem.add(innerParticles);

    const keyLight = new THREE.PointLight(coreColor, 8.5, 12, 2);
    keyLight.position.set(2.5, 2.4, 3.7);
    scene.add(keyLight);

    const rimLight = new THREE.PointLight(primaryColor, 7.5, 11, 2);
    rimLight.position.set(-2.9, -1.4, 2.6);
    scene.add(rimLight);

    const ambientLight = new THREE.AmbientLight(0x173944, 2.2);
    scene.add(ambientLight);

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const clock = new THREE.Clock();

    const resize = () => {
      const width = Math.max(mount.clientWidth, 1);
      const height = Math.max(mount.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(mount);
    resize();

    let frameId = 0;
    let disposed = false;

    const animate = () => {
      if (disposed) return;
      frameId = window.requestAnimationFrame(animate);

      const delta = Math.min(clock.getDelta(), 0.05);
      const elapsed = clock.elapsedTime;
      const target = currentConfig(stateRef.current);
      const speaking = speakingRef.current || stateRef.current === "speaking";
      const colorBlend = 1 - Math.exp(-4.8 * delta);

      targetPrimary.set(target.primary);
      targetSecondary.set(target.secondary);
      targetCore.set(target.core);
      primaryColor.lerp(targetPrimary, colorBlend);
      secondaryColor.lerp(targetSecondary, colorBlend);
      coreColor.lerp(targetCore, colorBlend);

      activity = damp(activity, target.intensity, 4.5, delta);
      rotationSpeed = damp(rotationSpeed, target.speed, 4.2, delta);
      pulseAmount = damp(pulseAmount, target.pulse, 5, delta);
      particleOpacity = damp(
        particleOpacity,
        target.particleOpacity,
        4.2,
        delta,
      );
      ringOpacity = damp(ringOpacity, target.ringOpacity, 4.5, delta);
      distortion = damp(distortion, target.distortion, 4.2, delta);
      jitter = damp(jitter, target.jitter, 7, delta);

      shellMaterial.uniforms.uTime.value = elapsed;
      shellMaterial.uniforms.uPrimary.value.copy(primaryColor);
      shellMaterial.uniforms.uSecondary.value.copy(secondaryColor);
      shellMaterial.uniforms.uCore.value.copy(coreColor);
      shellMaterial.uniforms.uIntensity.value = activity;
      shellMaterial.uniforms.uDistortion.value = distortion;

      auraMaterial.uniforms.uTime.value = elapsed;
      auraMaterial.uniforms.uColor.value.copy(primaryColor);
      auraMaterial.uniforms.uIntensity.value = activity;

      glassMaterial.color.copy(primaryColor);
      glassMaterial.emissive.copy(secondaryColor);
      glassMaterial.emissiveIntensity = 0.3 + activity * 0.32;
      glassMaterial.opacity = 0.12 + activity * 0.16;
      wireMaterial.color.copy(coreColor);
      wireMaterial.opacity = 0.14 + activity * 0.22;
      outerCageMaterial.color.copy(primaryColor);
      outerCageMaterial.opacity = ringOpacity * 0.105;
      nucleusMaterial.color.copy(coreColor);
      nucleusGlowMaterial.color.copy(primaryColor);
      innerBandMaterial.color.copy(coreColor);
      spokeMaterial.color.copy(secondaryColor);
      spokeMaterial.opacity = 0.07 + activity * 0.14;
      orbitAccentMaterial.color.copy(coreColor);
      orbitAccentMaterial.opacity = ringOpacity * 0.96;
      nodeMaterial.color.copy(coreColor);
      nodeMaterial.opacity = Math.min(1, 0.68 + activity * 0.3);
      nodeHaloMaterial.color.copy(primaryColor);
      nodeHaloMaterial.opacity = 0.12 + activity * 0.2;
      shardMaterial.color.copy(secondaryColor);
      shardMaterial.opacity = 0.38 + activity * 0.38;

      orbitBaseMaterials.forEach((material) => {
        material.color.copy(primaryColor);
        material.opacity =
          ringOpacity * (material.userData.opacityScale as number);
      });

      outerParticleMaterial.color.copy(primaryColor);
      outerParticleMaterial.opacity = particleOpacity;
      outerParticleMaterial.size = 0.032 + activity * 0.014;
      innerParticleMaterial.color.copy(coreColor);
      innerParticleMaterial.opacity = Math.min(1, particleOpacity * 1.12);
      innerParticleMaterial.size = 0.038 + activity * 0.015;
      keyLight.color.copy(coreColor);
      keyLight.intensity = 5.5 + activity * 4.5;
      rimLight.color.copy(primaryColor);
      rimLight.intensity = 4.5 + activity * 4.2;

      const rhythm = speaking ? 7.2 : stateRef.current === "listening" ? 3.8 : 2.15;
      const pulse = 1 + Math.sin(elapsed * rhythm) * pulseAmount;
      coreGroup.scale.setScalar(pulse);
      nucleus.scale.setScalar(
        0.94 + Math.sin(elapsed * (speaking ? 8.4 : 3.1)) * pulseAmount * 1.8,
      );
      nucleusGlow.scale.setScalar(
        1 + Math.sin(elapsed * (speaking ? 6.2 : 2.4)) * pulseAmount * 2.4,
      );
      nucleusGlowMaterial.opacity =
        0.2 + activity * 0.18 + Math.sin(elapsed * 2.8) * 0.05;

      root.position.x = Math.sin(elapsed * 23) * jitter;
      root.position.y = Math.sin(elapsed * 19 + 0.8) * jitter * 0.7;

      if (!reducedMotion) {
        root.rotation.y += delta * (0.055 + rotationSpeed * 0.055);
        energyShell.rotation.y += delta * (0.15 + rotationSpeed * 0.2);
        energyShell.rotation.x = Math.sin(elapsed * 0.31) * 0.1;
        glassShell.rotation.y -= delta * (0.08 + rotationSpeed * 0.11);
        wireframe.rotation.y -= delta * (0.2 + rotationSpeed * 0.22);
        wireframe.rotation.z += delta * (0.09 + rotationSpeed * 0.08);
        outerCage.rotation.y += delta * (0.025 + rotationSpeed * 0.04);
        outerCage.rotation.x = Math.sin(elapsed * 0.12) * 0.14;
        spokeGroup.rotation.y -= delta * (0.12 + rotationSpeed * 0.18);
        spokeGroup.rotation.z += delta * 0.05;

        innerBands.forEach((band, index) => {
          const direction = index % 2 === 0 ? 1 : -1;
          band.rotation.z +=
            delta * direction * (0.28 + rotationSpeed * (0.18 + index * 0.05));
        });

        orbitRecords.forEach((orbit, index) => {
          orbit.group.rotation.z +=
            delta * orbit.direction * orbit.speed * rotationSpeed;
          orbit.group.rotation.y +=
            Math.sin(elapsed * 0.21 + index) * delta * 0.012;
        });

        particleSystem.rotation.y += delta * (0.035 + rotationSpeed * 0.07);
        outerParticles.rotation.x = Math.sin(elapsed * 0.14) * 0.15;
        outerParticles.rotation.z -= delta * 0.025 * rotationSpeed;
        innerParticles.rotation.y -= delta * (0.12 + rotationSpeed * 0.12);
        innerParticles.rotation.x += delta * 0.035;
      }

      renderer.render(scene, camera);
    };

    animate();

    return () => {
      disposed = true;
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();

      const geometries = new Set<THREE.BufferGeometry>();
      const materials = new Set<THREE.Material>();
      scene.traverse((object) => {
        const renderable = object as THREE.Mesh | THREE.Points;
        if (renderable.geometry) geometries.add(renderable.geometry);
        if (renderable.material) {
          const objectMaterials = Array.isArray(renderable.material)
            ? renderable.material
            : [renderable.material];
          objectMaterials.forEach((material) => materials.add(material));
        }
      });

      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      particleTexture?.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, []);

  return (
    <div
      className={`orb-backdrop orb-backdrop-${state}`}
      style={cssVariables}
      aria-label={visualState.ariaLabel}
      role="status"
    >
      <div
        className={`orb-wrap ${visualState.cssClass} ${
          isSpeaking ? "orb-voice-active" : ""
        }`}
      >
        <div className="orb-energy-field" aria-hidden="true" />
        <div
          ref={mountRef}
          className="orb-wireframe-mount"
          aria-hidden="true"
        />
        <div className="orb-state-readout" aria-hidden="true">
          <span /> {visualState.label}
        </div>
      </div>
    </div>
  );
}