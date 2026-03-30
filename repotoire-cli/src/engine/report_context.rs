//! Report context building for the analysis engine.
//!
//! Extracted from `engine/mod.rs` — pure data aggregation methods that
//! assemble `ReportContext` from analysis results for reporters to consume.

use crate::graph::traits::GraphQuery;

impl super::AnalysisEngine {
    // ── Report context building ──────────────────────────────────────────

    /// Build a `ReportContext` from the current engine state.
    ///
    /// Called by the CLI after analysis completes. Reads from the frozen CodeGraph
    /// (via `GraphQuery`), the retained `CoChangeMatrix`, and the filesystem to
    /// assemble a `ReportContext` that reporters consume.
    pub fn build_report_context(
        &self,
        health: crate::models::HealthReport,
        format: crate::reporters::OutputFormat,
    ) -> anyhow::Result<crate::reporters::report_context::ReportContext> {
        use crate::reporters::OutputFormat;
        use crate::reporters::report_context::ReportContext;

        let needs_rich = matches!(format, OutputFormat::Html | OutputFormat::Text);

        let graph_data = if needs_rich {
            self.build_graph_data()
        } else {
            None
        };

        let git_data = if needs_rich {
            self.build_git_data()
        } else {
            None
        };

        let source_snippets = if matches!(format, OutputFormat::Html) {
            self.build_snippets(&health.findings)
        } else {
            Vec::new()
        };

        let previous_health = self.load_previous_health();

        let style_profile = self
            .state
            .as_ref()
            .map(|s| s.style_profile.clone());

        // Enrich modules with finding counts from the health report
        let graph_data = graph_data.map(|mut gd| {
            let mut dir_findings: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
            for finding in &health.findings {
                if let Some(file) = finding.affected_files.first() {
                    let dir = file.parent()
                        .and_then(|p| p.to_str())
                        .unwrap_or(".")
                        .to_string();
                    *dir_findings.entry(dir).or_default() += 1;
                }
            }
            for module in &mut gd.modules {
                module.finding_count = dir_findings.get(&module.path).copied().unwrap_or(0);
                module.finding_density = if module.loc > 0 {
                    (module.finding_count as f64) / (module.loc as f64 / 1000.0)
                } else {
                    0.0
                };
                module.health_score = (100.0 - module.finding_density * 10.0).clamp(0.0, 100.0);
            }
            gd
        });

        Ok(ReportContext {
            health,
            graph_data,
            git_data,
            source_snippets,
            previous_health,
            style_profile,
        })
    }

    /// Build graph-derived data for rich reporters.
    fn build_graph_data(&self) -> Option<crate::reporters::report_context::GraphData> {
        use crate::reporters::report_context::GraphData;

        let graph = self.graph()?;
        let interner = graph.interner();

        // Top PageRank (functions, top 20)
        let mut pr_scores: Vec<(String, f64)> = graph
            .functions_idx()
            .iter()
            .filter_map(|&idx| {
                let node = graph.node_idx(idx)?;
                let score = graph.primitives().page_rank.get(&idx).copied().unwrap_or(0.0);
                if score > 0.0 {
                    Some((interner.resolve(node.qualified_name).to_string(), score))
                } else {
                    None
                }
            })
            .collect();
        pr_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        pr_scores.truncate(20);

        // Top betweenness (functions, top 20)
        let mut bw_scores: Vec<(String, f64)> = graph
            .functions_idx()
            .iter()
            .filter_map(|&idx| {
                let node = graph.node_idx(idx)?;
                let score = graph.primitives().betweenness.get(&idx).copied().unwrap_or(0.0);
                if score > 0.0 {
                    Some((interner.resolve(node.qualified_name).to_string(), score))
                } else {
                    None
                }
            })
            .collect();
        bw_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        bw_scores.truncate(20);

        // Articulation points
        let art_points: Vec<String> = graph
            .primitives().articulation_points
            .iter()
            .filter_map(|&idx| {
                let node = graph.node_idx(idx)?;
                Some(interner.resolve(node.qualified_name).to_string())
            })
            .collect();

        // Call cycles
        let call_cycles: Vec<Vec<String>> = graph
            .primitives().call_cycles
            .iter()
            .map(|cycle| {
                cycle
                    .iter()
                    .filter_map(|&idx| {
                        let node = graph.node_idx(idx)?;
                        Some(interner.resolve(node.qualified_name).to_string())
                    })
                    .collect()
            })
            .collect();

        // Aggregate modules
        let modules = self.aggregate_modules(graph);
        let module_edges = self.aggregate_module_edges(graph, &modules);
        let (communities, modularity) = self.map_communities(graph, &modules);

        Some(GraphData {
            modules,
            module_edges,
            communities,
            modularity,
            top_pagerank: pr_scores,
            top_betweenness: bw_scores,
            articulation_points: art_points,
            call_cycles,
        })
    }

