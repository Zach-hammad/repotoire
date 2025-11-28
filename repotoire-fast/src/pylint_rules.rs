use rustpython_parser::ast::{Stmt, Suite, Expr, StmtClassDef, ExceptHandler};
use std::collections::HashSet;
use rustc_hash::FxHashMap;
use line_numbers::LinePositions;
use std::path::Path;

pub struct Finding {
    pub code: String,
    pub message: String,
    pub line: usize,
}

/// Trait for pylint rules that check AST patterns
pub trait PylintRule {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding>;
}

/// R0902: too-many-instance-attributes
pub struct TooManyAttributes {
    pub threshold: usize,
}

/// R0903: too-few-public-methods
pub struct TooFewPublicMethods {
    pub threshold: usize,
}

impl PylintRule for TooManyAttributes {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions = LinePositions::from(source);

        // Build a map of class names to their dataclass attributes (for inheritance)
        let mut class_attrs: FxHashMap<String, HashSet<String>> = FxHashMap::default();
        let mut class_bases: FxHashMap<String, Vec<String>> = FxHashMap::default();
        let mut dataclass_set: HashSet<String> = HashSet::new();

        // First pass: collect all class info
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt {
                let class_name = class.name.to_string();

                // Check if this is a dataclass
                let is_dc = class.decorator_list.iter().any(|dec| {
                    match dec {
                        Expr::Name(name) => name.id.as_str() == "dataclass",
                        Expr::Attribute(attr) => attr.attr.as_str() == "dataclass",
                        Expr::Call(call) => match call.func.as_ref() {
                            Expr::Name(name) => name.id.as_str() == "dataclass",
                            Expr::Attribute(attr) => attr.attr.as_str() == "dataclass",
                            _ => false,
                        },
                        _ => false,
                    }
                });

                if is_dc {
                    dataclass_set.insert(class_name.clone());
                }

                // Collect base classes
                let bases: Vec<String> = class.bases.iter()
                    .filter_map(|base| {
                        match base {
                            Expr::Name(name) => Some(name.id.to_string()),
                            Expr::Attribute(attr) => Some(attr.attr.to_string()),
                            _ => None,
                        }
                    })
                    .collect();
                class_bases.insert(class_name.clone(), bases);

                // Collect this class's own attributes
                let mut attrs: HashSet<String> = HashSet::new();
                Self::collect_class_own_attributes(class, is_dc, &mut attrs);
                class_attrs.insert(class_name, attrs);
            }
        }

        // Second pass: count total attributes including inherited
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt {
                let class_name = class.name.to_string();
                let is_dataclass = dataclass_set.contains(&class_name);

                // For dataclasses, count inherited attributes too
                let count = if is_dataclass {
                    Self::count_total_attributes(&class_name, &class_attrs, &class_bases, &dataclass_set, &mut HashSet::new())
                } else {
                    class_attrs.get(&class_name).map(|a| a.len()).unwrap_or(0)
                };

                if count > self.threshold {
                    let line_num = line_positions.from_offset(class.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0902".to_string(),
                        message: format!("Class {} has {} instance attributes (max {})", class.name, count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }
}

impl TooManyAttributes {
    fn collect_class_own_attributes(class: &StmtClassDef, is_dataclass: bool, attrs: &mut HashSet<String>) {
        for class_stmt in &class.body {
            match class_stmt {
                // For dataclasses: count annotated class variables as instance attributes
                Stmt::AnnAssign(ann) if is_dataclass => {
                    if let Expr::Name(name) = ann.target.as_ref() {
                        // Skip ClassVar annotations
                        let is_class_var = match &ann.annotation.as_ref() {
                            Expr::Subscript(sub) => {
                                match sub.value.as_ref() {
                                    Expr::Name(n) => n.id.as_str() == "ClassVar",
                                    Expr::Attribute(a) => a.attr.as_str() == "ClassVar",
                                    _ => false,
                                }
                            }
                            _ => false,
                        };
                        if !is_class_var {
                            attrs.insert(name.id.to_string());
                        }
                    }
                }
                // For regular classes: count self.x = y in __init__
                Stmt::FunctionDef(func) if func.name.as_str() == "__init__" => {
                    Self::collect_init_attributes(&func.body, attrs);
                }
                _ => {}
            }
        }
    }

    fn count_total_attributes(
        class_name: &str,
        class_attrs: &FxHashMap<String, HashSet<String>>,
        class_bases: &FxHashMap<String, Vec<String>>,
        dataclass_set: &HashSet<String>,
        visited: &mut HashSet<String>,
    ) -> usize {
        if visited.contains(class_name) {
            return 0;
        }
        visited.insert(class_name.to_string());

        // Get this class's own attributes
        let own_count = class_attrs.get(class_name).map(|a| a.len()).unwrap_or(0);

        // For dataclasses, add inherited attributes from parent dataclasses
        let inherited_count: usize = if dataclass_set.contains(class_name) {
            class_bases.get(class_name)
                .map(|bases| {
                    bases.iter()
                        .filter(|base| dataclass_set.contains(*base))
                        .map(|base| Self::count_total_attributes(base, class_attrs, class_bases, dataclass_set, visited))
                        .sum()
                })
                .unwrap_or(0)
        } else {
            0
        };

        own_count + inherited_count
    }

    fn collect_init_attributes(stmts: &[Stmt], attrs: &mut HashSet<String>) {
        for stmt in stmts {
            match stmt {
                Stmt::Assign(assign) => {
                    for target in &assign.targets {
                        if let Expr::Attribute(attr) = target {
                            if let Expr::Name(name) = attr.value.as_ref() {
                                if name.id.as_str() == "self" {
                                    attrs.insert(attr.attr.to_string());
                                }
                            }
                        }
                    }
                }
                Stmt::AnnAssign(ann) => {
                    if let Expr::Attribute(attr) = ann.target.as_ref() {
                        if let Expr::Name(name) = attr.value.as_ref() {
                            if name.id.as_str() == "self" {
                                attrs.insert(attr.attr.to_string());
                            }
                        }
                    }
                }
                // Recurse into if/for/while/try blocks
                Stmt::If(if_stmt) => {
                    Self::collect_init_attributes(&if_stmt.body, attrs);
                    Self::collect_init_attributes(&if_stmt.orelse, attrs);
                }
                Stmt::For(for_stmt) => {
                    Self::collect_init_attributes(&for_stmt.body, attrs);
                }
                Stmt::While(while_stmt) => {
                    Self::collect_init_attributes(&while_stmt.body, attrs);
                }
                Stmt::Try(try_stmt) => {
                    Self::collect_init_attributes(&try_stmt.body, attrs);
                    for handler in &try_stmt.handlers {
                        let ExceptHandler::ExceptHandler(h) = handler;
                        Self::collect_init_attributes(&h.body, attrs);
                    }
                }
                Stmt::With(with_stmt) => {
                    Self::collect_init_attributes(&with_stmt.body, attrs);
                }
                _ => {}
            }
        }
    }
}

fn count_public_methods(class: &StmtClassDef) -> usize {
    class.body.iter().filter(|stmt| {
            match stmt {
                Stmt::FunctionDef(func) => !func.name.as_str().starts_with("_"),
                _ => false,
            }
        }).count()
}

/// Check if a class should be excluded from R0903 (too-few-public-methods)
/// Excludes: Exception subclasses, Enum subclasses, dataclasses, TypedDict, Protocol, ABC
fn should_exclude_from_r0903(class: &StmtClassDef) -> bool {
    // Common base classes that should be excluded from R0903
    const EXCLUDED_BASES: &[&str] = &[
        // Exception types
        "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
        "IndexError", "AttributeError", "RuntimeError", "StopIteration",
        "OSError", "IOError", "ImportError", "LookupError", "ArithmeticError",
        "AssertionError", "EnvironmentError", "EOFError", "FloatingPointError",
        "GeneratorExit", "MemoryError", "NameError", "NotImplementedError",
        "OverflowError", "RecursionError", "ReferenceError", "SystemError",
        "SystemExit", "UnboundLocalError", "UnicodeError", "ZeroDivisionError",
        "Warning", "UserWarning", "DeprecationWarning", "PendingDeprecationWarning",
        "SyntaxWarning", "RuntimeWarning", "FutureWarning", "ImportWarning",
        "UnicodeWarning", "BytesWarning", "ResourceWarning",
        // Enum types
        "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "auto",
        // Typing/Protocol types
        "TypedDict", "Protocol", "ABC", "ABCMeta",
        // NamedTuple
        "NamedTuple",
    ];

    // Check decorators for @dataclass, @attrs, etc.
    for decorator in &class.decorator_list {
        let dec_name = match decorator {
            Expr::Name(name) => name.id.as_str(),
            Expr::Attribute(attr) => attr.attr.as_str(),
            Expr::Call(call) => match call.func.as_ref() {
                Expr::Name(name) => name.id.as_str(),
                Expr::Attribute(attr) => attr.attr.as_str(),
                _ => continue,
            },
            _ => continue,
        };

        // Dataclass and similar decorators
        if matches!(dec_name, "dataclass" | "dataclasses.dataclass" | "attrs" | "attr.s" | "define" | "frozen") {
            return true;
        }
    }

    // Check base classes
    for base in &class.bases {
        let base_name = match base {
            Expr::Name(name) => name.id.as_str(),
            Expr::Attribute(attr) => attr.attr.as_str(),
            _ => continue,
        };

        // Check if base is a known excluded type
        if EXCLUDED_BASES.contains(&base_name) {
            return true;
        }

        // Check if base name ends with "Error" or "Exception"
        if base_name.ends_with("Error") || base_name.ends_with("Exception") {
            return true;
        }
    }

    false
}

impl PylintRule for TooFewPublicMethods {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions = LinePositions::from(source);
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt {
                // Skip special classes that don't need public methods
                // (exceptions, enums, dataclasses, protocols, etc.)
                if should_exclude_from_r0903(class) {
                    continue;
                }

                let count = count_public_methods(class);
                if count < self.threshold {
                    let line_num = line_positions.from_offset(class.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0903".to_string(),
                        message: format!("Class {} has {} public methods (min {})", class.name, count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }
}

fn module_imports_self(path: &str) -> String {
    let path = Path::new(path);
    let stem = path.file_stem().unwrap_or_default();

    if stem == "__init__" {
        path.parent().and_then(|p| p.file_name()).map(|s| s.to_string_lossy().to_string()).unwrap_or_default()
    } else {
        stem.to_string_lossy().to_string()
    }
}

// R0401: import-self / cyclic-import
pub fn check_import_self(ast: &Suite, source: &str, module_path: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);
    let module_name = module_imports_self(module_path);
    for stmt in ast {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    if alias.name.as_str() == module_name {
                        let line_num = line_positions.from_offset(import.range.start().into()).as_usize();
                        findings.push(Finding {
                            code: "R0401".to_string(),
                            message: format!("Importing self in module {}", module_name),
                            line: line_num + 1,
                        });
                    }
                }
            }
            Stmt::ImportFrom(import) => {
                if let Some(module) = &import.module {
                    if module.as_str() == module_name {
                        let line_num = line_positions.from_offset(import.range.start().into()).as_usize();
                        findings.push(Finding {
                            code: "R0401".to_string(),
                            message: format!("Importing from self in module {}", module_name),
                            line: line_num + 1,
                        });
                    }
                }
            }
            _ => {}
        }
    }
    findings
}

// C0302: too-many-lines
pub fn check_too_many_lines(source: &str, max_lines: usize) -> Vec<Finding> {
    let line_count = source.lines().count();
    if line_count > max_lines {
        vec![Finding {
            code: "C0302".to_string(),
            message: format!("Module has too many lines ({} > {} lines)", line_count, max_lines),
            line: 1,
        }]
    } else {
        vec![]
    }
}

// R0901: too-many-ancestors
pub fn check_too_many_ancestors(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    // Build a map of class names to their direct base classes
    let mut class_bases: FxHashMap<String, Vec<String>> = FxHashMap::default();

    for stmt in ast {
        if let Stmt::ClassDef(class) = stmt {
            let bases: Vec<String> = class.bases.iter()
                .filter_map(|base| {
                    match base {
                        Expr::Name(name) => Some(name.id.to_string()),
                        Expr::Attribute(attr) => Some(attr.attr.to_string()),
                        _ => None,
                    }
                })
                .collect();
            class_bases.insert(class.name.to_string(), bases);
        }
    }

    // Count ancestors for each class
    for stmt in ast {
        if let Stmt::ClassDef(class) = stmt {
            let count = count_ancestors(&class.name.to_string(), &class_bases, &mut HashSet::new());
            if count > threshold {
                let line_num = line_positions.from_offset(class.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "R0901".to_string(),
                    message: format!("Class {} has {} ancestors (max {})", class.name, count, threshold),
                    line: line_num + 1,
                });
            }
        }
    }

    findings
}

