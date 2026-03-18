// This file is intentionally bad Rust code for testing Repotoire detectors.
// DO NOT fix these issues — they are here to verify detectors fire correctly.

use std::collections::HashMap;
use std::fs;
use std::io::{self, Read};
use std::ptr;

// =============================================================================
// 1. .unwrap() without context (UnwrapWithoutContextDetector)
// =============================================================================

pub fn load_config(path: &str) -> String {
    let content = fs::read_to_string(path).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
    parsed["key"].as_str().unwrap().to_string()
}

pub fn get_env_value(key: &str) -> String {
    std::env::var(key).unwrap()
}

// =============================================================================
// 2. unsafe blocks without // SAFETY: comments (UnsafeWithoutSafetyCommentDetector)
// =============================================================================

pub fn raw_pointer_deref(data: *const u8, len: usize) -> Vec<u8> {
    let mut result = Vec::with_capacity(len);
    unsafe {
        for i in 0..len {
            result.push(*data.add(i));
        }
    }
    result
}

pub fn transmute_value(val: u64) -> f64 {
    unsafe { std::mem::transmute(val) }
}

pub fn write_volatile_data(dst: *mut u32, val: u32) {
    unsafe {
        ptr::write_volatile(dst, val);
    }
}

// =============================================================================
// 3. .clone() in loops / hot paths (CloneInHotPathDetector)
// =============================================================================

pub fn process_items(items: &[String], prefix: String) -> Vec<String> {
    let mut results = Vec::new();
    for item in items.iter() {
        let owned_prefix = prefix.clone();
        let owned_item = item.clone();
        results.push(format!("{}{}", owned_prefix, owned_item));
    }
    results
}

pub fn aggregate_maps(maps: &[HashMap<String, String>]) -> HashMap<String, String> {
    let mut merged = HashMap::new();
    for map in maps.iter() {
        for (key, value) in map.iter() {
            let k = key.clone();
            let v = value.clone();
            merged.insert(k, v);
        }
    }
    merged
}

// =============================================================================
// 4. pub fn returning Result without #[must_use] (MissingMustUseDetector)
// =============================================================================

pub fn connect_to_database(url: &str) -> Result<(), io::Error> {
    if url.is_empty() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "empty url"));
    }
    Ok(())
}

pub fn write_report(path: &str, data: &[u8]) -> Result<usize, io::Error> {
    fs::write(path, data)?;
    Ok(data.len())
}

pub fn validate_input(input: &str) -> Result<String, String> {
    if input.len() < 3 {
        return Err("too short".into());
    }
    Ok(input.to_uppercase())
}

pub fn parse_config_file(path: &str) -> Result<HashMap<String, String>, io::Error> {
    let content = fs::read_to_string(path)?;
    let mut map = HashMap::new();
    for line in content.lines() {
        if let Some((k, v)) = line.split_once('=') {
            map.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    Ok(map)
}

// =============================================================================
// 5. Multiple panic!() calls — high panic density (PanicDensityDetector)
// =============================================================================

pub fn panic_heavy_function(stage: u32) {
    if stage == 0 {
        panic!("stage 0 is invalid");
    }
    let data = fs::read_to_string("/tmp/data").unwrap();
    let parsed = data.parse::<u64>().unwrap();
    if parsed == 0 {
        panic!("parsed value must not be zero");
    }
    let result = some_fallible_call().unwrap();
    if result.is_empty() {
        panic!("result cannot be empty");
    }
    let extra = another_call().expect("");
    if extra > 1000 {
        panic!("extra value out of range");
    }
}

fn some_fallible_call() -> Result<String, String> {
    Ok("ok".into())
}

fn another_call() -> Result<u64, String> {
    Ok(42)
}

// =============================================================================
// 6. Empty match arms (EmptyCatchDetector)
// =============================================================================

pub fn handle_status(code: u32) -> &'static str {
    match code {
        200 => "ok",
        404 => "not found",
        500 => {},
        _ => {},
    };
    "unknown"
}

pub fn process_result(r: Result<String, io::Error>) {
    match r {
        Ok(val) => println!("{}", val),
        Err(_) => {} // silently swallowed error
    }
}

// =============================================================================
// 7. Deep nesting — 5+ levels (DeepNestingDetector, threshold=4)
// =============================================================================

pub fn deeply_nested_logic(data: &[Vec<Option<HashMap<String, Vec<u32>>>>]) -> u32 {
    let mut total = 0u32;
    for outer in data.iter() {                          // level 1
        for inner in outer.iter() {                     // level 2
            if let Some(map) = inner {                  // level 3
                for (_key, values) in map.iter() {      // level 4
                    for val in values.iter() {           // level 5
                        if *val > 10 {                   // level 6
                            total += *val;
                        }
                    }
                }
            }
        }
    }
    total
}

pub fn another_deep_function(x: i32) -> i32 {
    if x > 0 {
        if x > 10 {
            if x > 100 {
                if x > 1000 {
                    if x > 10000 {
                        return x * 2;
                    }
                }
            }
        }
    }
    x
}

// =============================================================================
// 8. Magic numbers (MagicNumbersDetector)
// =============================================================================

pub fn calculate_shipping(weight: f64, distance: f64) -> f64 {
    let base = weight * 3.75;
    let surcharge = if distance > 500.0 { 12.99 } else { 0.0 };
    let tax_rate = 0.0825;
    base + surcharge + (base * tax_rate) + 4.50
}

pub fn compute_score(raw: u32) -> u32 {
    let adjusted = raw * 17 + 42;
    if adjusted > 9999 {
        return 255;
    }
    adjusted % 256
}

// =============================================================================
// 9. TODO comments (TodoScanner)
// =============================================================================

// TODO: implement proper error handling
// TODO: this function needs major refactoring
// TODO(security): sanitize inputs before processing
// FIXME: race condition when accessing shared state
// HACK: temporary workaround for upstream bug

pub fn placeholder_function() -> bool {
    // TODO: replace stub with real implementation
    false
}

// =============================================================================
// 10. Additional smells: commented-out code, dead code
// =============================================================================

// fn old_implementation() {
//     let x = 42;
//     println!("{}", x);
//     for i in 0..10 {
//         process(i);
//     }
// }

#[allow(dead_code)]
fn never_called_helper(a: i32, b: i32, c: i32, d: i32, e: i32, f: i32, g: i32) -> i32 {
    a + b + c + d + e + f + g
}
