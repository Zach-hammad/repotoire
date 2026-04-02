//! Hand-rolled graph algorithms: Tarjan SCC and iterative dominator tree.

/// Tarjan's strongly connected components algorithm.
///
/// Returns SCCs in reverse topological order (matches petgraph's behavior).
/// Uses struct-of-arrays layout for cache efficiency.
pub fn tarjan_scc<'a>(node_count: usize, successors: impl Fn(u32) -> &'a [u32]) -> Vec<Vec<u32>> {
    if node_count == 0 {
        return Vec::new();
    }

    let mut index = vec![u32::MAX; node_count];
    let mut lowlink = vec![0u32; node_count];
    let mut on_stack = vec![false; node_count];
    let mut stack = Vec::new();
    let mut sccs = Vec::new();
    let mut current_index = 0u32;

    // Iterative Tarjan using an explicit call stack to avoid deep recursion.
    // Each frame stores (node, successor_index).
    let mut call_stack: Vec<(u32, usize)> = Vec::new();

    for root in 0..node_count as u32 {
        if index[root as usize] != u32::MAX {
            continue;
        }

        call_stack.push((root, 0));
        index[root as usize] = current_index;
        lowlink[root as usize] = current_index;
        current_index += 1;
        on_stack[root as usize] = true;
        stack.push(root);

        while let Some(&mut (v, ref mut si)) = call_stack.last_mut() {
            let succs = successors(v);
            if *si < succs.len() {
                let w = succs[*si];
                *si += 1;

                if (w as usize) >= node_count {
                    continue;
                }

                if index[w as usize] == u32::MAX {
                    // Not yet visited — push onto call stack
                    index[w as usize] = current_index;
                    lowlink[w as usize] = current_index;
                    current_index += 1;
                    on_stack[w as usize] = true;
                    stack.push(w);
                    call_stack.push((w, 0));
                } else if on_stack[w as usize] {
                    lowlink[v as usize] = lowlink[v as usize].min(index[w as usize]);
                }
            } else {
                // Done with all successors of v
                if lowlink[v as usize] == index[v as usize] {
                    // v is root of an SCC
                    let mut scc = Vec::new();
                    loop {
                        let w = stack.pop().expect("stack underflow in tarjan");
                        on_stack[w as usize] = false;
                        scc.push(w);
                        if w == v {
                            break;
                        }
                    }
                    sccs.push(scc);
                }

                call_stack.pop();

                // Update parent's lowlink
                if let Some(&(parent, _)) = call_stack.last() {
                    lowlink[parent as usize] =
                        lowlink[parent as usize].min(lowlink[v as usize]);
                }
            }
        }
    }

    sccs
}

