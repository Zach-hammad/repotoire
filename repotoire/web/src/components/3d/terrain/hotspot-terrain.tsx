'use client';

import * as React from 'react';
import { useRef, useMemo, Suspense } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Text, Billboard } from '@react-three/drei';
import * as THREE from 'three';

interface FileHotspot {
  file_path: string;
  finding_count: number;
  severity_breakdown: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
}

interface HotspotTerrainProps {
  /** File hotspot data */
  hotspots: FileHotspot[];
  /** Maximum number of files to display */
  maxFiles?: number;
  /** Height of the visualization */
  height?: number;
  /** Callback when a file marker is clicked */
  onFileClick?: (hotspot: FileHotspot) => void;
  className?: string;
}

// Calculate severity weight for elevation
function getSeverityWeight(breakdown: FileHotspot['severity_breakdown']): number {
  return (
    breakdown.critical * 4 +
    breakdown.high * 3 +
    breakdown.medium * 2 +
    breakdown.low * 1
  );
}

// Get color based on severity
function getSeverityColor(breakdown: FileHotspot['severity_breakdown']): string {
  if (breakdown.critical > 0) return '#ef4444';
  if (breakdown.high > 0) return '#f97316';
  if (breakdown.medium > 0) return '#eab308';
  return '#22c55e';
}

// Create terrain mesh
function TerrainMesh({ hotspots, maxFiles }: { hotspots: FileHotspot[]; maxFiles: number }) {
  const meshRef = useRef<THREE.Mesh>(null);

  // Take top N files by severity
  const topHotspots = useMemo(() => {
    return [...hotspots]
      .sort((a, b) => getSeverityWeight(b.severity_breakdown) - getSeverityWeight(a.severity_breakdown))
      .slice(0, maxFiles);
  }, [hotspots, maxFiles]);

  // Create heightmap
  const { geometry, maxHeight } = useMemo(() => {
    const size = 20;
    const segments = 50;
    const geo = new THREE.PlaneGeometry(size, size, segments, segments);

    // Create elevation data
    const positions = geo.attributes.position.array as Float32Array;
    const maxWeight = Math.max(...topHotspots.map((h) => getSeverityWeight(h.severity_breakdown)));
    let maxH = 0;

    // Map hotspots to grid positions
    const hotspotGrid = new Map<string, number>();
    topHotspots.forEach((hotspot, i) => {
      const gridX = Math.floor((i % 10) * (segments / 10));
      const gridY = Math.floor(Math.floor(i / 10) * (segments / 10));
      const weight = getSeverityWeight(hotspot.severity_breakdown);
      hotspotGrid.set(`${gridX},${gridY}`, weight / maxWeight);
    });

    // Apply elevations with gaussian spread
    for (let i = 0; i <= segments; i++) {
      for (let j = 0; j <= segments; j++) {
        const idx = (i * (segments + 1) + j) * 3 + 2; // z component
        let elevation = 0;

        // Add contribution from nearby hotspots
        hotspotGrid.forEach((weight, key) => {
          const [gx, gy] = key.split(',').map(Number);
          const dist = Math.sqrt((i - gx) ** 2 + (j - gy) ** 2);
          const falloff = Math.exp(-(dist ** 2) / 50);
          elevation += weight * falloff * 3;
        });

        positions[idx] = elevation;
        maxH = Math.max(maxH, elevation);
      }
    }

    geo.computeVertexNormals();
    return { geometry: geo, maxHeight: maxH };
  }, [topHotspots]);

  // Animate gentle wave
  useFrame((state) => {
    if (meshRef.current) {
      const positions = meshRef.current.geometry.attributes.position.array as Float32Array;
      const time = state.clock.elapsedTime;

      // Very subtle wave animation
      for (let i = 0; i < positions.length; i += 3) {
        const x = positions[i];
        const y = positions[i + 1];
        const baseZ = positions[i + 2];
        positions[i + 2] = baseZ + Math.sin(x * 0.5 + time * 0.5) * Math.cos(y * 0.5 + time * 0.3) * 0.02;
      }

      meshRef.current.geometry.attributes.position.needsUpdate = true;
    }
  });

  return (
    <mesh ref={meshRef} geometry={geometry} rotation={[-Math.PI / 2, 0, 0]} position={[0, -1, 0]}>
      <meshStandardMaterial
        vertexColors={false}
        side={THREE.DoubleSide}
        metalness={0.2}
        roughness={0.8}
      >
        {/* Use shader for gradient coloring */}
      </meshStandardMaterial>
    </mesh>
  );
}

// File markers on the terrain
interface FileMarkersProps {
  hotspots: FileHotspot[];
  maxFiles: number;
  onFileClick?: (hotspot: FileHotspot) => void;
}

