'use client';

import * as React from 'react';
import { useRef, useMemo, Suspense } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Text, Billboard, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

interface OrbitalHealthScoreProps {
  /** Health score 0-100 */
  score: number;
  /** Grade letter (A, B, C, D, F) */
  grade?: string;
  /** Category scores */
  categories?: {
    structure: number;
    quality: number;
    architecture: number;
  };
  /** Size of the component */
  size?: 'sm' | 'md' | 'lg';
  /** Enable orbit controls */
  interactive?: boolean;
  className?: string;
}

// Size configuration
const sizeConfig = {
  sm: { width: 150, height: 150 },
  md: { width: 220, height: 220 },
  lg: { width: 300, height: 300 },
};

// Color based on score
function getScoreColor(score: number): string {
  if (score >= 90) return '#22c55e'; // Green - A
  if (score >= 80) return '#84cc16'; // Lime - B
  if (score >= 70) return '#eab308'; // Yellow - C
  if (score >= 60) return '#f97316'; // Orange - D
  return '#ef4444'; // Red - F
}

// Category colors
const categoryColors = {
  structure: '#22d3ee', // Cyan
  quality: '#10b981',   // Green
  architecture: '#f59e0b', // Amber
};

interface ScoreRingProps {
  score: number;
  radius: number;
  tubeRadius: number;
  color: string;
  rotationSpeed?: number;
  tiltX?: number;
  tiltZ?: number;
}

function ScoreRing({
  score,
  radius,
  tubeRadius,
  color,
  rotationSpeed = 0.5,
  tiltX = 0,
  tiltZ = 0,
}: ScoreRingProps) {
  const ringRef = useRef<THREE.Mesh>(null);

  // Create partial torus geometry based on score
  const geometry = useMemo(() => {
    const arc = (score / 100) * Math.PI * 2;
    return new THREE.TorusGeometry(radius, tubeRadius, 16, 100, arc);
  }, [score, radius, tubeRadius]);

  useFrame((state, delta) => {
    if (ringRef.current) {
      ringRef.current.rotation.y += delta * rotationSpeed;
    }
  });

  return (
    <mesh ref={ringRef} geometry={geometry} rotation={[tiltX, 0, tiltZ]}>
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={0.3}
        metalness={0.5}
        roughness={0.3}
      />
    </mesh>
  );
}

interface CenterSphereProps {
  score: number;
  grade?: string;
}

function CenterSphere({ score, grade }: CenterSphereProps) {
  const sphereRef = useRef<THREE.Mesh>(null);
  const color = getScoreColor(score);

  useFrame((state) => {
    if (sphereRef.current) {
      // Pulse effect
      const pulse = Math.sin(state.clock.elapsedTime * 2) * 0.05 + 1;
      sphereRef.current.scale.setScalar(pulse);
    }
  });

  return (
    <group>
      {/* Inner sphere */}
      <mesh ref={sphereRef}>
        <sphereGeometry args={[0.8, 32, 32]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.4}
          metalness={0.3}
          roughness={0.4}
          transparent
          opacity={0.9}
        />
      </mesh>

      {/* Grade text */}
      {grade && (
        <Billboard follow lockX={false} lockY={false} lockZ={false}>
          <Text
            fontSize={0.6}
            color="white"
            anchorX="center"
            anchorY="middle"
            font="/fonts/inter-bold.woff"
          >
            {grade}
          </Text>
        </Billboard>
      )}
    </group>
  );
}

interface CategoryOrbitsProps {
  categories: {
    structure: number;
    quality: number;
    architecture: number;
  };
}

function CategoryOrbits({ categories }: CategoryOrbitsProps) {
  return (
    <group>
      {/* Structure orbit - horizontal */}
      <ScoreRing
        score={categories.structure}
        radius={1.8}
        tubeRadius={0.05}
        color={categoryColors.structure}
        rotationSpeed={0.3}
        tiltX={Math.PI / 2}
      />

      {/* Quality orbit - tilted 45deg */}
      <ScoreRing
        score={categories.quality}
        radius={2.2}
        tubeRadius={0.05}
        color={categoryColors.quality}
        rotationSpeed={0.25}
        tiltX={Math.PI / 4}
        tiltZ={Math.PI / 6}
      />

      {/* Architecture orbit - tilted opposite */}
      <ScoreRing
        score={categories.architecture}
        radius={2.6}
        tubeRadius={0.05}
        color={categoryColors.architecture}
        rotationSpeed={0.2}
        tiltX={-Math.PI / 4}
        tiltZ={-Math.PI / 6}
      />
    </group>
  );
}