fn count_ancestors(
    class_name: &str,
    class_bases: &FxHashMap<String, Vec<String>>,
    visited: &mut HashSet<String>,
) -> usize {
    if visited.contains(class_name) {
        return 0;
    }
    visited.insert(class_name.to_string());

    if let Some(bases) = class_bases.get(class_name) {
        let direct = bases.len();
        let indirect: usize = bases.iter()
            .map(|b| count_ancestors(b, class_bases, visited))
            .sum();
        direct + indirect
    } else {
        0
    }
}

/// Check if a class is a dataclass (has @dataclass decorator)
fn is_dataclass(class: &StmtClassDef) -> bool {
    class.decorator_list.iter().any(|dec| {
        match dec {
            Expr::Name(name) => name.id.as_str() == "dataclass",
            Expr::Attribute(attr) => attr.attr.as_str() == "dataclass",
            Expr::Call(call) => match call.func.as_ref() {
                Expr::Name(name) => name.id.as_str() == "dataclass",
                Expr::Attribute(attr) => attr.attr.as_str() == "dataclass",
                _ => false,
            },
            _ => false,
        }
    })
}

// W0201: attribute-defined-outside-init
pub fn check_attribute_defined_outside_init(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        if let Stmt::ClassDef(class) = stmt {
            // Skip dataclasses - their attributes are defined via class-level annotations
            if is_dataclass(class) {
                continue;
            }

            let mut init_attrs: HashSet<String> = HashSet::new();

            // Also collect class-level annotated attributes (for dataclass-like patterns)
            for class_stmt in &class.body {
                if let Stmt::AnnAssign(ann) = class_stmt {
                    if let Expr::Name(name) = ann.target.as_ref() {
                        init_attrs.insert(name.id.to_string());
                    }
                }
            }

            for class_stmt in &class.body {
                if let Stmt::FunctionDef(func) = class_stmt {
                    if func.name.as_str() == "__init__" {
                        collect_self_attributes(&func.body, &mut init_attrs);
                    }
                }
            }

            for class_stmt in &class.body {
                if let Stmt::FunctionDef(func) = class_stmt {
                    if func.name.as_str() != "__init__" {
                        let mut method_attrs: HashSet<String> = HashSet::new();
                        collect_self_attribute_assignments(&func.body, &mut method_attrs);

                        for attr in method_attrs {
                            if !init_attrs.contains(&attr) {
                                if let Some(line) = find_attr_assignment_line(&func.body, &attr, &line_positions) {
                                    findings.push(Finding {
                                        code: "W0201".to_string(),
                                        message: format!("Attribute '{}' defined outside __init__ in {}.{}", attr, class.name, func.name),
                                        line,
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    findings
}

fn collect_self_attributes(stmts: &[Stmt], attrs: &mut HashSet<String>) {
    for stmt in stmts {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if let Expr::Attribute(attr) = target {
                        if let Expr::Name(name) = attr.value.as_ref() {
                            if name.id.as_str() == "self" {
                                attrs.insert(attr.attr.to_string());
                            }
                        }
                    }
                }
            }
            Stmt::AnnAssign(ann) => {
                if let Expr::Attribute(attr) = ann.target.as_ref() {
                    if let Expr::Name(name) = attr.value.as_ref() {
                        if name.id.as_str() == "self" {
                            attrs.insert(attr.attr.to_string());
                        }
                    }
                }
            }
            Stmt::If(if_stmt) => {
                collect_self_attributes(&if_stmt.body, attrs);
                collect_self_attributes(&if_stmt.orelse, attrs);
            }
            Stmt::For(for_stmt) => {
                collect_self_attributes(&for_stmt.body, attrs);
            }
            Stmt::While(while_stmt) => {
                collect_self_attributes(&while_stmt.body, attrs);
            }
            Stmt::With(with_stmt) => {
                collect_self_attributes(&with_stmt.body, attrs);
            }
            Stmt::Try(try_stmt) => {
                collect_self_attributes(&try_stmt.body, attrs);
                collect_self_attributes(&try_stmt.orelse, attrs);
                collect_self_attributes(&try_stmt.finalbody, attrs);
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    collect_self_attributes(&h.body, attrs);
                }
            }
            _ => {}
        }
    }
}

fn collect_self_attribute_assignments(stmts: &[Stmt], attrs: &mut HashSet<String>) {
    for stmt in stmts {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if let Expr::Attribute(attr) = target {
                        if let Expr::Name(name) = attr.value.as_ref() {
                            if name.id.as_str() == "self" {
                                attrs.insert(attr.attr.to_string());
                            }
                        }
                    }
                }
            }
            Stmt::AnnAssign(ann) => {
                if let Expr::Attribute(attr) = ann.target.as_ref() {
                    if let Expr::Name(name) = attr.value.as_ref() {
                        if name.id.as_str() == "self" {
                            attrs.insert(attr.attr.to_string());
                        }
                    }
                }
            }
            Stmt::If(if_stmt) => {
                collect_self_attribute_assignments(&if_stmt.body, attrs);
                collect_self_attribute_assignments(&if_stmt.orelse, attrs);
            }
            Stmt::For(for_stmt) => {
                collect_self_attribute_assignments(&for_stmt.body, attrs);
            }
            Stmt::While(while_stmt) => {
                collect_self_attribute_assignments(&while_stmt.body, attrs);
            }
            Stmt::With(with_stmt) => {
                collect_self_attribute_assignments(&with_stmt.body, attrs);
            }
            Stmt::Try(try_stmt) => {
                collect_self_attribute_assignments(&try_stmt.body, attrs);
                collect_self_attribute_assignments(&try_stmt.orelse, attrs);
                collect_self_attribute_assignments(&try_stmt.finalbody, attrs);
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    collect_self_attribute_assignments(&h.body, attrs);
                }
            }
            _ => {}
        }
    }
}

fn find_attr_assignment_line(stmts: &[Stmt], attr_name: &str, line_positions: &LinePositions) -> Option<usize> {
    for stmt in stmts {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if let Expr::Attribute(attr) = target {
                        if let Expr::Name(name) = attr.value.as_ref() {
                            if name.id.as_str() == "self" && attr.attr.as_str() == attr_name {
                                return Some(line_positions.from_offset(assign.range.start().into()).as_usize() + 1);
                            }
                        }
                    }
                }
            }
            Stmt::AnnAssign(ann) => {
                if let Expr::Attribute(attr) = ann.target.as_ref() {
                    if let Expr::Name(name) = attr.value.as_ref() {
                        if name.id.as_str() == "self" && attr.attr.as_str() == attr_name {
                            return Some(line_positions.from_offset(ann.range.start().into()).as_usize() + 1);
                        }
                    }
                }
            }
            Stmt::If(if_stmt) => {
                if let Some(line) = find_attr_assignment_line(&if_stmt.body, attr_name, line_positions) {
                    return Some(line);
                }
                if let Some(line) = find_attr_assignment_line(&if_stmt.orelse, attr_name, line_positions) {
                    return Some(line);
                }
            }
            Stmt::For(for_stmt) => {
                if let Some(line) = find_attr_assignment_line(&for_stmt.body, attr_name, line_positions) {
                    return Some(line);
                }
            }
            Stmt::While(while_stmt) => {
                if let Some(line) = find_attr_assignment_line(&while_stmt.body, attr_name, line_positions) {
                    return Some(line);
                }
            }
            Stmt::With(with_stmt) => {
                if let Some(line) = find_attr_assignment_line(&with_stmt.body, attr_name, line_positions) {
                    return Some(line);
                }
            }
            Stmt::Try(try_stmt) => {
                if let Some(line) = find_attr_assignment_line(&try_stmt.body, attr_name, line_positions) {
                    return Some(line);
                }
                if let Some(line) = find_attr_assignment_line(&try_stmt.orelse, attr_name, line_positions) {
                    return Some(line);
                }
                if let Some(line) = find_attr_assignment_line(&try_stmt.finalbody, attr_name, line_positions) {
                    return Some(line);
                }
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    if let Some(line) = find_attr_assignment_line(&h.body, attr_name, line_positions) {
                        return Some(line);
                    }
                }
            }
            _ => {}
        }
    }
    None
}

