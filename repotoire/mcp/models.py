"""Data models for MCP pattern detection and server generation."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class PatternType(Enum):
    """Types of patterns that can be detected."""
    FASTAPI_ROUTE = "fastapi_route"
    FLASK_ROUTE = "flask_route"
    CLICK_COMMAND = "click_command"
    ARGPARSE_COMMAND = "argparse_command"
    PUBLIC_FUNCTION = "public_function"
    PUBLIC_METHOD = "public_method"


class HTTPMethod(Enum):
    """HTTP methods for API routes."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


@dataclass
class Parameter:
    """Function parameter with type information.

    Attributes:
        name: Parameter name
        type_hint: Python type annotation (str representation)
        default_value: Optional default value
        required: Whether parameter is required
        description: Optional parameter description
    """
    name: str
    type_hint: Optional[str] = None
    default_value: Optional[str] = None
    required: bool = True
    description: Optional[str] = None


@dataclass
class DetectedPattern:
    """Base class for detected patterns.

    Attributes:
        pattern_type: Type of pattern detected
        qualified_name: Fully qualified name (e.g., module.Class.method)
        function_name: Simple function name
        parameters: List of function parameters
        return_type: Return type annotation
        docstring: Function docstring
        source_file: Path to source file
        line_number: Line number where function is defined
        decorators: List of decorator names
        is_async: Whether function is async
    """
    pattern_type: PatternType
    qualified_name: str
    function_name: str
    parameters: List[Parameter]
    return_type: Optional[str] = None
    docstring: Optional[str] = None
    source_file: Optional[str] = None
    line_number: Optional[int] = None
    decorators: List[str] = field(default_factory=list)
    is_async: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "pattern_type": self.pattern_type.value,
            "qualified_name": self.qualified_name,
            "function_name": self.function_name,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type_hint,
                    "default": p.default_value,
                    "required": p.required,
                    "description": p.description,
                }
                for p in self.parameters
            ],
            "return_type": self.return_type,
            "docstring": self.docstring,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "decorators": self.decorators,
            "is_async": self.is_async,
        }


@dataclass
class RoutePattern(DetectedPattern):
    """Detected API route pattern (FastAPI, Flask, etc.).

    Additional Attributes:
        http_method: HTTP method (GET, POST, etc.)
        path: API path (e.g., /users/{user_id})
        path_parameters: Parameters extracted from path
        query_parameters: Query string parameters
        request_body_type: Type of request body
        response_model: Response model type
        status_code: Default status code
        tags: API tags for grouping
    """
    http_method: HTTPMethod = HTTPMethod.GET
    path: str = "/"
    path_parameters: List[str] = field(default_factory=list)
    query_parameters: List[str] = field(default_factory=list)
    request_body_type: Optional[str] = None
    response_model: Optional[str] = None
    status_code: int = 200
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with route-specific fields."""
        base = super().to_dict()
        base.update({
            "http_method": self.http_method.value,
            "path": self.path,
            "path_parameters": self.path_parameters,
            "query_parameters": self.query_parameters,
            "request_body_type": self.request_body_type,
            "response_model": self.response_model,
            "status_code": self.status_code,
            "tags": self.tags,
        })
        return base


@dataclass
class CommandPattern(DetectedPattern):
    """Detected CLI command pattern (Click, argparse, etc.).

    Additional Attributes:
        command_name: CLI command name
        command_group: Command group (for subcommands)
        short_help: Short help text
        options: Command-line options
        arguments: Positional arguments
        aliases: Command aliases
    """
    command_name: str = ""
    command_group: Optional[str] = None
    short_help: Optional[str] = None
    options: List[Parameter] = field(default_factory=list)
    arguments: List[Parameter] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with command-specific fields."""
        base = super().to_dict()
        base.update({
            "command_name": self.command_name,
            "command_group": self.command_group,
            "short_help": self.short_help,
            "options": [
                {
                    "name": o.name,
                    "type": o.type_hint,
                    "default": o.default_value,
                    "required": o.required,
                    "description": o.description,
                }
                for o in self.options
            ],
            "arguments": [
                {
                    "name": a.name,
                    "type": a.type_hint,
                    "required": a.required,
                    "description": a.description,
                }
                for a in self.arguments
            ],
            "aliases": self.aliases,
        })
        return base


@dataclass
class FunctionPattern(DetectedPattern):
    """Detected public function/method pattern.

    Additional Attributes:
        is_public: Whether function is considered public (via __all__ or naming)
        is_method: Whether this is a class method
        class_name: Class name if this is a method
        is_staticmethod: Whether this is a static method
        is_classmethod: Whether this is a class method
    """
    is_public: bool = True
    is_method: bool = False
    class_name: Optional[str] = None
    is_staticmethod: bool = False
    is_classmethod: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with function-specific fields."""
        base = super().to_dict()
        base.update({
            "is_public": self.is_public,
            "is_method": self.is_method,
            "class_name": self.class_name,
            "is_staticmethod": self.is_staticmethod,
            "is_classmethod": self.is_classmethod,
        })
        return base
