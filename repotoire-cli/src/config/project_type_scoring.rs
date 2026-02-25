use std::path::Path;

/// Read Cargo.toml content if it exists
fn read_cargo_toml(repo_path: &Path) -> Option<String> {
    let path = repo_path.join("Cargo.toml");
    std::fs::read_to_string(path).ok()
}

/// Score Cargo.toml dependencies: add `points` for each matching dep
fn score_cargo_deps(repo_path: &Path, deps: &[&str], points: u32) -> u32 {
    let Some(content) = read_cargo_toml(repo_path) else { return 0 };
    deps.iter().filter(|dep| content.contains(*dep)).count() as u32 * points
}

pub(super) fn score_framework_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const FRAMEWORK_DIRS: &[&str] = &[
        "reconciler",
        "scheduler",
        "renderer",
        "dom",
        "fiber",
        "packages/react",
        "packages/vue",
        "packages/angular",
    ];

    // Check for framework-specific directories
    for dir in FRAMEWORK_DIRS {
        if repo_path.join(dir).is_dir() {
            score += 3;
        }
    }

    // Check package.json for framework name in "name" field
    let package_json = repo_path.join("package.json");
    if package_json.exists() {
        if let Ok(content) = std::fs::read_to_string(&package_json) {
            // Check if this IS a framework (not just uses one)
            if content.contains("\"name\": \"react\"")
                || content.contains("\"name\": \"vue\"")
                || content.contains("\"name\": \"angular\"")
                || content.contains("\"name\": \"svelte\"")
                || content.contains("\"name\": \"preact\"")
                || content.contains("\"name\": \"solid-js\"")
            {
                score += 10; // Strong signal
            }
        }
    }

    // Check for monorepo packages that indicate framework
    if let Ok(packages) = std::fs::read_dir(repo_path.join("packages")) {
        for entry in packages.filter_map(|e| e.ok()) {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.contains("reconciler")
                || name_str.contains("scheduler")
                || name_str.contains("dom")
                || name_str.contains("core")
                || name_str.contains("runtime")
            {
                score += 2;
            }
        }
    }

    score
}

/// Score interpreter/VM markers
pub(super) fn score_interpreter_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const INTERPRETER_DIRS: &[&str] = &[
        "vm",
        "interpreter",
        "bytecode",
        "runtime",
        "eval",
        "opcode",
        "jit",
        "gc",
        "allocator",
    ];
    const INTERPRETER_FILES: &[&str] = &[
        "vm.c",
        "vm.rs",
        "interpreter.c",
        "interpreter.rs",
        "eval.c",
        "eval.rs",
        "bytecode.c",
        "bytecode.rs",
        "opcode.h",
        "opcodes.h",
    ];

    for dir in INTERPRETER_DIRS {
        if repo_path.join(dir).is_dir()
            || repo_path.join(format!("src/{}", dir)).is_dir()
            || repo_path.join(format!("pkg/{}", dir)).is_dir()
        {
            score += 3;
        }
    }
    for file in INTERPRETER_FILES {
        if repo_path.join(file).exists() || repo_path.join(format!("src/{}", file)).exists() {
            score += 2;
        }
    }
    score
}

/// Score compiler markers
pub(super) fn score_compiler_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const COMPILER_DIRS: &[&str] = &[
        "parser",
        "lexer",
        "codegen",
        "ast",
        "ir",
        "optimizer",
        "frontend",
        "backend",
        "compiler",
        "HIR",
        "MIR",
        "LIR",
        "transform",
        "analysis",
    ];

    for dir in COMPILER_DIRS {
        if repo_path.join(dir).is_dir()
            || repo_path.join(format!("src/{}", dir)).is_dir()
            || repo_path.join(format!("packages/{}", dir)).is_dir()
        {
            score += 2;
        }
    }

    // Check for packages/*/compiler pattern (monorepo like React)
    if let Ok(packages) = std::fs::read_dir(repo_path.join("packages")) {
        for entry in packages.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if name.contains("compiler") || name.contains("transform") {
                score += 5; // Strong signal
            }
        }
    }

    score
}

/// Score kernel/embedded markers
pub(super) fn score_kernel_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const KERNEL_DIRS: &[&str] = &[
        "kernel",
        "drivers",
        "arch",
        "syscall",
        "interrupt",
        "hal",
        "bsp",
    ];
    const KERNEL_FILES: &[&str] = &[
        "Kconfig",
        "Makefile.inc",
        "linker.ld",
        "boot.S",
        "startup.s",
    ];

    for dir in KERNEL_DIRS {
        if repo_path.join(dir).is_dir() {
            score += 4;
        }
    }
    for file in KERNEL_FILES {
        if repo_path.join(file).exists() {
            score += 5;
        }
    }
    score
}

