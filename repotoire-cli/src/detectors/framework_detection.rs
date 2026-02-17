//! Framework and ORM detection
//!
//! Detects frameworks and ORMs used in a repository by analyzing:
//! - package.json (Node.js)
//! - requirements.txt / pyproject.toml (Python)
//! - Cargo.toml (Rust)
//! - pom.xml / build.gradle (Java)
//! - Gemfile (Ruby)
//!
//! This is used to reduce false positives in security detectors by recognizing
//! safe ORM patterns that use parameterized queries internally.

use serde::Deserialize;
use std::collections::HashSet;
use std::path::Path;
use tracing::debug;

/// Known frameworks and ORMs
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Framework {
    // JavaScript/TypeScript ORMs
    Prisma,
    Drizzle,
    TypeORM,
    Sequelize,
    Knex,
    Mongoose,
    Objection,
    MikroORM,
    BetterSQLite3,

    // Python ORMs
    Django,
    SQLAlchemy,
    Peewee,
    TortoiseORM,
    Pony,
    SQLObject,

    // Rust ORMs
    Diesel,
    SeaORM,
    SQLx,

    // Java ORMs
    Hibernate,
    JPA,
    MyBatis,
    JOOQ,
    SpringData,

    // Ruby ORMs
    ActiveRecord,
    Sequel,

    // Go ORMs
    GORM,
    Ent,
    SQLBoiler,
    Bun,
}

