# Changelog

All notable changes to Repotoire will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Bug Fixes

- Resolve critical bugs in parser and graph relationship handling
- Security hardening, batch relationships, and inheritance extraction
- Add findings support and improve data ingestion robustness
- Correct Neo4j config serialization and inheritance relationships
- Improve Python parser relationship extraction
- Improve dead code detection and call resolution
- Extract nested functions and track function references (USES)
- Update Neo4j configuration to use port 7688
- Handle both absolute and relative file paths from mypy
- Resolve GodClassDetector and integration test failures
- Use embed_query instead of non-existent embed_documents
- MCP server generates correct import paths even with path mismatch
- MCP server properly handles dependency injection parameters
- Correct TemporalIngestionPipeline constructor parameter name
- Improve DI parameter detection in MCP schema generation

### Documentation

- Add comprehensive configuration documentation (FAL-59)
- Create comprehensive user guide with examples (FAL-66)
- Expand architecture documentation with design decisions (FAL-65)
- Enhance models.py with comprehensive docstrings and examples (FAL-64)
- Create comprehensive Jupyter notebook examples (FAL-67)
- Update CLAUDE.md with connection pooling and env examples
- Add comprehensive guides and test results
- Add architecture and security documentation
- Add comprehensive hybrid detector pattern documentation
- Document hybrid detector architecture and fix parser bug
- Add comprehensive RAG API documentation and CLAUDE.md integration
- **REPO-140:** Add comprehensive TimescaleDB documentation (Phase 7)
- Add CI/CD auto-fix integration and detector audit guides

### Features

- Add module nodes, override detection, and composite indexes
- Implement three production-grade code smell detectors
- Implement Phase 6 infrastructure (logging, config, security)
- Implement environment variable fallback chains (FAL-57)
- Add configurable batch sizes and detector thresholds (FAL-58)
- Add Neo4j connection retry logic with exponential backoff (FAL-53)
- Add CLI input validation with helpful error messages (FAL-54)
- Add configuration validation on startup (FAL-55)
- Add falkor validate command (FAL-63)
- Add progress bars for ingestion and analysis (FAL-61)
- Enhance CLI output formatting with Rich (FAL-60)
- Add HTML report export with code snippets (FAL-62)
- Implement secrets detection during code ingestion (FAL-100)
- Complete INHERITS relationship extraction (FAL-84)
- Implement incremental ingestion with file hash tracking (FAL-93)
- Add advanced connection pooling to Neo4j client
- Enhance entity models with AI and temporal support
- Enhance graph queries and schema for temporal analysis
- Improve detectors with new algorithms and exports
- Add migration commands and AI clue generation
- Add advanced code smell detectors
- Add tree-sitter parser framework for multi-language support
- Add database migrations and temporal tracking
- Add spaCy-powered AI clue generator
- Add git integration for temporal analysis
- Complete detector testing and tuning (REPO-116)
- Implement RuffImportDetector to replace TrulyUnusedImportsDetector
- Add MypyDetector hybrid detector for type checking
- Add Pylint, Bandit, and Radon hybrid detectors
- Optimize hybrid detectors for 6x performance improvement
- Add Vulture and Semgrep hybrid detectors
- Implement DECORATES relationship tracking for decorators
- Add RAG Phase 1 - Embedding Infrastructure
- Add RAG Phase 2 - Hybrid Retrieval System
- Add RAG Phase 3 - FastAPI application for code Q&A
- Integrate embedding generation into ingestion pipeline
- Add comprehensive RAG integration tests with OpenAI skip decorator
- MCP server automatically instantiates classes for instance methods
- Implement custom rule engine with time-based priority (REPO-125)
- Add code execution MCP following Anthropic's pattern (REPO-122 extension)
- Optimize MCP context usage following top engineers' best practices
- **REPO-140:** Add TimescaleDB integration infrastructure (Phases 1-2)
- **REPO-140:** Add CLI integration for TimescaleDB metrics tracking (Phase 3)
- **REPO-140:** Add TimescaleDB metrics tracking to MCP server
- **REPO-140:** Add metrics query CLI commands (Phase 4-5)
- Git + Graphiti integration for temporal code evolution tracking (REPO-139)
- AI-powered auto-fix system with human-in-the-loop approval
- Add security scanning and SBOM generation capabilities
- Add monorepo support and CI/CD auto-fix integration
- Add cross-detector collaboration and finding deduplication
- Integrate all detectors with graph enricher and deduplicator
- Add CLI enhancements for metadata retention and CI/CD auto-fix
- Enhance HTML reports with deduplication statistics
- Add graph detectors, Rust scanner, and security enhancements

### Miscellaneous

- Ignore analysis outputs and temporary test files
- Temporarily disable coverage in pytest config
- Ignore benchmark.py temporary script
- Add PyYAML and tomli as optional dependencies
- Update dependencies for new features
- Add generated files to .gitignore
- Remove 61 unused imports detected by RuffImportDetector
- Update dependencies for collaboration and deduplication features

### Refactoring

- Improve detector queries and relationship handling
- Rename project from Falkor to Repotoire
- Fix feature envy in SecretsScanner.scan_string

### Testing

- Add integration tests and fixtures
- Add comprehensive test coverage for new features
- Add comprehensive tests for RAG components
- Comprehensive MCP server validation - all tests passed ✅
- **REPO-140:** Add comprehensive TimescaleDB integration tests (Phase 6)
- Add CI integration tests for auto-fix system

---
Generated by [git-cliff](https://git-cliff.org)
