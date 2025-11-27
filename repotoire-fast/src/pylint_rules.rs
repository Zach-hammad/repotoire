use rustpython_parser::ast::{Stmt, Suite, Expr, StmtClassDef};
use std::collections::HashSet;
use line_numbers::LinePositions;
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