impl Framework {
    /// Returns the safe patterns for this framework that should NOT trigger SQL injection warnings
    pub fn safe_patterns(&self) -> &'static [&'static str] {
        match self {
            // Drizzle ORM - all these are parameterized internally
            Framework::Drizzle => &[
                "db.select(",
                "db.insert(",
                "db.update(",
                "db.delete(",
                ".from(",
                ".where(",
                ".set(",
                ".values(",
                "eq(",
                "ne(",
                "gt(",
                "gte(",
                "lt(",
                "lte(",
                "like(",
                "ilike(",
                "inArray(",
                "notInArray(",
                "isNull(",
                "isNotNull(",
                "between(",
                "and(",
                "or(",
            ],

            // Prisma - completely safe ORM, always parameterized
            Framework::Prisma => &[
                "prisma.",
                ".findMany(",
                ".findFirst(",
                ".findUnique(",
                ".findUniqueOrThrow(",
                ".findFirstOrThrow(",
                ".create(",
                ".createMany(",
                ".update(",
                ".updateMany(",
                ".upsert(",
                ".delete(",
                ".deleteMany(",
                ".count(",
                ".aggregate(",
                ".groupBy(",
            ],

            // TypeORM
            Framework::TypeORM => &[
                "repository.find(",
                "repository.findOne(",
                "repository.findOneBy(",
                "repository.findBy(",
                "repository.save(",
                "repository.insert(",
                "repository.update(",
                "repository.delete(",
                "repository.count(",
                "createQueryBuilder(",
                ".where(",
                ".andWhere(",
                ".orWhere(",
                ".getOne(",
                ".getMany(",
                ".getRepository(",
            ],

            // Sequelize
            Framework::Sequelize => &[
                ".findAll(",
                ".findOne(",
                ".findByPk(",
                ".findOrCreate(",
                ".findAndCountAll(",
                ".create(",
                ".bulkCreate(",
                ".update(",
                ".destroy(",
                ".count(",
                ".max(",
                ".min(",
                ".sum(",
                "Op.eq",
                "Op.ne",
                "Op.gt",
                "Op.gte",
                "Op.lt",
                "Op.lte",
                "Op.like",
                "Op.in",
                "Op.notIn",
            ],

            // Knex - query builder, uses parameterized queries
            Framework::Knex => &[
                "knex(",
                ".select(",
                ".where(",
                ".whereIn(",
                ".whereNotIn(",
                ".whereBetween(",
                ".whereNull(",
                ".whereNotNull(",
                ".insert(",
                ".update(",
                ".del(",
                ".delete(",
                ".first(",
            ],

            // better-sqlite3 - uses prepared statements
            Framework::BetterSQLite3 => &[
                ".prepare(",
                ".prepare().all(",
                ".prepare().get(",
                ".prepare().run(",
                ".prepare().iterate(",
                ".prepare().pluck(",
                ".prepare().expand(",
                "stmt.all(",
                "stmt.get(",
                "stmt.run(",
                "stmt.iterate(",
                "stmt.pluck(",
            ],

            // Django ORM
            Framework::Django => &[
                ".objects.",
                ".filter(",
                ".exclude(",
                ".get(",
                ".create(",
                ".update(",
                ".delete(",
                ".annotate(",
                ".aggregate(",
                ".values(",
                ".values_list(",
                ".only(",
                ".defer(",
                ".select_related(",
                ".prefetch_related(",
                "Q(",
                "F(",
            ],

            // SQLAlchemy (when using ORM, not raw text())
            Framework::SQLAlchemy => &[
                "session.query(",
                ".filter(",
                ".filter_by(",
                ".get(",
                ".all(",
                ".first(",
                ".one(",
                ".one_or_none(",
                ".scalar(",
                ".count(",
                ".order_by(",
                ".group_by(",
                ".join(",
                ".outerjoin(",
                "select(",
                "insert(",
                "update(",
                "delete(",
            ],

            // Peewee
            Framework::Peewee => &[
                ".select(",
                ".where(",
                ".get(",
                ".get_or_none(",
                ".get_or_create(",
                ".create(",
                ".insert(",
                ".insert_many(",
                ".update(",
                ".delete(",
            ],

            // Tortoise ORM
            Framework::TortoiseORM => &[
                ".all(",
                ".filter(",
                ".get(",
                ".get_or_none(",
                ".create(",
                ".update_or_create(",
                ".delete(",
                ".first(",
                ".count(",
            ],

            // Diesel (Rust)
            Framework::Diesel => &[
                ".filter(",
                ".find(",
                ".select(",
                ".order(",
                ".limit(",
                ".offset(",
                ".load(",
                ".get_result(",
                ".get_results(",
                ".execute(",
                ".first(",
                "diesel::",
                "insert_into(",
                "update(",
                "delete(",
            ],

            // SeaORM (Rust)
            Framework::SeaORM => &[
                "Entity::find(",
                "Entity::insert(",
                "Entity::update(",
                "Entity::delete(",
                ".filter(",
                ".all(",
                ".one(",
                ".into_model(",
            ],

            // SQLx (Rust) - parameterized queries
            Framework::SQLx => &[
                "sqlx::query!(",
                "sqlx::query_as!(",
                "query!(",
                "query_as!(",
                ".fetch_one(",
                ".fetch_all(",
                ".fetch_optional(",
                ".execute(",
            ],

            // GORM (Go)
            Framework::GORM => &[
                "db.Find(",
                "db.First(",
                "db.Last(",
                "db.Take(",
                "db.Create(",
                "db.Save(",
                "db.Update(",
                "db.Updates(",
                "db.Delete(",
                "db.Where(",
                "db.Or(",
                "db.Not(",
                "db.Model(",
                "db.Select(",
                "db.Omit(",
            ],

            // ActiveRecord (Ruby)
            Framework::ActiveRecord => &[
                ".find(",
                ".find_by(",
                ".where(",
                ".find_or_create_by(",
                ".find_or_initialize_by(",
                ".create(",
                ".create!(",
                ".update(",
                ".update!(",
                ".destroy(",
                ".pluck(",
                ".select(",
                ".joins(",
                ".includes(",
            ],

            // For others, return empty - we'll add as needed
            _ => &[],
        }
    }

    /// Returns patterns that indicate UNSAFE usage even with this framework
    /// (e.g., raw SQL methods that bypass ORM safety)
    pub fn unsafe_patterns(&self) -> &'static [&'static str] {
        match self {
            Framework::Django => &[".raw(", ".extra(", "connection.cursor()"],
            Framework::SQLAlchemy => &["text(", ".from_statement(", "engine.execute("],
            Framework::TypeORM => &[".query(", ".createQueryBuilder().query("],
            Framework::Sequelize => &[".query(", "sequelize.query("],
            Framework::ActiveRecord => &[".find_by_sql(", ".execute(", "connection.execute("],
            Framework::GORM => &[".Raw(", ".Exec("],
            Framework::Knex => &[".raw("],
            _ => &[],
        }
    }
}