/// Score game engine markers
pub(super) fn score_game_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const GAME_DIRS: &[&str] = &[
        "engine", "ecs", "physics", "renderer", "assets", "scenes", "shaders",
    ];
    const GAME_FILES: &[&str] = &["game.rs", "game.cpp", "engine.rs", "engine.cpp"];

    for dir in GAME_DIRS {
        if repo_path.join(dir).is_dir() || repo_path.join(format!("src/{}", dir)).is_dir() {
            score += 2;
        }
    }
    for file in GAME_FILES {
        if repo_path.join(file).exists() || repo_path.join(format!("src/{}", file)).exists() {
            score += 3;
        }
    }

    // Check for game-specific dependencies
    score += score_cargo_deps(repo_path, &["bevy", "ggez", "amethyst", "macroquad", "fyrox", "godot"], 5);

    score
}

/// Score CLI tool markers
pub(super) fn score_cli_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const CLI_DIRS: &[&str] = &["cli", "cmd", "commands"];

    // Check for CLI framework deps
    let cargo_content = read_cargo_toml(repo_path);
    if let Some(ref content) = cargo_content {
        if content.contains("clap") || content.contains("structopt") {
            score += 4;
        }
    }

    // Check go.mod for cobra
    let go_mod = repo_path.join("go.mod");
    if go_mod.exists() {
        if let Ok(content) = std::fs::read_to_string(&go_mod) {
            if content.contains("cobra") || content.contains("urfave/cli") {
                score += 4;
            }
        }
    }

    // Check for click/argparse in Python
    let requirements = repo_path.join("requirements.txt");
    let pyproject = repo_path.join("pyproject.toml");
    for file_path in [requirements, pyproject] {
        if !file_path.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&file_path) else {
            continue;
        };
        if content.contains("click")
            || content.contains("typer")
            || content.contains("argparse")
        {
            score += 3;
        }
    }

    for dir in CLI_DIRS {
        if repo_path.join(dir).is_dir() || repo_path.join(format!("src/{}", dir)).is_dir() {
            score += 2;
        }
    }

    // cli.rs or cli.go is a strong signal
    if repo_path.join("src/cli.rs").exists()
        || repo_path.join("cli.go").exists()
        || repo_path.join("cmd/main.go").exists()
    {
        score += 3;
    }

    score
}

/// Score library markers
pub(super) fn score_library_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    let lib_rs = repo_path.join("src/lib.rs");
    let main_rs = repo_path.join("src/main.rs");

    // Pure library: has lib.rs but no main.rs
    if lib_rs.exists() && !main_rs.exists() {
        score += 5;
    } else if lib_rs.exists() {
        score += 2; // Both lib and main = mixed
    }

    // Check Cargo.toml for [lib] section
    let cargo_toml = repo_path.join("Cargo.toml");
    if cargo_toml.exists() {
        if let Ok(content) = std::fs::read_to_string(&cargo_toml) {
            if content.contains("[lib]") {
                score += 2;
            }
            if !content.contains("[[bin]]") {
                score += 1;
            }
        }
    }

    // Check for setup.py / pyproject.toml with library structure
    if (repo_path.join("setup.py").exists() || repo_path.join("pyproject.toml").exists())
        && !repo_path.join("__main__.py").exists()
    {
        score += 3;
    }

    score
}

/// Score Rust web framework dependencies from Cargo.toml.
fn score_cargo_web_deps(repo_path: &Path) -> u32 {
    let cargo_toml = repo_path.join("Cargo.toml");
    let Ok(content) = std::fs::read_to_string(&cargo_toml) else {
        return 0;
    };
    let web_deps = ["actix-web", "axum", "rocket", "warp", "tide"];
    let mut score = 0u32;
    for dep in web_deps {
        if content.contains(dep) {
            score += 4;
        }
    }
    score
}

/// Score JavaScript web framework dependencies from package.json.
fn score_package_json_web_deps(repo_path: &Path) -> u32 {
    let package_json = repo_path.join("package.json");
    let Ok(content) = std::fs::read_to_string(&package_json) else {
        return 0;
    };
    let mut score = 0u32;
    // Backend frameworks
    let backend_deps = ["express", "fastify", "koa", "hapi", "nest"];
    for dep in backend_deps {
        if content.contains(&format!("\"{}\"", dep)) {
            score += 4;
        }
    }
    // Frontend (but using, not being)
    let frontend_deps = ["next", "nuxt", "gatsby"];
    for dep in frontend_deps {
        if content.contains(&format!("\"{}\"", dep)) {
            score += 3;
        }
    }
    score
}

