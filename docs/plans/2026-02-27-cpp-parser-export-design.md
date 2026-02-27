# C/C++ Parser Export Annotations Design

**Goal:** Add export annotations to C and C++ parsers for consistency with the other 7 language parsers, and complete C++ class method extraction with access specifier tracking.

**Architecture:** Conservative opt-in model matching all other parsers' semantics.

## C Parser Changes

**Rule:** Only `extern` on a function definition = "exported" annotation.

C has external linkage by default, but marking ALL non-static functions as exported would be too noisy (nearly everything would be exported). Per SEI CERT DCL15-C, only explicit `extern` signals intentional export, matching the opt-in semantics of Python (`def` at module scope), Java (`public`), C# (`public`), TypeScript (`export`), Rust (`pub`), and Go (capitalized names).

**Implementation:** Check children of `function_definition` node for `storage_class_specifier` child with text `"extern"`.

## C++ Parser Changes

### 1. Access Specifier Tracking

In tree-sitter-cpp, `access_specifier` nodes are boundary markers (siblings) in a flat `field_declaration_list`, not containers. Default access is `private` for `class`, `public` for `struct`.

**Implementation:** Walk `field_declaration_list` children sequentially, track `current_access` state variable, update on `access_specifier` nodes (text format: `"public:"`, `"private:"`, `"protected:"`). Methods where `current_access == "public"` get "exported" annotation.

### 2. Free Function Export

Same rule as C: only `extern` on a definition = "exported".

### 3. Base Class Extraction

Extract base classes from `base_class_clause` children to populate `Class.bases`. Currently `bases: vec![]` for all C++ classes.

### 4. Fix Missing `#[test]` Attribute

`test_complexity` at line 581 of `cpp.rs` is missing `#[test]` attribute.

## Test Plan

- C: `test_extern_function_exported`, `test_static_function_not_exported`, `test_plain_function_not_exported`
- C++: `test_public_methods_exported`, `test_private_methods_not_exported`, `test_struct_methods_default_public`, `test_base_class_extraction`, `test_extern_free_function_exported`
