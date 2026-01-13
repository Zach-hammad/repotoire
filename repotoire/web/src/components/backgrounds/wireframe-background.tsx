'use client';

import * as React from 'react';
import { useRef, useMemo, Suspense, useState, useEffect } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Points, PointMaterial, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { useBackgroundState, type BackgroundState } from './background-provider';
import { useVisualSettings } from '@/lib/use-visual-settings';

// Color mapping for different states (using hex - Three.js doesn't support oklch)
const stateColors: Record<BackgroundState, THREE.Color> = {
  healthy: new THREE.Color('#22d3ee'),    // Cyan
  warning: new THREE.Color('#f59e0b'),    // Orange
  critical: new THREE.Color('#ef4444'),   // Red
  neutral: new THREE.Color('#a855f7'),    // Purple
  analyzing: new THREE.Color('#c084fc'),  // Light purple
};

// Hex color strings for PointMaterial (which expects string colors)
const stateColorHex: Record<BackgroundState, string> = {
  healthy: '#22d3ee',    // Cyan
  warning: '#f59e0b',    // Orange
  critical: '#ef4444',   // Red
  neutral: '#a855f7',    // Purple
  analyzing: '#c084fc',  // Light purple
};

interface WireframeMeshProps {
  count?: number;
  radius?: number;
}

function WireframeMesh({ count = 2000, radius = 10 }: WireframeMeshProps) {
  const pointsRef = useRef<THREE.Points>(null);
  const { state, intensity, pulseActive } = useBackgroundState();

  // Generate random points in a sphere
  const [positions, initialPositions] = useMemo(() => {
    const pos = new Float32Array(count * 3);
    const initial = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      // Spherical distribution
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = radius * Math.cbrt(Math.random()); // Cube root for uniform volume distribution

      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.sin(phi) * Math.sin(theta);
      const z = r * Math.cos(phi);

      pos[i * 3] = x;
      pos[i * 3 + 1] = y;
      pos[i * 3 + 2] = z;

      initial[i * 3] = x;
      initial[i * 3 + 1] = y;
      initial[i * 3 + 2] = z;
    }

    return [pos, initial];
  }, [count, radius]);

  // Animate the points
  useFrame((state) => {
    if (!pointsRef.current) return;

    const time = state.clock.elapsedTime;
    const positions = pointsRef.current.geometry.attributes.position.array as Float32Array;

    // Gentle wave animation
    for (let i = 0; i < count; i++) {
      const ix = i * 3;
      const iy = i * 3 + 1;
      const iz = i * 3 + 2;

      // Get initial position
      const initX = initialPositions[ix];
      const initY = initialPositions[iy];
      const initZ = initialPositions[iz];

      // Calculate wave offset based on position and time
      const waveX = Math.sin(time * 0.5 + initY * 0.5) * 0.1 * intensity;
      const waveY = Math.cos(time * 0.3 + initX * 0.5) * 0.1 * intensity;
      const waveZ = Math.sin(time * 0.4 + initZ * 0.5) * 0.1 * intensity;

      // Pulse effect for critical state
      const pulse = pulseActive ? Math.sin(time * 2) * 0.05 + 1 : 1;

      positions[ix] = initX * pulse + waveX;
      positions[iy] = initY * pulse + waveY;
      positions[iz] = initZ * pulse + waveZ;
    }

    pointsRef.current.geometry.attributes.position.needsUpdate = true;

    // Slow rotation
    pointsRef.current.rotation.y += 0.001 * intensity;
    pointsRef.current.rotation.x += 0.0005 * intensity;
  });

  // Get color based on state
  const color = useMemo(() => stateColorHex[state], [state]);

  return (
    <Points ref={pointsRef} positions={positions} stride={3} frustumCulled={false}>
      <PointMaterial
        transparent
        color={color}
        size={0.02}
        sizeAttenuation={true}
        depthWrite={false}
        opacity={0.6}
        blending={THREE.AdditiveBlending}
      />
    </Points>
  );
}

interface GridMeshProps {
  size?: number;
  divisions?: number;
}

