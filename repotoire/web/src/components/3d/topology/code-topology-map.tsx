'use client';

import * as React from 'react';
import { useRef, useMemo, useState, Suspense, useCallback } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Line, Html, Billboard, Text } from '@react-three/drei';
import * as THREE from 'three';

// Types for topology data
interface TopologyNode {
  id: string;
  label: string;
  type: 'file' | 'module' | 'class' | 'function';
  healthScore: number;
  findingCount: number;
  position?: [number, number, number];
}

interface TopologyEdge {
  source: string;
  target: string;
  type: 'imports' | 'calls' | 'inherits' | 'uses';
}

interface CodeTopologyMapProps {
  /** Node data */
  nodes: TopologyNode[];
  /** Edge data */
  edges: TopologyEdge[];
  /** Currently selected node ID */
  selectedNode?: string | null;
  /** Callback when a node is clicked */
  onNodeClick?: (node: TopologyNode) => void;
  /** Callback when a node is hovered */
  onNodeHover?: (node: TopologyNode | null) => void;
  /** Height of the visualization */
  height?: number;
  className?: string;
}

// Color based on health score
function getNodeColor(healthScore: number): string {
  if (healthScore >= 80) return '#22c55e';
  if (healthScore >= 60) return '#eab308';
  return '#ef4444';
}

// Node type colors
const nodeTypeColors = {
  file: '#a855f7',
  module: '#3b82f6',
  class: '#22d3ee',
  function: '#10b981',
};

// Edge type colors
const edgeTypeColors = {
  imports: '#64748b',
  calls: '#22d3ee',
  inherits: '#a855f7',
  uses: '#f59e0b',
};

// Simple force-directed layout
function useForceLayout(nodes: TopologyNode[], edges: TopologyEdge[]) {
  return useMemo(() => {
    const positioned = nodes.map((node, i) => {
      // Simple circular layout with some randomness
      const angle = (i / nodes.length) * Math.PI * 2;
      const radius = 5 + Math.random() * 3;
      return {
        ...node,
        position: [
          Math.cos(angle) * radius + (Math.random() - 0.5) * 2,
          (Math.random() - 0.5) * 4,
          Math.sin(angle) * radius + (Math.random() - 0.5) * 2,
        ] as [number, number, number],
      };
    });

    return positioned;
  }, [nodes, edges]);
}

interface NodeMeshProps {
  node: TopologyNode & { position: [number, number, number] };
  isSelected: boolean;
  isHovered: boolean;
  onClick: () => void;
  onHover: (hovered: boolean) => void;
}

function NodeMesh({ node, isSelected, isHovered, onClick, onHover }: NodeMeshProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const color = getNodeColor(node.healthScore);
  const size = 0.3 + Math.log(node.findingCount + 1) * 0.1;

  useFrame((state) => {
    if (meshRef.current) {
      // Pulse effect for selected node
      if (isSelected) {
        const pulse = Math.sin(state.clock.elapsedTime * 3) * 0.1 + 1;
        meshRef.current.scale.setScalar(size * pulse);
      }
    }
  });

  return (
    <group position={node.position}>
      <mesh
        ref={meshRef}
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        onPointerEnter={(e) => {
          e.stopPropagation();
          onHover(true);
          document.body.style.cursor = 'pointer';
        }}
        onPointerLeave={(e) => {
          e.stopPropagation();
          onHover(false);
          document.body.style.cursor = 'auto';
        }}
        scale={size}
      >
        <sphereGeometry args={[1, 16, 16]} />
        <meshStandardMaterial
          color={isSelected ? '#ffffff' : color}
          emissive={color}
          emissiveIntensity={isHovered || isSelected ? 0.5 : 0.2}
          metalness={0.3}
          roughness={0.4}
        />
      </mesh>

      {/* Label */}
      {(isHovered || isSelected) && (
        <Billboard follow lockX={false} lockY={false} lockZ={false}>
          <Text
            position={[0, size + 0.3, 0]}
            fontSize={0.25}
            color="white"
            anchorX="center"
            anchorY="bottom"
            outlineWidth={0.02}
            outlineColor="#000000"
          >
            {node.label}
          </Text>
        </Billboard>
      )}

      {/* Type indicator ring */}
      <mesh rotation={[Math.PI / 2, 0, 0]} scale={size * 1.3}>
        <ringGeometry args={[0.9, 1, 32]} />
        <meshBasicMaterial
          color={nodeTypeColors[node.type]}
          transparent
          opacity={0.5}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  );
}

interface EdgeLineProps {
  source: [number, number, number];
  target: [number, number, number];
  type: TopologyEdge['type'];
  isHighlighted: boolean;
}

function EdgeLine({ source, target, type, isHighlighted }: EdgeLineProps) {
  const color = edgeTypeColors[type];

  return (
    <Line
      points={[source, target]}
      color={color}
      lineWidth={isHighlighted ? 2 : 1}
      transparent
      opacity={isHighlighted ? 0.8 : 0.3}
      dashed={type === 'imports'}
      dashSize={0.5}
      gapSize={0.2}
    />
  );
}

