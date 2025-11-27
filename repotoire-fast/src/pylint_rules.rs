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

// W0611: unused-import
// Detects imports that are never used in the module
pub fn check_unused_imports(ast: &Suite, source: &str) -> Vec<Finding> {
    use std::collections::HashSet;

    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    // Collect all imports: (name, line, is_from_import)
    let mut imports: Vec<(String, usize, usize)> = Vec::new(); // (name, line, offset)

    for stmt in ast {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    // Use alias if provided, otherwise use module name
                    let name = alias.asname.as_ref()
                        .map(|n| n.to_string())
                        .unwrap_or_else(|| {
                            // For "import foo.bar", the usable name is "foo"
                            alias.name.to_string().split('.').next().unwrap_or("").to_string()
                        });
                    if !name.is_empty() {
                        imports.push((name, import.range.start().into(), import.range.start().into()));
                    }
                }
            }
            Stmt::ImportFrom(import) => {
                for alias in &import.names {
                    // Skip "from x import *"
                    if alias.name.as_str() == "*" {
                        continue;
                    }
                    // Use alias if provided, otherwise use imported name
                    let name = alias.asname.as_ref()
                        .map(|n| n.to_string())
                        .unwrap_or_else(|| alias.name.to_string());
                    imports.push((name, import.range.start().into(), import.range.start().into()));
                }
            }
            _ => {}
        }
    }

    // Collect all name usages in the code (excluding import statements themselves)
    let used_names: HashSet<String> = collect_used_names(ast);

    // Report imports not found in used names
    for (name, _offset, byte_offset) in imports {
        if !used_names.contains(&name) {
            let line_num = line_positions.from_offset(byte_offset).as_usize();
            findings.push(Finding {
                code: "W0611".to_string(),
                message: format!("Unused import: {}", name),
                line: line_num + 1,
            });
        }
    }

    findings
}

