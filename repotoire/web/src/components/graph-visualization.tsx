"use client"

import { useEffect, useRef } from "react"

interface Node {
  id: string
  x: number
  y: number
  vx: number
  vy: number
  label: string
  type: "component" | "service" | "util" | "issue"
  size: number
}

interface Edge {
  source: string
  target: string
  type: "normal" | "circular" | "warning"
}

export function GraphVisualization() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animationRef = useRef<number | undefined>(undefined)
  const nodesRef = useRef<Node[]>([])
  const edgesRef = useRef<Edge[]>([])
  const initializedRef = useRef(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const resizeCanvas = () => {
      const rect = canvas.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1

      // Set actual canvas dimensions (scaled for retina)
      canvas.width = rect.width * dpr
      canvas.height = rect.height * dpr

      // Reset transform and apply fresh scale
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    resizeCanvas()
    window.addEventListener("resize", resizeCanvas)

    if (!initializedRef.current) {
      const nodeLabels = [
        { label: "AuthService", type: "service" as const },
        { label: "UserController", type: "component" as const },
        { label: "PaymentGateway", type: "service" as const },
        { label: "OrderService", type: "service" as const },
        { label: "DatabaseUtil", type: "util" as const },
        { label: "CacheManager", type: "util" as const },
        { label: "NotificationHub", type: "service" as const },
        { label: "APIRouter", type: "component" as const },
        { label: "CircularDep", type: "issue" as const },
        { label: "LogService", type: "util" as const },
        { label: "ConfigLoader", type: "util" as const },
        { label: "SessionManager", type: "component" as const },
      ]

      const rect = canvas.getBoundingClientRect()
      nodesRef.current = nodeLabels.map((n, i) => ({
        id: `node-${i}`,
        x: Math.random() * (rect.width - 100) + 50,
        y: Math.random() * (rect.height - 100) + 50,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        label: n.label,
        type: n.type,
        size: n.type === "issue" ? 12 : n.type === "service" ? 10 : 8,
      }))

      edgesRef.current = [
        { source: "node-0", target: "node-1", type: "normal" },
        { source: "node-1", target: "node-3", type: "normal" },
        { source: "node-2", target: "node-3", type: "normal" },
        { source: "node-3", target: "node-4", type: "normal" },
        { source: "node-4", target: "node-5", type: "normal" },
        { source: "node-5", target: "node-0", type: "circular" },
        { source: "node-6", target: "node-7", type: "normal" },
        { source: "node-7", target: "node-0", type: "normal" },
        { source: "node-8", target: "node-3", type: "warning" },
        { source: "node-8", target: "node-0", type: "warning" },
        { source: "node-9", target: "node-4", type: "normal" },
        { source: "node-10", target: "node-0", type: "normal" },
        { source: "node-11", target: "node-0", type: "normal" },
        { source: "node-11", target: "node-1", type: "normal" },
      ]

      initializedRef.current = true
    }

    const animate = () => {
      const rect = canvas.getBoundingClientRect()
      ctx.clearRect(0, 0, rect.width, rect.height)

      // Update node positions
      nodesRef.current.forEach((node) => {
        node.x += node.vx
        node.y += node.vy

        // Bounce off edges
        if (node.x < 50 || node.x > rect.width - 50) node.vx *= -1
        if (node.y < 50 || node.y > rect.height - 50) node.vy *= -1

        // Add slight random movement
        node.vx += (Math.random() - 0.5) * 0.02
        node.vy += (Math.random() - 0.5) * 0.02

        // Damping
        node.vx *= 0.99
        node.vy *= 0.99
      })

      // Draw edges
      edgesRef.current.forEach((edge) => {
        const source = nodesRef.current.find((n) => n.id === edge.source)
        const target = nodesRef.current.find((n) => n.id === edge.target)
        if (!source || !target) return

        ctx.beginPath()
        ctx.moveTo(source.x, source.y)
        ctx.lineTo(target.x, target.y)

        if (edge.type === "circular") {
          ctx.strokeStyle = "rgba(239, 68, 68, 0.6)"
          ctx.lineWidth = 2
          ctx.setLineDash([5, 5])
        } else if (edge.type === "warning") {
          ctx.strokeStyle = "rgba(251, 146, 60, 0.5)"
          ctx.lineWidth = 1.5
          ctx.setLineDash([3, 3])
        } else {
          ctx.strokeStyle = "rgba(34, 211, 238, 0.3)"
          ctx.lineWidth = 1
          ctx.setLineDash([])
        }
        ctx.stroke()
        ctx.setLineDash([])
      })

      // Draw nodes
      nodesRef.current.forEach((node) => {
        // Glow effect
        const gradient = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, node.size * 2)
        if (node.type === "issue") {
          gradient.addColorStop(0, "rgba(239, 68, 68, 0.8)")
          gradient.addColorStop(1, "rgba(239, 68, 68, 0)")
        } else if (node.type === "service") {
          gradient.addColorStop(0, "rgba(34, 211, 238, 0.6)")
          gradient.addColorStop(1, "rgba(34, 211, 238, 0)")
        } else {
          gradient.addColorStop(0, "rgba(139, 92, 246, 0.6)")
          gradient.addColorStop(1, "rgba(139, 92, 246, 0)")
        }

        ctx.beginPath()
        ctx.arc(node.x, node.y, node.size * 2, 0, Math.PI * 2)
        ctx.fillStyle = gradient
        ctx.fill()

        // Node circle
        ctx.beginPath()
        ctx.arc(node.x, node.y, node.size, 0, Math.PI * 2)
        if (node.type === "issue") {
          ctx.fillStyle = "#ef4444"
        } else if (node.type === "service") {
          ctx.fillStyle = "#22d3ee"
        } else if (node.type === "component") {
          ctx.fillStyle = "#8b5cf6"
        } else {
          ctx.fillStyle = "#64748b"
        }
        ctx.fill()

        ctx.font = "600 12px system-ui, -apple-system, sans-serif"
        ctx.fillStyle = "rgba(255, 255, 255, 0.9)"
        ctx.textAlign = "center"
        ctx.textBaseline = "top"
        ctx.fillText(node.label, node.x, node.y + node.size + 8)
      })

      animationRef.current = requestAnimationFrame(animate)
    }

    animate()

    return () => {
      window.removeEventListener("resize", resizeCanvas)
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [])

  return (
    <div className="relative w-full h-full bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      <canvas ref={canvasRef} className="w-full h-full" style={{ display: "block" }} />

      {/* Legend */}
      <div className="absolute bottom-4 left-4 flex flex-wrap gap-4 text-xs">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-cyan-500" />
          <span className="text-slate-400">Service</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-violet-500" />
          <span className="text-slate-400">Component</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-red-500" />
          <span className="text-slate-400">Issue Detected</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-8 h-0.5 bg-red-500" style={{ borderStyle: "dashed" }} />
          <span className="text-slate-400">Circular Dependency</span>
        </div>
      </div>

      {/* Issue callout */}
      <div className="absolute top-4 right-4 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 max-w-xs">
        <div className="flex items-center gap-2 text-red-400 text-sm font-medium mb-1">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
          Circular Dependency Found
        </div>
        <p className="text-slate-400 text-xs">
          AuthService → UserController → OrderService → DatabaseUtil → CacheManager → AuthService
        </p>
      </div>
    </div>
  )
}