/// Minimal package.json structure for parsing
#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct PackageJson {
    dependencies: std::collections::HashMap<String, serde_json::Value>,
    #[serde(rename = "devDependencies")]
    dev_dependencies: std::collections::HashMap<String, serde_json::Value>,
}

/// Minimal pyproject.toml structure
#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct PyProjectToml {
    project: Option<PyProjectProject>,
    tool: Option<PyProjectTool>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct PyProjectProject {
    dependencies: Vec<String>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct PyProjectTool {
    poetry: Option<PoetrySection>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct PoetrySection {
    dependencies: std::collections::HashMap<String, serde_json::Value>,
}

/// Minimal Cargo.toml structure
#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct CargoToml {
    dependencies: std::collections::HashMap<String, serde_json::Value>,
}

/// Detect frameworks and ORMs used in a repository
pub fn detect_frameworks(repo_path: &Path) -> HashSet<Framework> {
    let mut frameworks = HashSet::new();

    // Check Node.js (package.json)
    let package_json_path = repo_path.join("package.json");
    if package_json_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&package_json_path) {
            if let Ok(pkg) = serde_json::from_str::<PackageJson>(&content) {
                detect_node_frameworks(&pkg, &mut frameworks);
            }
        }
    }

    // Check Python (requirements.txt)
    let requirements_path = repo_path.join("requirements.txt");
    if requirements_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&requirements_path) {
            detect_python_frameworks_from_requirements(&content, &mut frameworks);
        }
    }

    // Check Python (pyproject.toml)
    let pyproject_path = repo_path.join("pyproject.toml");
    if pyproject_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&pyproject_path) {
            detect_python_frameworks_from_pyproject(&content, &mut frameworks);
        }
    }

    // Check Rust (Cargo.toml)
    let cargo_path = repo_path.join("Cargo.toml");
    if cargo_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&cargo_path) {
            detect_rust_frameworks(&content, &mut frameworks);
        }
    }

    // Check Ruby (Gemfile)
    let gemfile_path = repo_path.join("Gemfile");
    if gemfile_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&gemfile_path) {
            detect_ruby_frameworks(&content, &mut frameworks);
        }
    }

    // Check Go (go.mod)
    let gomod_path = repo_path.join("go.mod");
    if gomod_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&gomod_path) {
            detect_go_frameworks(&content, &mut frameworks);
        }
    }

    // Check Java (pom.xml)
    let pom_path = repo_path.join("pom.xml");
    if pom_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&pom_path) {
            detect_java_frameworks(&content, &mut frameworks);
        }
    }

    // Check Java (build.gradle)
    let gradle_path = repo_path.join("build.gradle");
    let gradle_kts_path = repo_path.join("build.gradle.kts");
    for path in [gradle_path, gradle_kts_path] {
        if path.exists() {
            if let Ok(content) = std::fs::read_to_string(&path) {
                detect_java_frameworks(&content, &mut frameworks);
            }
        }
    }

    debug!("Detected frameworks: {:?}", frameworks);
    frameworks
}

fn detect_node_frameworks(pkg: &PackageJson, frameworks: &mut HashSet<Framework>) {
    let all_deps: Vec<&str> = pkg
        .dependencies
        .keys()
        .chain(pkg.dev_dependencies.keys())
        .map(|s| s.as_str())
        .collect();

    for dep in all_deps {
        match dep {
            "prisma" | "@prisma/client" => {
                frameworks.insert(Framework::Prisma);
            }
            "drizzle-orm" => {
                frameworks.insert(Framework::Drizzle);
            }
            "typeorm" => {
                frameworks.insert(Framework::TypeORM);
            }
            "sequelize" => {
                frameworks.insert(Framework::Sequelize);
            }
            "knex" => {
                frameworks.insert(Framework::Knex);
            }
            "mongoose" => {
                frameworks.insert(Framework::Mongoose);
            }
            "objection" => {
                frameworks.insert(Framework::Objection);
            }
            "@mikro-orm/core" | "mikro-orm" => {
                frameworks.insert(Framework::MikroORM);
            }
            "better-sqlite3" => {
                frameworks.insert(Framework::BetterSQLite3);
            }
            _ => {}
        }
    }
}