function GridMesh({ size = 20, divisions = 40 }: GridMeshProps) {
  const gridRef = useRef<THREE.LineSegments>(null);
  const { state, intensity } = useBackgroundState();

  // Create grid geometry
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const vertices: number[] = [];

    const step = size / divisions;
    const half = size / 2;

    // Create grid lines
    for (let i = 0; i <= divisions; i++) {
      const pos = -half + i * step;

      // Horizontal lines
      vertices.push(-half, 0, pos);
      vertices.push(half, 0, pos);

      // Vertical lines
      vertices.push(pos, 0, -half);
      vertices.push(pos, 0, half);
    }

    geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    return geo;
  }, [size, divisions]);

  // Animate the grid
  useFrame((state) => {
    if (!gridRef.current) return;

    const time = state.clock.elapsedTime;
    const positions = gridRef.current.geometry.attributes.position.array as Float32Array;

    // Wave effect on y-axis
    for (let i = 0; i < positions.length; i += 3) {
      const x = positions[i];
      const z = positions[i + 2];
      positions[i + 1] = Math.sin(x * 0.5 + time * 0.5) * Math.cos(z * 0.5 + time * 0.3) * 0.3 * intensity;
    }

    gridRef.current.geometry.attributes.position.needsUpdate = true;
  });

  const color = useMemo(() => stateColorHex[state], [state]);

  return (
    <lineSegments ref={gridRef} geometry={geometry} position={[0, -3, 0]}>
      <lineBasicMaterial color={color} transparent opacity={0.15} />
    </lineSegments>
  );
}

function Scene() {
  const { camera } = useThree();

  // Set camera position
  React.useEffect(() => {
    camera.position.set(0, 5, 15);
    camera.lookAt(0, 0, 0);
  }, [camera]);

  return (
    <>
      <ambientLight intensity={0.5} />
      <WireframeMesh count={1500} radius={8} />
      <GridMesh size={25} divisions={50} />
    </>
  );
}

interface WireframeBackgroundProps {
  /** Enable orbit controls for debugging */
  debug?: boolean;
  /** Custom class name */
  className?: string;
}

/**
 * Animated 3D wireframe background using React Three Fiber.
 * Responds to background state changes (health score, severity).
 * Respects visual settings from localStorage (enable/disable via settings page).
 *
 * Must be used within a BackgroundProvider.
 *
 * @example
 * ```tsx
 * <BackgroundProvider>
 *   <WireframeBackground />
 *   <main>{children}</main>
 * </BackgroundProvider>
 * ```
 */
export function WireframeBackground({ debug = false, className }: WireframeBackgroundProps) {
  // Check for reduced motion preference
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  // Get visual settings
  const { isAnimatedBackgroundEnabled, isLoaded } = useVisualSettings();

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  // Don't render 3D canvas if:
  // - Reduced motion is preferred
  // - Visual settings are loaded and animated background is disabled
  if (prefersReducedMotion || (isLoaded && !isAnimatedBackgroundEnabled)) {
    return (
      <div className={`wireframe-container ${className || ''}`}>
        <div className="wireframe-fallback absolute inset-0" />
      </div>
    );
  }

  // Don't render anything until settings are loaded (prevents flash)
  if (!isLoaded) {
    return (
      <div className={`wireframe-container ${className || ''}`}>
        <div className="wireframe-fallback absolute inset-0" />
      </div>
    );
  }

  return (
    <div className={`wireframe-container ${className || ''}`}>
      <Canvas
        dpr={[1, 1.5]} // Cap pixel ratio for performance
        camera={{ fov: 50, near: 0.1, far: 1000 }}
        style={{ background: 'transparent' }}
        gl={{ alpha: true, antialias: true }}
      >
        <Suspense fallback={null}>
          <Scene />
          {debug && <OrbitControls enableZoom enablePan enableRotate />}
        </Suspense>
      </Canvas>
    </div>
  );
}

/**
 * Standalone wireframe background that doesn't require BackgroundProvider.
 * Uses a static neutral state.
 */
