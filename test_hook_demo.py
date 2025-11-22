"""Test file to demonstrate pre-commit hook."""

def complex_function(a, b, c, d, e):
    """A function with high complexity."""
    if a > 0:
        if b > 0:
            if c > 0:
                if d > 0:
                    if e > 0:
                        return a + b + c + d + e
                    else:
                        return a + b + c + d
                else:
                    return a + b + c
            else:
                return a + b
        else:
            return a
    else:
        return 0


def missing_docstring_function():
    x = 1
    y = 2
    return x + y


# Unused variable
unused_var = "This is never used"
