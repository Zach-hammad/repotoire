use rustpython_parser::ast::{Expr, Stmt, Suite};
use rustpython_parser::Parse;
use std::collections::HashMap;

pub fn calculate_complexity(source: &str) -> Option<u32> {
    let ast: Suite = Suite::parse(source, "<string>").ok()?;
    let mut complexity: u32 =1;
    for stmt in &ast {
        complexity += count_complexity_in_stmt(stmt);
    }
    Some(complexity)
}

fn count_complexity_in_stmt(stmt: &Stmt) -> u32 {
    let mut count: u32 = 0;
    match stmt {
        Stmt::If(if_stmt) => {
            count += 1;
            count += count_complexity_in_expr(&if_stmt.test);
            for s in &if_stmt.body {
                count += count_complexity_in_stmt(s);
            }
            for s in &if_stmt.orelse {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::While(while_stmt) => {
            count += 1;
            count += count_complexity_in_expr(&while_stmt.test);
            for s in &while_stmt.body {
                count += count_complexity_in_stmt(s);
            }
            for s in &while_stmt.orelse {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::For(for_stmt)=> {
            count += 1;
            for s in &for_stmt.body {
                count += count_complexity_in_stmt(s);
            }
            for s in &for_stmt.orelse {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::AsyncFor(for_stmt)=> {
            // AsyncFor not counted by Python impl, but walk into body
            for s in &for_stmt.body {
                count += count_complexity_in_stmt(s);
            }
            for s in &for_stmt.orelse {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::With(with_stmt)=> {
            count += 1;
            for s in &with_stmt.body {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::AsyncWith(with_stmt)=> {
            // AsyncWith not counted by Python impl, but walk into body
            for s in &with_stmt.body {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::Assert(a)=> {
            count += 1;
            count += count_complexity_in_expr(&a.test);
        }
        Stmt::Try(try_stmt)=> {
            // Try block + each exception handler is a decision point
            count += 1;
            count += try_stmt.handlers.len() as u32;
            for s in &try_stmt.body {
                count += count_complexity_in_stmt(s);
            }
            for handler in &try_stmt.handlers {
                let rustpython_parser::ast::ExceptHandler::ExceptHandler(e) = handler;
                for s in &e.body {
                    count += count_complexity_in_stmt(s);
                }
            }
            for s in &try_stmt.orelse {
                count += count_complexity_in_stmt(s);
            }
            for s in &try_stmt.finalbody {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::FunctionDef(f)=> {
            for s in &f.body {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::AsyncFunctionDef(f)=> {
            for s in &f.body {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::ClassDef(c)=> {
            for s in &c.body {
                count += count_complexity_in_stmt(s);
            }
        }
        Stmt::Assign(a)=> {
            count += count_complexity_in_expr(&a.value);
        }
        Stmt::AnnAssign(a)=> {
            if let Some(v) = &a.value {
                count += count_complexity_in_expr(v);
            }
        }
        Stmt::AugAssign(a)=> {
            count += count_complexity_in_expr(&a.value);
        }
        Stmt::Return(r)=> {
            if let Some(v) = &r.value {
                count += count_complexity_in_expr(v);
            }
        }
        Stmt::Expr(e)=> {
            count += count_complexity_in_expr(&e.value);
        }
        Stmt::Raise(r)=> {
            if let Some(exc) = &r.exc {
                count += count_complexity_in_expr(exc);
            }
        }
        _ => {}
    }
    count
}

fn count_complexity_in_expr(expr: &Expr) -> u32 {
    let mut count: u32 = 0;
    match expr {
        Expr::BoolOp(b) => {
            // Each 'and'/'or' is a decision point
            // 'a and b' = 1, 'a and b and c' = 2
            count += (b.values.len() as u32).saturating_sub(1);
            for e in &b.values {
                count += count_complexity_in_expr(e);
            }
        }
        Expr::IfExp(i) => {
            // Ternary expressions are decision points
            count += 1;
            count += count_complexity_in_expr(&i.test);
            count += count_complexity_in_expr(&i.body);
            count += count_complexity_in_expr(&i.orelse);
        }
        Expr::Compare(c) => {
            count += count_complexity_in_expr(&c.left);
            for e in &c.comparators {
                count += count_complexity_in_expr(e);
            }
        }
        Expr::Call(c) => {
            count += count_complexity_in_expr(&c.func);
            for arg in &c.args {
                count += count_complexity_in_expr(arg);
            }
            // Also check keyword argument values
            for kw in &c.keywords {
                count += count_complexity_in_expr(&kw.value);
            }
        }
        Expr::BinOp(b) => {
            count += count_complexity_in_expr(&b.left);
            count += count_complexity_in_expr(&b.right);
        }
        Expr::UnaryOp(u) => {
            count += count_complexity_in_expr(&u.operand);
        }
        Expr::Lambda(l) => {
            count += count_complexity_in_expr(&l.body);
        }
        Expr::ListComp(l) => {
            count += count_complexity_in_expr(&l.elt);
            // Walk into comprehension generators (iter and ifs)
            for gen in &l.generators {
                count += count_complexity_in_expr(&gen.iter);
                for if_clause in &gen.ifs {
                    count += count_complexity_in_expr(if_clause);
                }
            }
        }
        Expr::SetComp(s) => {
            count += count_complexity_in_expr(&s.elt);
            for gen in &s.generators {
                count += count_complexity_in_expr(&gen.iter);
                for if_clause in &gen.ifs {
                    count += count_complexity_in_expr(if_clause);
                }
            }
        }
        Expr::DictComp(d) => {
            count += count_complexity_in_expr(&d.key);
            count += count_complexity_in_expr(&d.value);
            for gen in &d.generators {
                count += count_complexity_in_expr(&gen.iter);
                for if_clause in &gen.ifs {
                    count += count_complexity_in_expr(if_clause);
                }
            }
        }
        Expr::GeneratorExp(g) => {
            count += count_complexity_in_expr(&g.elt);
            for gen in &g.generators {
                count += count_complexity_in_expr(&gen.iter);
                for if_clause in &gen.ifs {
                    count += count_complexity_in_expr(if_clause);
                }
            }
        }
        Expr::Await(a) => {
            count += count_complexity_in_expr(&a.value);
        }
        Expr::Yield(y) => {
            if let Some(v) = &y.value {
                count += count_complexity_in_expr(v);
            }
        }
        Expr::YieldFrom(y) => {
            count += count_complexity_in_expr(&y.value);
        }
        Expr::Attribute(a) => {
            count += count_complexity_in_expr(&a.value);
        }
        Expr::Subscript(s) => {
            count += count_complexity_in_expr(&s.value);
            count += count_complexity_in_expr(&s.slice);
        }
        Expr::Starred(s) => {
            count += count_complexity_in_expr(&s.value);
        }
        Expr::List(l) => {
            for e in &l.elts {
                count += count_complexity_in_expr(e);
            }
        }
        Expr::Tuple(t) => {
            for e in &t.elts {
                count += count_complexity_in_expr(e);
            }
        }
        Expr::Dict(d) => {
            for k in d.keys.iter().flatten() {
                count += count_complexity_in_expr(k);
            }
            for v in &d.values {
                count += count_complexity_in_expr(v);
            }
        }
        Expr::Set(s) => {
            for e in &s.elts {
                count += count_complexity_in_expr(e);
            }
        }
        Expr::NamedExpr(n) => {
            count += count_complexity_in_expr(&n.value);
        }
        _ => {}
    };
    count
}

pub fn calculate_complexity_batch(source: &str) -> Option<HashMap<String, u32>> {
    let ast: Suite = Suite::parse(source, "<string>").ok()?;
    let mut results: HashMap<String, u32> = HashMap::new();

    for stmt in &ast {
        match stmt {
            Stmt::FunctionDef(f) => {
                let mut complexity: u32 = 1;
                for s in &f.body {
                    complexity += count_complexity_in_stmt(s);
                }
                results.insert(f.name.to_string(), complexity);
            }
            Stmt::AsyncFunctionDef(f) => {
                let mut complexity: u32 = 1;
                for s in &f.body {
                    complexity += count_complexity_in_stmt(s);
                }
                results.insert(f.name.to_string(), complexity);
            }
            Stmt::ClassDef(c) => {
                for s in &c.body {
                    match s {
                        Stmt::FunctionDef(f) => {
                            let mut complexity: u32 = 1;
                            for stmt in &f.body {
                                complexity += count_complexity_in_stmt(stmt);
                            }
                            let key = format!("{}.{}", c.name, f.name);
                            results.insert(key, complexity);
                        }
                        Stmt::AsyncFunctionDef(f) => {
                            let mut complexity: u32 = 1;
                            for stmt in &f.body {
                                complexity += count_complexity_in_stmt(stmt);
                            }
                            let key = format!("{}.{}", c.name, f.name);
                            results.insert(key, complexity);
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }

    Some(results)
}