// W0212: protected-access
pub fn check_protected_access(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        check_protected_access_in_stmts(&func.body, &class.name.to_string(), &line_positions, &mut findings);
                    }
                }
            }
            Stmt::FunctionDef(func) => {
                check_protected_access_in_stmts(&func.body, "", &line_positions, &mut findings);
            }
            _ => {
                check_protected_access_in_stmt(stmt, "", &line_positions, &mut findings);
            }
        }
    }

    findings
}

fn check_protected_access_in_stmts(stmts: &[Stmt], current_class: &str, line_positions: &LinePositions, findings: &mut Vec<Finding>) {
    for stmt in stmts {
        check_protected_access_in_stmt(stmt, current_class, line_positions, findings);
    }
}

fn check_protected_access_in_stmt(stmt: &Stmt, current_class: &str, line_positions: &LinePositions, findings: &mut Vec<Finding>) {
    match stmt {
        Stmt::Expr(expr_stmt) => {
            check_protected_access_in_expr(&expr_stmt.value, current_class, line_positions, findings);
        }
        Stmt::Assign(assign) => {
            check_protected_access_in_expr(&assign.value, current_class, line_positions, findings);
        }
        Stmt::AugAssign(aug) => {
            check_protected_access_in_expr(&aug.value, current_class, line_positions, findings);
        }
        Stmt::AnnAssign(ann) => {
            if let Some(val) = &ann.value {
                check_protected_access_in_expr(val, current_class, line_positions, findings);
            }
        }
        Stmt::Return(ret) => {
            if let Some(val) = &ret.value {
                check_protected_access_in_expr(val, current_class, line_positions, findings);
            }
        }
        Stmt::If(if_stmt) => {
            check_protected_access_in_expr(&if_stmt.test, current_class, line_positions, findings);
            check_protected_access_in_stmts(&if_stmt.body, current_class, line_positions, findings);
            check_protected_access_in_stmts(&if_stmt.orelse, current_class, line_positions, findings);
        }
        Stmt::For(for_stmt) => {
            check_protected_access_in_expr(&for_stmt.iter, current_class, line_positions, findings);
            check_protected_access_in_stmts(&for_stmt.body, current_class, line_positions, findings);
            check_protected_access_in_stmts(&for_stmt.orelse, current_class, line_positions, findings);
        }
        Stmt::While(while_stmt) => {
            check_protected_access_in_expr(&while_stmt.test, current_class, line_positions, findings);
            check_protected_access_in_stmts(&while_stmt.body, current_class, line_positions, findings);
            check_protected_access_in_stmts(&while_stmt.orelse, current_class, line_positions, findings);
        }
        Stmt::With(with_stmt) => {
            for item in &with_stmt.items {
                check_protected_access_in_expr(&item.context_expr, current_class, line_positions, findings);
            }
            check_protected_access_in_stmts(&with_stmt.body, current_class, line_positions, findings);
        }
        Stmt::Try(try_stmt) => {
            check_protected_access_in_stmts(&try_stmt.body, current_class, line_positions, findings);
            for handler in &try_stmt.handlers {
                let ExceptHandler::ExceptHandler(h) = handler;
                check_protected_access_in_stmts(&h.body, current_class, line_positions, findings);
            }
            check_protected_access_in_stmts(&try_stmt.orelse, current_class, line_positions, findings);
            check_protected_access_in_stmts(&try_stmt.finalbody, current_class, line_positions, findings);
        }
        Stmt::Assert(assert_stmt) => {
            check_protected_access_in_expr(&assert_stmt.test, current_class, line_positions, findings);
            if let Some(msg) = &assert_stmt.msg {
                check_protected_access_in_expr(msg, current_class, line_positions, findings);
            }
        }
        _ => {}
    }
}

