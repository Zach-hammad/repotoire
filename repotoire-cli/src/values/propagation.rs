//! Cross-function value propagation.
//!
//! After all files have been parsed and ingested into a [`ValueStore`], this
//! module resolves inter-function references by walking functions in
//! topological order (leaves first) and substituting `Variable` and `Call`
//! references with their resolved values.
//!
//! Cycle detection and depth limiting prevent infinite recursion on mutually
//! recursive functions or deeply chained call graphs.

use std::collections::{HashMap, HashSet};

use super::store::{Assignment, ValueStore};
use super::types::{SymbolicValue, MAX_RESOLUTION_DEPTH};

/// Resolve cross-function value references in the store.
///
/// `topo_order` is the topological ordering of function qualified names
/// (leaves first). `call_map` maps caller to the set of callees for
/// cycle-aware resolution.
///
/// After this function returns, the store's `return_values` and
/// `function_values` will have `Variable` and `Call` references resolved
/// where possible.
pub fn resolve_cross_function(
    store: &mut ValueStore,
    topo_order: &[String],
    _call_map: &HashMap<String, HashSet<String>>,
) {
    let mut resolving: HashSet<String> = HashSet::new();

    for func_qn in topo_order {
        resolve_function(store, func_qn, &mut resolving, 0);
    }
}

/// Resolve a single function's return value and assignments.
///
/// Uses `resolving` to detect cycles and `depth` to enforce a recursion
/// limit of [`MAX_RESOLUTION_DEPTH`].
fn resolve_function(
    store: &mut ValueStore,
    func_qn: &str,
    resolving: &mut HashSet<String>,
    depth: usize,
) {
    if resolving.contains(func_qn) || depth > MAX_RESOLUTION_DEPTH {
        return;
    }
    resolving.insert(func_qn.to_string());

    // Clone the raw return expression for resolution (avoids borrow conflict).
    if let Some(raw_return) = store.return_values.get(func_qn).cloned() {
        let resolved = substitute_references(&raw_return, store, resolving, depth + 1);
        store.return_values.insert(func_qn.to_string(), resolved);
    }

    // Clone assignments for resolution.
    if let Some(assignments) = store.function_values.get(func_qn).cloned() {
        let resolved: Vec<Assignment> = assignments
            .into_iter()
            .map(|mut a| {
                a.value = substitute_references(&a.value, store, resolving, depth + 1);
                a
            })
            .collect();
        store.function_values.insert(func_qn.to_string(), resolved);
    }

    resolving.remove(func_qn);
}

/// Recursively substitute `Variable` and `Call` references within a
/// [`SymbolicValue`].
///
/// - `Variable(qn)`: resolved via module constants in the store.
/// - `Call(callee, args)`: inlined by looking up the callee's return value
///   and substituting `Parameter(n)` with the actual arguments.
/// - Compound variants (`BinaryOp`, `Concat`, `Phi`, `FieldAccess`,
///   `Index`) recurse on their children.
/// - Leaf variants (`Literal`, `Parameter`, `Unknown`) are returned as-is.
fn substitute_references(
    value: &SymbolicValue,
    store: &ValueStore,
    resolving: &HashSet<String>,
    depth: usize,
) -> SymbolicValue {
    if depth > MAX_RESOLUTION_DEPTH {
        return value.clone();
    }

    match value {
        SymbolicValue::Variable(qn) => {
            // Try to resolve as a module-level constant.
            if let Some(constant) = store.constants.get(qn) {
                return substitute_references(constant, store, resolving, depth + 1);
            }
            // Otherwise leave as-is (may be a local or unresolvable reference).
            value.clone()
        }

        SymbolicValue::Call(callee, args) => {
            // Resolve arguments first.
            let resolved_args: Vec<SymbolicValue> = args
                .iter()
                .map(|a| substitute_references(a, store, resolving, depth + 1))
                .collect();

            // Skip resolution if callee is currently being resolved (cycle).
            if resolving.contains(callee.as_str()) {
                return SymbolicValue::Call(callee.clone(), resolved_args);
            }

            // Look up the callee's return value.
            match store.return_values.get(callee) {
                Some(ret_val) if *ret_val != SymbolicValue::Unknown => {
                    // Substitute parameters in the return value with actual args.
                    let substituted = substitute_params(ret_val, &resolved_args);
                    // Recursively resolve any remaining references in the result.
                    substitute_references(&substituted, store, resolving, depth + 1)
                }
                _ => {
                    // Cannot resolve — keep as Call with resolved args.
                    SymbolicValue::Call(callee.clone(), resolved_args)
                }
            }
        }

        SymbolicValue::BinaryOp(op, lhs, rhs) => {
            let new_lhs = substitute_references(lhs, store, resolving, depth + 1);
            let new_rhs = substitute_references(rhs, store, resolving, depth + 1);
            SymbolicValue::BinaryOp(*op, Box::new(new_lhs), Box::new(new_rhs))
        }

        SymbolicValue::Concat(parts) => {
            let new_parts: Vec<SymbolicValue> = parts
                .iter()
                .map(|p| substitute_references(p, store, resolving, depth + 1))
                .collect();
            SymbolicValue::Concat(new_parts)
        }

        SymbolicValue::Phi(arms) => {
            let new_arms: Vec<SymbolicValue> = arms
                .iter()
                .map(|a| substitute_references(a, store, resolving, depth + 1))
                .collect();
            SymbolicValue::Phi(new_arms)
        }

        SymbolicValue::FieldAccess(obj, field) => {
            let new_obj = substitute_references(obj, store, resolving, depth + 1);
            SymbolicValue::FieldAccess(Box::new(new_obj), field.clone())
        }

        SymbolicValue::Index(obj, key) => {
            let new_obj = substitute_references(obj, store, resolving, depth + 1);
            let new_key = substitute_references(key, store, resolving, depth + 1);
            SymbolicValue::Index(Box::new(new_obj), Box::new(new_key))
        }

        // Leaf values — return as-is.
        SymbolicValue::Literal(_) | SymbolicValue::Parameter(_) | SymbolicValue::Unknown => {
            value.clone()
        }
    }
}

