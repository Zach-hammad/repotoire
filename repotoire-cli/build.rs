use std::process::Command;

fn main() {
    // Git commit hash
    let hash = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .unwrap_or_default();
    println!("cargo:rustc-env=BUILD_GIT_HASH={}", hash.trim());

    // Build date
    let date = Command::new("date")
        .arg("+%Y-%m-%d")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .unwrap_or_default();
    println!("cargo:rustc-env=BUILD_DATE={}", date.trim());

    // Allocator
    let allocator = if cfg!(feature = "jemalloc") {
        "jemalloc"
    } else {
        "system"
    };
    println!("cargo:rustc-env=BUILD_ALLOCATOR={allocator}");
}
