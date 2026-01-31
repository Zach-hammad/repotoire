//! Fast indexed call resolution (REPO-406)
//!
//! Replaces Python's 4 linear scans with O(1) hash-based lookups.
//! Pre-indexes entities by name, suffix, and file for instant resolution.

use rustc_hash::FxHashMap;

/// Node type for entities
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum NodeType {
    Class,
    Function,
    Module,
    File,
    Variable,
    Unknown,
}

impl NodeType {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "class" => NodeType::Class,
            "function" => NodeType::Function,
            "module" => NodeType::Module,
            "file" => NodeType::File,
            "variable" => NodeType::Variable,
            _ => NodeType::Unknown,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            NodeType::Class => "Class",
            NodeType::Function => "Function",
            NodeType::Module => "Module",
            NodeType::File => "File",
            NodeType::Variable => "Variable",
            NodeType::Unknown => "Unknown",
        }
    }
}

/// Entity information for resolution
#[derive(Debug, Clone)]
pub struct EntityInfo {
    pub qualified_name: String,
    pub name: String,
    pub node_type: NodeType,
    pub file_path: String,
}

/// Pre-indexed call resolver for O(1) lookups
#[derive(Debug, Default)]
pub struct CallResolver {
    /// Map from simple name to list of (qualified_name, node_type)
    by_name: FxHashMap<String, Vec<(String, NodeType)>>,

    /// Map from qualified_name suffix to list of qualified_names
    /// e.g., "::method:" -> ["module.Class.method", "other.Class.method"]
    by_suffix: FxHashMap<String, Vec<String>>,

    /// Map from (file_path, name) to qualified_name
    by_file_and_name: FxHashMap<(String, String), String>,

    /// Map from qualified_name to full entity info
    entities: FxHashMap<String, EntityInfo>,
}

impl CallResolver {
    /// Create a new resolver from a list of entities
    pub fn new(entities: Vec<EntityInfo>) -> Self {
        let mut resolver = CallResolver::default();

        for entity in entities {
            let name = entity.name.clone();
            let qname = entity.qualified_name.clone();
            let file_path = entity.file_path.clone();
            let node_type = entity.node_type;

            // Index by simple name
            resolver
                .by_name
                .entry(name.clone())
                .or_default()
                .push((qname.clone(), node_type));

            // Index by suffix patterns (::name: and .name:)
            let suffix1 = format!("::{}:", name);
            let suffix2 = format!(".{}:", name);
            resolver
                .by_suffix
                .entry(suffix1)
                .or_default()
                .push(qname.clone());
            resolver
                .by_suffix
                .entry(suffix2)
                .or_default()
                .push(qname.clone());

            // Index by file and name
            if !file_path.is_empty() {
                resolver
                    .by_file_and_name
                    .insert((file_path.clone(), name.clone()), qname.clone());
            }

            // Store full entity info
            resolver.entities.insert(qname, entity);
        }

        resolver
    }

    /// Resolve a callee name to a qualified name.
    ///
    /// # Arguments
    /// * `callee` - The callee name to resolve (e.g., "foo", "Class.method")
    /// * `caller_file` - The file containing the call site
    /// * `is_self_call` - Whether this is a self.method() call
    /// * `caller_class` - The class name if this is a method call
    ///
    /// # Returns
    /// The resolved qualified name, or None if not found
    pub fn resolve(
        &self,
        callee: &str,
        caller_file: &str,
        is_self_call: bool,
        caller_class: Option<&str>,
    ) -> Option<String> {
        // Strategy 1: Self-call resolution
        if is_self_call {
            if let Some(class_name) = caller_class {
                // Look for method in the same class
                if let Some(entities) = self.by_name.get(callee) {
                    for (qname, node_type) in entities {
                        if *node_type == NodeType::Function && qname.contains(class_name) {
                            return Some(qname.clone());
                        }
                    }
                }
            }
        }

        // Strategy 2: Exact name match (prefer Class for capitalized names)
        if let Some(entities) = self.by_name.get(callee) {
            // If callee starts with uppercase, prefer Class
            let prefer_class = callee.chars().next().map(|c| c.is_uppercase()).unwrap_or(false);

            if prefer_class {
                for (qname, node_type) in entities {
                    if *node_type == NodeType::Class {
                        return Some(qname.clone());
                    }
                }
            }

            // Return first match
            if let Some((qname, _)) = entities.first() {
                return Some(qname.clone());
            }
        }

        // Strategy 3: Suffix match (for qualified calls like "module.func")
        let suffix1 = format!("::{}:", callee);
        let suffix2 = format!(".{}:", callee);

        if let Some(matches) = self.by_suffix.get(&suffix1) {
            if let Some(qname) = matches.first() {
                return Some(qname.clone());
            }
        }

        if let Some(matches) = self.by_suffix.get(&suffix2) {
            if let Some(qname) = matches.first() {
                return Some(qname.clone());
            }
        }

        // Strategy 4: Same file match
        if !caller_file.is_empty() {
            if let Some(qname) = self.by_file_and_name.get(&(caller_file.to_string(), callee.to_string())) {
                return Some(qname.clone());
            }
        }

        // Strategy 5: Return callee as-is (external reference)
        None
    }