fn detect_python_frameworks_from_requirements(content: &str, frameworks: &mut HashSet<Framework>) {
    for line in content.lines() {
        let line = line.trim().to_lowercase();
        // Skip comments and empty lines
        if line.starts_with('#') || line.is_empty() {
            continue;
        }

        // Extract package name (before any version specifier)
        let pkg = line
            .split(['=', '<', '>', '[', ';'])
            .next()
            .unwrap_or("")
            .trim();

        match pkg {
            "django" => {
                frameworks.insert(Framework::Django);
            }
            "sqlalchemy" => {
                frameworks.insert(Framework::SQLAlchemy);
            }
            "peewee" => {
                frameworks.insert(Framework::Peewee);
            }
            "tortoise-orm" => {
                frameworks.insert(Framework::TortoiseORM);
            }
            "pony" => {
                frameworks.insert(Framework::Pony);
            }
            "sqlobject" => {
                frameworks.insert(Framework::SQLObject);
            }
            _ => {}
        }
    }
}

fn detect_python_frameworks_from_pyproject(content: &str, frameworks: &mut HashSet<Framework>) {
    // Try to parse as TOML
    if let Ok(pyproject) = toml::from_str::<PyProjectToml>(content) {
        // Check project.dependencies
        if let Some(project) = &pyproject.project {
            for dep in &project.dependencies {
                let dep_lower = dep.to_lowercase();
                let pkg = dep_lower
                    .split(['=', '<', '>', '[', ';'])
                    .next()
                    .unwrap_or("")
                    .trim();
                match pkg {
                    "django" => {
                        frameworks.insert(Framework::Django);
                    }
                    "sqlalchemy" => {
                        frameworks.insert(Framework::SQLAlchemy);
                    }
                    "peewee" => {
                        frameworks.insert(Framework::Peewee);
                    }
                    "tortoise-orm" => {
                        frameworks.insert(Framework::TortoiseORM);
                    }
                    "pony" => {
                        frameworks.insert(Framework::Pony);
                    }
                    "sqlobject" => {
                        frameworks.insert(Framework::SQLObject);
                    }
                    _ => {}
                }
            }
        }

        // Check tool.poetry.dependencies
        if let Some(tool) = &pyproject.tool {
            if let Some(poetry) = &tool.poetry {
                for dep in poetry.dependencies.keys() {
                    let dep_lower = dep.to_lowercase();
                    match dep_lower.as_str() {
                        "django" => {
                            frameworks.insert(Framework::Django);
                        }
                        "sqlalchemy" => {
                            frameworks.insert(Framework::SQLAlchemy);
                        }
                        "peewee" => {
                            frameworks.insert(Framework::Peewee);
                        }
                        "tortoise-orm" => {
                            frameworks.insert(Framework::TortoiseORM);
                        }
                        "pony" => {
                            frameworks.insert(Framework::Pony);
                        }
                        "sqlobject" => {
                            frameworks.insert(Framework::SQLObject);
                        }
                        _ => {}
                    }
                }
            }
        }
    }
}

fn detect_rust_frameworks(content: &str, frameworks: &mut HashSet<Framework>) {
    if let Ok(cargo) = toml::from_str::<CargoToml>(content) {
        for dep in cargo.dependencies.keys() {
            match dep.as_str() {
                "diesel" => {
                    frameworks.insert(Framework::Diesel);
                }
                "sea-orm" => {
                    frameworks.insert(Framework::SeaORM);
                }
                "sqlx" => {
                    frameworks.insert(Framework::SQLx);
                }
                _ => {}
            }
        }
    }
}

fn detect_ruby_frameworks(content: &str, frameworks: &mut HashSet<Framework>) {
    let content_lower = content.to_lowercase();

    // Check for Rails (includes ActiveRecord)
    if content_lower.contains("'rails'") || content_lower.contains("\"rails\"") {
        frameworks.insert(Framework::ActiveRecord);
    }

    // Check for ActiveRecord directly
    if content_lower.contains("'activerecord'") || content_lower.contains("\"activerecord\"") {
        frameworks.insert(Framework::ActiveRecord);
    }

    // Check for Sequel
    if content_lower.contains("'sequel'") || content_lower.contains("\"sequel\"") {
        frameworks.insert(Framework::Sequel);
    }
}

