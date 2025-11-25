"""Test fixture: Common decorator patterns.

These patterns commonly cause false positives in dead code detection.
All functions here should be detected as USED, not dead code.
"""

from functools import wraps


# Pattern 1: Simple decorator
def simple_decorator(func):
    """Simple decorator with wrapper function."""
    def wrapper(*args, **kwargs):
        print("Before")
        result = func(*args, **kwargs)
        print("After")
        return result
    return wrapper


# Pattern 2: Decorator with arguments (factory)
def log_operation(operation_name):
    """Decorator factory - creates decorator with custom name."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            print(f"Starting {operation_name}")
            result = func(*args, **kwargs)
            print(f"Finished {operation_name}")
            return result
        return wrapper
    return decorator


# Pattern 3: Decorator using functools.wraps
def preserve_metadata(func):
    """Decorator that preserves function metadata."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


# Pattern 4: Class-based decorator
class ClassDecorator:
    """Class-based decorator."""

    def __init__(self, func):
        self.func = func

    def __call__(self, *args, **kwargs):
        print("Class decorator called")
        return self.func(*args, **kwargs)


# Pattern 5: Decorator with state
def counting_decorator(func):
    """Decorator that counts calls."""
    def wrapper(*args, **kwargs):
        wrapper.count += 1
        return func(*args, **kwargs)
    wrapper.count = 0
    return wrapper


# Pattern 6: Nested decorator factory (common in logging/metrics)
def metric_wrapper(metric_name):
    """Metric wrapper decorator factory."""
    def outer_decorator(func):
        def inner_wrapper(*args, **kwargs):
            # Start timer
            clear_context()  # This should NOT be dead code
            start_metric(metric_name)
            try:
                result = func(*args, **kwargs)
                record_success(metric_name)
                return result
            except Exception as e:
                record_failure(metric_name, e)
                raise
        return inner_wrapper
    return outer_decorator


def clear_context():
    """Clear metric context - called from wrapper."""
    pass


def start_metric(name):
    """Start metric - called from wrapper."""
    pass


def record_success(name):
    """Record success - called from wrapper."""
    pass


def record_failure(name, error):
    """Record failure - called from wrapper."""
    pass


# Pattern 7: Retry decorator
def retry(max_attempts=3):
    """Retry decorator factory."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception:
                    attempts += 1
                    if attempts >= max_attempts:
                        raise
        return wrapper
    return decorator


# Pattern 8: Caching decorator
def memoize(func):
    """Memoization decorator."""
    cache = {}

    def wrapper(*args):
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]

    return wrapper


# Pattern 9: Authentication decorator
def require_auth(func):
    """Authentication decorator."""
    def wrapper(request, *args, **kwargs):
        if not is_authenticated(request):
            raise PermissionError("Not authenticated")
        return func(request, *args, **kwargs)
    return wrapper


def is_authenticated(request):
    """Check if request is authenticated."""
    return True


# Pattern 10: Validation decorator
def validate_input(validator):
    """Input validation decorator factory."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not validator(*args, **kwargs):
                raise ValueError("Invalid input")
            return func(*args, **kwargs)
        return wrapper
    return decorator


def positive_number_validator(n):
    """Validate positive number."""
    return n > 0


# Usage examples (to ensure they're not dead code)
@simple_decorator
def decorated_function():
    """Function with simple decorator."""
    return "hello"


@log_operation("test")
def logged_function():
    """Function with logging decorator."""
    return "logged"


@counting_decorator
def counted_function():
    """Function with counting decorator."""
    return "counted"


@memoize
def cached_function(x):
    """Function with memoization."""
    return x * 2
