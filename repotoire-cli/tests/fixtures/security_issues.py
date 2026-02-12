"""
File with intentional security vulnerabilities for testing.
"""

import os
import subprocess
import pickle
import hashlib


# SQL Injection vulnerabilities
def get_user_unsafe(db, user_input):
    """SQL injection vulnerability."""
    query = f"SELECT * FROM users WHERE name = '{user_input}'"
    return db.execute(query)


def search_products(db, search_term):
    """Another SQL injection."""
    query = "SELECT * FROM products WHERE name LIKE '%" + search_term + "%'"
    return db.execute(query)


# Command injection
def run_command(user_input):
    """Command injection vulnerability."""
    os.system(f"echo {user_input}")  # Dangerous!
    

def run_subprocess(cmd):
    """Another command injection."""
    subprocess.call(cmd, shell=True)  # shell=True is dangerous


# Hardcoded secrets
API_KEY = "sk-1234567890abcdef"  # Hardcoded API key
DATABASE_PASSWORD = "super_secret_password_123"  # Hardcoded password
AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"  # AWS-like key


def connect_to_service():
    """Function with hardcoded credentials."""
    password = "admin123"  # Hardcoded password
    token = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # GitHub token pattern
    return f"Connecting with {password} and {token}"


# Insecure deserialization
def load_user_data(data):
    """Unsafe pickle deserialization."""
    return pickle.loads(data)  # Can execute arbitrary code!


# Weak cryptography
def hash_password(password):
    """Using weak hash algorithm."""
    return hashlib.md5(password.encode()).hexdigest()  # MD5 is weak!


def hash_with_sha1(data):
    """SHA1 is also weak for security."""
    return hashlib.sha1(data.encode()).hexdigest()


# Path traversal
def read_file(filename):
    """Path traversal vulnerability."""
    # No validation of filename - user could pass "../../../etc/passwd"
    with open(f"/var/data/{filename}") as f:
        return f.read()


# Insecure random
import random


def generate_token():
    """Using insecure random for security-sensitive operation."""
    return ''.join(random.choice('abcdef0123456789') for _ in range(32))


# XML External Entity (XXE) potential
def parse_xml(xml_string):
    """Potential XXE vulnerability."""
    import xml.etree.ElementTree as ET
    return ET.fromstring(xml_string)