fn detect_go_frameworks(content: &str, frameworks: &mut HashSet<Framework>) {
    let content_lower = content.to_lowercase();

    if content_lower.contains("gorm.io/gorm") {
        frameworks.insert(Framework::GORM);
    }
    if content_lower.contains("entgo.io/ent") {
        frameworks.insert(Framework::Ent);
    }
    if content_lower.contains("github.com/volatiletech/sqlboiler") {
        frameworks.insert(Framework::SQLBoiler);
    }
    if content_lower.contains("github.com/uptrace/bun") {
        frameworks.insert(Framework::Bun);
    }
}

fn detect_java_frameworks(content: &str, frameworks: &mut HashSet<Framework>) {
    let content_lower = content.to_lowercase();

    // Hibernate
    if content_lower.contains("hibernate") {
        frameworks.insert(Framework::Hibernate);
    }

    // JPA
    if content_lower.contains("jakarta.persistence") || content_lower.contains("javax.persistence")
    {
        frameworks.insert(Framework::JPA);
    }

    // Spring Data
    if content_lower.contains("spring-data") || content_lower.contains("spring-boot-starter-data") {
        frameworks.insert(Framework::SpringData);
    }

    // MyBatis
    if content_lower.contains("mybatis") {
        frameworks.insert(Framework::MyBatis);
    }

    // jOOQ
    if content_lower.contains("jooq") {
        frameworks.insert(Framework::JOOQ);
    }
}

/// Check if a line of code uses a safe ORM pattern
pub fn is_safe_orm_pattern(line: &str, frameworks: &HashSet<Framework>) -> bool {
    let line_lower = line.to_lowercase();

    for framework in frameworks {
        // First check if line uses an unsafe pattern for this framework
        for unsafe_pattern in framework.unsafe_patterns() {
            if line_lower.contains(&unsafe_pattern.to_lowercase()) {
                return false; // Using raw SQL bypass - not safe
            }
        }

        // Then check for safe patterns
        for safe_pattern in framework.safe_patterns() {
            if line_lower.contains(&safe_pattern.to_lowercase()) {
                return true;
            }
        }
    }

    false
}

