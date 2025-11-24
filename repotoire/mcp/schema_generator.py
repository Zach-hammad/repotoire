"""MCP tool schema generation from detected patterns.

Converts Python function signatures to JSON Schema for MCP tools.
"""

import re
import os
from typing import List, Dict, Any, Optional, Tuple
from repotoire.mcp.models import DetectedPattern, Parameter
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class SchemaGenerator:
    """Generates JSON Schema for MCP tools from detected patterns.

    Converts Python type annotations to JSON Schema and generates
    rich descriptions for tools and parameters.
    """

    # Python type to JSON Schema type mapping
    TYPE_MAPPING = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
        "List": "array",
        "Dict": "object",
        "Any": None,  # No type constraint
        "None": "null",
    }

    def __init__(self, rag_retriever=None, neo4j_client=None):
        """Initialize schema generator.

        Args:
            rag_retriever: Optional GraphRAGRetriever for enhanced descriptions
            neo4j_client: Optional Neo4j client for relationship queries
        """
        self.rag_retriever = rag_retriever
        self.neo4j_client = neo4j_client

        # Initialize OpenAI client for GPT-4o descriptions
        self.openai_client = None
        if os.getenv("OPENAI_API_KEY"):
            try:
                from openai import OpenAI
                self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            except ImportError:
                logger.warning("OpenAI not available for enhanced descriptions")
            except Exception as e:
                logger.warning(f"Could not initialize OpenAI client: {e}")

    def generate_tool_schema(self, pattern: DetectedPattern) -> Dict[str, Any]:
        """Generate complete MCP tool schema from detected pattern.

        Args:
            pattern: Detected pattern (Route, Command, or Function)

        Returns:
            MCP tool schema dictionary with:
            - name: Tool name
            - description: Tool description
            - inputSchema: JSON Schema for parameters
            - examples: Optional usage examples

        Example:
            >>> pattern = RoutePattern(function_name="get_user", ...)
            >>> schema = generator.generate_tool_schema(pattern)
            >>> schema["name"]
            'get_user'
            >>> schema["inputSchema"]["type"]
            'object'
        """
        # Generate tool name
        tool_name = self._generate_tool_name(pattern)

        # Generate tool description
        description = self._generate_description(pattern)

        # Generate input schema
        input_schema = self._generate_input_schema(pattern)

        # Build schema
        schema = {
            "name": tool_name,
            "description": description,
            "inputSchema": input_schema,
        }

        # Extract examples from docstring if available
        if pattern.docstring:
            examples = self._extract_examples_from_docstring(pattern.docstring)
            if examples:
                schema["examples"] = examples

        return schema

    def _is_dependency_injection(self, param_name: str, type_hint: Optional[str]) -> bool:
        """Check if a parameter indicates dependency injection.

        Args:
            param_name: Parameter name
            type_hint: Parameter type hint (optional)

        Returns:
            True if this is a dependency injection parameter
        """
        # Check type hint if available
        if type_hint:
            # Match exact DI types or specific patterns
            # Use word boundaries to avoid matching CodeAskRequest, CodeSearchRequest, etc.
            import re

            dependency_patterns = [
                r'\bDepends\b',
                r'\bGraphRAGRetriever\b',
                r'\bNeo4jClient\b',
                r'\bCodeEmbedder\b',
                r'\bOpenAI\b',
                r'^Request$',  # Exact match for FastAPI Request (not CodeAskRequest)
                r'^Response$',  # Exact match for FastAPI Response
                r'starlette\.requests\.Request',  # Full module path
            ]

            for pattern in dependency_patterns:
                if re.search(pattern, type_hint):
                    return True

        # Also check parameter name (fallback when type hints not available)
        # Note: Don't include 'request' or 'response' here as they're ambiguous
        # (could be Pydantic models like CodeAskRequest, not just FastAPI Request)
        param_name_lower = param_name.lower()
        dependency_param_names = [
            'client', 'neo4j_client',
            'embedder', 'code_embedder',
            'retriever', 'graph_rag_retriever', 'graphragretriever',
            'db', 'database',
        ]

        return param_name_lower in dependency_param_names

    def _generate_tool_name(self, pattern: DetectedPattern) -> str:
        """Generate MCP tool name from pattern.

        MCP tool names must be valid identifiers (alphanumeric + underscore).

        Args:
            pattern: Detected pattern

        Returns:
            Valid tool name
        """
        # Use function name as base
        name = pattern.function_name

        # Replace non-alphanumeric characters with underscores
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name)

        # Ensure it doesn't start with a number
        if name and name[0].isdigit():
            name = f"tool_{name}"

        return name

    def _generate_description(self, pattern: DetectedPattern) -> str:
        """Generate tool description.

        Uses docstring if available, can be enhanced with RAG retriever,
        otherwise generates from function name.

        Args:
            pattern: Detected pattern

        Returns:
            Tool description string
        """
        # 1. Start with docstring if available
        if pattern.docstring:
            # Extract first line or paragraph as summary
            lines = pattern.docstring.strip().split('\n')
            description = lines[0].strip()

            # Remove trailing periods for consistency
            if description.endswith('.'):
                description = description[:-1]

            return description

        # 2. Try RAG enhancement if no docstring
        if self.rag_retriever:
            rag_desc = self._rag_enhanced_tool_description(pattern)
            if rag_desc:
                return rag_desc

        # 3. Fallback: generate from function name
        # Convert snake_case to human readable
        readable_name = pattern.function_name.replace('_', ' ')
        return f"Execute {readable_name}"

    def _generate_input_schema(self, pattern: DetectedPattern) -> Dict[str, Any]:
        """Generate JSON Schema for function parameters.

        Args:
            pattern: Detected pattern with parameters

        Returns:
            JSON Schema object describing input parameters

        Example:
            {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID"},
                    "name": {"type": "string", "description": "User name"}
                },
                "required": ["user_id"]
            }
        """
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {},
        }

        required: List[str] = []

        for param in pattern.parameters:
            # Skip 'self' and 'cls' parameters (methods)
            if param.name in ('self', 'cls'):
                continue

            # Skip dependency injection parameters (FastAPI Depends or common dependency names)
            if self._is_dependency_injection(param.name, param.type_hint):
                logger.debug(f"Skipping dependency injection parameter: {param.name} ({param.type_hint})")
                continue

            # Generate parameter schema
            param_schema = self._generate_parameter_schema(param, pattern)
            schema["properties"][param.name] = param_schema

            # Track required parameters
            if param.required:
                required.append(param.name)

        # Only add required field if there are required parameters
        if required:
            schema["required"] = required

        return schema

    def _generate_parameter_schema(
        self,
        param: Parameter,
        pattern: DetectedPattern
    ) -> Dict[str, Any]:
        """Generate JSON Schema for a single parameter.

        Args:
            param: Parameter to generate schema for
            pattern: Parent pattern (for context)

        Returns:
            JSON Schema for parameter
        """
        schema: Dict[str, Any] = {}

        # Map Python type to JSON Schema type
        if param.type_hint:
            json_type = self._python_type_to_json_schema(param.type_hint)
            if json_type:
                schema["type"] = json_type

        # Add description (from parameter description or docstring parsing)
        description = self._generate_parameter_description(param, pattern)
        if description:
            schema["description"] = description

        # Add default value if present
        if param.default_value is not None and param.default_value != "None":
            schema["default"] = self._parse_default_value(param.default_value)

        return schema

    def _python_type_to_json_schema(self, type_hint: str) -> Optional[str]:
        """Convert Python type annotation to JSON Schema type.

        Handles:
        - Basic types (str, int, float, bool)
        - Collections (List, Dict, Set, Tuple)
        - Optional types
        - Union types (returns first type or null)
        - Literal types (returns string with enum)

        Args:
            type_hint: Python type annotation string

        Returns:
            JSON Schema type string or dict for complex types

        Examples:
            >>> _python_type_to_json_schema("str")
            'string'
            >>> _python_type_to_json_schema("Optional[int]")
            'integer'
            >>> _python_type_to_json_schema("List[str]")
            'array'
            >>> _python_type_to_json_schema("Union[str, int]")
            'string'
        """
        # Handle None type
        if type_hint == "None":
            return "null"

        # Remove Optional wrapper (Optional[X] = Union[X, None])
        type_hint = re.sub(r'Optional\[(.*?)\]', r'\1', type_hint)

        # Handle Union types - use first non-None type
        union_match = re.match(r'Union\[(.*?)\]', type_hint)
        if union_match:
            types = [t.strip() for t in union_match.group(1).split(',')]
            # Use first non-None type
            for t in types:
                if t != 'None':
                    return self._python_type_to_json_schema(t)

        # Handle Literal types - extract enum values
        literal_match = re.match(r'Literal\[(.*?)\]', type_hint)
        if literal_match:
            # Literals become string enums in JSON Schema
            return "string"  # Could enhance to return full enum schema

        # Extract base type from generics (List[X] -> List, Dict[X, Y] -> Dict)
        match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)', type_hint)
        if match:
            base_type = match.group(1)
            return self.TYPE_MAPPING.get(base_type)

        return None

    def _generate_parameter_description(
        self,
        param: Parameter,
        pattern: DetectedPattern
    ) -> Optional[str]:
        """Generate description for a parameter.

        Extracts from:
        1. Parameter's own description field
        2. Docstring Args section
        3. RAG retriever (if available)
        4. Fallback to parameter name

        Args:
            param: Parameter to describe
            pattern: Parent pattern for context

        Returns:
            Parameter description or None
        """
        # 1. Use parameter's description if available
        if param.description:
            return param.description

        # 2. Parse from docstring Args section
        if pattern.docstring:
            desc = self._extract_param_from_docstring(param.name, pattern.docstring)
            if desc:
                return desc

        # 3. Use RAG retriever for enhanced descriptions
        if self.rag_retriever:
            desc = self._rag_enhanced_parameter_description(param, pattern)
            if desc:
                return desc

        # 4. Fallback: humanize parameter name
        return self._humanize_param_name(param.name)

    def _extract_param_from_docstring(
        self,
        param_name: str,
        docstring: str
    ) -> Optional[str]:
        """Extract parameter description from docstring Args section.

        Supports Google, NumPy, and Sphinx docstring styles.

        Args:
            param_name: Parameter name to find
            docstring: Function docstring

        Returns:
            Parameter description or None
        """
        # Google style: "    param_name: Description"
        # NumPy style: "    param_name : type\n        Description"
        # Sphinx style: ":param param_name: Description"

        patterns = [
            # Google style
            rf'{param_name}\s*:\s*(.+?)(?:\n|$)',
            # Sphinx style
            rf':param\s+{param_name}\s*:\s*(.+?)(?:\n|$)',
            # NumPy style (simplified)
            rf'{param_name}\s*:\s*\w+\n\s+(.+?)(?:\n|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, docstring, re.MULTILINE)
            if match:
                desc = match.group(1).strip()
                # Remove trailing periods
                if desc.endswith('.'):
                    desc = desc[:-1]
                return desc

        return None

    def _humanize_param_name(self, param_name: str) -> str:
        """Convert parameter name to human-readable description.

        Args:
            param_name: Parameter name (e.g., "user_id")

        Returns:
            Human-readable description (e.g., "User ID")
        """
        # Split on underscores
        words = param_name.split('_')

        # Capitalize first word, lowercase rest
        humanized = ' '.join(word.capitalize() for word in words)

        return humanized

    def _parse_default_value(self, default_str: str) -> Any:
        """Parse default value string to appropriate Python type.

        Args:
            default_str: String representation of default value

        Returns:
            Parsed value in appropriate type
        """
        # Try to evaluate safely
        if default_str == "True":
            return True
        elif default_str == "False":
            return False
        elif default_str == "None":
            return None
        elif default_str.startswith('"') or default_str.startswith("'"):
            # String literal
            return default_str.strip('"\'')
        else:
            # Try to parse as number
            try:
                if '.' in default_str:
                    return float(default_str)
                else:
                    return int(default_str)
            except ValueError:
                # Return as string if all else fails
                return default_str

    def _get_relationship_context(self, pattern: DetectedPattern) -> Dict[str, Any]:
        """Get graph relationship context for a function.

        Queries Neo4j to find:
        - Functions that call this function
        - Functions this function calls
        - Related classes/modules

        Args:
            pattern: Pattern to get context for

        Returns:
            Dictionary with relationship information
        """
        if not self.neo4j_client:
            return {}

        context = {
            "called_by": [],
            "calls": [],
            "in_module": None,
            "in_class": None
        }

        try:
            # Get functions that call this function
            callers_query = """
            MATCH (caller:Function)-[:CALLS]->(f:Function {qualifiedName: $qname})
            RETURN caller.qualifiedName as caller, caller.name as caller_name
            LIMIT 5
            """
            callers = self.neo4j_client.execute_query(
                callers_query,
                {"qname": pattern.qualified_name}
            )
            context["called_by"] = [r["caller_name"] for r in callers]

            # Get functions this function calls
            calls_query = """
            MATCH (f:Function {qualifiedName: $qname})-[:CALLS]->(callee:Function)
            RETURN callee.qualifiedName as callee, callee.name as callee_name
            LIMIT 5
            """
            calls = self.neo4j_client.execute_query(
                calls_query,
                {"qname": pattern.qualified_name}
            )
            context["calls"] = [r["callee_name"] for r in calls]

            # Get module/class context
            if "::" in pattern.qualified_name:
                parts = pattern.qualified_name.split("::")
                if len(parts) > 1:
                    context["in_module"] = parts[0].split("/")[-1].replace(".py", "")
                if len(parts) > 2:
                    context["in_class"] = parts[1]

        except Exception as e:
            logger.warning(f"Failed to get relationship context: {e}")

        return context

    def _extract_usage_examples(self, pattern: DetectedPattern) -> List[str]:
        """Extract usage examples from tests and other code.

        Args:
            pattern: Pattern to find examples for

        Returns:
            List of code examples showing usage
        """
        if not self.neo4j_client:
            return []

        examples = []

        try:
            # Find test functions that call this function
            examples_query = """
            MATCH (test:File {is_test: true})-[:CONTAINS]->
                  (test_func:Function)-[:CALLS]->
                  (target:Function {qualifiedName: $qname})
            RETURN test_func.qualifiedName as test_name
            LIMIT 3
            """
            results = self.neo4j_client.execute_query(
                examples_query,
                {"qname": pattern.qualified_name}
            )

            if results:
                # Function is tested - construct synthetic examples
                func_name = pattern.function_name

                # Build example call with placeholder params
                params = []
                for param in pattern.parameters:
                    if param.name == "self":
                        continue  # Skip self parameter
                    # Use type hints to generate example values
                    if param.type_hint:
                        if "str" in param.type_hint.lower():
                            params.append(f'"{param.name}_value"')
                        elif "int" in param.type_hint.lower():
                            params.append("1")
                        elif "bool" in param.type_hint.lower():
                            params.append("True")
                        elif "list" in param.type_hint.lower():
                            params.append("[]")
                        elif "dict" in param.type_hint.lower():
                            params.append("{}")
                        else:
                            params.append(f"{param.name}")
                    elif param.default_value:
                        params.append(param.default_value)
                    else:
                        params.append(f"{param.name}")

                # Create example call
                if params:
                    example = f"{func_name}({', '.join(params)})"
                    examples.append(example)
                    logger.debug(f"Generated usage example: {example}")

        except Exception as e:
            logger.warning(f"Failed to extract usage examples: {e}")

        return examples

    def _generate_gpt4_description(
        self,
        pattern: DetectedPattern,
        rag_context: List[Any],
        relationship_context: Dict[str, Any],
        examples: List[str]
    ) -> Optional[str]:
        """Generate enhanced description using GPT-4o.

        Combines all available context to generate a rich description.

        Args:
            pattern: Pattern to describe
            rag_context: RAG retrieval results
            relationship_context: Graph relationship info
            examples: Usage examples

        Returns:
            GPT-generated description or None
        """
        if not self.openai_client:
            return None

        try:
            # Build context for GPT
            context_parts = []

            # Add function signature
            params_str = ", ".join([p.name for p in pattern.parameters])
            context_parts.append(f"Function: {pattern.function_name}({params_str})")

            if pattern.return_type:
                context_parts.append(f"Returns: {pattern.return_type}")

            # Add original docstring if available
            if pattern.docstring:
                context_parts.append(f"Docstring: {pattern.docstring}")

            # Add RAG context from similar functions
            if rag_context:
                context_parts.append("\nSimilar functions:")
                for i, result in enumerate(rag_context[:2], 1):
                    if result.docstring:
                        context_parts.append(f"{i}. {result.qualified_name}: {result.docstring[:100]}")

            # Add relationship context
            if relationship_context:
                if relationship_context.get("called_by"):
                    callers = ", ".join(relationship_context["called_by"][:3])
                    context_parts.append(f"\nCalled by: {callers}")
                if relationship_context.get("calls"):
                    calls = ", ".join(relationship_context["calls"][:3])
                    context_parts.append(f"Calls: {calls}")

            # Add usage examples
            if examples:
                context_parts.append("\nUsage examples:")
                for i, example in enumerate(examples[:2], 1):
                    context_parts.append(f"{i}. {example}")

            context = "\n".join(context_parts)

            # Generate description with GPT-4o
            prompt = f"""Generate a concise, clear description (1-2 sentences) for this MCP tool based on the context below.
Focus on what the tool does and when to use it. Be specific and actionable.

Context:
{context}

Generate a tool description:"""

            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",  # Use mini for cost efficiency
                messages=[
                    {"role": "system", "content": "You are a technical documentation expert. Generate concise, clear tool descriptions."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=100,
                temperature=0.3
            )

            description = response.choices[0].message.content.strip()

            # Remove trailing periods
            if description.endswith('.'):
                description = description[:-1]

            logger.info(f"GPT-4o enhanced description for {pattern.function_name}: {description}")
            return description

        except Exception as e:
            logger.warning(f"GPT-4o description generation failed: {e}")
            return None

    def _rag_enhanced_parameter_description(
        self,
        param: Parameter,
        pattern: DetectedPattern
    ) -> Optional[str]:
        """Use RAG + GPT-4o to generate enhanced parameter description.

        Combines multiple RAG results, code context, and GPT-4o to generate
        rich parameter descriptions.

        Args:
            param: Parameter to describe
            pattern: Parent pattern for context

        Returns:
            Enhanced description or None
        """
        if not self.rag_retriever:
            return None

        try:
            # Query for context about this parameter
            query = f"parameter {param.name} in {pattern.function_name}"

            # Retrieve relevant code context (top 3 results)
            results = self.rag_retriever.retrieve(
                query=query,
                top_k=3,
                entity_types=["Function"],
                include_related=True
            )

            if not results:
                return None

            # Aggregate information from multiple results
            docstrings = []
            for result in results:
                if result.docstring and param.name in result.docstring:
                    # Try to extract parameter description
                    desc = self._extract_param_from_docstring(param.name, result.docstring)
                    if desc:
                        docstrings.append(desc)

            # If we found descriptions in docstrings, use the best one
            if docstrings:
                # Use the first (most relevant) description
                logger.debug(f"RAG enhanced description for {param.name}: {docstrings[0]}")
                return docstrings[0]

            # If no docstring match, use GPT-4o to generate from context
            if self.openai_client:
                context_parts = [f"Parameter: {param.name}"]
                if param.type_hint:
                    context_parts.append(f"Type: {param.type_hint}")

                # Add context from similar functions
                for i, result in enumerate(results[:2], 1):
                    if result.code and param.name in result.code:
                        context_parts.append(f"Usage in {result.qualified_name}")

                context = "\n".join(context_parts)

                prompt = f"""Generate a brief (5-10 words) parameter description:

{context}

Description:"""

                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a technical documentation expert. Generate brief parameter descriptions."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=30,
                    temperature=0.3
                )

                description = response.choices[0].message.content.strip()
                logger.debug(f"GPT-4o parameter description for {param.name}: {description}")
                return description

            return None

        except Exception as e:
            logger.warning(f"RAG enhancement failed for {param.name}: {e}")
            return None

    def _rag_enhanced_tool_description(
        self,
        pattern: DetectedPattern
    ) -> Optional[str]:
        """Use RAG + GPT-4o to generate comprehensive tool description.

        Combines:
        - RAG retrieval (multiple results)
        - Graph relationships (callers, calls)
        - Usage examples from tests
        - GPT-4o synthesis

        Args:
            pattern: Pattern to describe

        Returns:
            Comprehensive enhanced description or None
        """
        if not self.rag_retriever:
            return None

        try:
            # 1. Get RAG context - retrieve top 3 similar functions
            query = f"function {pattern.function_name}"
            rag_results = self.rag_retriever.retrieve(
                query=query,
                top_k=3,
                entity_types=["Function", "Class"],
                include_related=True
            )

            # 2. Get graph relationship context
            rel_context = self._get_relationship_context(pattern)

            # 3. Extract usage examples from tests
            examples = self._extract_usage_examples(pattern)

            # 4. Generate enhanced description with GPT-4o
            gpt_description = self._generate_gpt4_description(
                pattern,
                rag_results,
                rel_context,
                examples
            )

            if gpt_description:
                return gpt_description

            # Fallback: use top RAG result if GPT fails
            if rag_results and rag_results[0].docstring:
                lines = rag_results[0].docstring.strip().split('\n')
                description = lines[0].strip()
                if description.endswith('.'):
                    description = description[:-1]
                logger.debug(f"Fallback description for {pattern.function_name}: {description}")
                return description

            return None

        except Exception as e:
            logger.warning(f"RAG enhancement failed for {pattern.function_name}: {e}")
            return None

    def _extract_examples_from_docstring(self, docstring: str) -> Optional[List[Dict[str, Any]]]:
        """Extract usage examples from docstring.

        Looks for Example/Examples sections with code blocks.

        Args:
            docstring: Function docstring

        Returns:
            List of example dictionaries or None

        Example docstring format:
            '''
            Do something.

            Example:
                >>> my_function("hello", 42)
                {'result': 'success'}

            Examples:
                >>> my_function("test", 1)
                >>> my_function("foo", 2)
            '''
        """
        if not docstring:
            return None

        examples = []

        # Find Example/Examples sections
        # Pattern matches "Example:" or "Examples:" followed by indented lines
        example_patterns = [
            r'Examples?:\s*\n((?:\s+.*\n?)*)',  # Captures indented lines after Example:
        ]

        for pattern in example_patterns:
            match = re.search(pattern, docstring, re.MULTILINE | re.IGNORECASE)
            if match:
                example_text = match.group(1).strip()

                # Extract Python code examples (lines starting with >>>)
                code_lines = []
                for line in example_text.split('\n'):
                    line = line.strip()
                    if line.startswith('>>>'):
                        # Remove >>> prefix
                        code = line[3:].strip()
                        code_lines.append(code)
                    elif line.startswith('...'):
                        # Continuation line
                        code = line[3:].strip()
                        if code_lines:
                            code_lines[-1] += f" {code}"

                # Create example objects
                for code in code_lines:
                    if code:
                        examples.append({
                            "code": code,
                            "language": "python"
                        })

        return examples if examples else None

    def generate_batch_schemas(
        self,
        patterns: List[DetectedPattern]
    ) -> List[Dict[str, Any]]:
        """Generate schemas for multiple patterns.

        Args:
            patterns: List of detected patterns

        Returns:
            List of MCP tool schemas
        """
        schemas = []

        for pattern in patterns:
            try:
                schema = self.generate_tool_schema(pattern)
                schemas.append(schema)
            except Exception as e:
                logger.error(
                    f"Failed to generate schema for {pattern.function_name}: {e}"
                )

        logger.info(f"Generated {len(schemas)} tool schemas")
        return schemas
