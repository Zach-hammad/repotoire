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

    let pkg: PackageJson = serde_json::from_str(content).expect("should parse JSON");
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

    let pkg: PackageJson = serde_json::from_str(content).expect("should parse JSON");
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

    assert!(is_safe_orm_pattern("stmt.all(userId)", &frameworks));

    assert!(is_safe_orm_pattern(
        "db.prepare('SELECT * FROM users WHERE id = @id').get({ id: userId })",
        &frameworks
    ));

    assert!(is_safe_orm_pattern("stmt.run(params)", &frameworks));
}
