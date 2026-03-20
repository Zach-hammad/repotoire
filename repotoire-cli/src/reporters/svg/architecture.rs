//! Layered architecture map SVG generator for module dependencies.

use std::collections::HashMap;
use std::fmt::Write;

use crate::reporters::report_context::{Community, ModuleEdge, ModuleNode};

/// Community background color palette (soft colors at ~10% opacity).
const COMMUNITY_COLORS: &[&str] = &[
    "rgba(99,102,241,0.08)",  // indigo
    "rgba(16,185,129,0.08)",  // emerald
    "rgba(245,158,11,0.08)",  // amber
    "rgba(239,68,68,0.08)",   // red
    "rgba(168,85,247,0.08)",  // purple
    "rgba(6,182,212,0.08)",   // cyan
    "rgba(236,72,153,0.08)",  // pink
    "rgba(132,204,22,0.08)",  // lime
];

const NODE_SPACING_X: f64 = 120.0;
const LAYER_SPACING_Y: f64 = 100.0;
const PADDING: f64 = 60.0;
const MAX_RADIUS: f64 = 30.0;
const MIN_RADIUS: f64 = 8.0;
const MAX_MODULES: usize = 20;

/// Render a layered architecture map as inline SVG.
pub fn render_architecture_map(
    modules: &[ModuleNode],
    edges: &[ModuleEdge],
    communities: &[Community],
) -> String {
    let mut svg = String::with_capacity(2048);

    if modules.is_empty() {
        svg.push_str(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100" style="font-family: sans-serif;"></svg>"#);
        return svg;
    }

    // Cap at MAX_MODULES by LOC, collapse rest into "other"
    let (working_modules, working_edges) = cap_modules(modules, edges);

    // Build adjacency and index maps
    let mod_index: HashMap<&str, usize> = working_modules
        .iter()
        .enumerate()
        .map(|(i, m)| (m.path.as_str(), i))
        .collect();

    // Topological sort via Kahn's algorithm + layer assignment
    let layers = assign_layers(&working_modules, &working_edges, &mod_index);
    let num_layers = layers.iter().copied().max().unwrap_or(0) + 1;

    // Group modules by layer
    let mut layer_members: Vec<Vec<usize>> = vec![vec![]; num_layers];
    for (i, &layer) in layers.iter().enumerate() {
        layer_members[layer].push(i);
    }

    let max_layer_width = layer_members.iter().map(|l| l.len()).max().unwrap_or(1);
    let width = (max_layer_width as f64) * NODE_SPACING_X + PADDING * 2.0;
    let height = (num_layers as f64) * LAYER_SPACING_Y + PADDING * 2.0;

    // Compute positions
    let mut positions: Vec<(f64, f64)> = vec![(0.0, 0.0); working_modules.len()];
    for (layer_idx, members) in layer_members.iter().enumerate() {
        let count = members.len();
        let layer_width = (count as f64) * NODE_SPACING_X;
        let start_x = (width - layer_width) / 2.0 + NODE_SPACING_X / 2.0;
        let y = PADDING + (layer_idx as f64) * LAYER_SPACING_Y + LAYER_SPACING_Y / 2.0;
        for (pos, &mod_idx) in members.iter().enumerate() {
            positions[mod_idx] = (start_x + (pos as f64) * NODE_SPACING_X, y);
        }
    }

    // Compute radii
    let max_loc = working_modules.iter().map(|m| m.loc).max().unwrap_or(1) as f64;
    let radii: Vec<f64> = working_modules
        .iter()
        .map(|m| {
            let ratio = (m.loc as f64 / max_loc).sqrt();
            MIN_RADIUS + ratio * (MAX_RADIUS - MIN_RADIUS)
        })
        .collect();

    // Start SVG
    write!(
        svg,
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0} {height:.0}" width="{width:.0}" height="{height:.0}" style="font-family: sans-serif;">"#,
    )
    .unwrap();

    // Defs: arrowhead marker
    svg.push_str(
        r##"<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>"##,
    );

    // Community backgrounds
    render_community_backgrounds(
        &mut svg,
        communities,
        &working_modules,
        &mod_index,
        &positions,
        &radii,
    );

    // Edges
    for edge in &working_edges {
        let from_idx = mod_index.get(edge.from.as_str());
        let to_idx = mod_index.get(edge.to.as_str());
        if let (Some(&fi), Some(&ti)) = (from_idx, to_idx) {
            let (x1, y1) = positions[fi];
            let (x2, y2) = positions[ti];
            // Shorten line to stop at circle edge
            let dx = x2 - x1;
            let dy = y2 - y1;
            let dist = (dx * dx + dy * dy).sqrt();
            if dist > 0.0 {
                let nx = dx / dist;
                let ny = dy / dist;
                let ax1 = x1 + nx * radii[fi];
                let ay1 = y1 + ny * radii[fi];
                let ax2 = x2 - nx * radii[ti];
                let ay2 = y2 - ny * radii[ti];

                let (stroke, dash) = if edge.is_cycle {
                    ("#ef4444", r##" stroke-dasharray="5,3""##)
                } else {
                    ("#94a3b8", "")
                };
                write!(
                    svg,
                    r##"<line x1="{ax1:.1}" y1="{ay1:.1}" x2="{ax2:.1}" y2="{ay2:.1}" stroke="{stroke}" stroke-width="1.5" marker-end="url(#arrow)"{dash}/>"##,
                )
                .unwrap();
            }
        }
    }

    // Nodes
    for (i, module) in working_modules.iter().enumerate() {
        let (cx, cy) = positions[i];
        let r = radii[i];
        let color = super::health_color(1.0 - module.health_score / 100.0);
        write!(
            svg,
            r#"<circle cx="{cx:.1}" cy="{cy:.1}" r="{r:.1}" fill="{color}" stroke="white" stroke-width="2"/>"#,
        )
        .unwrap();
    }

    // Labels
    for (i, module) in working_modules.iter().enumerate() {
        let (cx, cy) = positions[i];
        let r = radii[i];
        let label = extract_label(&module.path);
        let escaped = super::xml_escape(&label);
        let ty = cy + r + 14.0;
        write!(
            svg,
            r##"<text x="{cx:.1}" y="{ty:.1}" font-size="10" text-anchor="middle" fill="#475569">{escaped}</text>"##,
        )
        .unwrap();
    }

    svg.push_str("</svg>");
    svg
}

/// Cap modules to MAX_MODULES by LOC, collapsing the rest into an "other" node.
fn cap_modules(modules: &[ModuleNode], edges: &[ModuleEdge]) -> (Vec<ModuleNode>, Vec<ModuleEdge>) {
    if modules.len() <= MAX_MODULES {
        return (modules.to_vec(), edges.to_vec());
    }

    let mut sorted: Vec<&ModuleNode> = modules.iter().collect();
    sorted.sort_by(|a, b| b.loc.cmp(&a.loc));

    let kept: Vec<ModuleNode> = sorted[..MAX_MODULES].iter().map(|m| (*m).clone()).collect();
    let kept_paths: std::collections::HashSet<String> =
        kept.iter().map(|m| m.path.clone()).collect();

    // Collapse remainder
    let rest: Vec<&ModuleNode> = sorted[MAX_MODULES..].iter().copied().collect();
    let other_loc: usize = rest.iter().map(|m| m.loc).sum();
    let other_files: usize = rest.iter().map(|m| m.file_count).sum();
    let other_findings: usize = rest.iter().map(|m| m.finding_count).sum();
    let other_health: f64 = if !rest.is_empty() {
        rest.iter().map(|m| m.health_score).sum::<f64>() / rest.len() as f64
    } else {
        50.0
    };

    let other = ModuleNode {
        path: "(other)".into(),
        loc: other_loc,
        file_count: other_files,
        finding_count: other_findings,
        finding_density: 0.0,
        avg_complexity: 0.0,
        community_id: None,
        health_score: other_health,
    };

    let mut result_modules = kept;
    result_modules.push(other);

    // Rewrite edges: remap collapsed module paths to "(other)"
    let result_edges: Vec<ModuleEdge> = edges
        .iter()
        .map(|e| {
            let from = if kept_paths.contains(&e.from) {
                e.from.clone()
            } else {
                "(other)".into()
            };
            let to = if kept_paths.contains(&e.to) {
                e.to.clone()
            } else {
                "(other)".into()
            };
            ModuleEdge {
                from,
                to,
                weight: e.weight,
                is_cycle: e.is_cycle,
            }
        })
        .filter(|e| e.from != e.to) // remove self-loops from collapse
        .collect();

    (result_modules, result_edges)
}

/// Assign layers via Kahn's algorithm with longest-path assignment.
fn assign_layers(
    modules: &[ModuleNode],
    edges: &[ModuleEdge],
    mod_index: &HashMap<&str, usize>,
) -> Vec<usize> {
    let n = modules.len();
    let mut in_degree = vec![0usize; n];
    let mut adj: Vec<Vec<usize>> = vec![vec![]; n];

    // Build adjacency, skipping cycle edges
    for edge in edges {
        if edge.is_cycle {
            continue;
        }
        if let (Some(&fi), Some(&ti)) = (
            mod_index.get(edge.from.as_str()),
            mod_index.get(edge.to.as_str()),
        ) {
            adj[fi].push(ti);
            in_degree[ti] += 1;
        }
    }

    // Kahn's with longest-path layer assignment
    let mut layers = vec![0usize; n];
    let mut queue: std::collections::VecDeque<usize> = std::collections::VecDeque::new();

    for i in 0..n {
        if in_degree[i] == 0 {
            queue.push_back(i);
        }
    }

    let mut visited = 0;
    while let Some(u) = queue.pop_front() {
        visited += 1;
        for &v in &adj[u] {
            layers[v] = layers[v].max(layers[u] + 1);
            in_degree[v] -= 1;
            if in_degree[v] == 0 {
                queue.push_back(v);
            }
        }
    }

    // If there are cycles (visited < n), assign unvisited nodes to layer 0
    if visited < n {
        for i in 0..n {
            if in_degree[i] > 0 {
                layers[i] = 0;
            }
        }
    }

    layers
}

/// Render community background rectangles.
fn render_community_backgrounds(
    svg: &mut String,
    communities: &[Community],
    _modules: &[ModuleNode],
    mod_index: &HashMap<&str, usize>,
    positions: &[(f64, f64)],
    radii: &[f64],
) {
    for community in communities {
        let color = COMMUNITY_COLORS[community.id % COMMUNITY_COLORS.len()];
        let mut min_x = f64::MAX;
        let mut min_y = f64::MAX;
        let mut max_x = f64::MIN;
        let mut max_y = f64::MIN;
        let mut found = false;

        for mod_path in &community.modules {
            if let Some(&idx) = mod_index.get(mod_path.as_str()) {
                let (cx, cy) = positions[idx];
                let r = radii[idx];
                min_x = min_x.min(cx - r);
                min_y = min_y.min(cy - r);
                max_x = max_x.max(cx + r);
                max_y = max_y.max(cy + r);
                found = true;
            }
        }

        if found {
            let pad = 16.0;
            let x = min_x - pad;
            let y = min_y - pad;
            let w = max_x - min_x + pad * 2.0;
            let h = max_y - min_y + pad * 2.0;
            write!(
                svg,
                r#"<rect x="{x:.1}" y="{y:.1}" width="{w:.1}" height="{h:.1}" fill="{color}" rx="8"/>"#,
            )
            .unwrap();
        }
    }
}

/// Extract a short label from a module path (last directory component, truncated).
fn extract_label(path: &str) -> String {
    let label = path
        .rsplit('/')
        .next()
        .unwrap_or(path);
    if label.len() > 12 {
        format!("{}...", &label[..9])
    } else {
        label.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::report_context::{Community, ModuleEdge, ModuleNode};

    fn test_modules() -> Vec<ModuleNode> {
        vec![
            ModuleNode {
                path: "src/engine".into(),
                loc: 5000,
                file_count: 10,
                finding_count: 3,
                finding_density: 0.6,
                avg_complexity: 5.0,
                community_id: Some(0),
                health_score: 80.0,
            },
            ModuleNode {
                path: "src/graph".into(),
                loc: 3000,
                file_count: 8,
                finding_count: 1,
                finding_density: 0.3,
                avg_complexity: 4.0,
                community_id: Some(0),
                health_score: 90.0,
            },
            ModuleNode {
                path: "src/cli".into(),
                loc: 2000,
                file_count: 5,
                finding_count: 0,
                finding_density: 0.0,
                avg_complexity: 3.0,
                community_id: Some(1),
                health_score: 95.0,
            },
        ]
    }

    #[test]
    fn test_architecture_map_renders_svg() {
        let modules = test_modules();
        let edges = vec![
            ModuleEdge {
                from: "src/cli".into(),
                to: "src/engine".into(),
                weight: 5,
                is_cycle: false,
            },
            ModuleEdge {
                from: "src/engine".into(),
                to: "src/graph".into(),
                weight: 12,
                is_cycle: false,
            },
        ];
        let communities = vec![
            Community {
                id: 0,
                modules: vec!["src/engine".into(), "src/graph".into()],
                label: "src/".into(),
            },
            Community {
                id: 1,
                modules: vec!["src/cli".into()],
                label: "src/cli".into(),
            },
        ];
        let svg = render_architecture_map(&modules, &edges, &communities);
        assert!(svg.contains("<svg"));
        assert!(svg.contains("engine"));
        assert!(svg.contains("graph"));
        assert!(svg.contains("<line") || svg.contains("<path"));
        assert!(svg.contains("</svg>"));
    }

    #[test]
    fn test_empty_map() {
        let svg = render_architecture_map(&[], &[], &[]);
        assert!(svg.contains("<svg"));
        assert!(svg.contains("</svg>"));
    }

    #[test]
    fn test_single_node() {
        let modules = vec![ModuleNode {
            path: "src".into(),
            loc: 1000,
            file_count: 5,
            finding_count: 0,
            finding_density: 0.0,
            avg_complexity: 3.0,
            community_id: None,
            health_score: 95.0,
        }];
        let svg = render_architecture_map(&modules, &[], &[]);
        assert!(svg.contains("<circle"));
    }
}
