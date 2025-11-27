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
    check_too_many_attributes,
    check_too_few_public_methods,
    check_too_many_public_methods,
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
    "check_too_many_attributes",
    "check_too_few_public_methods",
    "check_too_many_public_methods",
]
