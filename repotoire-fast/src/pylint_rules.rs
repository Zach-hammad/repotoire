use rustpython_parser::ast::{Stmt, Suite, Expr, StmtClassDef, ExceptHandler};
use std::collections::HashSet;
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

/// R0904: too-many-public-methods
pub struct TooManyPublicMethods {
    pub threshold: usize,
}

/// R0916: too-many-boolean-expressions
pub struct TooManyBooleanExpressions {
    pub threshold: usize,
}


impl PylintRule for TooManyAttributes {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions = LinePositions::from(source);
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt {
                let count = Self::count_instance_attributes(class);
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
    fn count_instance_attributes(class: &StmtClassDef) -> usize {
        let mut attrs: HashSet<String> = HashSet::new();
        for class_stmt in &class.body {
            if let Stmt::FunctionDef(func) = class_stmt {
                if func.name.as_str() == "__init__" {
                    for stmt in &func.body {
                        if let Stmt::Assign(assign) = stmt {
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
                    }
                }
            }
        }
        attrs.len()
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

impl PylintRule for TooFewPublicMethods {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions = LinePositions::from(source);
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt {
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

impl PylintRule for TooManyPublicMethods {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions = LinePositions::from(source);
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt {
                let count = count_public_methods(class);
                if count > self.threshold {
                    let line_num = line_positions.from_offset(class.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0904".to_string(),
                        message: format!("Class {} has {} public methods (max {})", class.name, count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }
}

fn count_boolean_expressions(expr: &Expr) -> usize {
    match expr {
        Expr::BoolOp(b) => {
            let ops = b.values.len().saturating_sub(1);
            let children: usize = b.values.iter().map(|v| count_boolean_expressions(v)).sum();
            ops + children
        }
        Expr::BinOp(b) => {
            count_boolean_expressions(&b.left) + count_boolean_expressions(&b.right)
        }
        Expr::UnaryOp(u) => count_boolean_expressions(&u.operand),
        _ => 0,
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

impl PylintRule for TooManyBooleanExpressions {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions = LinePositions::from(source);
        for stmt in ast {
            if let Stmt::If(if_stmt) = stmt {
                let count = count_boolean_expressions(&if_stmt.test);
                if count > self.threshold {
                    let line_num = line_positions.from_offset(if_stmt.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0916".to_string(),
                        message: format!("If statement has {} boolean expressions (max {})", count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }
}


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
            Stmt::ImportFrom(import_from) => {
                if let Some(module) = &import_from.module {
                    if module.as_str() == module_name {
                        let line_num = line_positions.from_offset(import_from.range.start().into()).as_usize();
                        findings.push(Finding {
                            code: "R0401".to_string(),
                            message: format!("Importing self in module {}", module_name),
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

// Helper: count return statements in a function body (recursive)
fn count_returns(stmts: &[Stmt]) -> usize {
    let mut count = 0;
    for stmt in stmts {
        match stmt {
            Stmt::Return(_) => count += 1,
            Stmt::If(if_stmt) => {
                count += count_returns(&if_stmt.body);
                count += count_returns(&if_stmt.orelse);
            }
            Stmt::For(for_stmt) => {
                count += count_returns(&for_stmt.body);
                count += count_returns(&for_stmt.orelse);
            }
            Stmt::While(while_stmt) => {
                count += count_returns(&while_stmt.body);
                count += count_returns(&while_stmt.orelse);
            }
            Stmt::With(with_stmt) => {
                count += count_returns(&with_stmt.body);
            }
            Stmt::Try(try_stmt) => {
                count += count_returns(&try_stmt.body);
                count += count_returns(&try_stmt.orelse);
                count += count_returns(&try_stmt.finalbody);
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    count += count_returns(&h.body);
                }
            }
            _ => {}
        }
    }
    count
}

// Helper: count branches (if/elif/else, for, while, try/except)
fn count_branches(stmts: &[Stmt]) -> usize {
    let mut count = 0;
    for stmt in stmts {
        match stmt {
            Stmt::If(if_stmt) => {
                count += 1; // the if itself
                count += count_branches(&if_stmt.body);
                if !if_stmt.orelse.is_empty() {
                    // Check if orelse is an elif (single If) or else block
                    if if_stmt.orelse.len() == 1 {
                        if let Stmt::If(_) = &if_stmt.orelse[0] {
                            // elif - count recursively (will add 1 for the elif)
                            count += count_branches(&if_stmt.orelse);
                        } else {
                            count += 1; // else block
                            count += count_branches(&if_stmt.orelse);
                        }
                    } else {
                        count += 1; // else block
                        count += count_branches(&if_stmt.orelse);
                    }
                }
            }
            Stmt::For(for_stmt) => {
                count += 1;
                count += count_branches(&for_stmt.body);
                if !for_stmt.orelse.is_empty() {
                    count += 1;
                    count += count_branches(&for_stmt.orelse);
                }
            }
            Stmt::While(while_stmt) => {
                count += 1;
                count += count_branches(&while_stmt.body);
                if !while_stmt.orelse.is_empty() {
                    count += 1;
                    count += count_branches(&while_stmt.orelse);
                }
            }
            Stmt::With(with_stmt) => {
                count += count_branches(&with_stmt.body);
            }
            Stmt::Try(try_stmt) => {
                count += 1; // try block
                count += count_branches(&try_stmt.body);
                for handler in &try_stmt.handlers {
                    count += 1; // each except
                    let ExceptHandler::ExceptHandler(h) = handler;
                    count += count_branches(&h.body);
                }
                if !try_stmt.orelse.is_empty() {
                    count += 1;
                    count += count_branches(&try_stmt.orelse);
                }
                if !try_stmt.finalbody.is_empty() {
                    count += 1;
                    count += count_branches(&try_stmt.finalbody);
                }
            }
            _ => {}
        }
    }
    count
}

// Helper: count local variable assignments in function
fn count_locals(stmts: &[Stmt]) -> usize {
    let mut locals: HashSet<String> = HashSet::new();
    collect_locals(stmts, &mut locals);
    locals.len()
}

fn collect_locals(stmts: &[Stmt], locals: &mut HashSet<String>) {
    for stmt in stmts {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    collect_names_from_expr(target, locals);
                }
            }
            Stmt::AnnAssign(ann_assign) => {
                collect_names_from_expr(&ann_assign.target, locals);
            }
            Stmt::AugAssign(aug_assign) => {
                collect_names_from_expr(&aug_assign.target, locals);
            }
            Stmt::For(for_stmt) => {
                collect_names_from_expr(&for_stmt.target, locals);
                collect_locals(&for_stmt.body, locals);
                collect_locals(&for_stmt.orelse, locals);
            }
            Stmt::If(if_stmt) => {
                collect_locals(&if_stmt.body, locals);
                collect_locals(&if_stmt.orelse, locals);
            }
            Stmt::While(while_stmt) => {
                collect_locals(&while_stmt.body, locals);
                collect_locals(&while_stmt.orelse, locals);
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    if let Some(var) = &item.optional_vars {
                        collect_names_from_expr(var, locals);
                    }
                }
                collect_locals(&with_stmt.body, locals);
            }
            Stmt::Try(try_stmt) => {
                collect_locals(&try_stmt.body, locals);
                collect_locals(&try_stmt.orelse, locals);
                collect_locals(&try_stmt.finalbody, locals);
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    if let Some(name) = &h.name {
                        locals.insert(name.to_string());
                    }
                    collect_locals(&h.body, locals);
                }
            }
            _ => {}
        }
    }
}

fn collect_names_from_expr(expr: &Expr, locals: &mut HashSet<String>) {
    match expr {
        Expr::Name(name) => {
            locals.insert(name.id.to_string());
        }
        Expr::Tuple(tuple) => {
            for elt in &tuple.elts {
                collect_names_from_expr(elt, locals);
            }
        }
        Expr::List(list) => {
            for elt in &list.elts {
                collect_names_from_expr(elt, locals);
            }
        }
        _ => {}
    }
}

// R0911: too-many-return-statements
pub fn check_too_many_returns(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let count = count_returns(&func.body);
                if count > threshold {
                    let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0911".to_string(),
                        message: format!("Function {} has {} return statements (max {})", func.name, count, threshold),
                        line: line_num + 1,
                    });
                }
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        let count = count_returns(&func.body);
                        if count > threshold {
                            let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                            findings.push(Finding {
                                code: "R0911".to_string(),
                                message: format!("Method {}.{} has {} return statements (max {})", class.name, func.name, count, threshold),
                                line: line_num + 1,
                            });
                        }
                    }
                }
            }
            _ => {}
        }
    }
    findings
}

// R0912: too-many-branches
pub fn check_too_many_branches(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let count = count_branches(&func.body);
                if count > threshold {
                    let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0912".to_string(),
                        message: format!("Function {} has {} branches (max {})", func.name, count, threshold),
                        line: line_num + 1,
                    });
                }
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        let count = count_branches(&func.body);
                        if count > threshold {
                            let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                            findings.push(Finding {
                                code: "R0912".to_string(),
                                message: format!("Method {}.{} has {} branches (max {})", class.name, func.name, count, threshold),
                                line: line_num + 1,
                            });
                        }
                    }
                }
            }
            _ => {}
        }
    }
    findings
}

