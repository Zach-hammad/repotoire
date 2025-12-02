"""Module with security vulnerabilities for testing bandit and semgrep.

WARNING: This code contains intentional security vulnerabilities.
Do not use this code in production!
"""

import os
import pickle
import hashlib
import subprocess
import tempfile
import yaml


# =============================================================================
# Command Injection Vulnerabilities
# =============================================================================


def execute_command_unsafe(user_input: str) -> str:
    """Execute a command with user input - UNSAFE!

    B602: subprocess_popen_with_shell_equals_true
    """
    result = subprocess.run(user_input, shell=True, capture_output=True)
    return result.stdout.decode()


def system_command_unsafe(filename: str) -> None:
    """Run system command with user input - UNSAFE!

    B605: start_process_with_a_shell
    """
    os.system(f"cat {filename}")


# =============================================================================
# SQL Injection Vulnerabilities
# =============================================================================


def get_user_unsafe(username: str) -> str:
    """Build SQL query with string formatting - UNSAFE!

    S608: Possible SQL injection vector
    """
    query = f"SELECT * FROM users WHERE username = '{username}'"
    return query


def delete_user_unsafe(user_id: str) -> str:
    """Build SQL query with concatenation - UNSAFE!

    S608: Possible SQL injection vector
    """
    query = "DELETE FROM users WHERE id = " + user_id
    return query


# =============================================================================
# Insecure Deserialization
# =============================================================================


def load_data_unsafe(data: bytes) -> object:
    """Deserialize pickle data - UNSAFE!

    B301: pickle and modules that wrap it can be unsafe
    """
    return pickle.loads(data)


def load_yaml_unsafe(yaml_string: str) -> dict:
    """Load YAML without safe_load - UNSAFE!

    B506: yaml_load
    """
    return yaml.load(yaml_string, Loader=yaml.FullLoader)


# =============================================================================
# Hardcoded Secrets
# =============================================================================


# S105: Possible hardcoded password string
DATABASE_PASSWORD = "super_secret_password123"
API_KEY = "sk-1234567890abcdef"
SECRET_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"


def connect_to_database() -> None:
    """Connect using hardcoded credentials - UNSAFE!"""
    password = "admin123"  # S105
    connection_string = f"mysql://root:{password}@localhost/db"
    return connection_string


# =============================================================================
# Weak Cryptography
# =============================================================================


def hash_password_md5(password: str) -> str:
    """Hash password with MD5 - UNSAFE!

    B303: Use of insecure MD5 hash function
    B324: hashlib.md5 and hashlib.sha1 are insecure
    """
    return hashlib.md5(password.encode()).hexdigest()


def hash_password_sha1(password: str) -> str:
    """Hash password with SHA1 - UNSAFE!

    B303: Use of insecure SHA1 hash function
    """
    return hashlib.sha1(password.encode()).hexdigest()


# =============================================================================
# Insecure Temporary Files
# =============================================================================


def create_temp_file_unsafe() -> str:
    """Create temporary file insecurely - UNSAFE!

    B108: Probable insecure usage of temp file
    """
    filename = "/tmp/myapp_" + str(os.getpid())
    with open(filename, "w") as f:
        f.write("sensitive data")
    return filename


# =============================================================================
# Path Traversal
# =============================================================================


def read_file_unsafe(user_path: str) -> str:
    """Read file from user-controlled path - UNSAFE!

    Path traversal vulnerability
    """
    with open(user_path, "r") as f:
        return f.read()


# =============================================================================
# Assert Used for Security
# =============================================================================


def check_admin_unsafe(user: dict) -> bool:
    """Use assert for security check - UNSAFE!

    B101: Use of assert detected - can be disabled with -O flag
    """
    assert user.get("is_admin") == True, "User is not admin"
    return True


# =============================================================================
# Exec/Eval
# =============================================================================


def evaluate_expression_unsafe(expression: str) -> object:
    """Evaluate user expression - UNSAFE!

    B307: Use of eval() is a security issue
    """
    return eval(expression)


def execute_code_unsafe(code: str) -> None:
    """Execute user code - UNSAFE!

    B102: Use of exec detected
    """
    exec(code)
