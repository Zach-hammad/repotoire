# Fixing Issues

A practical guide to fixing each type of issue Repotoire detects. Organized by category with before/after code examples.

## Table of Contents

- [Security Vulnerabilities](#security-vulnerabilities)
- [Code Smells](#code-smells)
- [Architecture Issues](#architecture-issues)
- [Performance Problems](#performance-problems)
- [Code Quality](#code-quality)
- [Async Patterns](#async-patterns)

---

## Security Vulnerabilities

### SQL Injection

**Severity:** 🔴 Critical

**Problem:** SQL queries built with string concatenation or interpolation allow attackers to inject malicious SQL.

❌ **Before:**
```python
def get_user(user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return db.execute(query)
```

✅ **After:**
```python
def get_user(user_id):
    query = "SELECT * FROM users WHERE id = ?"
    return db.execute(query, (user_id,))
```

**Fix by language:**

```javascript
// JavaScript - Use parameterized queries
// ❌ Bad
db.query(`SELECT * FROM users WHERE id = ${userId}`);
// ✅ Good
db.query('SELECT * FROM users WHERE id = ?', [userId]);
```

```java
// Java - Use PreparedStatement
// ❌ Bad
stmt.executeQuery("SELECT * FROM users WHERE id = " + userId);
// ✅ Good
PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE id = ?");
ps.setInt(1, userId);
```

---

### Command Injection

**Severity:** 🔴 Critical

**Problem:** Shell commands with user input can be exploited to run arbitrary commands.

❌ **Before:**
```python
import os

def process_file(filename):
    os.system(f"cat {filename}")
```

✅ **After:**
```python
import subprocess

def process_file(filename):
    # Use array form to prevent shell injection
    subprocess.run(["cat", filename], check=True)
```

**Alternative:** Use shell=False (default) with argument list:
```python
subprocess.run(["grep", pattern, filename])  # Safe
subprocess.run(f"grep {pattern} {filename}", shell=True)  # Dangerous!
```

---

### Hardcoded Secrets

**Severity:** 🟠 High

**Problem:** API keys, passwords, and tokens committed to source code.

❌ **Before:**
```python
API_KEY = "sk-abc123secret456"
DATABASE_URL = "postgres://admin:password123@localhost/db"
```

✅ **After:**
```python
import os

API_KEY = os.environ["API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]
```

**Best practices:**
1. Use environment variables
2. Use a secrets manager (AWS Secrets Manager, HashiCorp Vault)
3. Add patterns to `.gitignore`
4. Use `.env` files (not committed) with `python-dotenv`

```python
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.environ["API_KEY"]
```

---

### Eval/Exec

**Severity:** 🔴 Critical

**Problem:** `eval()` and `exec()` with user input can execute arbitrary code.

❌ **Before:**
```python
def calculate(expression):
    return eval(expression)  # User could input: __import__('os').system('rm -rf /')
```

✅ **After:**
```python
import ast
import operator

SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

def calculate(expression):
    tree = ast.parse(expression, mode='eval')
    return _eval_node(tree.body)

def _eval_node(node):
    if isinstance(node, ast.Num):
        return node.n
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return SAFE_OPERATORS[type(node.op)](left, right)
    raise ValueError("Unsafe expression")
```

---

### Unsafe Deserialization

**Severity:** 🔴 Critical

**Problem:** `pickle.load()`, `yaml.load()` can execute arbitrary code.

❌ **Before:**
```python
import pickle

def load_data(file_path):
    with open(file_path, 'rb') as f:
        return pickle.load(f)  # Dangerous!
```

✅ **After:**
```python
import json

def load_data(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)  # Safe
```

For YAML:
```python
import yaml

# ❌ Dangerous
data = yaml.load(content)

# ✅ Safe
data = yaml.safe_load(content)
```

---

### Insecure Cryptography

**Severity:** 🟡 Medium

**Problem:** Weak algorithms like MD5, SHA1, or DES are cryptographically broken.

❌ **Before:**
```python
import hashlib

def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()
```

✅ **After:**
```python
import bcrypt

def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed)
```

**Secure alternatives:**
- Passwords: `bcrypt`, `argon2`, `scrypt`
- General hashing: `SHA-256`, `SHA-3`
- Encryption: `AES-256-GCM`, `ChaCha20-Poly1305`

---

## Code Smells

### God Class

**Severity:** 🟠 High

**Problem:** Classes with too many methods, lines, or responsibilities.

❌ **Before:**
```python
class UserManager:
    def create_user(self): ...
    def delete_user(self): ...
    def update_user(self): ...
    def send_email(self): ...
    def send_sms(self): ...
    def generate_report(self): ...
    def export_csv(self): ...
    def validate_email(self): ...
    def hash_password(self): ...
    # ... 50 more methods
```

✅ **After:**
```python
class UserRepository:
    def create(self, user): ...
    def delete(self, user_id): ...
    def update(self, user): ...

class NotificationService:
    def send_email(self, to, subject, body): ...
    def send_sms(self, to, message): ...

class ReportGenerator:
    def generate(self, data): ...
    def export_csv(self, report): ...

class UserValidator:
    def validate_email(self, email): ...
    def hash_password(self, password): ...
```

**Guidelines:**
- Classes should have one responsibility
- Aim for <20 methods per class
- Aim for <500 lines per class

---

### Long Methods

**Severity:** 🟡 Medium

**Problem:** Methods that are too long are hard to understand and maintain.

❌ **Before:**
```python
def process_order(order):
    # 200 lines of validation, pricing, inventory, shipping, email...
```

✅ **After:**
```python
def process_order(order):
    validate_order(order)
    calculate_pricing(order)
    update_inventory(order)
    schedule_shipping(order)
    send_confirmation_email(order)

def validate_order(order):
    # Focused validation logic

def calculate_pricing(order):
    # Focused pricing logic

# etc.
```

**Rule of thumb:** Methods should do one thing and fit on one screen (~25-50 lines).

---

### Long Parameter Lists

**Severity:** 🟡 Medium

**Problem:** Functions with many parameters are hard to call correctly.

❌ **Before:**
```python
def create_user(name, email, password, age, address, city, state, zip_code, 
                phone, role, department, manager_id, start_date, salary):
    ...
```

✅ **After:**
```python
from dataclasses import dataclass

@dataclass
class Address:
    street: str
    city: str
    state: str
    zip_code: str

@dataclass
class CreateUserRequest:
    name: str
    email: str
    password: str
    age: int
    address: Address
    phone: str
    role: str
    department: str
    manager_id: int
    start_date: str
    salary: float

def create_user(request: CreateUserRequest):
    ...
```

---

### Deep Nesting

**Severity:** 🟡 Medium

**Problem:** Deeply nested code is hard to follow.

❌ **Before:**
```python
def process(data):
    if data:
        if data.is_valid:
            if data.user:
                if data.user.is_active:
                    if data.user.has_permission:
                        return do_work(data)
    return None
```

✅ **After:**
```python
def process(data):
    if not data:
        return None
    if not data.is_valid:
        return None
    if not data.user:
        return None
    if not data.user.is_active:
        return None
    if not data.user.has_permission:
        return None
    
    return do_work(data)
```

**Techniques:**
- Use early returns (guard clauses)
- Extract methods
- Use polymorphism instead of conditionals

---

### Duplicate Code

**Severity:** 🟡 Medium

**Problem:** Repeated code blocks are a maintenance burden.

❌ **Before:**
```python
def get_active_users():
    users = db.query("SELECT * FROM users")
    result = []
    for user in users:
        if user.status == 'active':
            result.append(user)
    return result

def get_active_admins():
    admins = db.query("SELECT * FROM admins")
    result = []
    for admin in admins:
        if admin.status == 'active':
            result.append(admin)
    return result
```

✅ **After:**
```python
def filter_active(items):
    return [item for item in items if item.status == 'active']

def get_active_users():
    return filter_active(db.query("SELECT * FROM users"))

def get_active_admins():
    return filter_active(db.query("SELECT * FROM admins"))
```

---

## Architecture Issues

### Circular Dependencies

**Severity:** 🟠 High

**Problem:** Modules that import each other create tight coupling and import errors.

❌ **Before:**
```python
# user.py
from order import Order

class User:
    def get_orders(self):
        return Order.find_by_user(self.id)

# order.py
from user import User  # Circular!

class Order:
    def get_user(self):
        return User.find_by_id(self.user_id)
```

✅ **After:**

**Option 1: Move import inside function**
```python
# order.py
class Order:
    def get_user(self):
        from user import User  # Import when needed
        return User.find_by_id(self.user_id)
```

**Option 2: Create an interface/protocol**
```python
# interfaces.py
from typing import Protocol

class HasOrders(Protocol):
    def get_orders(self): ...

# user.py (no circular import)
from interfaces import HasOrders
```

**Option 3: Dependency injection**
```python
# order.py
class Order:
    def get_user(self, user_repository):
        return user_repository.find_by_id(self.user_id)
```

---

### Dead Code

**Severity:** 🔵 Low

**Problem:** Unreachable or unused code clutters the codebase.

❌ **Before:**
```python
def calculate_total(items):
    return sum(item.price for item in items)

def calculate_total_v2(items):  # Never called
    total = 0
    for item in items:
        total += item.price
    return total

def old_helper():  # Never called
    pass
```

✅ **After:**
```python
def calculate_total(items):
    return sum(item.price for item in items)
```

**Tips:**
- Use `repotoire` to detect dead code
- Delete with confidence when tests pass
- Use version control to recover if needed

---

## Performance Problems

### N+1 Query

**Severity:** 🟠 High

**Problem:** Querying the database in a loop causes N+1 queries.

❌ **Before:**
```python
def get_users_with_orders():
    users = User.query.all()  # 1 query
    for user in users:
        orders = Order.query.filter_by(user_id=user.id).all()  # N queries!
        user.orders = orders
    return users
```

✅ **After:**
```python
def get_users_with_orders():
    # Eager load with a join (1-2 queries total)
    return User.query.options(joinedload(User.orders)).all()
```

**Django:**
```python
# ❌ Bad
users = User.objects.all()
for user in users:
    print(user.orders.count())  # N queries

# ✅ Good
users = User.objects.prefetch_related('orders')
```

---

### Sync I/O in Async

**Severity:** 🟠 High

**Problem:** Blocking I/O in async functions defeats the purpose of async.

❌ **Before:**
```python
import asyncio
import requests  # Blocking!

async def fetch_data(url):
    return requests.get(url)  # Blocks the event loop
```

✅ **After:**
```python
import asyncio
import aiohttp

async def fetch_data(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()
```

**Common blocking calls to avoid in async:**
- `requests.get()` → use `aiohttp` or `httpx`
- `time.sleep()` → use `asyncio.sleep()`
- `open().read()` → use `aiofiles`
- Database sync drivers → use async drivers

---

### Regex in Loop

**Severity:** 🟡 Medium

**Problem:** Compiling regex in a loop wastes CPU.

❌ **Before:**
```python
def validate_emails(emails):
    results = []
    for email in emails:
        if re.match(r'^[\w.-]+@[\w.-]+\.\w+$', email):  # Compiled every iteration
            results.append(email)
    return results
```

✅ **After:**
```python
import re

EMAIL_PATTERN = re.compile(r'^[\w.-]+@[\w.-]+\.\w+$')  # Compile once

def validate_emails(emails):
    return [email for email in emails if EMAIL_PATTERN.match(email)]
```

---

## Code Quality

### Empty Catch/Except

**Severity:** 🟡 Medium

**Problem:** Silently swallowing exceptions hides bugs.

❌ **Before:**
```python
try:
    process_data()
except:
    pass  # What went wrong? We'll never know.
```

✅ **After:**
```python
import logging

try:
    process_data()
except ValueError as e:
    logging.warning(f"Invalid data: {e}")
    return default_value
except ConnectionError as e:
    logging.error(f"Connection failed: {e}")
    raise  # Re-raise if can't handle
```

**Rules:**
- Never use bare `except:`
- Always log or handle the exception
- Be specific about which exceptions to catch

---

### Broad Exception

**Severity:** 🔵 Low

**Problem:** Catching base `Exception` catches too much.

❌ **Before:**
```python
try:
    value = int(user_input)
except Exception:  # Catches KeyboardInterrupt, SystemExit, etc.
    value = 0
```

✅ **After:**
```python
try:
    value = int(user_input)
except ValueError:  # Specific
    value = 0
```

---

### Mutable Default Arguments

**Severity:** 🟡 Medium

**Problem:** Mutable defaults are shared between calls.

❌ **Before:**
```python
def append_to(item, target=[]):  # Same list reused!
    target.append(item)
    return target

append_to(1)  # [1]
append_to(2)  # [1, 2] - Surprise!
```

✅ **After:**
```python
def append_to(item, target=None):
    if target is None:
        target = []
    target.append(item)
    return target
```

---

## Async Patterns

### Missing Await

**Severity:** 🟠 High

**Problem:** Forgetting `await` on async calls returns a coroutine, not the result.

❌ **Before:**
```python
async def get_data():
    return fetch_from_api()  # Returns coroutine, not data!
```

✅ **After:**
```python
async def get_data():
    return await fetch_from_api()
```

---

### Unhandled Promise

**Severity:** 🟡 Medium

**Problem:** Promises without error handling can fail silently.

❌ **Before:**
```javascript
fetchData()  // Error? Who knows!
```

✅ **After:**
```javascript
fetchData()
  .then(data => process(data))
  .catch(error => console.error('Fetch failed:', error));

// Or with async/await:
try {
  const data = await fetchData();
  process(data);
} catch (error) {
  console.error('Fetch failed:', error);
}
```

---

## Quick Reference

| Issue | Quick Fix |
|-------|-----------|
| SQL Injection | Use parameterized queries |
| Command Injection | Use subprocess with array args |
| Hardcoded Secrets | Use environment variables |
| God Class | Split into focused classes |
| Long Method | Extract smaller functions |
| Deep Nesting | Use early returns |
| Circular Dependency | Dependency injection or interfaces |
| N+1 Query | Use eager loading / joins |
| Empty Catch | Log and handle specifically |
| Mutable Default | Use `None` and create in function |

---

## Using AI Fixes

Let Repotoire generate fixes automatically:

```bash
# View finding details
repotoire findings

# Generate fix for finding #3
repotoire fix 3

# Auto-apply the fix
repotoire fix 3 --apply
```

Requires an AI API key. See [USER_GUIDE.md](USER_GUIDE.md#fix) for setup.