fn check_protected_access_in_expr(expr: &Expr, current_class: &str, line_positions: &LinePositions, findings: &mut Vec<Finding>) {
    match expr {
        Expr::Attribute(attr) => {
            let attr_name = attr.attr.as_str();
            if attr_name.starts_with('_') && !attr_name.starts_with("__") {
                let is_self_access = match attr.value.as_ref() {
                    Expr::Name(name) => name.id.as_str() == "self" || name.id.as_str() == "cls",
                    _ => false,
                };

                if !is_self_access && !current_class.is_empty() {
                    let line_num = line_positions.from_offset(attr.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "W0212".to_string(),
                        message: format!("Access to protected member '{}'", attr_name),
                        line: line_num + 1,
                    });
                } else if current_class.is_empty() {
                    let line_num = line_positions.from_offset(attr.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "W0212".to_string(),
                        message: format!("Access to protected member '{}'", attr_name),
                        line: line_num + 1,
                    });
                }
            }
            check_protected_access_in_expr(&attr.value, current_class, line_positions, findings);
        }
        Expr::Call(call) => {
            check_protected_access_in_expr(&call.func, current_class, line_positions, findings);
            for arg in &call.args {
                check_protected_access_in_expr(arg, current_class, line_positions, findings);
            }
            for kw in &call.keywords {
                check_protected_access_in_expr(&kw.value, current_class, line_positions, findings);
            }
        }
        Expr::BinOp(binop) => {
            check_protected_access_in_expr(&binop.left, current_class, line_positions, findings);
            check_protected_access_in_expr(&binop.right, current_class, line_positions, findings);
        }
        Expr::Compare(cmp) => {
            check_protected_access_in_expr(&cmp.left, current_class, line_positions, findings);
            for comp in &cmp.comparators {
                check_protected_access_in_expr(comp, current_class, line_positions, findings);
            }
        }
        Expr::BoolOp(boolop) => {
            for val in &boolop.values {
                check_protected_access_in_expr(val, current_class, line_positions, findings);
            }
        }
        Expr::IfExp(ifexp) => {
            check_protected_access_in_expr(&ifexp.test, current_class, line_positions, findings);
            check_protected_access_in_expr(&ifexp.body, current_class, line_positions, findings);
            check_protected_access_in_expr(&ifexp.orelse, current_class, line_positions, findings);
        }
        Expr::List(list) => {
            for elt in &list.elts {
                check_protected_access_in_expr(elt, current_class, line_positions, findings);
            }
        }
        Expr::Tuple(tuple) => {
            for elt in &tuple.elts {
                check_protected_access_in_expr(elt, current_class, line_positions, findings);
            }
        }
        Expr::Dict(dict) => {
            for key in dict.keys.iter().flatten() {
                check_protected_access_in_expr(key, current_class, line_positions, findings);
            }
            for val in &dict.values {
                check_protected_access_in_expr(val, current_class, line_positions, findings);
            }
        }
        Expr::Subscript(sub) => {
            check_protected_access_in_expr(&sub.value, current_class, line_positions, findings);
            check_protected_access_in_expr(&sub.slice, current_class, line_positions, findings);
        }
        _ => {}
    }
}

