"""Sample module with intentional code quality issues.

This module contains various code smells and issues for testing
code analysis tools like ruff, bandit, and mypy.
"""

import os, sys  # E401: multiple imports on one line
import json  # F401: 'json' imported but unused
from typing import *  # F403: 'from typing import *' used

# Missing type hints throughout


def foo(x,y,z):  # E231: missing whitespace after ','
    """Function with multiple issues."""
    unused_var = 1  # F841: local variable 'unused_var' is assigned but never used
    eval(x)  # S307: Use of eval() is a security issue
    return y+z  # E225: missing whitespace around operator


class badClassName:  # N801: class name should use CapWords convention
    """Class with bad naming."""

    def __init__(self):
        pass  # Empty __init__ is fine but pointless

    def BadMethodName(self):  # N802: function name should be lowercase
        pass


def complex_function(a, b, c, d, e, f, g, h, i, j, k):  # Too many parameters
    """Function with high cyclomatic complexity."""
    result = 0

    # Excessive nesting and complexity
    if a > 0:
        if b > 0:
            if c > 0:
                if d > 0:
                    if e > 0:
                        result = a + b + c + d + e
                    else:
                        result = a + b + c + d
                else:
                    result = a + b + c
            else:
                result = a + b
        else:
            result = a
    else:
        if f > 0:
            if g > 0:
                result = f + g
            else:
                result = f
        else:
            result = 0

    return result + h + i + j + k


def security_issues(user_input):
    """Function with security vulnerabilities."""
    import subprocess

    # B602: subprocess_popen_with_shell_equals_true
    subprocess.Popen(user_input, shell=True)

    # B605: start_process_with_a_shell
    os.system(user_input)

    # S608: Possible SQL injection
    query = f"SELECT * FROM users WHERE name = '{user_input}'"

    # S105: Possible hardcoded password
    password = "hardcoded_password123"

    return query, password


def type_issues(x, y):
    """Function with type issues."""
    # No type hints
    result = x + y  # Could be anything

    # Inconsistent return types
    if x > 0:
        return str(result)
    else:
        return result  # Returns int, but sometimes returns str


class DuplicateCode:
    """Class with duplicate code."""

    def method_one(self, data):
        """First method with duplicated logic."""
        result = []
        for item in data:
            if item > 0:
                result.append(item * 2)
            else:
                result.append(item)
        return result

    def method_two(self, data):
        """Second method with same logic - code duplication."""
        result = []
        for item in data:
            if item > 0:
                result.append(item * 2)
            else:
                result.append(item)
        return result


# Global variable - often a code smell
GLOBAL_STATE = {"counter": 0}


def modify_global():
    """Function that modifies global state."""
    global GLOBAL_STATE
    GLOBAL_STATE["counter"] += 1
    return GLOBAL_STATE["counter"]


# Dead code
def never_called_function():
    """This function is never called anywhere."""
    return "I'm dead code!"


if False:  # Dead branch
    print("This will never execute")