// R0913: too-many-arguments
pub fn check_too_many_arguments(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let count = func.args.args.len()
                    + func.args.posonlyargs.len()
                    + func.args.kwonlyargs.len();
                if count > threshold {
                    let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0913".to_string(),
                        message: format!("Function {} has {} arguments (max {})", func.name, count, threshold),
                        line: line_num + 1,
                    });
                }
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        // For methods, subtract 1 for self/cls
                        let total = func.args.args.len()
                            + func.args.posonlyargs.len()
                            + func.args.kwonlyargs.len();
                        let count = total.saturating_sub(1); // Don't count self/cls
                        if count > threshold {
                            let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                            findings.push(Finding {
                                code: "R0913".to_string(),
                                message: format!("Method {}.{} has {} arguments (max {})", class.name, func.name, count, threshold),
                                line: line_num + 1,
                            });
                        }
                    }
                }
            }
            _ => {}
        }
    }
    findings
}

// R0914: too-many-locals
pub fn check_too_many_locals(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let count = count_locals(&func.body);
                if count > threshold {
                    let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0914".to_string(),
                        message: format!("Function {} has {} local variables (max {})", func.name, count, threshold),
                        line: line_num + 1,
                    });
                }
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        let count = count_locals(&func.body);
                        if count > threshold {
                            let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                            findings.push(Finding {
                                code: "R0914".to_string(),
                                message: format!("Method {}.{} has {} local variables (max {})", class.name, func.name, count, threshold),
                                line: line_num + 1,
                            });
                        }
                    }
                }
            }
            _ => {}
        }
    }
    findings
}

// R0915: too-many-statements
pub fn check_too_many_statements(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let count = func.body.len();
                if count > threshold {
                    let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0915".to_string(),
                        message: format!("Function {} has {} statements (max {})", func.name, count, threshold),
                        line: line_num + 1,
                    });
                }
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        let count = func.body.len();
                        if count > threshold {
                            let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                            findings.push(Finding {
                                code: "R0915".to_string(),
                                message: format!("Method {}.{} has {} statements (max {})", class.name, func.name, count, threshold),
                                line: line_num + 1,
                            });
                        }
                    }
                }
            }
            _ => {}
        }
    }
    findings
}