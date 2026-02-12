"""
A simple, well-written Python file with no code smells.
Used to verify that analysis doesn't produce false positives.
"""


class User:
    """Simple user class with single responsibility."""
    
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email
        
    def get_display_name(self) -> str:
        """Return formatted display name."""
        return f"{self.name} <{self.email}>"
        
    def is_valid(self) -> bool:
        """Check if user has valid data."""
        return bool(self.name and self.email and "@" in self.email)


def greet(user: User) -> str:
    """Generate a greeting for the user."""
    return f"Hello, {user.name}!"


def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


def calculate_average(numbers: list[float]) -> float:
    """Calculate the average of a list of numbers."""
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)