    /// Build git-derived data (hidden coupling, co-change, file ownership).
    fn build_git_data(&self) -> Option<crate::reporters::report_context::GitData> {
        use crate::reporters::report_context::GitData;

        let graph = self.graph()?;
        let interner = graph.interner();

        // Hidden coupling from graph primitives
        let hidden_coupling: Vec<(String, String, f32)> = graph
            .primitives().hidden_coupling
            .iter()
            .filter_map(|&(a, b, w, _lift, _confidence)| {
                let na = graph.node_idx(a)?;
                let nb = graph.node_idx(b)?;
                Some((
                    interner.resolve(na.qualified_name).to_string(),
                    interner.resolve(nb.qualified_name).to_string(),
                    w,
                ))
            })
            .collect();

        // Top co-change from CoChangeMatrix (top 20)
        let mut top_co_change: Vec<(String, String, f32)> = Vec::new();
        if let Some(matrix) = self.co_change() {
            let gi = crate::graph::interner::global_interner();
            let mut pairs: Vec<(String, String, f32)> = matrix
                .iter()
                .map(|(&(a, b), &w)| {
                    (gi.resolve(a).to_string(), gi.resolve(b).to_string(), w)
                })
                .collect();
            pairs.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
            pairs.truncate(20);
            top_co_change = pairs;
        }

        // File ownership from OwnershipModel (replaces old ExtraProps-based compute_file_ownership)
        let file_ownership = if let Some(ref ownership) = self.ownership_model {
            ownership.files.values().map(|fo| {
                crate::reporters::report_context::FileOwnership {
                    path: fo.path.clone(),
                    authors: fo.authors.iter()
                        .map(|a| (a.author.clone(), a.normalized_doa))
                        .collect(),
                    bus_factor: fo.bus_factor,
                }
            }).collect()
        } else {
            self.compute_file_ownership(graph)
        };

        let project_bus_factor = self.ownership_model.as_ref().map(|o| o.project_bus_factor);

        // Bus factor: files with only 1-2 contributors
        let bus_factor_files: Vec<(String, usize)> = file_ownership
            .iter()
            .filter(|fo| fo.bus_factor <= 2)
            .map(|fo| (fo.path.clone(), fo.bus_factor))
            .collect();

        // Return None if we have no meaningful git data
        if hidden_coupling.is_empty()
            && top_co_change.is_empty()
            && file_ownership.is_empty()
        {
            return None;
        }

        Some(GitData {
            hidden_coupling,
            top_co_change,
            file_ownership,
            bus_factor_files,
            project_bus_factor,
        })
    }

    /// Read source code snippets for the top findings.
    fn build_snippets(
        &self,
        findings: &[crate::models::Finding],
    ) -> Vec<crate::reporters::report_context::FindingSnippet> {
        use crate::reporters::report_context::FindingSnippet;

        findings
            .iter()
            .take(20)
            .filter_map(|f| {
                let file = f.affected_files.first()?;
                let abs_path = if file.is_absolute() {
                    file.clone()
                } else {
                    self.repo_path.join(file)
                };
                let bytes = std::fs::read(&abs_path).ok()?;
                let code = String::from_utf8_lossy(&bytes);

                // Extract relevant lines around the finding
                let start = f.line_start.unwrap_or(1).saturating_sub(1) as usize;
                let end = f.line_end.unwrap_or(f.line_start.unwrap_or(1)) as usize;
                let lines: Vec<&str> = code.lines().collect();

                // Context: 3 lines before, finding lines, 3 lines after
                let ctx_start = start.saturating_sub(3);
                let ctx_end = (end + 3).min(lines.len());
                let snippet: String = lines
                    .get(ctx_start..ctx_end)
                    .unwrap_or(&[])
                    .join("\n");

                // Highlight lines are the finding lines (1-indexed)
                let highlight: Vec<u32> = (f.line_start.unwrap_or(1)..=f.line_end.unwrap_or(f.line_start.unwrap_or(1)))
                    .collect();

                // Detect language from extension
                let language = abs_path
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("")
                    .to_string();

                Some(FindingSnippet {
                    finding_id: f.id.clone(),
                    code: snippet,
                    highlight_lines: highlight,
                    language,
                })
            })
            .collect()
    }