// Helper to collect all names used in the AST (excluding imports)
fn collect_used_names(ast: &Suite) -> std::collections::HashSet<String> {
    use std::collections::HashSet;

    let mut names = HashSet::new();

    fn visit_expr(expr: &Expr, names: &mut HashSet<String>) {
        match expr {
            Expr::Name(name) => {
                names.insert(name.id.to_string());
            }
            Expr::Attribute(attr) => {
                // For foo.bar, we need "foo"
                visit_expr(&attr.value, names);
            }
            Expr::Call(call) => {
                visit_expr(&call.func, names);
                for arg in &call.args {
                    visit_expr(arg, names);
                }
                for keyword in &call.keywords {
                    visit_expr(&keyword.value, names);
                }
            }
            Expr::BinOp(binop) => {
                visit_expr(&binop.left, names);
                visit_expr(&binop.right, names);
            }
            Expr::UnaryOp(unary) => {
                visit_expr(&unary.operand, names);
            }
            Expr::Compare(cmp) => {
                visit_expr(&cmp.left, names);
                for comp in &cmp.comparators {
                    visit_expr(comp, names);
                }
            }
            Expr::BoolOp(boolop) => {
                for val in &boolop.values {
                    visit_expr(val, names);
                }
            }
            Expr::IfExp(ifexp) => {
                visit_expr(&ifexp.test, names);
                visit_expr(&ifexp.body, names);
                visit_expr(&ifexp.orelse, names);
            }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    visit_expr(key, names);
                }
                for val in &dict.values {
                    visit_expr(val, names);
                }
            }
            Expr::List(list) => {
                for elt in &list.elts {
                    visit_expr(elt, names);
                }
            }
            Expr::Tuple(tuple) => {
                for elt in &tuple.elts {
                    visit_expr(elt, names);
                }
            }
            Expr::Set(set) => {
                for elt in &set.elts {
                    visit_expr(elt, names);
                }
            }
            Expr::Subscript(sub) => {
                visit_expr(&sub.value, names);
                visit_expr(&sub.slice, names);
            }
            Expr::Starred(starred) => {
                visit_expr(&starred.value, names);
            }
            Expr::Lambda(lambda) => {
                visit_expr(&lambda.body, names);
            }
            Expr::ListComp(comp) => {
                visit_expr(&comp.elt, names);
                for gen in &comp.generators {
                    visit_expr(&gen.iter, names);
                    for if_clause in &gen.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::SetComp(comp) => {
                visit_expr(&comp.elt, names);
                for gen in &comp.generators {
                    visit_expr(&gen.iter, names);
                    for if_clause in &gen.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::DictComp(comp) => {
                visit_expr(&comp.key, names);
                visit_expr(&comp.value, names);
                for gen in &comp.generators {
                    visit_expr(&gen.iter, names);
                    for if_clause in &gen.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::GeneratorExp(gen) => {
                visit_expr(&gen.elt, names);
                for generator in &gen.generators {
                    visit_expr(&generator.iter, names);
                    for if_clause in &generator.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::Await(await_expr) => {
                visit_expr(&await_expr.value, names);
            }
            Expr::Yield(yield_expr) => {
                if let Some(val) = &yield_expr.value {
                    visit_expr(val, names);
                }
            }
            Expr::YieldFrom(yf) => {
                visit_expr(&yf.value, names);
            }
            Expr::FormattedValue(fv) => {
                visit_expr(&fv.value, names);
            }
            Expr::JoinedStr(js) => {
                for val in &js.values {
                    visit_expr(val, names);
                }
            }
            Expr::NamedExpr(named) => {
                visit_expr(&named.value, names);
            }
            Expr::Slice(slice) => {
                if let Some(lower) = &slice.lower {
                    visit_expr(lower, names);
                }
                if let Some(upper) = &slice.upper {
                    visit_expr(upper, names);
                }
                if let Some(step) = &slice.step {
                    visit_expr(step, names);
                }
            }
            _ => {}
        }
    }

    fn visit_stmt(stmt: &Stmt, names: &mut HashSet<String>) {
        match stmt {
            // Skip import statements - we don't want to count the import itself as a usage
            Stmt::Import(_) | Stmt::ImportFrom(_) => {}

            Stmt::Expr(expr_stmt) => {
                visit_expr(&expr_stmt.value, names);
            }
            Stmt::Assign(assign) => {
                visit_expr(&assign.value, names);
                for target in &assign.targets {
                    visit_expr(target, names);
                }
            }
            Stmt::AugAssign(aug) => {
                visit_expr(&aug.target, names);
                visit_expr(&aug.value, names);
            }
            Stmt::AnnAssign(ann) => {
                visit_expr(&ann.annotation, names);
                if let Some(val) = &ann.value {
                    visit_expr(val, names);
                }
            }
            Stmt::Return(ret) => {
                if let Some(val) = &ret.value {
                    visit_expr(val, names);
                }
            }
            Stmt::Raise(raise) => {
                if let Some(exc) = &raise.exc {
                    visit_expr(exc, names);
                }
                if let Some(cause) = &raise.cause {
                    visit_expr(cause, names);
                }
            }
            Stmt::Assert(assert) => {
                visit_expr(&assert.test, names);
                if let Some(msg) = &assert.msg {
                    visit_expr(msg, names);
                }
            }
            Stmt::If(if_stmt) => {
                visit_expr(&if_stmt.test, names);
                for s in &if_stmt.body {
                    visit_stmt(s, names);
                }
                for s in &if_stmt.orelse {
                    visit_stmt(s, names);
                }
            }
            Stmt::For(for_stmt) => {
                visit_expr(&for_stmt.iter, names);
                for s in &for_stmt.body {
                    visit_stmt(s, names);
                }
                for s in &for_stmt.orelse {
                    visit_stmt(s, names);
                }
            }
            Stmt::While(while_stmt) => {
                visit_expr(&while_stmt.test, names);
                for s in &while_stmt.body {
                    visit_stmt(s, names);
                }
                for s in &while_stmt.orelse {
                    visit_stmt(s, names);
                }
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    visit_expr(&item.context_expr, names);
                }
                for s in &with_stmt.body {
                    visit_stmt(s, names);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    visit_stmt(s, names);
                }
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    if let Some(typ) = &h.type_ {
                        visit_expr(typ, names);
                    }
                    for s in &h.body {
                        visit_stmt(s, names);
                    }
                }
                for s in &try_stmt.orelse {
                    visit_stmt(s, names);
                }
                for s in &try_stmt.finalbody {
                    visit_stmt(s, names);
                }
            }
            Stmt::FunctionDef(func) => {
                // Visit decorators
                for dec in &func.decorator_list {
                    visit_expr(dec, names);
                }
                // Visit annotations
                if let Some(ret) = &func.returns {
                    visit_expr(ret, names);
                }
                // Visit body
                for s in &func.body {
                    visit_stmt(s, names);
                }
            }
            Stmt::ClassDef(class) => {
                // Visit decorators
                for dec in &class.decorator_list {
                    visit_expr(dec, names);
                }
                // Visit base classes
                for base in &class.bases {
                    visit_expr(base, names);
                }
                // Visit keywords
                for kw in &class.keywords {
                    visit_expr(&kw.value, names);
                }
                // Visit body
                for s in &class.body {
                    visit_stmt(s, names);
                }
            }
            Stmt::Match(match_stmt) => {
                visit_expr(&match_stmt.subject, names);
                // Match cases would need pattern visiting too
            }
            _ => {}
        }
    }

    for stmt in ast {
        visit_stmt(stmt, &mut names);
    }

    names
}

// C0301: line-too-long
// Detects lines that exceed a specified length
pub fn check_line_too_long(source: &str, max_length: usize) -> Vec<Finding> {
    let mut findings = Vec::new();

    for (line_num, line) in source.lines().enumerate() {
        let length = line.len();
        if length > max_length {
            findings.push(Finding {
                code: "C0301".to_string(),
                message: format!("Line too long ({} > {} characters)", length, max_length),
                line: line_num + 1,
            });
        }
    }

    findings
}

// C0302: too-many-lines
// Detects modules that have too many lines
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

// W0612: unused-variable
// Detects variables that are assigned but never used
pub fn check_unused_variables(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    // Process each function/method independently
    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let func_findings = check_unused_vars_in_function(&func.body, &func.name, None, source, &line_positions);
                findings.extend(func_findings);
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        let func_findings = check_unused_vars_in_function(
                            &func.body,
                            &func.name,
                            Some(&class.name),
                            source,
                            &line_positions
                        );
                        findings.extend(func_findings);
                    }
                }
            }
            _ => {}
        }
    }

    findings
}

