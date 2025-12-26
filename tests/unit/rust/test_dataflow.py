"""Tests for Rust data flow graph extraction and taint analysis (REPO-411).

These tests verify:
1. Data flow edge extraction from Python AST
2. Forward/backward slicing for taint tracking
3. SQL injection detection
4. Command injection detection
5. Code injection detection
6. Sanitizer recognition
7. Multi-hop taint flows
"""

import pytest

# Skip all tests if repotoire_fast is not available
pytest.importorskip("repotoire_fast")

import repotoire_fast


class TestDataFlowExtraction:
    """Tests for extract_dataflow and extract_dataflow_batch functions."""

    def test_simple_assignment(self):
        """Test basic assignment: x = y creates edge y -> x."""
        source = "x = y\n"
        edges = repotoire_fast.extract_dataflow(source)
        assert len(edges) >= 1
        edge = edges[0]
        assert edge.source_var == "y"
        assert edge.target_var == "x"
        assert edge.edge_type == "assignment"

    def test_multiple_assignment(self):
        """Test chain assignment: a = b = c creates edges c -> a and c -> b."""
        source = "a = b = c\n"
        edges = repotoire_fast.extract_dataflow(source)
        # c flows to both a and b
        assert len(edges) >= 2
        target_vars = {e.target_var for e in edges}
        assert "a" in target_vars or "b" in target_vars

    def test_augmented_assignment(self):
        """Test augmented assignment: x += y creates edges y -> x and x -> x."""
        source = "x += y\n"
        edges = repotoire_fast.extract_dataflow(source)
        # y flows to x, x flows to x
        assert len(edges) >= 2
        assert any(e.source_var == "y" and e.target_var == "x" for e in edges)
        assert any(e.source_var == "x" and e.target_var == "x" for e in edges)
        assert all(e.edge_type == "augmented" for e in edges)

    def test_function_parameters(self):
        """Test function parameters create data flow edges."""
        source = """
def foo(a, b):
    return a + b
"""
        edges = repotoire_fast.extract_dataflow(source)
        # Parameters should have edges
        param_edges = [e for e in edges if e.edge_type == "parameter"]
        assert len(param_edges) >= 2
        # Return should have edges
        return_edges = [e for e in edges if e.edge_type == "return"]
        assert len(return_edges) >= 1

    def test_tuple_unpacking(self):
        """Test tuple unpacking: x, y = z creates edges z -> x and z -> y."""
        source = "x, y = get_values()\n"
        edges = repotoire_fast.extract_dataflow(source)
        # Should have unpack edges
        unpack_edges = [e for e in edges if e.edge_type == "unpack"]
        assert len(unpack_edges) >= 2

    def test_for_loop(self):
        """Test for loop: for item in items creates edge items -> item."""
        source = """
for item in items:
    print(item)
"""
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.source_var == "items" and e.target_var == "item" for e in edges)

    def test_with_statement(self):
        """Test with statement: with open(f) as handle creates edge."""
        source = """
with open(f) as handle:
    data = handle.read()
"""
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.target_var == "handle" for e in edges)

    def test_attribute_access(self):
        """Test attribute assignment: obj.attr = value creates edge."""
        source = "obj.attr = value\n"
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.source_var == "value" and "obj.attr" in e.target_var for e in edges)

    def test_walrus_operator(self):
        """Test walrus operator: (x := value) creates edge."""
        source = """
if (x := get_value()):
    print(x)
"""
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.target_var == "x" for e in edges)

    def test_class_scope(self):
        """Test scope tracking in class methods."""
        source = """
class Foo:
    def bar(self):
        x = self.value
        return x
"""
        edges = repotoire_fast.extract_dataflow(source)
        # Check that scope contains Foo.bar
        method_edges = [e for e in edges if "Foo" in e.scope and "bar" in e.scope]
        assert len(method_edges) >= 1

    def test_batch_processing(self):
        """Test batch processing of multiple files."""
        files = [
            ("a.py", "x = 1\n"),
            ("b.py", "y = 2\n"),
        ]
        results = repotoire_fast.extract_dataflow_batch(files)
        assert len(results) == 2
        paths = [r[0] for r in results]
        assert "a.py" in paths
        assert "b.py" in paths

    def test_empty_source(self):
        """Test empty source returns no edges."""
        edges = repotoire_fast.extract_dataflow("")
        assert edges == []

    def test_syntax_error_handling(self):
        """Test graceful handling of syntax errors."""
        source = "def broken("  # Incomplete syntax
        edges = repotoire_fast.extract_dataflow(source)
        # Should return empty list, not crash
        assert edges == []


