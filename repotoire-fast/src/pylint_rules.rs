use rustpython_parser::ast::{Stmt, Suite, Expr, StmtClassDef};
use std::collections::HashSet;
use line_numbers::LinePositions;
use std::path::Path;
pub struct Finding {
    pub code: String,
    pub message: String,
    pub line: usize,
}

pub trait PylintRule {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding>;
    fn code(&self) -> &str;
}

pub struct TooManyAttributes {
    pub threshold: usize,
}

pub struct TooFewPublicMethods {
    pub threshold: usize,
}

pub struct TooManyPublicMethods {
    pub threshold: usize,
}

pub struct TooManyBooleanExpressions {
    pub threshold: usize,
}


impl PylintRule for TooManyAttributes {
    fn check(&self, ast: &Suite, source: &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions= LinePositions::from(source);
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt{
                let count = Self::count_instance_attributes(class);
                
                if count > self.threshold {
                    let line_num= line_positions.from_offset(class.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0902".to_string(),
                        message: format!("Class {} has {} instance attributes (max, {})", class.name, count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }
    fn code(&self) -> &str {
        "R0902"
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
    fn check(&self, ast: &Suite, source : &str) -> Vec<Finding> {
        let mut findings = Vec::new();
        let line_positions= LinePositions::from(source);
        for stmt in ast {
            if let Stmt::ClassDef(class) = stmt{
                let count = count_public_methods(class);
                if count < self.threshold {
                    let line_num= line_positions.from_offset(class.range.start().into()).as_usize();
                    findings.push(Finding {
                        code: "R0903".to_string(),
                        message: format!("Class {} has {} public methods (min, {})", class.name, count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }
    fn code(&self) -> &str {
        "R0903"
    }
}

impl PylintRule for TooManyPublicMethods {
    fn check(&self, ast: &Suite, source : &str) -> Vec<Finding> {

    let mut findings = Vec::new();
    let line_positions= LinePositions::from(source);
    for stmt in ast {
        if let Stmt::ClassDef(class) = stmt{
            let count = count_public_methods(class);
            if count > self.threshold {
                let line_num= line_positions.from_offset(class.range.start().into()).as_usize();
                findings.push(Finding {
                    code: "R0904".to_string(),
                    message: format!("Class {} has {} public methods (max, {})", class.name, count, self.threshold),
                    line: line_num + 1,
                });
            }
        }
    }
    findings
    }
    fn code(&self) -> &str {
        "R0904"
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
                        message: format!("If statement has {} boolean expressions (max, {})", count, self.threshold),
                        line: line_num + 1,
                    });
                }
            }
        }
        findings
    }

    fn code(&self) -> &str {
        "R0916"
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