/// Get which framework a line matches (for reporting purposes)
pub fn get_matching_framework(line: &str, frameworks: &HashSet<Framework>) -> Option<Framework> {
    let line_lower = line.to_lowercase();

    for framework in frameworks {
        // Skip if using unsafe pattern
        for unsafe_pattern in framework.unsafe_patterns() {
            if line_lower.contains(&unsafe_pattern.to_lowercase()) {
                return None;
            }
        }

        for safe_pattern in framework.safe_patterns() {
            if line_lower.contains(&safe_pattern.to_lowercase()) {
                return Some(*framework);
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn test_prisma_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::Prisma);

        assert!(is_safe_orm_pattern(
            "prisma.user.findMany({ where: { id } })",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "const users = await prisma.user.findFirst()",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "prisma.post.create({ data: { title } })",
            &frameworks
        ));
    }

    #[test]
    fn test_drizzle_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::Drizzle);

        assert!(is_safe_orm_pattern(
            "db.select().from(users).where(eq(users.id, userId))",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "db.insert(users).values({ name })",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "db.update(users).set({ name }).where(eq(users.id, 1))",
            &frameworks
        ));
    }

    #[test]
    fn test_django_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::Django);

        assert!(is_safe_orm_pattern(
            "User.objects.filter(id=user_id)",
            &frameworks
        ));
        assert!(is_safe_orm_pattern("Model.objects.get(pk=1)", &frameworks));
        assert!(is_safe_orm_pattern(
            "queryset.exclude(status='deleted')",
            &frameworks
        ));
    }

    #[test]
    fn test_django_unsafe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::Django);

        // .raw() is unsafe even in Django
        assert!(!is_safe_orm_pattern(
            "User.objects.raw('SELECT * FROM users WHERE id=' + id)",
            &frameworks
        ));
    }

    #[test]
    fn test_sqlalchemy_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::SQLAlchemy);

        assert!(is_safe_orm_pattern(
            "session.query(User).filter_by(id=user_id)",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "session.query(User).filter(User.id == user_id).first()",
            &frameworks
        ));
    }

    #[test]
    fn test_sqlalchemy_unsafe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::SQLAlchemy);

        // text() with string interpolation is unsafe
        assert!(!is_safe_orm_pattern(
            "engine.execute(text(f'SELECT * FROM users WHERE id={id}'))",
            &frameworks
        ));
    }

    #[test]
    fn test_typeorm_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::TypeORM);

        assert!(is_safe_orm_pattern(
            "repository.find({ where: { id } })",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "repository.findOneBy({ email })",
            &frameworks
        ));
    }

    #[test]
    fn test_sequelize_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::Sequelize);

        assert!(is_safe_orm_pattern(
            "Model.findAll({ where: { id } })",
            &frameworks
        ));
        assert!(is_safe_orm_pattern(
            "User.findOne({ where: { email: Op.eq } })",
            &frameworks
        ));
    }

    #[test]
    fn test_gorm_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::GORM);

        assert!(is_safe_orm_pattern(
            "db.Where(\"id = ?\", id).Find(&users)",
            &frameworks
        ));
        assert!(is_safe_orm_pattern("db.First(&user, id)", &frameworks));
    }

    #[test]
    fn test_gorm_unsafe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::GORM);

        // .Raw() is unsafe
        assert!(!is_safe_orm_pattern(
            "db.Raw(fmt.Sprintf(\"SELECT * FROM users WHERE id = %s\", id))",
            &frameworks
        ));
    }

    #[test]
    fn test_no_framework_match() {
        let frameworks = HashSet::new();

        // With no frameworks detected, nothing should match as safe
        assert!(!is_safe_orm_pattern(
            "db.query(`SELECT * FROM users`)",
            &frameworks
        ));
    }

    #[test]
    fn test_node_package_detection() {
        let content = r#"
        {
            "dependencies": {
                "prisma": "^5.0.0",
                "express": "^4.0.0"
            },
            "devDependencies": {
                "drizzle-orm": "^0.30.0"
            }
        }
        "#;

        let pkg: PackageJson = serde_json::from_str(content).unwrap();
        let mut frameworks = HashSet::new();
        detect_node_frameworks(&pkg, &mut frameworks);

        assert!(frameworks.contains(&Framework::Prisma));
        assert!(frameworks.contains(&Framework::Drizzle));
    }

    #[test]
    fn test_requirements_txt_detection() {
        let content = r#"
Django>=4.0
sqlalchemy==2.0.0
requests>=2.28
        "#;

        let mut frameworks = HashSet::new();
        detect_python_frameworks_from_requirements(content, &mut frameworks);

        assert!(frameworks.contains(&Framework::Django));
        assert!(frameworks.contains(&Framework::SQLAlchemy));
    }

    #[test]
    fn test_better_sqlite3_detection() {
        let content = r#"
        {
            "dependencies": {
                "better-sqlite3": "^9.0.0",
                "express": "^4.0.0"
            }
        }
        "#;

        let pkg: PackageJson = serde_json::from_str(content).unwrap();
        let mut frameworks = HashSet::new();
        detect_node_frameworks(&pkg, &mut frameworks);

        assert!(frameworks.contains(&Framework::BetterSQLite3));
    }

    #[test]
    fn test_better_sqlite3_safe_patterns() {
        let mut frameworks = HashSet::new();
        frameworks.insert(Framework::BetterSQLite3);

        // Prepared statements are safe
        assert!(is_safe_orm_pattern(
            "const stmt = db.prepare('SELECT * FROM users WHERE id = ?')",
            &frameworks
        ));
        
        assert!(is_safe_orm_pattern(
            "stmt.all(userId)",
            &frameworks
        ));

        assert!(is_safe_orm_pattern(
            "db.prepare('SELECT * FROM users WHERE id = @id').get({ id: userId })",
            &frameworks
        ));

        assert!(is_safe_orm_pattern(
            "stmt.run(params)",
            &frameworks
        ));
    }
}