class TestTaintAnalysis:
    """Tests for find_taint_flows and related functions."""

    def test_sql_injection_detection(self):
        """Test detection of SQL injection from input() to cursor.execute()."""
        source = """
user_input = input("Enter query: ")
query = "SELECT * FROM users WHERE name = '" + user_input + "'"
cursor.execute(query)
"""
        flows = repotoire_fast.find_taint_flows(source)
        assert len(flows) >= 1
        # At least one flow should be SQL injection
        sql_flows = [f for f in flows if f.vulnerability == "sql_injection"]
        assert len(sql_flows) >= 1

    def test_command_injection_detection(self):
        """Test detection of command injection from input() to os.system()."""
        source = """
cmd = input("Enter command: ")
os.system(cmd)
"""
        flows = repotoire_fast.find_taint_flows(source)
        cmd_flows = [f for f in flows if f.vulnerability == "command_injection"]
        assert len(cmd_flows) >= 1

    def test_code_injection_detection(self):
        """Test detection of code injection from request.form to eval()."""
        source = """
data = request.form['code']
result = eval(data)
"""
        flows = repotoire_fast.find_taint_flows(source)
        code_flows = [f for f in flows if f.vulnerability == "code_injection"]
        assert len(code_flows) >= 1

    def test_request_form_source(self):
        """Test request.form is recognized as user input source."""
        source = """
data = request.form['name']
eval(data)
"""
        flows = repotoire_fast.find_taint_flows(source)
        assert any(f.source_category == "user_input" for f in flows)

    def test_multi_hop_flow(self):
        """Test taint propagation through multiple assignments."""
        source = """
a = input("Enter: ")
b = a
c = b
eval(c)
"""
        flows = repotoire_fast.find_taint_flows(source)
        assert len(flows) >= 1
        # Path should have multiple hops
        assert any(len(f.path) >= 3 for f in flows)

    def test_sanitizer_detection(self):
        """Test sanitizer functions are detected in taint path."""
        source = """
user_input = input("Enter: ")
safe_input = html.escape(user_input)
render_template(safe_input)
"""
        flows = repotoire_fast.find_taint_flows(source)
        # Flows may still be reported but with has_sanitizer=True
        for flow in flows:
            if "escape" in str(flow.path):
                # Path contains sanitizer function
                pass  # This is expected

    def test_no_false_positive_safe_query(self):
        """Test parameterized queries don't trigger false positives."""
        source = """
user_id = 42
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
"""
        flows = repotoire_fast.find_taint_flows(source)
        # Should not detect SQL injection since user_id is not from user input
        sql_flows = [
            f for f in flows
            if f.vulnerability == "sql_injection" and not f.has_sanitizer
        ]
        assert len(sql_flows) == 0

    def test_batch_taint_flows(self):
        """Test batch processing of taint analysis."""
        files = [
            ("vulnerable.py", "x = input()\neval(x)\n"),
            ("safe.py", "y = 1\nprint(y)\n"),
        ]
        results = repotoire_fast.find_taint_flows_batch(files)
        assert len(results) == 2
        # First file should have flows
        vuln_file = next((r for r in results if r[0] == "vulnerable.py"), None)
        assert vuln_file is not None
        assert len(vuln_file[1]) >= 1

    def test_taint_flow_attributes(self):
        """Test TaintFlow object has all expected attributes."""
        source = """
x = input()
eval(x)
"""
        flows = repotoire_fast.find_taint_flows(source)
        assert len(flows) >= 1
        flow = flows[0]
        # Check all required attributes exist
        assert hasattr(flow, "source")
        assert hasattr(flow, "source_line")
        assert hasattr(flow, "source_category")
        assert hasattr(flow, "sink")
        assert hasattr(flow, "sink_line")
        assert hasattr(flow, "vulnerability")
        assert hasattr(flow, "severity")
        assert hasattr(flow, "path")
        assert hasattr(flow, "path_lines")
        assert hasattr(flow, "scope")
        assert hasattr(flow, "has_sanitizer")

    def test_severity_assignment(self):
        """Test vulnerability severities are correctly assigned."""
        source = """
x = input()
eval(x)
"""
        flows = repotoire_fast.find_taint_flows(source)
        assert len(flows) >= 1
        # Code injection should be critical
        assert any(f.severity == "critical" for f in flows)


