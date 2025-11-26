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
]