/// Iterative dominator tree computation (Cooper, Harvey, Kennedy 2001).
///
/// Returns `idom[v]` = `Some(immediate_dominator)` for each reachable vertex,
/// `None` for unreachable vertices. `idom[root]` = `None`.
///
/// The `predecessors` function returns incoming edges for a node (reverse graph).
pub fn compute_dominators<'a>(
    node_count: usize,
    root: u32,
    successors: impl Fn(u32) -> &'a [u32],
    predecessors: impl Fn(u32) -> &'a [u32],
) -> Vec<Option<u32>> {
    if node_count == 0 {
        return Vec::new();
    }

    // Step 1: DFS to compute reverse postorder
    let mut rpo = Vec::with_capacity(node_count);
    let mut rpo_number = vec![u32::MAX; node_count]; // node -> rpo index
    {
        let mut visited = vec![false; node_count];
        let mut dfs_stack: Vec<(u32, usize)> = Vec::new();
        visited[root as usize] = true;
        dfs_stack.push((root, 0));

        while let Some(&mut (v, ref mut si)) = dfs_stack.last_mut() {
            let succs = successors(v);
            if *si < succs.len() {
                let w = succs[*si];
                *si += 1;
                if (w as usize) < node_count && !visited[w as usize] {
                    visited[w as usize] = true;
                    dfs_stack.push((w, 0));
                }
            } else {
                dfs_stack.pop();
                rpo_number[v as usize] = rpo.len() as u32;
                rpo.push(v);
            }
        }
        rpo.reverse();
        // Update rpo_number to reflect reversed order
        for (i, &v) in rpo.iter().enumerate() {
            rpo_number[v as usize] = i as u32;
        }
    }

    // Step 2: Iterative dominator computation
    let mut idom: Vec<Option<u32>> = vec![None; node_count];
    idom[root as usize] = Some(root); // root dominates itself (sentinel)

    let intersect = |mut a: u32, mut b: u32, idom: &[Option<u32>]| -> u32 {
        while a != b {
            while rpo_number[a as usize] > rpo_number[b as usize] {
                a = idom[a as usize].expect("intersect: undefined idom");
            }
            while rpo_number[b as usize] > rpo_number[a as usize] {
                b = idom[b as usize].expect("intersect: undefined idom");
            }
        }
        a
    };

    let mut changed = true;
    while changed {
        changed = false;
        for &v in &rpo {
            if v == root {
                continue;
            }

            let preds = predecessors(v);
            // Find first processed predecessor
            let mut new_idom = None;
            for &p in preds {
                if idom[p as usize].is_some() {
                    new_idom = Some(p);
                    break;
                }
            }

            let Some(mut new_idom_val) = new_idom else {
                continue;
            };

            for &p in preds {
                if p == new_idom_val {
                    continue;
                }
                if idom[p as usize].is_some() {
                    new_idom_val = intersect(p, new_idom_val, &idom);
                }
            }

            if idom[v as usize] != Some(new_idom_val) {
                idom[v as usize] = Some(new_idom_val);
                changed = true;
            }
        }
    }

    // Root's idom is None (not self)
    idom[root as usize] = None;

    // Unreachable nodes stay None
    idom
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tarjan_no_cycles() {
        // 0 → 1 → 2 (DAG)
        let adj: Vec<Vec<u32>> = vec![vec![1], vec![2], vec![]];
        let sccs = tarjan_scc(3, |v| &adj[v as usize]);
        assert_eq!(sccs.len(), 3);
        assert!(sccs.iter().all(|scc| scc.len() == 1));
    }

    #[test]
    fn test_tarjan_single_cycle() {
        // 0 → 1 → 2 → 0
        let adj: Vec<Vec<u32>> = vec![vec![1], vec![2], vec![0]];
        let sccs = tarjan_scc(3, |v| &adj[v as usize]);
        assert_eq!(sccs.len(), 1);
        assert_eq!(sccs[0].len(), 3);
    }

    #[test]
    fn test_tarjan_mixed() {
        // 0 → 1 → 2 → 1 (cycle 1-2), 0 → 3 (no cycle)
        let adj: Vec<Vec<u32>> = vec![vec![1, 3], vec![2], vec![1], vec![]];
        let sccs = tarjan_scc(4, |v| &adj[v as usize]);
        let big: Vec<_> = sccs.iter().filter(|s| s.len() > 1).collect();
        assert_eq!(big.len(), 1);
        assert_eq!(big[0].len(), 2);
    }

    #[test]
    fn test_tarjan_empty() {
        let sccs = tarjan_scc(0, |_| &[]);
        assert!(sccs.is_empty());
    }

    #[test]
    fn test_dominators_linear() {
        // 0 → 1 → 2 → 3
        let fwd: Vec<Vec<u32>> = vec![vec![1], vec![2], vec![3], vec![]];
        let rev: Vec<Vec<u32>> = vec![vec![], vec![0], vec![1], vec![2]];
        let idom = compute_dominators(4, 0, |v| &fwd[v as usize], |v| &rev[v as usize]);
        assert_eq!(idom[0], None); // root
        assert_eq!(idom[1], Some(0));
        assert_eq!(idom[2], Some(1));
        assert_eq!(idom[3], Some(2));
    }

    #[test]
    fn test_dominators_diamond() {
        // 0 → 1, 0 → 2, 1 → 3, 2 → 3
        let fwd: Vec<Vec<u32>> = vec![vec![1, 2], vec![3], vec![3], vec![]];
        let rev: Vec<Vec<u32>> = vec![vec![], vec![0], vec![0], vec![1, 2]];
        let idom = compute_dominators(4, 0, |v| &fwd[v as usize], |v| &rev[v as usize]);
        assert_eq!(idom[0], None);
        assert_eq!(idom[1], Some(0));
        assert_eq!(idom[2], Some(0));
        assert_eq!(idom[3], Some(0)); // 0 dominates 3
    }

    #[test]
    fn test_dominators_unreachable() {
        // 0 → 1, node 2 is disconnected
        let fwd: Vec<Vec<u32>> = vec![vec![1], vec![], vec![]];
        let rev: Vec<Vec<u32>> = vec![vec![], vec![0], vec![]];
        let idom = compute_dominators(3, 0, |v| &fwd[v as usize], |v| &rev[v as usize]);
        assert_eq!(idom[0], None);
        assert_eq!(idom[1], Some(0));
        assert_eq!(idom[2], None); // unreachable
    }
}