    /// Load the previous health report from the cache directory.
    fn load_previous_health(&self) -> Option<crate::models::HealthReport> {
        let path = crate::cache::paths::health_cache_path(&self.repo_path);
        let json = std::fs::read_to_string(&path).ok()?;
        serde_json::from_str(&json).ok()
    }

    /// Group file nodes by parent directory into ModuleNodes.
    fn aggregate_modules(
        &self,
        graph: &dyn GraphQuery,
    ) -> Vec<crate::reporters::report_context::ModuleNode> {
        use crate::reporters::report_context::ModuleNode;
        use std::collections::HashMap;

        let interner = graph.interner();

        // Collect file info grouped by parent directory
        struct FileInfo {
            loc: usize,
            complexity_sum: f64,
            complexity_count: usize,
            community_id: Option<usize>,
        }

        let mut modules: HashMap<String, Vec<FileInfo>> = HashMap::new();

        for &idx in graph.files_idx() {
            let node = match graph.node_idx(idx) {
                Some(n) => n,
                None => continue,
            };
            let file_path = interner.resolve(node.file_path);
            let parent = std::path::Path::new(file_path)
                .parent()
                .and_then(|p| p.to_str())
                .unwrap_or(".")
                .to_string();

            // Aggregate function complexities in this file
            let funcs_in_file = graph.functions_in_file_idx(file_path);
            let (cx_sum, cx_count) = funcs_in_file
                .iter()
                .filter_map(|&fidx| graph.node_idx(fidx))
                .fold((0.0, 0usize), |(sum, cnt), f| {
                    (sum + f.complexity as f64, cnt + 1)
                });

            let community = graph.primitives().community.get(&idx).copied();

            modules.entry(parent).or_default().push(FileInfo {
                loc: node.loc() as usize,
                complexity_sum: cx_sum,
                complexity_count: cx_count,
                community_id: community,
            });
        }

        modules
            .into_iter()
            .map(|(path, files)| {
                let file_count = files.len();
                let loc: usize = files.iter().map(|f| f.loc).sum();
                let total_cx: f64 = files.iter().map(|f| f.complexity_sum).sum();
                let total_cx_count: usize = files.iter().map(|f| f.complexity_count).sum();
                let avg_complexity = if total_cx_count > 0 {
                    total_cx / total_cx_count as f64
                } else {
                    0.0
                };

                // Majority-vote community for this module
                let community_id = {
                    let mut votes: HashMap<usize, usize> = HashMap::new();
                    for f in &files {
                        if let Some(c) = f.community_id {
                            *votes.entry(c).or_default() += 1;
                        }
                    }
                    votes.into_iter().max_by_key(|&(_, count)| count).map(|(id, _)| id)
                };

                ModuleNode {
                    path,
                    loc,
                    file_count,
                    finding_count: 0, // populated by caller if needed
                    finding_density: 0.0,
                    avg_complexity,
                    community_id,
                    health_score: 0.0, // populated by caller if needed
                }
            })
            .collect()
    }