// W0614: unused-wildcard-import
pub fn check_unused_wildcard_import(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        if let Stmt::ImportFrom(import) = stmt {
            for alias in &import.names {
                if alias.name.as_str() == "*" {
                    let module_name = import.module.as_ref()
                        .map(|m| m.to_string())
                        .unwrap_or_else(|| "unknown".to_string());
                    let line_num = line_positions.from_offset(import.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "W0614".to_string(),
                        message: format!("Unused wildcard import from {}", module_name),
                        line: line_num + 1,
                    });
                }
            }
        }
    }

    findings
}

// W0631: undefined-loop-variable
pub fn check_undefined_loop_variable(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                check_loop_vars_in_stmts(&func.body, &mut HashSet::new(), &line_positions, &mut findings);
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        check_loop_vars_in_stmts(&func.body, &mut HashSet::new(), &line_positions, &mut findings);
                    }
                }
            }
            _ => {}
        }
    }

    findings
}

fn check_loop_vars_in_stmts(
    stmts: &[Stmt],
    loop_vars: &mut HashSet<String>,
    line_positions: &LinePositions,
    findings: &mut Vec<Finding>,
) {
    let mut local_loop_vars: HashSet<String> = HashSet::new();

    for stmt in stmts {
        match stmt {
            Stmt::For(for_stmt) => {
                let mut new_vars: HashSet<String> = HashSet::new();
                collect_for_target_names(&for_stmt.target, &mut new_vars);

                let mut inner_loop_vars = loop_vars.clone();
                inner_loop_vars.extend(new_vars.clone());
                check_loop_vars_in_stmts(&for_stmt.body, &mut inner_loop_vars, line_positions, findings);
                check_loop_vars_in_stmts(&for_stmt.orelse, &mut inner_loop_vars, line_positions, findings);

                local_loop_vars.extend(new_vars);
            }
            Stmt::If(if_stmt) => {
                check_loop_var_usage_in_expr(&if_stmt.test, &local_loop_vars, line_positions, findings);
                check_loop_vars_in_stmts(&if_stmt.body, loop_vars, line_positions, findings);
                check_loop_vars_in_stmts(&if_stmt.orelse, loop_vars, line_positions, findings);
            }
            Stmt::While(while_stmt) => {
                check_loop_var_usage_in_expr(&while_stmt.test, &local_loop_vars, line_positions, findings);
                check_loop_vars_in_stmts(&while_stmt.body, loop_vars, line_positions, findings);
                check_loop_vars_in_stmts(&while_stmt.orelse, loop_vars, line_positions, findings);
            }
            Stmt::Return(ret) => {
                if let Some(val) = &ret.value {
                    check_loop_var_usage_in_expr(val, &local_loop_vars, line_positions, findings);
                }
            }
            Stmt::Expr(expr_stmt) => {
                check_loop_var_usage_in_expr(&expr_stmt.value, &local_loop_vars, line_positions, findings);
            }
            Stmt::Assign(assign) => {
                check_loop_var_usage_in_expr(&assign.value, &local_loop_vars, line_positions, findings);
            }
            _ => {}
        }
    }
}