function FileMarkers({ hotspots, maxFiles, onFileClick }: FileMarkersProps) {
  const topHotspots = useMemo(() => {
    return [...hotspots]
      .sort((a, b) => getSeverityWeight(b.severity_breakdown) - getSeverityWeight(a.severity_breakdown))
      .slice(0, maxFiles);
  }, [hotspots, maxFiles]);

  const maxWeight = Math.max(...topHotspots.map((h) => getSeverityWeight(h.severity_breakdown)));

  return (
    <group>
      {topHotspots.map((hotspot, i) => {
        const x = ((i % 10) - 4.5) * 2;
        const z = (Math.floor(i / 10) - 2.5) * 2;
        const weight = getSeverityWeight(hotspot.severity_breakdown);
        const height = (weight / maxWeight) * 3 + 0.5;
        const color = getSeverityColor(hotspot.severity_breakdown);
        const fileName = hotspot.file_path.split('/').pop() || hotspot.file_path;

        return (
          <group key={hotspot.file_path} position={[x, height / 2 - 0.5, z]}>
            {/* Marker pillar */}
            <mesh
              onClick={() => onFileClick?.(hotspot)}
              onPointerEnter={() => (document.body.style.cursor = 'pointer')}
              onPointerLeave={() => (document.body.style.cursor = 'auto')}
            >
              <cylinderGeometry args={[0.15, 0.2, height, 8]} />
              <meshStandardMaterial
                color={color}
                emissive={color}
                emissiveIntensity={0.3}
                metalness={0.5}
                roughness={0.3}
              />
            </mesh>

            {/* File name label */}
            <Billboard
              follow
              lockX={false}
              lockY={false}
              lockZ={false}
              position={[0, height / 2 + 0.3, 0]}
            >
              <Text fontSize={0.2} color="white" anchorX="center" anchorY="bottom">
                {fileName.length > 15 ? fileName.slice(0, 12) + '...' : fileName}
              </Text>
            </Billboard>

            {/* Finding count badge */}
            <mesh position={[0, height / 2 + 0.1, 0]}>
              <sphereGeometry args={[0.15, 16, 16]} />
              <meshStandardMaterial color="#ffffff" />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}

// Grid plane
function GridPlane() {
  return (
    <gridHelper
      args={[20, 20, '#374151', '#1f2937']}
      position={[0, -1.01, 0]}
      rotation={[0, 0, 0]}
    />
  );
}

function Scene({
  hotspots,
  maxFiles,
  onFileClick,
}: {
  hotspots: FileHotspot[];
  maxFiles: number;
  onFileClick?: (hotspot: FileHotspot) => void;
}) {
  return (
    <>
      <ambientLight intensity={0.4} />
      <directionalLight position={[10, 20, 10]} intensity={0.8} castShadow />
      <pointLight position={[-10, 10, -10]} intensity={0.4} color="#8b5cf6" />

      <GridPlane />
      <TerrainMesh hotspots={hotspots} maxFiles={maxFiles} />
      <FileMarkers hotspots={hotspots} maxFiles={maxFiles} onFileClick={onFileClick} />
    </>
  );
}

/**
 * 3D Hotspot Terrain visualization - Shows file severity as 3D elevation.
 *
 * @example
 * ```tsx
 * <HotspotTerrain
 *   hotspots={[
 *     { file_path: 'auth.py', finding_count: 5, severity_breakdown: { critical: 2, high: 2, medium: 1, low: 0 } },
 *   ]}
 *   onFileClick={(hotspot) => console.log(hotspot)}
 * />
 * ```
 */
export function HotspotTerrain({
  hotspots,
  maxFiles = 50,
  height = 400,
  onFileClick,
  className,
}: HotspotTerrainProps) {
  // Check for reduced motion preference
  const [prefersReducedMotion, setPrefersReducedMotion] = React.useState(false);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  if (prefersReducedMotion || hotspots.length === 0) {
    return (
      <HotspotTerrainFallback
        hotspots={hotspots}
        height={height}
        className={className}
      />
    );
  }

  return (
    <div className={`relative ${className}`} style={{ height }}>
      <Canvas
        dpr={[1, 2]}
        camera={{ position: [10, 10, 10], fov: 50 }}
        style={{ background: 'transparent' }}
        gl={{ alpha: true, antialias: true }}
        shadows
      >
        <Suspense fallback={null}>
          <Scene hotspots={hotspots} maxFiles={maxFiles} onFileClick={onFileClick} />
          <OrbitControls
            enableZoom
            enablePan
            enableRotate
            minDistance={5}
            maxDistance={30}
            maxPolarAngle={Math.PI / 2.2}
          />
        </Suspense>
      </Canvas>

      {/* Legend */}
      <div className="absolute bottom-4 left-4 p-3 rounded-lg bg-card/80 backdrop-blur-sm border border-border/50 text-xs">
        <div className="font-medium mb-2">Severity</div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-red-500" />
            <span>Critical</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-orange-500" />
            <span>High</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-yellow-500" />
            <span>Medium</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-green-500" />
            <span>Low</span>
          </div>
        </div>
        <div className="mt-2 pt-2 border-t border-border/50 text-muted-foreground">
          Height = Severity weight
        </div>
      </div>
    </div>
  );
}

/**
 * Fallback 2D version
 */
function HotspotTerrainFallback({
  hotspots,
  height,
  className,
}: {
  hotspots: FileHotspot[];
  height: number;
  className?: string;
}) {
  const topHotspots = hotspots
    .sort((a, b) => getSeverityWeight(b.severity_breakdown) - getSeverityWeight(a.severity_breakdown))
    .slice(0, 10);

  return (
    <div className={`rounded-lg bg-muted/20 p-4 ${className}`} style={{ height }}>
      <h4 className="font-medium mb-3">File Hotspots</h4>
      <div className="space-y-2">
        {topHotspots.map((hotspot) => {
          const color = getSeverityColor(hotspot.severity_breakdown);
          const weight = getSeverityWeight(hotspot.severity_breakdown);
          const maxWeight = getSeverityWeight(topHotspots[0].severity_breakdown);

          return (
            <div key={hotspot.file_path} className="flex items-center gap-2">
              <div
                className="h-2 rounded-full"
                style={{
                  backgroundColor: color,
                  width: `${(weight / maxWeight) * 100}%`,
                  minWidth: '20px',
                }}
              />
              <span className="text-xs truncate flex-1">
                {hotspot.file_path.split('/').pop()}
              </span>
              <span className="text-xs text-muted-foreground">{hotspot.finding_count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export type { FileHotspot };