// Helper to check unused variables within a function
fn check_unused_vars_in_function(
    body: &[Stmt],
    func_name: &str,
    class_name: Option<&str>,
    _source: &str,
    line_positions: &LinePositions,
) -> Vec<Finding> {
    use std::collections::{HashMap, HashSet};

    let mut findings = Vec::new();

    // Collect all variable assignments: name -> (line, byte_offset)
    let mut assigned_vars: HashMap<String, (usize, usize)> = HashMap::new();
    // Collect all variable usages
    let mut used_vars: HashSet<String> = HashSet::new();

    fn collect_assignments(stmt: &Stmt, assigned: &mut HashMap<String, (usize, usize)>) {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    collect_assigned_names(target, assigned);
                }
            }
            Stmt::AnnAssign(ann) => {
                if let Expr::Name(name) = ann.target.as_ref() {
                    let var_name = name.id.to_string();
                    // Don't track underscore variables
                    if !var_name.starts_with('_') {
                        assigned.insert(var_name, (0, name.range.start().into()));
                    }
                }
            }
            Stmt::For(for_stmt) => {
                collect_assigned_names(&for_stmt.target, assigned);
                for s in &for_stmt.body {
                    collect_assignments(s, assigned);
                }
                for s in &for_stmt.orelse {
                    collect_assignments(s, assigned);
                }
            }
            Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    collect_assignments(s, assigned);
                }
                for s in &while_stmt.orelse {
                    collect_assignments(s, assigned);
                }
            }
            Stmt::If(if_stmt) => {
                for s in &if_stmt.body {
                    collect_assignments(s, assigned);
                }
                for s in &if_stmt.orelse {
                    collect_assignments(s, assigned);
                }
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    if let Some(optional_vars) = &item.optional_vars {
                        collect_assigned_names(optional_vars, assigned);
                    }
                }
                for s in &with_stmt.body {
                    collect_assignments(s, assigned);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    collect_assignments(s, assigned);
                }
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    // Exception variable binding
                    if let Some(name) = &h.name {
                        let var_name = name.to_string();
                        if !var_name.starts_with('_') {
                            assigned.insert(var_name, (0, h.range.start().into()));
                        }
                    }
                    for s in &h.body {
                        collect_assignments(s, assigned);
                    }
                }
                for s in &try_stmt.orelse {
                    collect_assignments(s, assigned);
                }
                for s in &try_stmt.finalbody {
                    collect_assignments(s, assigned);
                }
            }
            _ => {}
        }
    }

    fn collect_assigned_names(expr: &Expr, assigned: &mut HashMap<String, (usize, usize)>) {
        match expr {
            Expr::Name(name) => {
                let var_name = name.id.to_string();
                // Don't track underscore variables (convention for unused)
                if !var_name.starts_with('_') {
                    assigned.insert(var_name, (0, name.range.start().into()));
                }
            }
            Expr::Tuple(tuple) => {
                for elt in &tuple.elts {
                    collect_assigned_names(elt, assigned);
                }
            }
            Expr::List(list) => {
                for elt in &list.elts {
                    collect_assigned_names(elt, assigned);
                }
            }
            Expr::Starred(starred) => {
                collect_assigned_names(&starred.value, assigned);
            }
            _ => {}
        }
    }

    fn collect_usages(stmt: &Stmt, used: &mut HashSet<String>) {
        fn visit_expr_for_usage(expr: &Expr, used: &mut HashSet<String>) {
            match expr {
                Expr::Name(name) => {
                    used.insert(name.id.to_string());
                }
                Expr::Attribute(attr) => {
                    visit_expr_for_usage(&attr.value, used);
                }
                Expr::Call(call) => {
                    visit_expr_for_usage(&call.func, used);
                    for arg in &call.args {
                        visit_expr_for_usage(arg, used);
                    }
                    for kw in &call.keywords {
                        visit_expr_for_usage(&kw.value, used);
                    }
                }
                Expr::BinOp(binop) => {
                    visit_expr_for_usage(&binop.left, used);
                    visit_expr_for_usage(&binop.right, used);
                }
                Expr::UnaryOp(unary) => {
                    visit_expr_for_usage(&unary.operand, used);
                }
                Expr::Compare(cmp) => {
                    visit_expr_for_usage(&cmp.left, used);
                    for comp in &cmp.comparators {
                        visit_expr_for_usage(comp, used);
                    }
                }
                Expr::BoolOp(boolop) => {
                    for val in &boolop.values {
                        visit_expr_for_usage(val, used);
                    }
                }
                Expr::IfExp(ifexp) => {
                    visit_expr_for_usage(&ifexp.test, used);
                    visit_expr_for_usage(&ifexp.body, used);
                    visit_expr_for_usage(&ifexp.orelse, used);
                }
                Expr::Dict(dict) => {
                    for key in dict.keys.iter().flatten() {
                        visit_expr_for_usage(key, used);
                    }
                    for val in &dict.values {
                        visit_expr_for_usage(val, used);
                    }
                }
                Expr::List(list) => {
                    for elt in &list.elts {
                        visit_expr_for_usage(elt, used);
                    }
                }
                Expr::Tuple(tuple) => {
                    for elt in &tuple.elts {
                        visit_expr_for_usage(elt, used);
                    }
                }
                Expr::Set(set) => {
                    for elt in &set.elts {
                        visit_expr_for_usage(elt, used);
                    }
                }
                Expr::Subscript(sub) => {
                    visit_expr_for_usage(&sub.value, used);
                    visit_expr_for_usage(&sub.slice, used);
                }
                Expr::Starred(starred) => {
                    visit_expr_for_usage(&starred.value, used);
                }
                Expr::Lambda(lambda) => {
                    visit_expr_for_usage(&lambda.body, used);
                }
                Expr::ListComp(comp) => {
                    visit_expr_for_usage(&comp.elt, used);
                    for gen in &comp.generators {
                        visit_expr_for_usage(&gen.iter, used);
                        for if_clause in &gen.ifs {
                            visit_expr_for_usage(if_clause, used);
                        }
                    }
                }
                Expr::SetComp(comp) => {
                    visit_expr_for_usage(&comp.elt, used);
                    for gen in &comp.generators {
                        visit_expr_for_usage(&gen.iter, used);
                        for if_clause in &gen.ifs {
                            visit_expr_for_usage(if_clause, used);
                        }
                    }
                }
                Expr::DictComp(comp) => {
                    visit_expr_for_usage(&comp.key, used);
                    visit_expr_for_usage(&comp.value, used);
                    for gen in &comp.generators {
                        visit_expr_for_usage(&gen.iter, used);
                        for if_clause in &gen.ifs {
                            visit_expr_for_usage(if_clause, used);
                        }
                    }
                }
                Expr::GeneratorExp(gen) => {
                    visit_expr_for_usage(&gen.elt, used);
                    for generator in &gen.generators {
                        visit_expr_for_usage(&generator.iter, used);
                        for if_clause in &generator.ifs {
                            visit_expr_for_usage(if_clause, used);
                        }
                    }
                }
                Expr::Await(await_expr) => {
                    visit_expr_for_usage(&await_expr.value, used);
                }
                Expr::Yield(yield_expr) => {
                    if let Some(val) = &yield_expr.value {
                        visit_expr_for_usage(val, used);
                    }
                }
                Expr::YieldFrom(yf) => {
                    visit_expr_for_usage(&yf.value, used);
                }
                Expr::FormattedValue(fv) => {
                    visit_expr_for_usage(&fv.value, used);
                }
                Expr::JoinedStr(js) => {
                    for val in &js.values {
                        visit_expr_for_usage(val, used);
                    }
                }
                Expr::NamedExpr(named) => {
                    visit_expr_for_usage(&named.value, used);
                }
                Expr::Slice(slice) => {
                    if let Some(lower) = &slice.lower {
                        visit_expr_for_usage(lower, used);
                    }
                    if let Some(upper) = &slice.upper {
                        visit_expr_for_usage(upper, used);
                    }
                    if let Some(step) = &slice.step {
                        visit_expr_for_usage(step, used);
                    }
                }
                _ => {}
            }
        }

        match stmt {
            Stmt::Expr(expr_stmt) => {
                visit_expr_for_usage(&expr_stmt.value, used);
            }
            Stmt::Assign(assign) => {
                visit_expr_for_usage(&assign.value, used);
            }
            Stmt::AugAssign(aug) => {
                visit_expr_for_usage(&aug.target, used);
                visit_expr_for_usage(&aug.value, used);
            }
            Stmt::AnnAssign(ann) => {
                visit_expr_for_usage(&ann.annotation, used);
                if let Some(val) = &ann.value {
                    visit_expr_for_usage(val, used);
                }
            }
            Stmt::Return(ret) => {
                if let Some(val) = &ret.value {
                    visit_expr_for_usage(val, used);
                }
            }
            Stmt::Raise(raise) => {
                if let Some(exc) = &raise.exc {
                    visit_expr_for_usage(exc, used);
                }
                if let Some(cause) = &raise.cause {
                    visit_expr_for_usage(cause, used);
                }
            }
            Stmt::Assert(assert) => {
                visit_expr_for_usage(&assert.test, used);
                if let Some(msg) = &assert.msg {
                    visit_expr_for_usage(msg, used);
                }
            }
            Stmt::If(if_stmt) => {
                visit_expr_for_usage(&if_stmt.test, used);
                for s in &if_stmt.body {
                    collect_usages(s, used);
                }
                for s in &if_stmt.orelse {
                    collect_usages(s, used);
                }
            }
            Stmt::For(for_stmt) => {
                visit_expr_for_usage(&for_stmt.iter, used);
                for s in &for_stmt.body {
                    collect_usages(s, used);
                }
                for s in &for_stmt.orelse {
                    collect_usages(s, used);
                }
            }
            Stmt::While(while_stmt) => {
                visit_expr_for_usage(&while_stmt.test, used);
                for s in &while_stmt.body {
                    collect_usages(s, used);
                }
                for s in &while_stmt.orelse {
                    collect_usages(s, used);
                }
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    visit_expr_for_usage(&item.context_expr, used);
                }
                for s in &with_stmt.body {
                    collect_usages(s, used);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    collect_usages(s, used);
                }
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    if let Some(typ) = &h.type_ {
                        visit_expr_for_usage(typ, used);
                    }
                    for s in &h.body {
                        collect_usages(s, used);
                    }
                }
                for s in &try_stmt.orelse {
                    collect_usages(s, used);
                }
                for s in &try_stmt.finalbody {
                    collect_usages(s, used);
                }
            }
            _ => {}
        }
    }

    // Collect assignments and usages
    for stmt in body {
        collect_assignments(stmt, &mut assigned_vars);
        collect_usages(stmt, &mut used_vars);
    }

    // Report unused variables
    for (var_name, (_, byte_offset)) in assigned_vars {
        if !used_vars.contains(&var_name) {
            let line_num = line_positions.from_offset(byte_offset).as_usize();
            let location = match class_name {
                Some(cls) => format!("{}.{}", cls, func_name),
                None => func_name.to_string(),
            };
            findings.push(Finding {
                code: "W0612".to_string(),
                message: format!("Unused variable '{}' in {}", var_name, location),
                line: line_num + 1,
            });
        }
    }

    findings
}