/// Replace `Parameter(n)` references with actual argument values.
///
/// - `Parameter(n)` where `n < args.len()` is replaced with `args[n]`.
/// - `Parameter(n)` where `n >= args.len()` becomes `Unknown`.
/// - Compound values are recursed into; leaf values are returned as-is.
fn substitute_params(value: &SymbolicValue, args: &[SymbolicValue]) -> SymbolicValue {
    match value {
        SymbolicValue::Parameter(n) => {
            if *n < args.len() {
                args[*n].clone()
            } else {
                SymbolicValue::Unknown
            }
        }

        SymbolicValue::BinaryOp(op, lhs, rhs) => {
            let new_lhs = substitute_params(lhs, args);
            let new_rhs = substitute_params(rhs, args);
            SymbolicValue::BinaryOp(*op, Box::new(new_lhs), Box::new(new_rhs))
        }

        SymbolicValue::Concat(parts) => {
            let new_parts: Vec<SymbolicValue> =
                parts.iter().map(|p| substitute_params(p, args)).collect();
            SymbolicValue::Concat(new_parts)
        }

        SymbolicValue::Phi(arms) => {
            let new_arms: Vec<SymbolicValue> =
                arms.iter().map(|a| substitute_params(a, args)).collect();
            SymbolicValue::Phi(new_arms)
        }

        SymbolicValue::FieldAccess(obj, field) => {
            let new_obj = substitute_params(obj, args);
            SymbolicValue::FieldAccess(Box::new(new_obj), field.clone())
        }

        SymbolicValue::Index(obj, key) => {
            let new_obj = substitute_params(obj, args);
            let new_key = substitute_params(key, args);
            SymbolicValue::Index(Box::new(new_obj), Box::new(new_key))
        }

        SymbolicValue::Call(callee, call_args) => {
            let new_args: Vec<SymbolicValue> = call_args
                .iter()
                .map(|a| substitute_params(a, args))
                .collect();
            SymbolicValue::Call(callee.clone(), new_args)
        }

        SymbolicValue::Variable(_) => value.clone(),

        // Literal and Unknown — return as-is.
        SymbolicValue::Literal(_) | SymbolicValue::Unknown => value.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::values::store::*;
    use crate::values::types::*;
    use std::collections::HashMap;

    #[test]
    fn test_resolve_constant_reference() {
        let mut store = ValueStore::new();
        store.constants.insert(
            "config.TIMEOUT".into(),
            SymbolicValue::Literal(LiteralValue::Integer(3600)),
        );
        store.function_values.insert(
            "api.handler".into(),
            vec![Assignment {
                variable: "timeout".into(),
                value: SymbolicValue::Variable("config.TIMEOUT".into()),
                line: 5,
                column: 4,
            }],
        );

        resolve_cross_function(&mut store, &["api.handler".into()], &HashMap::new());

        let resolved = store.resolve_at("api.handler", "timeout", 10);
        assert_eq!(
            resolved,
            SymbolicValue::Literal(LiteralValue::Integer(3600))
        );
    }

    #[test]
    fn test_resolve_call_return_value() {
        let mut store = ValueStore::new();
        store.return_values.insert(
            "foo".into(),
            SymbolicValue::Literal(LiteralValue::Integer(42)),
        );
        store.function_values.insert(
            "bar".into(),
            vec![Assignment {
                variable: "x".into(),
                value: SymbolicValue::Call("foo".into(), vec![]),
                line: 1,
                column: 0,
            }],
        );

        resolve_cross_function(&mut store, &["foo".into(), "bar".into()], &HashMap::new());

        let resolved = store.resolve_at("bar", "x", 10);
        assert_eq!(resolved, SymbolicValue::Literal(LiteralValue::Integer(42)));
    }

    #[test]
    fn test_parameter_substitution() {
        let mut store = ValueStore::new();
        store
            .return_values
            .insert("foo".into(), SymbolicValue::Parameter(0));
        store.function_values.insert(
            "bar".into(),
            vec![Assignment {
                variable: "result".into(),
                value: SymbolicValue::Call(
                    "foo".into(),
                    vec![SymbolicValue::Literal(LiteralValue::Integer(42))],
                ),
                line: 1,
                column: 0,
            }],
        );

        resolve_cross_function(&mut store, &["foo".into(), "bar".into()], &HashMap::new());

        let resolved = store.resolve_at("bar", "result", 10);
        assert_eq!(resolved, SymbolicValue::Literal(LiteralValue::Integer(42)));
    }

    #[test]
    fn test_cycle_detection_does_not_hang() {
        let mut store = ValueStore::new();
        store
            .return_values
            .insert("a".into(), SymbolicValue::Call("b".into(), vec![]));
        store
            .return_values
            .insert("b".into(), SymbolicValue::Call("a".into(), vec![]));

        // Should not hang — cycles are detected.
        resolve_cross_function(&mut store, &["a".into(), "b".into()], &HashMap::new());
        // Just verify it completes.
    }

    #[test]
    fn test_depth_limit() {
        let mut store = ValueStore::new();
        // Create a deep chain beyond MAX_RESOLUTION_DEPTH.
        for i in 0..20 {
            store.return_values.insert(
                format!("f{i}"),
                SymbolicValue::Call(format!("f{}", i + 1), vec![]),
            );
        }
        store.return_values.insert(
            "f20".into(),
            SymbolicValue::Literal(LiteralValue::Integer(99)),
        );

        let order: Vec<String> = (0..=20).rev().map(|i| format!("f{i}")).collect();
        resolve_cross_function(&mut store, &order, &HashMap::new());
        // Should not panic or hang.
    }

    #[test]
    fn test_nested_resolution() {
        // foo() returns 10, bar() returns foo() + 5.
        let mut store = ValueStore::new();
        store.return_values.insert(
            "foo".into(),
            SymbolicValue::Literal(LiteralValue::Integer(10)),
        );
        store.return_values.insert(
            "bar".into(),
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Call("foo".into(), vec![])),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(5))),
            ),
        );

        resolve_cross_function(&mut store, &["foo".into(), "bar".into()], &HashMap::new());

        // bar's return should now be BinaryOp(Add, Literal(10), Literal(5)).
        let ret = store.return_value("bar");
        assert_eq!(
            ret,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(10))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(5))),
            )
        );
    }

    #[test]
    fn test_concat_resolution() {
        let mut store = ValueStore::new();
        store.constants.insert(
            "config.PREFIX".into(),
            SymbolicValue::Literal(LiteralValue::String("api".into())),
        );
        store.function_values.insert(
            "build_url".into(),
            vec![Assignment {
                variable: "url".into(),
                value: SymbolicValue::Concat(vec![
                    SymbolicValue::Variable("config.PREFIX".into()),
                    SymbolicValue::Literal(LiteralValue::String("/users".into())),
                ]),
                line: 1,
                column: 0,
            }],
        );

        resolve_cross_function(&mut store, &["build_url".into()], &HashMap::new());

        let resolved = store.resolve_at("build_url", "url", 10);
        assert_eq!(
            resolved,
            SymbolicValue::Concat(vec![
                SymbolicValue::Literal(LiteralValue::String("api".into())),
                SymbolicValue::Literal(LiteralValue::String("/users".into())),
            ])
        );
    }

    #[test]
    fn test_parameter_out_of_bounds_becomes_unknown() {
        let result = substitute_params(
            &SymbolicValue::Parameter(5),
            &[SymbolicValue::Literal(LiteralValue::Integer(1))],
        );
        assert_eq!(result, SymbolicValue::Unknown);
    }

    #[test]
    fn test_substitute_params_in_binary_op() {
        let value = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(SymbolicValue::Parameter(0)),
            Box::new(SymbolicValue::Parameter(1)),
        );
        let args = vec![
            SymbolicValue::Literal(LiteralValue::Integer(10)),
            SymbolicValue::Literal(LiteralValue::Integer(20)),
        ];
        let result = substitute_params(&value, &args);
        assert_eq!(
            result,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(10))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(20))),
            )
        );
    }

    #[test]
    fn test_phi_resolution() {
        let mut store = ValueStore::new();
        store.constants.insert(
            "config.A".into(),
            SymbolicValue::Literal(LiteralValue::Integer(1)),
        );
        store.constants.insert(
            "config.B".into(),
            SymbolicValue::Literal(LiteralValue::Integer(2)),
        );
        store.function_values.insert(
            "func".into(),
            vec![Assignment {
                variable: "x".into(),
                value: SymbolicValue::Phi(vec![
                    SymbolicValue::Variable("config.A".into()),
                    SymbolicValue::Variable("config.B".into()),
                ]),
                line: 5,
                column: 0,
            }],
        );

        resolve_cross_function(&mut store, &["func".into()], &HashMap::new());

        let resolved = store.resolve_at("func", "x", 10);
        assert_eq!(
            resolved,
            SymbolicValue::Phi(vec![
                SymbolicValue::Literal(LiteralValue::Integer(1)),
                SymbolicValue::Literal(LiteralValue::Integer(2)),
            ])
        );
    }

    #[test]
    fn test_field_access_resolution() {
        let mut store = ValueStore::new();
        store.constants.insert(
            "config.OBJ".into(),
            SymbolicValue::Literal(LiteralValue::String("resolved_obj".into())),
        );
        store.function_values.insert(
            "func".into(),
            vec![Assignment {
                variable: "val".into(),
                value: SymbolicValue::FieldAccess(
                    Box::new(SymbolicValue::Variable("config.OBJ".into())),
                    "field".into(),
                ),
                line: 1,
                column: 0,
            }],
        );

        resolve_cross_function(&mut store, &["func".into()], &HashMap::new());

        let resolved = store.resolve_at("func", "val", 10);
        assert_eq!(
            resolved,
            SymbolicValue::FieldAccess(
                Box::new(SymbolicValue::Literal(LiteralValue::String(
                    "resolved_obj".into()
                ))),
                "field".into(),
            )
        );
    }

    #[test]
    fn test_index_resolution() {
        let mut store = ValueStore::new();
        store.constants.insert(
            "config.IDX".into(),
            SymbolicValue::Literal(LiteralValue::Integer(0)),
        );
        store.function_values.insert(
            "func".into(),
            vec![Assignment {
                variable: "elem".into(),
                value: SymbolicValue::Index(
                    Box::new(SymbolicValue::Literal(LiteralValue::List(vec![
                        SymbolicValue::Literal(LiteralValue::Integer(42)),
                    ]))),
                    Box::new(SymbolicValue::Variable("config.IDX".into())),
                ),
                line: 1,
                column: 0,
            }],
        );

        resolve_cross_function(&mut store, &["func".into()], &HashMap::new());

        let resolved = store.resolve_at("func", "elem", 10);
        assert_eq!(
            resolved,
            SymbolicValue::Index(
                Box::new(SymbolicValue::Literal(LiteralValue::List(vec![
                    SymbolicValue::Literal(LiteralValue::Integer(42)),
                ]))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(0))),
            )
        );
    }

    #[test]
    fn test_empty_topo_order_is_noop() {
        let mut store = ValueStore::new();
        store
            .return_values
            .insert("foo".into(), SymbolicValue::Variable("config.X".into()));

        resolve_cross_function(&mut store, &[], &HashMap::new());

        // Nothing should be resolved since topo_order is empty.
        assert_eq!(
            store.return_value("foo"),
            SymbolicValue::Variable("config.X".into())
        );
    }
}