function Scene({
  score,
  grade,
  categories,
}: {
  score: number;
  grade?: string;
  categories?: { structure: number; quality: number; architecture: number };
}) {
  return (
    <>
      <ambientLight intensity={0.5} />
      <pointLight position={[10, 10, 10]} intensity={1} />
      <pointLight position={[-10, -10, -10]} intensity={0.5} color="#8b5cf6" />

      {/* Main score ring */}
      <ScoreRing
        score={score}
        radius={1.3}
        tubeRadius={0.12}
        color={getScoreColor(score)}
        rotationSpeed={0.5}
      />

      {/* Center sphere with grade */}
      <CenterSphere score={score} grade={grade} />

      {/* Category orbits */}
      {categories && <CategoryOrbits categories={categories} />}
    </>
  );
}

/**
 * 3D Orbital Health Score visualization using React Three Fiber.
 * Displays health score as a rotating ring with optional category orbits.
 *
 * @example
 * ```tsx
 * <OrbitalHealthScore
 *   score={85}
 *   grade="B"
 *   categories={{ structure: 80, quality: 90, architecture: 85 }}
 *   size="md"
 *   interactive
 * />
 * ```
 */
export function OrbitalHealthScore({
  score,
  grade,
  categories,
  size = 'md',
  interactive = false,
  className,
}: OrbitalHealthScoreProps) {
  const { width, height } = sizeConfig[size];

  // Check for reduced motion preference
  const [prefersReducedMotion, setPrefersReducedMotion] = React.useState(false);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  // Fallback for reduced motion
  if (prefersReducedMotion) {
    return <OrbitalHealthScoreFallback score={score} grade={grade} size={size} className={className} />;
  }

  return (
    <div className={className} style={{ width, height }}>
      <Canvas
        dpr={[1, 2]}
        camera={{ position: [0, 0, 6], fov: 50 }}
        style={{ background: 'transparent' }}
        gl={{ alpha: true, antialias: true }}
      >
        <Suspense fallback={null}>
          <Scene score={score} grade={grade} categories={categories} />
          {interactive && <OrbitControls enableZoom={false} enablePan={false} />}
        </Suspense>
      </Canvas>
    </div>
  );
}

/**
 * Fallback 2D version for reduced motion or non-WebGL browsers
 */
function OrbitalHealthScoreFallback({
  score,
  grade,
  size,
  className,
}: {
  score: number;
  grade?: string;
  size: 'sm' | 'md' | 'lg';
  className?: string;
}) {
  const { width, height } = sizeConfig[size];
  const color = getScoreColor(score);

  return (
    <div
      className={className}
      style={{
        width,
        height,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        position: 'relative',
      }}
    >
      {/* Outer ring */}
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ position: 'absolute' }}
      >
        <circle
          cx={width / 2}
          cy={height / 2}
          r={width / 2 - 20}
          fill="none"
          stroke="currentColor"
          strokeWidth="4"
          opacity={0.2}
        />
        <circle
          cx={width / 2}
          cy={height / 2}
          r={width / 2 - 20}
          fill="none"
          stroke={color}
          strokeWidth="4"
          strokeDasharray={`${(score / 100) * Math.PI * (width - 40)} ${Math.PI * (width - 40)}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${width / 2} ${height / 2})`}
        />
      </svg>

      {/* Center content */}
      <div className="text-center">
        <div className="text-3xl font-bold" style={{ color }}>
          {grade || score}
        </div>
        {grade && <div className="text-sm text-muted-foreground">{score}%</div>}
      </div>
    </div>
  );
}

export { OrbitalHealthScoreFallback };