    /// Count cross-module import edges.
    fn aggregate_module_edges(
        &self,
        graph: &dyn GraphQuery,
        _modules: &[crate::reporters::report_context::ModuleNode],
    ) -> Vec<crate::reporters::report_context::ModuleEdge> {
        use crate::reporters::report_context::ModuleEdge;
        use std::collections::HashMap;

        let interner = graph.interner();

        // Build file -> module mapping for edge aggregation

        let file_to_module: HashMap<String, String> = graph
            .files_idx()
            .iter()
            .filter_map(|&idx| {
                let node = graph.node_idx(idx)?;
                let fp = interner.resolve(node.file_path).to_string();
                let parent = std::path::Path::new(&fp)
                    .parent()
                    .and_then(|p| p.to_str())
                    .unwrap_or(".")
                    .to_string();
                Some((fp, parent))
            })
            .collect();

        // Count cross-module edges
        let mut edge_counts: HashMap<(String, String), usize> = HashMap::new();
        for &(from_idx, to_idx) in graph.all_import_edges() {
            let from_node = match graph.node_idx(from_idx) {
                Some(n) => n,
                None => continue,
            };
            let to_node = match graph.node_idx(to_idx) {
                Some(n) => n,
                None => continue,
            };
            let from_fp = interner.resolve(from_node.file_path).to_string();
            let to_fp = interner.resolve(to_node.file_path).to_string();

            let from_mod = file_to_module.get(&from_fp).cloned().unwrap_or_default();
            let to_mod = file_to_module.get(&to_fp).cloned().unwrap_or_default();

            if from_mod != to_mod && !from_mod.is_empty() && !to_mod.is_empty() {
                *edge_counts.entry((from_mod, to_mod)).or_default() += 1;
            }
        }

        edge_counts
            .into_iter()
            .map(|((from, to), weight)| ModuleEdge {
                from,
                to,
                weight,
                is_cycle: false, // could be enriched with cycle detection
            })
            .collect()
    }

    /// Map file-level communities to module-level via majority vote.
    fn map_communities(
        &self,
        graph: &dyn GraphQuery,
        modules: &[crate::reporters::report_context::ModuleNode],
    ) -> (Vec<crate::reporters::report_context::Community>, f64) {
        use crate::reporters::report_context::Community;
        use std::collections::HashMap;

        let modularity = graph.primitives().modularity;

        let mut community_modules: HashMap<usize, Vec<String>> = HashMap::new();
        for m in modules {
            if let Some(cid) = m.community_id {
                community_modules
                    .entry(cid)
                    .or_default()
                    .push(m.path.clone());
            }
        }

        let communities: Vec<Community> = community_modules
            .into_iter()
            .map(|(id, mods)| {
                // Label: longest common directory prefix, or module with most LOC
                let label = if mods.len() == 1 {
                    mods[0].clone()
                } else {
                    common_path_prefix(&mods).unwrap_or_else(|| {
                        // Fallback: module with most LOC
                        mods.iter()
                            .filter_map(|m| modules.iter().find(|n| n.path == *m))
                            .max_by_key(|n| n.loc)
                            .map(|n| n.path.clone())
                            .unwrap_or_else(|| format!("Community {}", id))
                    })
                };
                Community {
                    id,
                    modules: mods,
                    label,
                }
            })
            .collect();

        (communities, modularity)
    }
}

/// Find the longest common directory prefix among a set of paths.
fn common_path_prefix(paths: &[String]) -> Option<String> {
    if paths.is_empty() {
        return None;
    }
    let first = &paths[0];
    // Use char_indices to track byte positions (avoids UTF-8 byte-slice panic)
    let prefix_len = paths.iter().skip(1).fold(first.len(), |acc, p| {
        let common_bytes = first
            .char_indices()
            .zip(p.chars())
            .take_while(|((_, a), b)| a == b)
            .last()
            .map(|((i, c), _)| i + c.len_utf8())
            .unwrap_or(0);
        acc.min(common_bytes)
    });
    let prefix = &first[..prefix_len];
    // Trim to last '/' to get a clean directory path
    prefix
        .rfind('/')
        .map(|i| first[..=i].to_string())
}

impl super::AnalysisEngine {
    /// Extract file ownership info from ExtraProps author field.
    fn compute_file_ownership(
        &self,
        graph: &dyn GraphQuery,
    ) -> Vec<crate::reporters::report_context::FileOwnership> {
        use crate::reporters::report_context::FileOwnership;

        let interner = graph.interner();

        graph
            .files_idx()
            .iter()
            .filter_map(|&idx| {
                let node = graph.node_idx(idx)?;
                let props = graph.extra_props_ref(node.qualified_name)?;
                let author_key = props.author?;
                let author = interner.resolve(author_key);
                if author.is_empty() {
                    return None;
                }

                let file_path = interner.resolve(node.file_path).to_string();

                // Simple model: single author with 100% ownership
                // A more sophisticated version would parse blame data
                Some(FileOwnership {
                    path: file_path,
                    authors: vec![(author.to_string(), 1.0)],
                    bus_factor: 1,
                })
            })
            .collect()
    }
}