fn collect_for_target_names(expr: &Expr, names: &mut HashSet<String>) {
    match expr {
        Expr::Name(name) => {
            names.insert(name.id.to_string());
        }
        Expr::Tuple(tuple) => {
            for elt in &tuple.elts {
                collect_for_target_names(elt, names);
            }
        }
        Expr::List(list) => {
            for elt in &list.elts {
                collect_for_target_names(elt, names);
            }
        }
        _ => {}
    }
}

fn check_loop_var_usage_in_expr(
    expr: &Expr,
    loop_vars: &HashSet<String>,
    line_positions: &LinePositions,
    findings: &mut Vec<Finding>,
) {
    match expr {
        Expr::Name(name) => {
            let var_name = name.id.as_str();
            if loop_vars.contains(var_name) {
                let line_num = line_positions.from_offset(name.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "W0631".to_string(),
                    message: format!("Using possibly undefined loop variable '{}'", var_name),
                    line: line_num + 1,
                });
            }
        }
        Expr::Attribute(attr) => {
            check_loop_var_usage_in_expr(&attr.value, loop_vars, line_positions, findings);
        }
        Expr::Call(call) => {
            check_loop_var_usage_in_expr(&call.func, loop_vars, line_positions, findings);
            for arg in &call.args {
                check_loop_var_usage_in_expr(arg, loop_vars, line_positions, findings);
            }
            for kw in &call.keywords {
                check_loop_var_usage_in_expr(&kw.value, loop_vars, line_positions, findings);
            }
        }
        Expr::BinOp(binop) => {
            check_loop_var_usage_in_expr(&binop.left, loop_vars, line_positions, findings);
            check_loop_var_usage_in_expr(&binop.right, loop_vars, line_positions, findings);
        }
        Expr::Compare(cmp) => {
            check_loop_var_usage_in_expr(&cmp.left, loop_vars, line_positions, findings);
            for comp in &cmp.comparators {
                check_loop_var_usage_in_expr(comp, loop_vars, line_positions, findings);
            }
        }
        Expr::BoolOp(boolop) => {
            for val in &boolop.values {
                check_loop_var_usage_in_expr(val, loop_vars, line_positions, findings);
            }
        }
        Expr::IfExp(ifexp) => {
            check_loop_var_usage_in_expr(&ifexp.test, loop_vars, line_positions, findings);
            check_loop_var_usage_in_expr(&ifexp.body, loop_vars, line_positions, findings);
            check_loop_var_usage_in_expr(&ifexp.orelse, loop_vars, line_positions, findings);
        }
        Expr::List(list) => {
            for elt in &list.elts {
                check_loop_var_usage_in_expr(elt, loop_vars, line_positions, findings);
            }
        }
        Expr::Tuple(tuple) => {
            for elt in &tuple.elts {
                check_loop_var_usage_in_expr(elt, loop_vars, line_positions, findings);
            }
        }
        Expr::Dict(dict) => {
            for key in dict.keys.iter().flatten() {
                check_loop_var_usage_in_expr(key, loop_vars, line_positions, findings);
            }
            for val in &dict.values {
                check_loop_var_usage_in_expr(val, loop_vars, line_positions, findings);
            }
        }
        Expr::Subscript(sub) => {
            check_loop_var_usage_in_expr(&sub.value, loop_vars, line_positions, findings);
            check_loop_var_usage_in_expr(&sub.slice, loop_vars, line_positions, findings);
        }
        _ => {}
    }
}

