use super::*;
use std::path::PathBuf;

#[test]
fn test_parse_simple_function() {
    let source = r#"
function hello(name: string): string {
    return `Hello, ${name}!`;
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse simple function");

    assert_eq!(result.functions.len(), 1);
    let func = &result.functions[0];
    assert_eq!(func.name, "hello");
}

#[test]
fn test_parse_async_function() {
    let source = r#"
async function fetchData(url: string): Promise<string> {
    return await fetch(url);
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse async function");

    assert_eq!(result.functions.len(), 1);
    let func = &result.functions[0];
    assert!(func.is_async);
}

#[test]
fn test_parse_arrow_function() {
    let source = r#"
const add = (a: number, b: number): number => a + b;
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse arrow function");

    assert!(result.functions.iter().any(|f| f.name == "add"));
}

#[test]
fn test_parse_class() {
    let source = r#"
class MyClass extends BaseClass implements Interface {
    constructor() {
        super();
    }

    method(): void {
        console.log("hello");
    }
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse class");

    assert_eq!(result.classes.len(), 1);
    let class = &result.classes[0];
    assert_eq!(class.name, "MyClass");
}

#[test]
fn test_parse_interface() {
    let source = r#"
interface MyInterface {
    name: string;
    doSomething(): void;
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse interface");

    assert_eq!(result.classes.len(), 1);
    let iface = &result.classes[0];
    assert_eq!(iface.name, "MyInterface");
}

#[test]
fn test_parse_imports() {
    let source = r#"
import { Component } from 'react';
import axios from 'axios';
import * as fs from 'fs';

export function main() {}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse imports");

    assert!(result.imports.iter().any(|i| i.path == "react"));
    assert!(result.imports.iter().any(|i| i.path == "axios"));
}

#[test]
fn test_parse_javascript() {
    let source = r#"
function greet(name) {
    return "Hello, " + name;
}
"#;
    let path = PathBuf::from("test.js");
    let result = parse_source(source, &path, "js").expect("should parse JavaScript");

    assert_eq!(result.functions.len(), 1);
    let func = &result.functions[0];
    assert_eq!(func.name, "greet");
}

#[test]
fn test_complexity_simple() {
    let source = r#"
function simple(): number {
    return 42;
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse simple function for complexity");

    let func = &result.functions[0];
    assert_eq!(func.name, "simple");
    // Simple function should have complexity 1
    assert_eq!(func.complexity, Some(1));
}

#[test]
fn test_complexity_with_branches() {
    let source = r#"
function complex(x: number): string {
    if (x > 10) {
        return "big";
    } else if (x > 5) {
        return "medium";
    } else if (x > 0) {
        return "small";
    }
    return "zero";
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse complex function");

    let func = &result.functions[0];
    assert_eq!(func.name, "complex");
    // if + else if + else if = 3 branches, base 1 = 4 total
    assert!(
        func.complexity.unwrap_or(0) >= 4,
        "Expected complexity >= 4, got {:?}",
        func.complexity
    );
}

#[test]
fn test_complexity_with_loops_and_ternary() {
    let source = r#"
function loopy(items: string[]): number {
    let count = 0;
    for (const item of items) {
        if (item.length > 5) {
            count++;
        }
    }
    return count > 0 ? count : -1;
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse loopy function");

    let func = &result.functions[0];
    assert_eq!(func.name, "loopy");
    // for + if + ternary = 3 branches + base 1 = 4
    assert!(
        func.complexity.unwrap_or(0) >= 4,
        "Expected complexity >= 4, got {:?}",
        func.complexity
    );
}

#[test]
fn test_parse_calls() {
    let source = r#"
function helperA() {
    console.log("hello");
}

function helperB() {
    helperA();
}

async function main() {
    helperB();
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse calls");

    assert_eq!(result.functions.len(), 3, "Expected 3 functions");

    // Debug: print what we got
    eprintln!(
        "Functions: {:?}",
        result
            .functions
            .iter()
            .map(|f| (&f.name, f.line_start, f.line_end))
            .collect::<Vec<_>>()
    );
    eprintln!("Calls: {:?}", result.calls);

    // helperB calls helperA
    assert!(
        result
            .calls
            .iter()
            .any(|(caller, callee)| caller.contains("helperB") && callee == "helperA"),
        "Expected helperB -> helperA call, got {:?}",
        result.calls
    );

    // main calls helperB
    assert!(
        result
            .calls
            .iter()
            .any(|(caller, callee)| caller.contains("main") && callee == "helperB"),
        "Expected main -> helperB call, got {:?}",
        result.calls
    );
}

#[test]
fn test_method_count_excludes_nested() {
    // Issue #18: Parser should not count closures/callbacks as class methods
    let source = r#"
class Foo {
    bar() {
        const inner = () => {};  // NOT a method - nested arrow function
        items.map(x => x);       // NOT a method - callback
        function localHelper() {} // NOT a method - nested function
    }
    baz() {}  // IS a method
    qux = () => {};  // IS a method - arrow function class field
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse class methods");

    assert_eq!(result.classes.len(), 1, "Expected 1 class");
    let class = &result.classes[0];
    assert_eq!(class.name, "Foo");

    // Should have exactly 3 methods: bar, baz, qux
    // NOT: inner, map callback, localHelper (these are nested)
    assert_eq!(
        class.methods.len(),
        3,
        "Expected 3 methods (bar, baz, qux), got {:?}",
        class.methods
    );
    assert!(
        class.methods.contains(&"bar".to_string()),
        "Missing 'bar' method"
    );
    assert!(
        class.methods.contains(&"baz".to_string()),
        "Missing 'baz' method"
    );
    assert!(
        class.methods.contains(&"qux".to_string()),
        "Missing 'qux' arrow field"
    );
}

#[test]
fn test_method_count_excludes_property_values() {
    // Ensure non-function class fields are not counted as methods
    let source = r#"
class Config {
    name = "test";      // NOT a method - string property
    count = 42;         // NOT a method - number property
    items = [1, 2, 3];  // NOT a method - array property
    handler = () => {}; // IS a method - arrow function
    process() {}        // IS a method
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse property values");

    let class = &result.classes[0];
    assert_eq!(
        class.methods.len(),
        2,
        "Expected 2 methods (handler, process), got {:?}",
        class.methods
    );
    assert!(class.methods.contains(&"handler".to_string()));
    assert!(class.methods.contains(&"process".to_string()));
}

#[test]
fn test_js_method_count_excludes_nested() {
    // Same test for JavaScript
    let source = r#"
class Service {
    constructor() {
        this.callbacks = [];
    }

    register(callback) {
        const wrapper = () => callback();  // nested, not a method
        this.callbacks.push(wrapper);
    }

    execute() {
        this.callbacks.forEach(cb => cb());  // callback, not a method
    }
}
"#;
    let path = PathBuf::from("test.js");
    let result = parse_source(source, &path, "js").expect("should parse JS class methods");

    let class = &result.classes[0];
    assert_eq!(
        class.methods.len(),
        3,
        "Expected 3 methods (constructor, register, execute), got {:?}",
        class.methods
    );
}

#[test]
fn test_jsdoc_extracted() {
    let source = r#"
/**
 * Adds two numbers together.
 * @param a - first number
 * @param b - second number
 * @returns the sum
 */
function add(a: number, b: number): number {
    return a + b;
}
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse JSDoc");

    let func = &result.functions[0];
    assert_eq!(func.name, "add");
    assert!(func.doc_comment.is_some(), "Should have JSDoc");
    let doc = func.doc_comment.as_ref().expect("doc_comment should be Some");
    assert!(doc.contains("Adds two numbers"), "Got: {}", doc);
}

#[test]
fn test_jsdoc_on_arrow_function() {
    let source = r#"
/** Multiplies two values */
const multiply = (a: number, b: number): number => a * b;
"#;
    let path = PathBuf::from("test.ts");
    let result = parse_source(source, &path, "ts").expect("should parse arrow function JSDoc");

    let func = result.functions.iter().find(|f| f.name == "multiply").expect("should find multiply function");
    assert!(func.doc_comment.is_some(), "Arrow function should have JSDoc");
    assert!(func.doc_comment.as_ref().expect("doc_comment should be Some").contains("Multiplies"));
}

#[test]
fn test_react_component_detected() {
    let source = r#"
function MyComponent({ name }: { name: string }) {
    return <div>Hello {name}</div>;
}

function helperFunction() {
    return 42;
}
"#;
    let path = PathBuf::from("test.tsx");
    let result = parse_source(source, &path, "tsx").expect("should parse TSX components");

    let component = result.functions.iter().find(|f| f.name == "MyComponent").expect("should find MyComponent");
    assert!(
        component.annotations.contains(&"react:component".to_string()),
        "Should detect React component, got: {:?}",
        component.annotations
    );

    let helper = result.functions.iter().find(|f| f.name == "helperFunction").expect("should find helperFunction");
    assert!(
        !helper.annotations.contains(&"react:component".to_string()),
        "helperFunction should not be a React component"
    );
}

#[test]
fn test_react_hooks_detected() {
    let source = r#"
function Counter() {
    const [count, setCount] = useState(0);
    useEffect(() => {
        document.title = `Count: ${count}`;
    }, [count]);
    const ref = useRef(null);
    return <div>{count}</div>;
}
"#;
    let path = PathBuf::from("test.tsx");
    let result = parse_source(source, &path, "tsx").expect("should parse React hooks");

    let counter = result.functions.iter().find(|f| f.name == "Counter").expect("should find Counter");
    assert!(
        counter.annotations.iter().any(|a| a == "react:hook:useState"),
        "Should detect useState hook, got: {:?}",
        counter.annotations
    );
    assert!(
        counter.annotations.iter().any(|a| a == "react:hook:useEffect"),
        "Should detect useEffect hook, got: {:?}",
        counter.annotations
    );
    assert!(
        counter.annotations.iter().any(|a| a == "react:hook:useRef"),
        "Should detect useRef hook, got: {:?}",
        counter.annotations
    );
}