export function WireframeBackgroundStatic({ className }: { className?: string }) {
  const [prefersReducedMotion, setPrefersReducedMotion] = React.useState(false);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  if (prefersReducedMotion) {
    return (
      <div className={`wireframe-container ${className || ''}`}>
        <div className="wireframe-fallback absolute inset-0" />
      </div>
    );
  }

  return (
    <div className={`wireframe-container ${className || ''}`}>
      <Canvas
        dpr={[1, 1.5]}
        camera={{ fov: 50, near: 0.1, far: 1000, position: [0, 5, 15] }}
        style={{ background: 'transparent' }}
        gl={{ alpha: true, antialias: true }}
      >
        <Suspense fallback={null}>
          <ambientLight intensity={0.5} />
          <StaticWireframeMesh />
          <StaticGridMesh />
        </Suspense>
      </Canvas>
    </div>
  );
}

// Static versions that don't use context
function StaticWireframeMesh() {
  const pointsRef = useRef<THREE.Points>(null);

  const [positions, initialPositions] = useMemo(() => {
    const count = 1500;
    const radius = 8;
    const pos = new Float32Array(count * 3);
    const initial = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = radius * Math.cbrt(Math.random());

      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.sin(phi) * Math.sin(theta);
      const z = r * Math.cos(phi);

      pos[i * 3] = x;
      pos[i * 3 + 1] = y;
      pos[i * 3 + 2] = z;

      initial[i * 3] = x;
      initial[i * 3 + 1] = y;
      initial[i * 3 + 2] = z;
    }

    return [pos, initial];
  }, []);

  useFrame((state) => {
    if (!pointsRef.current) return;

    const time = state.clock.elapsedTime;
    const positions = pointsRef.current.geometry.attributes.position.array as Float32Array;

    for (let i = 0; i < 1500; i++) {
      const ix = i * 3;
      const iy = i * 3 + 1;
      const iz = i * 3 + 2;

      const initX = initialPositions[ix];
      const initY = initialPositions[iy];
      const initZ = initialPositions[iz];

      const waveX = Math.sin(time * 0.5 + initY * 0.5) * 0.05;
      const waveY = Math.cos(time * 0.3 + initX * 0.5) * 0.05;
      const waveZ = Math.sin(time * 0.4 + initZ * 0.5) * 0.05;

      positions[ix] = initX + waveX;
      positions[iy] = initY + waveY;
      positions[iz] = initZ + waveZ;
    }

    pointsRef.current.geometry.attributes.position.needsUpdate = true;
    pointsRef.current.rotation.y += 0.0005;
    pointsRef.current.rotation.x += 0.00025;
  });

  return (
    <Points ref={pointsRef} positions={positions} stride={3} frustumCulled={false}>
      <PointMaterial
        transparent
        color="#a855f7"
        size={0.02}
        sizeAttenuation={true}
        depthWrite={false}
        opacity={0.6}
        blending={THREE.AdditiveBlending}
      />
    </Points>
  );
}

function StaticGridMesh() {
  const gridRef = useRef<THREE.LineSegments>(null);

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const vertices: number[] = [];
    const size = 25;
    const divisions = 50;
    const step = size / divisions;
    const half = size / 2;

    for (let i = 0; i <= divisions; i++) {
      const pos = -half + i * step;
      vertices.push(-half, 0, pos);
      vertices.push(half, 0, pos);
      vertices.push(pos, 0, -half);
      vertices.push(pos, 0, half);
    }

    geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    return geo;
  }, []);

  useFrame((state) => {
    if (!gridRef.current) return;

    const time = state.clock.elapsedTime;
    const positions = gridRef.current.geometry.attributes.position.array as Float32Array;

    for (let i = 0; i < positions.length; i += 3) {
      const x = positions[i];
      const z = positions[i + 2];
      positions[i + 1] = Math.sin(x * 0.5 + time * 0.5) * Math.cos(z * 0.5 + time * 0.3) * 0.15;
    }

    gridRef.current.geometry.attributes.position.needsUpdate = true;
  });

  return (
    <lineSegments ref={gridRef} geometry={geometry} position={[0, -3, 0]}>
      <lineBasicMaterial color="#a855f7" transparent opacity={0.15} />
    </lineSegments>
  );
}
