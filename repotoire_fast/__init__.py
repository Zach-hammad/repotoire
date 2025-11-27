# Re-export all functions from the compiled Rust module
from .repotoire_fast import (
    scan_files,
    hash_file_md5,
    batch_hash_files,
    calculate_complexity_fast,
    calculate_complexity_batch,
    calculate_complexity_files,
    calculate_lcom_fast,
    calculate_lcom_batch,
    cosine_similarity_fast,
    batch_cosine_similarity_fast,
    find_top_k_similar,
    # Pylint rules not covered by Ruff (individual checks)
    check_too_many_attributes,        # R0902
    check_too_few_public_methods,     # R0903
    check_import_self,                # R0401
    check_too_many_lines,             # C0302
    check_too_many_ancestors,         # R0901
    check_attribute_defined_outside_init,  # W0201
    check_protected_access,           # W0212
    check_unused_wildcard_import,     # W0614
    check_undefined_loop_variable,    # W0631
    check_disallowed_name,            # C0104
    # Combined checks (parse once - faster)
    check_all_pylint_rules,           # All rules, single file
    check_all_pylint_rules_batch,     # All rules, multiple files in parallel
)

__all__ = [
    "scan_files",
    "hash_file_md5",
    "batch_hash_files",
    "calculate_complexity_fast",
    "calculate_complexity_batch",
    "calculate_complexity_files",
    "calculate_lcom_fast",
    "calculate_lcom_batch",
    "cosine_similarity_fast",
    "batch_cosine_similarity_fast",
    "find_top_k_similar",
    # Pylint rules not covered by Ruff (individual checks)
    "check_too_many_attributes",        # R0902
    "check_too_few_public_methods",     # R0903
    "check_import_self",                # R0401
    "check_too_many_lines",             # C0302
    "check_too_many_ancestors",         # R0901
    "check_attribute_defined_outside_init",  # W0201
    "check_protected_access",           # W0212
    "check_unused_wildcard_import",     # W0614
    "check_undefined_loop_variable",    # W0631
    "check_disallowed_name",            # C0104
    # Combined checks (parse once - faster)
    "check_all_pylint_rules",           # All rules, single file
    "check_all_pylint_rules_batch",     # All rules, multiple files in parallel
]
