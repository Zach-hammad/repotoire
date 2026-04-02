/// FNV-1a (64-bit) hash of byte slices. Same algorithm as
/// `detectors::base::finding_id()` — stable across Rust versions,
/// critical for persisted baseline files.
fn fnv1a(parts: &[&[u8]]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325; // FNV offset basis
    for part in parts {
        for &b in *part {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3); // FNV prime
        }
        h ^= 0xff_u64; // separator between parts
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Stable fingerprint for a finding tied to a single entity.
pub fn entity_fingerprint(detector_id: &str, qualified_name: &str) -> String {
    format!("{:016x}", fnv1a(&[detector_id.as_bytes(), qualified_name.as_bytes()]))
}

/// Stable fingerprint for a finding spanning multiple entities.
/// Qualified names are sorted for order independence.
pub fn multi_entity_fingerprint(detector_id: &str, qualified_names: &[&str]) -> String {
    let mut sorted: Vec<&str> = qualified_names.to_vec();
    sorted.sort();
    let mut parts: Vec<&[u8]> = vec![detector_id.as_bytes()];
    for qn in &sorted {
        parts.push(qn.as_bytes());
    }
    format!("{:016x}", fnv1a(&parts))
}

/// Fingerprint for a file-level finding with no entity anchor.
pub fn file_fingerprint(detector_id: &str, file_path: &str, first_line_content: &str) -> String {
    format!("{:016x}", fnv1a(&[
        detector_id.as_bytes(),
        file_path.as_bytes(),
        first_line_content.trim().as_bytes(),
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_entity_fingerprint_stable() {
        let a = entity_fingerprint("god-class", "src/engine/mod.rs::AnalysisEngine");
        let b = entity_fingerprint("god-class", "src/engine/mod.rs::AnalysisEngine");
        assert_eq!(a, b);
    }

    #[test]
    fn test_entity_fingerprint_differs_by_detector() {
        let a = entity_fingerprint("god-class", "src/engine/mod.rs::AnalysisEngine");
        let b = entity_fingerprint("long-method", "src/engine/mod.rs::AnalysisEngine");
        assert_ne!(a, b);
    }

    #[test]
    fn test_multi_entity_order_independent() {
        let a = multi_entity_fingerprint("hidden-coupling", &["mod_a::Foo", "mod_b::Bar"]);
        let b = multi_entity_fingerprint("hidden-coupling", &["mod_b::Bar", "mod_a::Foo"]);
        assert_eq!(a, b);
    }

    #[test]
    fn test_file_fingerprint_trims_whitespace() {
        let a = file_fingerprint("detector", "file.rs", "fn foo() {");
        let b = file_fingerprint("detector", "file.rs", "  fn foo() {  ");
        assert_eq!(a, b);
    }
}