/// Score web framework markers
pub(super) fn score_web_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    // Check for common web framework dependencies
    score += score_cargo_web_deps(repo_path);
    score += score_package_json_web_deps(repo_path);

    let requirements = repo_path.join("requirements.txt");
    let pyproject = repo_path.join("pyproject.toml");
    for file_path in [requirements, pyproject] {
        let Ok(content) = std::fs::read_to_string(&file_path) else { continue; };
        let web_deps = [
            "flask",
            "django",
            "fastapi",
            "starlette",
            "tornado",
            "sanic",
        ];
        for dep in web_deps {
            if content.contains(dep) {
                score += 4;
            }
        }
    }

    // Check go.mod for Go web frameworks
    let go_mod = repo_path.join("go.mod");
    if let Ok(content) = std::fs::read_to_string(&go_mod) {
        let go_web = ["gin-gonic", "echo", "fiber", "chi", "gorilla/mux"];
        for dep in go_web {
            if content.contains(dep) {
                score += 4;
            }
        }
    }

    // Check for routes/controllers/handlers directories
    const WEB_DIRS: &[&str] = &[
        "routes",
        "controllers",
        "handlers",
        "views",
        "api",
        "endpoints",
    ];
    for dir in WEB_DIRS {
        if repo_path.join(dir).is_dir()
            || repo_path.join(format!("src/{}", dir)).is_dir()
            || repo_path.join(format!("app/{}", dir)).is_dir()
        {
            score += 2;
        }
    }

    score
}

/// Score data science / ML markers
pub(super) fn score_datascience_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    // Check for Jupyter notebooks
    if let Ok(entries) = std::fs::read_dir(repo_path) {
        for entry in entries.filter_map(|e| e.ok()) {
            let name = entry.file_name();
            if name.to_string_lossy().ends_with(".ipynb") {
                score += 3;
            }
        }
    }
    if repo_path.join("notebooks").is_dir() {
        score += 4;
    }

    // Check for ML/DS dependencies
    let requirements = repo_path.join("requirements.txt");
    let pyproject = repo_path.join("pyproject.toml");
    let ml_deps = [
        "numpy",
        "pandas",
        "scikit-learn",
        "sklearn",
        "tensorflow",
        "torch",
        "pytorch",
        "keras",
        "xgboost",
        "lightgbm",
        "transformers",
        "matplotlib",
        "seaborn",
        "plotly",
        "jupyter",
        "scipy",
    ];
    for file_path in [requirements, pyproject] {
        let Ok(content) = std::fs::read_to_string(&file_path) else {
            continue;
        };
        score += 2 * ml_deps.iter().filter(|dep| content.contains(*dep)).count() as u32;
    }

    // Check for data/models directories
    const DS_DIRS: &[&str] = &[
        "data",
        "models",
        "training",
        "inference",
        "experiments",
        "notebooks",
    ];
    for dir in DS_DIRS {
        if repo_path.join(dir).is_dir() {
            score += 1;
        }
    }

    score
}

/// Score mobile app markers
pub(super) fn score_mobile_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    // iOS markers
    if repo_path.join("Info.plist").exists() || repo_path.join("AppDelegate.swift").exists() {
        score += 5;
    }
    if repo_path.join("Podfile").exists() || repo_path.join("Package.swift").exists() {
        score += 3;
    }
    let xcodeproj = repo_path.read_dir().ok().and_then(|mut d| {
        d.find(|e| {
            e.as_ref()
                .ok()
                .map(|e| {
                    e.path()
                        .extension()
                        .map(|x| x == "xcodeproj")
                        .unwrap_or(false)
                })
                .unwrap_or(false)
        })
    });
    if xcodeproj.is_some() {
        score += 5;
    }

    // Android markers
    if repo_path.join("AndroidManifest.xml").exists()
        || repo_path.join("app/src/main/AndroidManifest.xml").exists()
    {
        score += 5;
    }
    if repo_path.join("build.gradle").exists() || repo_path.join("build.gradle.kts").exists() {
        if let Ok(content) = std::fs::read_to_string(repo_path.join("build.gradle")) {
            if content.contains("android") {
                score += 4;
            }
        }
    }

    // React Native / Flutter
    let package_json = repo_path.join("package.json");
    if package_json.exists() {
        if let Ok(content) = std::fs::read_to_string(&package_json) {
            if content.contains("react-native") {
                score += 5;
            }
        }
    }
    if repo_path.join("pubspec.yaml").exists() {
        score += 5; // Flutter
    }

    score
}