    /// Resolve multiple calls in batch (parallel when beneficial)
    pub fn resolve_batch(
        &self,
        calls: Vec<(String, String, bool, Option<String>)>, // (callee, caller_file, is_self_call, caller_class)
    ) -> Vec<Option<String>> {
        // For small batches, sequential is faster
        if calls.len() < 100 {
            calls
                .into_iter()
                .map(|(callee, caller_file, is_self_call, caller_class)| {
                    self.resolve(&callee, &caller_file, is_self_call, caller_class.as_deref())
                })
                .collect()
        } else {
            // For large batches, use rayon
            use rayon::prelude::*;
            calls
                .into_par_iter()
                .map(|(callee, caller_file, is_self_call, caller_class)| {
                    self.resolve(&callee, &caller_file, is_self_call, caller_class.as_deref())
                })
                .collect()
        }
    }
}

/// Python-friendly function to create resolver and resolve calls in one step
pub fn resolve_calls_indexed(
    entities: Vec<(String, String, String, String)>, // (qualified_name, name, node_type, file_path)
    calls: Vec<(String, String, bool, Option<String>)>, // (callee, caller_file, is_self_call, caller_class)
) -> Vec<Option<String>> {
    // Convert to EntityInfo
    let entity_infos: Vec<EntityInfo> = entities
        .into_iter()
        .map(|(qname, name, node_type, file_path)| EntityInfo {
            qualified_name: qname,
            name,
            node_type: NodeType::from_str(&node_type),
            file_path,
        })
        .collect();

    // Create resolver
    let resolver = CallResolver::new(entity_infos);

    // Resolve all calls
    resolver.resolve_batch(calls)
}

/// Create a resolver instance for repeated use (Python wrapper)
pub fn create_call_resolver(
    entities: Vec<(String, String, String, String)>,
) -> CallResolver {
    let entity_infos: Vec<EntityInfo> = entities
        .into_iter()
        .map(|(qname, name, node_type, file_path)| EntityInfo {
            qualified_name: qname,
            name,
            node_type: NodeType::from_str(&node_type),
            file_path,
        })
        .collect();

    CallResolver::new(entity_infos)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_resolver() -> CallResolver {
        let entities = vec![
            EntityInfo {
                qualified_name: "mymodule.MyClass".to_string(),
                name: "MyClass".to_string(),
                node_type: NodeType::Class,
                file_path: "mymodule.py".to_string(),
            },
            EntityInfo {
                qualified_name: "mymodule.MyClass.my_method".to_string(),
                name: "my_method".to_string(),
                node_type: NodeType::Function,
                file_path: "mymodule.py".to_string(),
            },
            EntityInfo {
                qualified_name: "mymodule.helper_func".to_string(),
                name: "helper_func".to_string(),
                node_type: NodeType::Function,
                file_path: "mymodule.py".to_string(),
            },
            EntityInfo {
                qualified_name: "other.helper_func".to_string(),
                name: "helper_func".to_string(),
                node_type: NodeType::Function,
                file_path: "other.py".to_string(),
            },
        ];
        CallResolver::new(entities)
    }

    #[test]
    fn test_exact_match() {
        let resolver = create_test_resolver();

        // Class match (capitalized)
        let result = resolver.resolve("MyClass", "", false, None);
        assert_eq!(result, Some("mymodule.MyClass".to_string()));
    }

    #[test]
    fn test_self_call() {
        let resolver = create_test_resolver();

        // self.my_method() from MyClass
        let result = resolver.resolve("my_method", "mymodule.py", true, Some("MyClass"));
        assert_eq!(result, Some("mymodule.MyClass.my_method".to_string()));
    }

    #[test]
    fn test_same_file() {
        let resolver = create_test_resolver();

        // Call to helper_func from same file
        let result = resolver.resolve("helper_func", "mymodule.py", false, None);
        assert!(result.is_some());
    }

    #[test]
    fn test_not_found() {
        let resolver = create_test_resolver();

        let result = resolver.resolve("nonexistent", "", false, None);
        assert!(result.is_none());
    }

    #[test]
    fn test_batch_resolve() {
        let resolver = create_test_resolver();

        let calls = vec![
            ("MyClass".to_string(), "".to_string(), false, None),
            ("helper_func".to_string(), "mymodule.py".to_string(), false, None),
            ("nonexistent".to_string(), "".to_string(), false, None),
        ];

        let results = resolver.resolve_batch(calls);
        assert_eq!(results.len(), 3);
        assert!(results[0].is_some());
        assert!(results[1].is_some());
        assert!(results[2].is_none());
    }
}