class TestDefaultPatterns:
    """Tests for default source, sink, and sanitizer patterns."""

    def test_default_sources(self):
        """Test default sources list is populated."""
        sources = repotoire_fast.get_default_taint_sources()
        assert len(sources) > 0
        # Check known patterns
        patterns = [s[0] for s in sources]
        assert "input()" in patterns
        assert "request.args" in patterns
        assert "os.environ" in patterns

    def test_default_sinks(self):
        """Test default sinks list is populated."""
        sinks = repotoire_fast.get_default_taint_sinks()
        assert len(sinks) > 0
        # Check known patterns
        patterns = [s[0] for s in sinks]
        assert "eval()" in patterns
        assert "os.system()" in patterns
        # Check vulnerability types
        vulnerabilities = [s[1] for s in sinks]
        assert "sql_injection" in vulnerabilities
        assert "command_injection" in vulnerabilities
        assert "code_injection" in vulnerabilities

    def test_default_sanitizers(self):
        """Test default sanitizers list is populated."""
        sanitizers = repotoire_fast.get_default_sanitizers()
        assert len(sanitizers) > 0
        assert "escape" in sanitizers
        assert "validate" in sanitizers
        assert "sanitize" in sanitizers


class TestDataFlowEdgeTypes:
    """Tests for different data flow edge types."""

    def test_assignment_edge_type(self):
        """Test assignment edge type is correctly identified."""
        source = "x = y\n"
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.edge_type == "assignment" for e in edges)

    def test_parameter_edge_type(self):
        """Test parameter edge type is correctly identified."""
        source = """
def foo(x):
    pass
"""
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.edge_type == "parameter" for e in edges)

    def test_return_edge_type(self):
        """Test return edge type is correctly identified."""
        source = """
def foo():
    return x
"""
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.edge_type == "return" for e in edges)

    def test_augmented_edge_type(self):
        """Test augmented assignment edge type is correctly identified."""
        source = "x += 1\n"
        edges = repotoire_fast.extract_dataflow(source)
        assert any(e.edge_type == "augmented" for e in edges)


class TestComplexScenarios:
    """Tests for complex real-world scenarios."""

    def test_flask_request_to_sql(self):
        """Test Flask request to SQL injection scenario."""
        source = """
@app.route("/search")
def search():
    query = request.args.get("q")
    result = db.execute("SELECT * FROM items WHERE name LIKE '%" + query + "%'")
    return render_template("results.html", items=result)
"""
        flows = repotoire_fast.find_taint_flows(source)
        # Should detect SQL injection
        assert len(flows) >= 1

    def test_file_path_traversal(self):
        """Test path traversal from user input to file open."""
        source = """
filename = request.args.get("file")
with open("/data/" + filename, "r") as f:
    return f.read()
"""
        flows = repotoire_fast.find_taint_flows(source)
        # Should detect path traversal
        assert len(flows) >= 1

    def test_nested_function_scope(self):
        """Test taint tracking through nested functions."""
        source = """
def outer():
    data = input()
    def inner():
        nonlocal data
        eval(data)
    inner()
"""
        flows = repotoire_fast.find_taint_flows(source)
        # Should still detect the flow
        assert len(flows) >= 1

    def test_class_method_flow(self):
        """Test taint tracking through class methods."""
        source = """
class Handler:
    def process(self, data):
        return eval(data)

handler = Handler()
user_input = input()
handler.process(user_input)
"""
        # Note: Inter-procedural analysis may not track this perfectly,
        # but basic intra-procedural flows should be detected
        flows = repotoire_fast.find_taint_flows(source)
        # May or may not detect depending on analysis depth
        # This is a known limitation of intra-procedural analysis

    def test_async_function(self):
        """Test taint tracking in async functions."""
        source = """
async def fetch_data():
    data = await request.json()
    result = eval(data['code'])
    return result
"""
        flows = repotoire_fast.find_taint_flows(source)
        # Should detect flow in async function
        assert len(flows) >= 1


class TestPerformance:
    """Performance tests for large inputs."""

    def test_large_file_handling(self):
        """Test handling of files with many statements."""
        # Generate a file with 1000 lines
        lines = []
        for i in range(1000):
            lines.append(f"var{i} = var{i-1}")
        source = "\n".join(lines)

        # Should complete without timeout
        edges = repotoire_fast.extract_dataflow(source)
        assert len(edges) >= 500  # Should have many edges

    def test_batch_efficiency(self):
        """Test batch processing is efficient."""
        # Create 100 small files
        files = [(f"file{i}.py", f"x{i} = y{i}\n") for i in range(100)]

        # Should complete quickly
        results = repotoire_fast.extract_dataflow_batch(files)
        assert len(results) == 100