// W0613: unused-argument
// Detects function arguments that are never used
pub fn check_unused_arguments(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        match stmt {
            Stmt::FunctionDef(func) => {
                let func_findings = check_unused_args_in_function(func, None, &line_positions);
                findings.extend(func_findings);
            }
            Stmt::ClassDef(class) => {
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        let func_findings = check_unused_args_in_function(func, Some(&class.name), &line_positions);
                        findings.extend(func_findings);
                    }
                }
            }
            _ => {}
        }
    }

    findings
}

fn check_unused_args_in_function(
    func: &rustpython_parser::ast::StmtFunctionDef,
    class_name: Option<&str>,
    line_positions: &LinePositions,
) -> Vec<Finding> {
    let mut findings = Vec::new();

    // Collect all argument names
    let mut arg_names: Vec<(String, usize)> = Vec::new();

    // Regular positional args
    for arg in &func.args.args {
        let name = arg.def.arg.to_string();
        // Skip self/cls for methods
        if class_name.is_some() && (name == "self" || name == "cls") {
            continue;
        }
        // Skip underscore-prefixed args (convention for unused)
        if !name.starts_with('_') {
            arg_names.push((name, arg.def.range.start().into()));
        }
    }

    // Positional-only args
    for arg in &func.args.posonlyargs {
        let name = arg.def.arg.to_string();
        if !name.starts_with('_') {
            arg_names.push((name, arg.def.range.start().into()));
        }
    }

    // Keyword-only args
    for arg in &func.args.kwonlyargs {
        let name = arg.def.arg.to_string();
        if !name.starts_with('_') {
            arg_names.push((name, arg.def.range.start().into()));
        }
    }

    // *args
    if let Some(vararg) = &func.args.vararg {
        let name = vararg.arg.to_string();
        if !name.starts_with('_') {
            arg_names.push((name, vararg.range.start().into()));
        }
    }

    // **kwargs
    if let Some(kwarg) = &func.args.kwarg {
        let name = kwarg.arg.to_string();
        if !name.starts_with('_') {
            arg_names.push((name, kwarg.range.start().into()));
        }
    }

    // Collect all names used in the function body
    let used_names = collect_names_in_body(&func.body);

    // Report unused arguments
    for (arg_name, byte_offset) in arg_names {
        if !used_names.contains(&arg_name) {
            let line_num = line_positions.from_offset(byte_offset).as_usize();
            let location = match class_name {
                Some(cls) => format!("{}.{}", cls, func.name),
                None => func.name.to_string(),
            };
            findings.push(Finding {
                code: "W0613".to_string(),
                message: format!("Unused argument '{}' in {}", arg_name, location),
                line: line_num + 1,
            });
        }
    }

    findings
}