function Scene({
  nodes,
  edges,
  selectedNode,
  onNodeClick,
  onNodeHover,
}: {
  nodes: (TopologyNode & { position: [number, number, number] })[];
  edges: TopologyEdge[];
  selectedNode?: string | null;
  onNodeClick?: (node: TopologyNode) => void;
  onNodeHover?: (node: TopologyNode | null) => void;
}) {
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);

  // Create position lookup
  const nodePositions = useMemo(() => {
    const map = new Map<string, [number, number, number]>();
    nodes.forEach((node) => {
      map.set(node.id, node.position);
    });
    return map;
  }, [nodes]);

  // Get edges connected to selected/hovered node
  const highlightedEdges = useMemo(() => {
    const active = selectedNode || hoveredNode;
    if (!active) return new Set<string>();
    return new Set(
      edges
        .filter((e) => e.source === active || e.target === active)
        .map((e) => `${e.source}-${e.target}`)
    );
  }, [edges, selectedNode, hoveredNode]);

  return (
    <>
      <ambientLight intensity={0.4} />
      <pointLight position={[10, 10, 10]} intensity={0.8} />
      <pointLight position={[-10, -10, -10]} intensity={0.4} color="#8b5cf6" />

      {/* Edges */}
      {edges.map((edge) => {
        const sourcePos = nodePositions.get(edge.source);
        const targetPos = nodePositions.get(edge.target);
        if (!sourcePos || !targetPos) return null;

        return (
          <EdgeLine
            key={`${edge.source}-${edge.target}`}
            source={sourcePos}
            target={targetPos}
            type={edge.type}
            isHighlighted={highlightedEdges.has(`${edge.source}-${edge.target}`)}
          />
        );
      })}

      {/* Nodes */}
      {nodes.map((node) => (
        <NodeMesh
          key={node.id}
          node={node}
          isSelected={selectedNode === node.id}
          isHovered={hoveredNode === node.id}
          onClick={() => onNodeClick?.(node)}
          onHover={(hovered) => {
            setHoveredNode(hovered ? node.id : null);
            onNodeHover?.(hovered ? node : null);
          }}
        />
      ))}
    </>
  );
}

/**
 * 3D Code Topology Map - Interactive graph visualization of code dependencies.
 *
 * @example
 * ```tsx
 * <CodeTopologyMap
 *   nodes={[
 *     { id: '1', label: 'auth.py', type: 'file', healthScore: 85, findingCount: 3 },
 *     { id: '2', label: 'User', type: 'class', healthScore: 92, findingCount: 1 },
 *   ]}
 *   edges={[
 *     { source: '1', target: '2', type: 'imports' }
 *   ]}
 *   onNodeClick={(node) => console.log(node)}
 * />
 * ```
 */
export function CodeTopologyMap({
  nodes,
  edges,
  selectedNode,
  onNodeClick,
  onNodeHover,
  height = 500,
  className,
}: CodeTopologyMapProps) {
  const positionedNodes = useForceLayout(nodes, edges);

  // Check for reduced motion preference
  const [prefersReducedMotion, setPrefersReducedMotion] = React.useState(false);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  if (prefersReducedMotion || nodes.length === 0) {
    return (
      <CodeTopologyMapFallback
        nodes={nodes}
        edges={edges}
        height={height}
        className={className}
      />
    );
  }

  return (
    <div className={className} style={{ height }}>
      <Canvas
        dpr={[1, 2]}
        camera={{ position: [0, 5, 15], fov: 50 }}
        style={{ background: 'transparent' }}
        gl={{ alpha: true, antialias: true }}
      >
        <Suspense fallback={null}>
          <Scene
            nodes={positionedNodes}
            edges={edges}
            selectedNode={selectedNode}
            onNodeClick={onNodeClick}
            onNodeHover={onNodeHover}
          />
          <OrbitControls
            enableZoom
            enablePan
            enableRotate
            minDistance={5}
            maxDistance={50}
            dampingFactor={0.05}
          />
        </Suspense>
      </Canvas>

      {/* Legend */}
      <Legend />
    </div>
  );
}

function Legend() {
  return (
    <div className="absolute bottom-4 left-4 p-3 rounded-lg bg-card/80 backdrop-blur-sm border border-border/50 text-xs">
      <div className="font-medium mb-2">Node Types</div>
      <div className="space-y-1">
        {Object.entries(nodeTypeColors).map(([type, color]) => (
          <div key={type} className="flex items-center gap-2">
            <span
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span className="capitalize">{type}</span>
          </div>
        ))}
      </div>
      <div className="font-medium mt-3 mb-2">Health</div>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-green-500" />
          <span>Good (80+)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-yellow-500" />
          <span>Fair (60-79)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-red-500" />
          <span>Poor (&lt;60)</span>
        </div>
      </div>
    </div>
  );
}

/**
 * Fallback 2D version
 */
function CodeTopologyMapFallback({
  nodes,
  edges,
  height,
  className,
}: {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  height: number;
  className?: string;
}) {
  return (
    <div
      className={`flex items-center justify-center bg-muted/20 rounded-lg ${className}`}
      style={{ height }}
    >
      <div className="text-center text-muted-foreground">
        <p className="text-sm">3D visualization requires WebGL</p>
        <p className="text-xs mt-1">{nodes.length} nodes, {edges.length} connections</p>
      </div>
    </div>
  );
}

export type { TopologyNode, TopologyEdge };