// C0104: disallowed-name
pub fn check_disallowed_name(ast: &Suite, source: &str, disallowed: &[&str]) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);
    let disallowed_set: HashSet<&str> = disallowed.iter().copied().collect();

    for stmt in ast {
        check_disallowed_in_stmt(stmt, &disallowed_set, &line_positions, &mut findings);
    }

    findings
}

fn check_disallowed_in_stmt(stmt: &Stmt, disallowed: &HashSet<&str>, line_positions: &LinePositions, findings: &mut Vec<Finding>) {
    match stmt {
        Stmt::Assign(assign) => {
            for target in &assign.targets {
                check_disallowed_in_target(target, disallowed, line_positions, findings);
            }
        }
        Stmt::AnnAssign(ann) => {
            check_disallowed_in_target(&ann.target, disallowed, line_positions, findings);
        }
        Stmt::FunctionDef(func) => {
            if disallowed.contains(func.name.as_str()) {
                let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "C0104".to_string(),
                    message: format!("Disallowed name '{}'", func.name),
                    line: line_num + 1,
                });
            }
            for arg in &func.args.args {
                if disallowed.contains(arg.def.arg.as_str()) {
                    let line_num = line_positions.from_offset(arg.def.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "C0104".to_string(),
                        message: format!("Disallowed name '{}'", arg.def.arg),
                        line: line_num + 1,
                    });
                }
            }
            for s in &func.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
        }
        Stmt::ClassDef(class) => {
            if disallowed.contains(class.name.as_str()) {
                let line_num = line_positions.from_offset(class.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "C0104".to_string(),
                    message: format!("Disallowed name '{}'", class.name),
                    line: line_num + 1,
                });
            }
            for s in &class.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
        }
        Stmt::For(for_stmt) => {
            check_disallowed_in_target(&for_stmt.target, disallowed, line_positions, findings);
            for s in &for_stmt.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
        }
        Stmt::If(if_stmt) => {
            for s in &if_stmt.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
            for s in &if_stmt.orelse {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
        }
        Stmt::While(while_stmt) => {
            for s in &while_stmt.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
        }
        Stmt::With(with_stmt) => {
            for item in &with_stmt.items {
                if let Some(vars) = &item.optional_vars {
                    check_disallowed_in_target(vars, disallowed, line_positions, findings);
                }
            }
            for s in &with_stmt.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
        }
        Stmt::Try(try_stmt) => {
            for s in &try_stmt.body {
                check_disallowed_in_stmt(s, disallowed, line_positions, findings);
            }
            for handler in &try_stmt.handlers {
                let ExceptHandler::ExceptHandler(h) = handler;
                if let Some(name) = &h.name {
                    if disallowed.contains(name.as_str()) {
                        let line_num = line_positions.from_offset(h.range.start().into()).as_usize();
                        findings.push(Finding {
                            code: "C0104".to_string(),
                            message: format!("Disallowed name '{}'", name),
                            line: line_num + 1,
                        });
                    }
                }
                for s in &h.body {
                    check_disallowed_in_stmt(s, disallowed, line_positions, findings);
                }
            }
        }
        _ => {}
    }
}

fn check_disallowed_in_target(expr: &Expr, disallowed: &HashSet<&str>, line_positions: &LinePositions, findings: &mut Vec<Finding>) {
    match expr {
        Expr::Name(name) => {
            if disallowed.contains(name.id.as_str()) {
                let line_num = line_positions.from_offset(name.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "C0104".to_string(),
                    message: format!("Disallowed name '{}'", name.id),
                    line: line_num + 1,
                });
            }
        }
        Expr::Tuple(tuple) => {
            for elt in &tuple.elts {
                check_disallowed_in_target(elt, disallowed, line_positions, findings);
            }
        }
        Expr::List(list) => {
            for elt in &list.elts {
                check_disallowed_in_target(elt, disallowed, line_positions, findings);
            }
        }
        _ => {}
    }
}