// Helper to collect all names used in a function body
// R0901: too-many-ancestors
// Detects classes with too many parent classes in the inheritance chain
pub fn check_too_many_ancestors(ast: &Suite, source: &str, threshold: usize) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    // Build a map of class names to their direct base classes
    let mut class_bases: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();

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

    // Count ancestors for each class (only within the same file for now)
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
    class_bases: &std::collections::HashMap<String, Vec<String>>,
    visited: &mut HashSet<String>,
) -> usize {
    if visited.contains(class_name) {
        return 0; // Avoid cycles
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

// W0201: attribute-defined-outside-init
// Detects instance attributes defined outside of __init__
pub fn check_attribute_defined_outside_init(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    for stmt in ast {
        if let Stmt::ClassDef(class) = stmt {
            // Collect attributes defined in __init__
            let mut init_attrs: HashSet<String> = HashSet::new();

            for class_stmt in &class.body {
                if let Stmt::FunctionDef(func) = class_stmt {
                    if func.name.as_str() == "__init__" {
                        collect_self_attributes(&func.body, &mut init_attrs);
                    }
                }
            }

            // Check other methods for attributes not in __init__
            for class_stmt in &class.body {
                if let Stmt::FunctionDef(func) = class_stmt {
                    if func.name.as_str() != "__init__" {
                        let mut method_attrs: HashSet<String> = HashSet::new();
                        collect_self_attribute_assignments(&func.body, &mut method_attrs);

                        for attr in method_attrs {
                            if !init_attrs.contains(&attr) {
                                // Find the line where this attribute is assigned
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
// Detects access to protected members (prefixed with _) from outside the class
pub fn check_protected_access(ast: &Suite, source: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let line_positions = LinePositions::from(source);

    // Collect all class names defined in this module
    let mut class_names: HashSet<String> = HashSet::new();
    for stmt in ast {
        if let Stmt::ClassDef(class) = stmt {
            class_names.insert(class.name.to_string());
        }
    }

    // Check for protected access in module-level code
    for stmt in ast {
        match stmt {
            Stmt::ClassDef(class) => {
                // Check methods for protected access to other classes' members
                for class_stmt in &class.body {
                    if let Stmt::FunctionDef(func) = class_stmt {
                        check_protected_access_in_stmts(&func.body, &class.name.to_string(), &line_positions, &mut findings);
                    }
                }
            }
            Stmt::FunctionDef(func) => {
                // Module-level functions
                check_protected_access_in_stmts(&func.body, "", &line_positions, &mut findings);
            }
            _ => {
                // Module-level code
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
        _ => {}
    }
}

fn check_protected_access_in_expr(expr: &Expr, current_class: &str, line_positions: &LinePositions, findings: &mut Vec<Finding>) {
    match expr {
        Expr::Attribute(attr) => {
            let attr_name = attr.attr.as_str();
            // Check if it's a protected attribute (starts with _ but not __)
            if attr_name.starts_with('_') && !attr_name.starts_with("__") {
                // Check if the access is on something other than self
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
                    // Module-level access to protected member
                    let line_num = line_positions.from_offset(attr.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "W0212".to_string(),
                        message: format!("Access to protected member '{}'", attr_name),
                        line: line_num + 1,
                    });
                }
            }
            // Recurse into the value
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
// Detects wildcard imports (from x import *)
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
// Detects use of loop variable outside of the loop where it was defined
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
    // Find for loops and track their variables
    let mut local_loop_vars: HashSet<String> = HashSet::new();

    for stmt in stmts {
        match stmt {
            Stmt::For(for_stmt) => {
                // Collect loop variable names
                let mut new_vars: HashSet<String> = HashSet::new();
                collect_for_target_names(&for_stmt.target, &mut new_vars);

                // Process the loop body with these variables in scope
                let mut inner_loop_vars = loop_vars.clone();
                inner_loop_vars.extend(new_vars.clone());
                check_loop_vars_in_stmts(&for_stmt.body, &mut inner_loop_vars, line_positions, findings);
                check_loop_vars_in_stmts(&for_stmt.orelse, &mut inner_loop_vars, line_positions, findings);

                // After the loop, these vars might be used - track them
                local_loop_vars.extend(new_vars);
            }
            Stmt::If(if_stmt) => {
                // Check if loop variables are used in conditions after loop
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
// Detects use of disallowed variable names (like foo, bar, baz)
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
            // Check function name
            if disallowed.contains(func.name.as_str()) {
                let line_num = line_positions.from_offset(func.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "C0104".to_string(),
                    message: format!("Disallowed name '{}'", func.name),
                    line: line_num + 1,
                });
            }
            // Check arguments
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
            // Check body
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

fn collect_names_in_body(body: &[Stmt]) -> std::collections::HashSet<String> {
    use std::collections::HashSet;

    let mut names = HashSet::new();

    fn visit_expr(expr: &Expr, names: &mut HashSet<String>) {
        match expr {
            Expr::Name(name) => {
                names.insert(name.id.to_string());
            }
            Expr::Attribute(attr) => {
                visit_expr(&attr.value, names);
            }
            Expr::Call(call) => {
                visit_expr(&call.func, names);
                for arg in &call.args {
                    visit_expr(arg, names);
                }
                for kw in &call.keywords {
                    visit_expr(&kw.value, names);
                }
            }
            Expr::BinOp(binop) => {
                visit_expr(&binop.left, names);
                visit_expr(&binop.right, names);
            }
            Expr::UnaryOp(unary) => {
                visit_expr(&unary.operand, names);
            }
            Expr::Compare(cmp) => {
                visit_expr(&cmp.left, names);
                for comp in &cmp.comparators {
                    visit_expr(comp, names);
                }
            }
            Expr::BoolOp(boolop) => {
                for val in &boolop.values {
                    visit_expr(val, names);
                }
            }
            Expr::IfExp(ifexp) => {
                visit_expr(&ifexp.test, names);
                visit_expr(&ifexp.body, names);
                visit_expr(&ifexp.orelse, names);
            }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    visit_expr(key, names);
                }
                for val in &dict.values {
                    visit_expr(val, names);
                }
            }
            Expr::List(list) => {
                for elt in &list.elts {
                    visit_expr(elt, names);
                }
            }
            Expr::Tuple(tuple) => {
                for elt in &tuple.elts {
                    visit_expr(elt, names);
                }
            }
            Expr::Set(set) => {
                for elt in &set.elts {
                    visit_expr(elt, names);
                }
            }
            Expr::Subscript(sub) => {
                visit_expr(&sub.value, names);
                visit_expr(&sub.slice, names);
            }
            Expr::Starred(starred) => {
                visit_expr(&starred.value, names);
            }
            Expr::Lambda(lambda) => {
                visit_expr(&lambda.body, names);
            }
            Expr::ListComp(comp) => {
                visit_expr(&comp.elt, names);
                for gen in &comp.generators {
                    visit_expr(&gen.iter, names);
                    for if_clause in &gen.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::SetComp(comp) => {
                visit_expr(&comp.elt, names);
                for gen in &comp.generators {
                    visit_expr(&gen.iter, names);
                    for if_clause in &gen.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::DictComp(comp) => {
                visit_expr(&comp.key, names);
                visit_expr(&comp.value, names);
                for gen in &comp.generators {
                    visit_expr(&gen.iter, names);
                    for if_clause in &gen.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::GeneratorExp(gen) => {
                visit_expr(&gen.elt, names);
                for generator in &gen.generators {
                    visit_expr(&generator.iter, names);
                    for if_clause in &generator.ifs {
                        visit_expr(if_clause, names);
                    }
                }
            }
            Expr::Await(await_expr) => {
                visit_expr(&await_expr.value, names);
            }
            Expr::Yield(yield_expr) => {
                if let Some(val) = &yield_expr.value {
                    visit_expr(val, names);
                }
            }
            Expr::YieldFrom(yf) => {
                visit_expr(&yf.value, names);
            }
            Expr::FormattedValue(fv) => {
                visit_expr(&fv.value, names);
            }
            Expr::JoinedStr(js) => {
                for val in &js.values {
                    visit_expr(val, names);
                }
            }
            Expr::NamedExpr(named) => {
                visit_expr(&named.value, names);
            }
            Expr::Slice(slice) => {
                if let Some(lower) = &slice.lower {
                    visit_expr(lower, names);
                }
                if let Some(upper) = &slice.upper {
                    visit_expr(upper, names);
                }
                if let Some(step) = &slice.step {
                    visit_expr(step, names);
                }
            }
            _ => {}
        }
    }

    fn visit_stmt(stmt: &Stmt, names: &mut HashSet<String>) {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                visit_expr(&expr_stmt.value, names);
            }
            Stmt::Assign(assign) => {
                visit_expr(&assign.value, names);
                for target in &assign.targets {
                    visit_expr(target, names);
                }
            }
            Stmt::AugAssign(aug) => {
                visit_expr(&aug.target, names);
                visit_expr(&aug.value, names);
            }
            Stmt::AnnAssign(ann) => {
                visit_expr(&ann.annotation, names);
                if let Some(val) = &ann.value {
                    visit_expr(val, names);
                }
            }
            Stmt::Return(ret) => {
                if let Some(val) = &ret.value {
                    visit_expr(val, names);
                }
            }
            Stmt::Raise(raise) => {
                if let Some(exc) = &raise.exc {
                    visit_expr(exc, names);
                }
                if let Some(cause) = &raise.cause {
                    visit_expr(cause, names);
                }
            }
            Stmt::Assert(assert) => {
                visit_expr(&assert.test, names);
                if let Some(msg) = &assert.msg {
                    visit_expr(msg, names);
                }
            }
            Stmt::If(if_stmt) => {
                visit_expr(&if_stmt.test, names);
                for s in &if_stmt.body {
                    visit_stmt(s, names);
                }
                for s in &if_stmt.orelse {
                    visit_stmt(s, names);
                }
            }
            Stmt::For(for_stmt) => {
                visit_expr(&for_stmt.iter, names);
                for s in &for_stmt.body {
                    visit_stmt(s, names);
                }
                for s in &for_stmt.orelse {
                    visit_stmt(s, names);
                }
            }
            Stmt::While(while_stmt) => {
                visit_expr(&while_stmt.test, names);
                for s in &while_stmt.body {
                    visit_stmt(s, names);
                }
                for s in &while_stmt.orelse {
                    visit_stmt(s, names);
                }
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    visit_expr(&item.context_expr, names);
                }
                for s in &with_stmt.body {
                    visit_stmt(s, names);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    visit_stmt(s, names);
                }
                for handler in &try_stmt.handlers {
                    let ExceptHandler::ExceptHandler(h) = handler;
                    if let Some(typ) = &h.type_ {
                        visit_expr(typ, names);
                    }
                    for s in &h.body {
                        visit_stmt(s, names);
                    }
                }
                for s in &try_stmt.orelse {
                    visit_stmt(s, names);
                }
                for s in &try_stmt.finalbody {
                    visit_stmt(s, names);
                }
            }
            Stmt::FunctionDef(func) => {
                // Nested function - visit its body too
                for s in &func.body {
                    visit_stmt(s, names);
                }
            }
            Stmt::ClassDef(class) => {
                // Nested class - visit its body too
                for s in &class.body {
                    visit_stmt(s, names);
                }
            }
            _ => {}
        }
    }

    for stmt in body {
        visit_stmt(stmt, &mut names);
    }

    names
}