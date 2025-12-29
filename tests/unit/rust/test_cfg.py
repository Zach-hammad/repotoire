"""Tests for Rust control flow graph extraction (REPO-414).

These tests verify:
1. Basic CFG construction and analysis
2. Unreachable code detection (after return/raise)
3. Infinite loop detection
4. Cyclomatic complexity calculation
5. Batch processing
6. Interprocedural infinite loop detection (Phase 1)
"""

import pytest

# Skip all tests if repotoire_fast is not available
pytest.importorskip("repotoire_fast")

from repotoire_fast import (
    analyze_cfg,
    analyze_cfg_batch,
    analyze_cfg_interprocedural,
    analyze_interprocedural,
    analyze_cross_file,
)


class TestBasicCFGAnalysis:
    """Tests for basic CFG analysis."""

    def test_empty_source(self):
        """Test empty source returns no results."""
        results = analyze_cfg("")
        assert results == []

    def test_simple_function(self):
        """Test simple function analysis."""
        source = """
def foo():
    return 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["function_name"] == "foo"
        assert results[0]["unreachable_lines"] == []
        assert results[0]["has_infinite_loop"] is False

    def test_multiple_functions(self):
        """Test multiple functions are all analyzed."""
        source = """
def foo():
    return 1

def bar():
    return 2

def baz():
    return 3
"""
        results = analyze_cfg(source)
        assert len(results) == 3
        names = [r["function_name"] for r in results]
        assert "foo" in names
        assert "bar" in names
        assert "baz" in names


class TestUnreachableCode:
    """Tests for unreachable code detection."""

    def test_unreachable_after_return(self):
        """Test code after return is marked unreachable."""
        source = """
def foo():
    return 1
    print("unreachable")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert 4 in results[0]["unreachable_lines"]

    def test_unreachable_after_raise(self):
        """Test code after raise is marked unreachable."""
        source = """
def foo():
    raise ValueError()
    x = 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert 4 in results[0]["unreachable_lines"]

    def test_no_unreachable_in_conditional(self):
        """Test conditional with one return doesn't mark else as unreachable."""
        source = """
def foo(x):
    if x:
        return 1
    return 2
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["unreachable_lines"] == []

    def test_unreachable_after_both_branches_return(self):
        """Test code after if/else where both branches return."""
        source = """
def foo(x):
    if x:
        return 1
    else:
        return 2
    print("unreachable")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert 7 in results[0]["unreachable_lines"]

    def test_unreachable_after_break(self):
        """Test code after break is unreachable."""
        source = """
def foo():
    while True:
        break
        print("unreachable")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert 5 in results[0]["unreachable_lines"]

    def test_unreachable_after_continue(self):
        """Test code after continue is unreachable."""
        source = """
def foo():
    for i in range(10):
        if i > 5:
            continue
            print("unreachable")
        print(i)
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert 6 in results[0]["unreachable_lines"]


class TestInfiniteLoopDetection:
    """Tests for infinite loop detection.

    The implementation detects several patterns:
    1. `while True:` without break/return
    2. `while 1:` without break/return
    3. `for x in itertools.cycle(...)` without break/return
    4. `while condition:` where condition variable is never modified
    """

    def test_while_true_no_break(self):
        """Test while True without break is detected as infinite."""
        source = """
def foo():
    while True:
        pass
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "while_true"
        assert results[0]["infinite_loop_types"][0]["line"] == 3

    def test_while_one_no_break(self):
        """Test while 1 without break is detected as infinite."""
        source = """
def foo():
    while 1:
        print("looping")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "while_one"
        assert results[0]["infinite_loop_types"][0]["line"] == 3

    def test_while_true_with_break(self):
        """Test while True with break is not infinite."""
        source = """
def foo():
    while True:
        if condition:
            break
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False
        assert results[0]["infinite_loop_types"] == []

    def test_while_true_with_return(self):
        """Test while True with return is not infinite."""
        source = """
def foo():
    while True:
        return 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False
        assert results[0]["infinite_loop_types"] == []

    def test_while_with_condition(self):
        """Test normal while loop is not infinite."""
        source = """
def foo(x):
    while x > 0:
        x -= 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False
        assert results[0]["infinite_loop_types"] == []

    def test_for_loop_not_infinite(self):
        """Test for loop is not considered infinite."""
        source = """
def foo():
    for i in range(10):
        print(i)
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False
        assert results[0]["infinite_loop_types"] == []

    def test_itertools_cycle_qualified(self):
        """Test for loop over itertools.cycle() is detected as infinite."""
        source = """
def foo():
    for x in itertools.cycle([1, 2, 3]):
        print(x)
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "itertools_cycle"
        assert results[0]["infinite_loop_types"][0]["line"] == 3

    def test_itertools_cycle_imported(self):
        """Test for loop over cycle() (imported) is detected as infinite."""
        source = """
def foo():
    for x in cycle([1, 2, 3]):
        print(x)
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "itertools_cycle"

    def test_itertools_cycle_with_break(self):
        """Test for loop over itertools.cycle() with break is not infinite."""
        source = """
def foo():
    for x in itertools.cycle([1, 2, 3]):
        if x > 10:
            break
        print(x)
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False
        assert results[0]["infinite_loop_types"] == []

    def test_unmodified_condition_variable(self):
        """Test while loop where condition variable is never modified."""
        source = """
def foo(x):
    while x > 0:
        print("looping")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "unmodified_condition"
        assert "x" in results[0]["infinite_loop_types"][0]["description"]

    def test_modified_condition_variable(self):
        """Test while loop where condition variable is modified."""
        source = """
def foo(x):
    while x > 0:
        x -= 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False
        assert results[0]["infinite_loop_types"] == []

    def test_multiple_condition_variables_none_modified(self):
        """Test while loop with multiple condition variables, none modified."""
        source = """
def foo(x, y):
    while x > 0 and y < 10:
        print("looping")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "unmodified_condition"

    def test_multiple_condition_variables_one_modified(self):
        """Test while loop with multiple condition variables, one modified."""
        source = """
def foo(x, y):
    while x > 0 and y < 10:
        x -= 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # Only if ALL condition variables are unmodified do we flag it
        assert results[0]["has_infinite_loop"] is False

    def test_nested_while_true(self):
        """Test nested while True loops are both detected."""
        source = """
def foo():
    while True:
        while True:
            pass
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 2
        # Both should be while_true
        types = [lt["type"] for lt in results[0]["infinite_loop_types"]]
        assert types.count("while_true") == 2

    def test_while_true_in_if(self):
        """Test while True inside if block is detected."""
        source = """
def foo(x):
    if x:
        while True:
            pass
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "while_true"

    def test_while_true_with_nested_break(self):
        """Test while True with break in nested loop is still infinite."""
        source = """
def foo():
    while True:
        for i in range(10):
            if i > 5:
                break
        print("still looping")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # The break only breaks the inner for loop, not the outer while True
        assert results[0]["has_infinite_loop"] is True
        assert len(results[0]["infinite_loop_types"]) == 1
        assert results[0]["infinite_loop_types"][0]["type"] == "while_true"

    def test_while_true_with_conditional_return(self):
        """Test while True with conditional return is not infinite."""
        source = """
def foo():
    while True:
        if some_condition():
            return "done"
        print("looping")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # Has a return path, so not flagged as infinite
        assert results[0]["has_infinite_loop"] is False


class TestCyclomaticComplexity:
    """Tests for cyclomatic complexity calculation."""

    def test_linear_function(self):
        """Test linear function has low complexity."""
        source = """
def foo():
    x = 1
    y = 2
    return x + y
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # Linear function: complexity = 1
        assert results[0]["cyclomatic_complexity"] >= 1

    def test_single_if(self):
        """Test single if increases complexity."""
        source = """
def foo(x):
    if x:
        return 1
    return 2
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # One if: complexity >= 2
        assert results[0]["cyclomatic_complexity"] >= 2

    def test_nested_if(self):
        """Test nested if increases complexity more."""
        source = """
def foo(x, y):
    if x:
        if y:
            return 1
        return 2
    return 3
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # Two ifs should give complexity >= 2
        # Note: Exact value depends on CFG construction details
        assert results[0]["cyclomatic_complexity"] >= 2


class TestExceptionHandling:
    """Tests for try/except/finally control flow."""

    def test_try_except(self):
        """Test try/except creates proper blocks."""
        source = """
def foo():
    try:
        risky()
    except:
        handle()
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["block_count"] >= 3

    def test_try_except_finally(self):
        """Test try/except/finally creates proper blocks."""
        source = """
def foo():
    try:
        risky()
    except:
        handle()
    finally:
        cleanup()
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["block_count"] >= 4


class TestClassMethods:
    """Tests for class method analysis."""

    def test_class_methods(self):
        """Test class methods are analyzed with qualified names."""
        source = """
class Foo:
    def bar(self):
        return 1
    def baz(self):
        return 2
"""
        results = analyze_cfg(source)
        assert len(results) == 2
        names = [r["function_name"] for r in results]
        assert "Foo.bar" in names
        assert "Foo.baz" in names

    def test_nested_class(self):
        """Test nested class methods are analyzed."""
        source = """
class Outer:
    class Inner:
        def method(self):
            return 1
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["function_name"] == "Outer.Inner.method"


class TestAsyncFunctions:
    """Tests for async function analysis."""

    def test_async_function(self):
        """Test async function is analyzed."""
        source = """
async def foo():
    return await bar()
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["function_name"] == "foo"

    def test_async_for_loop(self):
        """Test async for loop is handled."""
        source = """
async def foo():
    async for item in get_items():
        print(item)
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["has_infinite_loop"] is False


class TestBatchProcessing:
    """Tests for batch CFG analysis."""

    def test_batch_multiple_files(self):
        """Test batch processing multiple files."""
        files = [
            ("a.py", "def foo():\n    return 1\n"),
            ("b.py", "def bar():\n    return 2\n"),
        ]
        results = analyze_cfg_batch(files)
        assert len(results) == 2

        # Results are (path, analyses) tuples
        paths = [r[0] for r in results]
        assert "a.py" in paths
        assert "b.py" in paths

    def test_batch_with_parse_error(self):
        """Test batch handles parse errors gracefully."""
        files = [
            ("good.py", "def foo():\n    return 1\n"),
            ("bad.py", "def invalid syntax here"),
        ]
        results = analyze_cfg_batch(files)
        assert len(results) == 2

        # Find results by path
        good_result = next(r for r in results if r[0] == "good.py")
        bad_result = next(r for r in results if r[0] == "bad.py")

        assert len(good_result[1]) == 1
        assert len(bad_result[1]) == 0  # Parse error = no functions


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_function(self):
        """Test function with just pass."""
        source = """
def foo():
    pass
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        assert results[0]["unreachable_lines"] == []

    def test_nested_function(self):
        """Test nested function is analyzed."""
        source = """
def outer():
    def inner():
        return 1
    return inner()
"""
        results = analyze_cfg(source)
        assert len(results) == 2
        names = [r["function_name"] for r in results]
        assert "outer" in names
        assert "outer.inner" in names

    def test_lambda_not_analyzed(self):
        """Test lambda expressions are not analyzed as functions."""
        source = """
def foo():
    f = lambda x: x + 1
    return f(1)
"""
        results = analyze_cfg(source)
        # Only foo should be analyzed, not the lambda
        assert len(results) == 1
        assert results[0]["function_name"] == "foo"

    def test_match_statement(self):
        """Test Python 3.10 match statement."""
        source = """
def foo(x):
    match x:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "other"
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # All cases return, so no unreachable code
        assert results[0]["unreachable_lines"] == []

    def test_with_statement(self):
        """Test with statement is handled."""
        source = """
def foo():
    with open("file") as f:
        return f.read()
    print("unreachable")
"""
        results = analyze_cfg(source)
        assert len(results) == 1
        # Return inside with makes following code unreachable
        assert 5 in results[0]["unreachable_lines"]


class TestInterproceduralAnalysis:
    """Tests for interprocedural infinite loop detection (Phase 1)."""

    def test_direct_infinite_loop_detected(self):
        """Test function with direct infinite loop is detected."""
        source = """
def infinite_loop():
    while True:
        pass

def caller():
    infinite_loop()
"""
        analysis = analyze_interprocedural(source)

        # infinite_loop should be marked as may_diverge
        inf_summary = analysis["summaries"]["infinite_loop"]
        assert inf_summary["terminates"] == "may_diverge"
        assert inf_summary["has_infinite_loop"] is True

        # caller should also be marked as may_diverge
        caller_summary = analysis["summaries"]["caller"]
        assert caller_summary["terminates"] == "may_diverge"
        assert caller_summary["inherited_from"] == "infinite_loop"

    def test_propagation_through_chain(self):
        """Test non-termination propagates through call chain."""
        source = """
def a():
    b()

def b():
    c()

def c():
    while True:
        pass
"""
        analysis = analyze_interprocedural(source)

        # All should be may_diverge
        assert analysis["summaries"]["a"]["terminates"] == "may_diverge"
        assert analysis["summaries"]["b"]["terminates"] == "may_diverge"
        assert analysis["summaries"]["c"]["terminates"] == "may_diverge"

    def test_terminating_functions(self):
        """Test terminating functions have Always status."""
        source = """
def foo():
    return 1

def bar():
    foo()
    return 2
"""
        analysis = analyze_interprocedural(source)

        assert analysis["summaries"]["foo"]["terminates"] == "always"
        assert analysis["summaries"]["bar"]["terminates"] == "always"
        assert len(analysis["diverging_functions"]) == 0

    def test_call_graph_built_correctly(self):
        """Test call graph captures function calls."""
        source = """
def a():
    b()
    c()

def b():
    c()

def c():
    pass
"""
        analysis = analyze_interprocedural(source)

        assert "b" in analysis["call_graph"]["a"]
        assert "c" in analysis["call_graph"]["a"]
        assert "c" in analysis["call_graph"]["b"]
        assert analysis["call_graph"]["c"] == []

    def test_external_calls_not_flagged(self):
        """Test calls to external functions are not flagged."""
        source = """
def foo():
    print("hello")
    external_function()
    return 1
"""
        analysis = analyze_interprocedural(source)

        # foo should terminate (external calls not considered)
        assert analysis["summaries"]["foo"]["terminates"] == "always"

    def test_class_method_propagation(self):
        """Test propagation works for class methods."""
        source = """
class Foo:
    def infinite(self):
        while True:
            pass

    def caller(self):
        self.infinite()
"""
        analysis = analyze_interprocedural(source)

        # Both methods should be may_diverge
        assert analysis["summaries"]["Foo.infinite"]["terminates"] == "may_diverge"
        assert analysis["summaries"]["Foo.caller"]["terminates"] == "may_diverge"

    def test_analyze_cfg_interprocedural(self):
        """Test combined interprocedural analysis function."""
        source = """
def infinite():
    while True:
        pass

def caller():
    infinite()
"""
        results = analyze_cfg_interprocedural(source)
        assert len(results) == 2

        # Find caller result
        caller_result = next(r for r in results if r["function_name"] == "caller")
        assert caller_result["calls_diverging"] is True
        assert caller_result["diverging_callee"] == "infinite"

        # Find infinite result
        infinite_result = next(r for r in results if r["function_name"] == "infinite")
        assert infinite_result["has_infinite_loop"] is True
        assert infinite_result["calls_diverging"] is False

    def test_partial_diverging(self):
        """Test mix of diverging and non-diverging functions."""
        source = """
def infinite():
    while True:
        pass

def terminating():
    return 42

def calls_infinite():
    infinite()

def calls_terminating():
    terminating()
"""
        analysis = analyze_interprocedural(source)

        assert "infinite" in analysis["diverging_functions"]
        assert "calls_infinite" in analysis["diverging_functions"]
        assert "terminating" not in analysis["diverging_functions"]
        assert "calls_terminating" not in analysis["diverging_functions"]

    def test_itertools_cycle_propagation(self):
        """Test itertools.cycle infinite loop propagates through calls."""
        source = """
def infinite_iter():
    for x in itertools.cycle([1, 2, 3]):
        print(x)

def caller():
    infinite_iter()
"""
        results = analyze_cfg_interprocedural(source)

        caller_result = next(r for r in results if r["function_name"] == "caller")
        assert caller_result["calls_diverging"] is True


class TestCrossFileAnalysis:
    """Tests for cross-file interprocedural infinite loop detection (Phase 2)."""

    def test_cross_file_basic(self):
        """Test cross-file infinite loop propagation."""
        files = [
            ("module_a.py", """
def infinite_loop():
    while True:
        pass
"""),
            ("module_b.py", """
def caller():
    infinite_loop()
"""),
        ]

        # Simulate TypeInference call graph
        call_graph = {
            "module_b.caller": ["module_a.infinite_loop"]
        }

        analysis = analyze_cross_file(files, call_graph)

        # Both should be diverging
        assert any("infinite_loop" in f for f in analysis["all_diverging"])
        assert any("caller" in f for f in analysis["all_diverging"])

    def test_cross_file_chain(self):
        """Test propagation through cross-file call chain."""
        files = [
            ("a.py", "def func_a():\n    func_b()\n"),
            ("b.py", "def func_b():\n    func_c()\n"),
            ("c.py", "def func_c():\n    while True:\n        pass\n"),
        ]

        call_graph = {
            "a.func_a": ["b.func_b"],
            "b.func_b": ["c.func_c"],
        }

        analysis = analyze_cross_file(files, call_graph)

        # All three should be diverging
        assert any("func_a" in f for f in analysis["all_diverging"])
        assert any("func_b" in f for f in analysis["all_diverging"])
        assert any("func_c" in f for f in analysis["all_diverging"])

    def test_cross_file_terminating(self):
        """Test terminating functions are not flagged."""
        files = [
            ("a.py", "def foo():\n    return 1\n"),
            ("b.py", "def bar():\n    foo()\n    return 2\n"),
        ]

        call_graph = {"b.bar": ["a.foo"]}

        analysis = analyze_cross_file(files, call_graph)

        # No diverging functions
        assert len(analysis["all_diverging"]) == 0

    def test_cross_file_external_ignored(self):
        """Test external calls are not considered for divergence."""
        files = [
            ("a.py", "def foo():\n    external_lib.call()\n    return 1\n"),
        ]

        call_graph = {"a.foo": ["external:external_lib.call"]}

        analysis = analyze_cross_file(files, call_graph)

        # foo should not be diverging
        assert len(analysis["all_diverging"]) == 0

    def test_cross_file_file_results_structure(self):
        """Test the file_results structure is correct."""
        files = [
            ("a.py", """
def infinite():
    while True:
        pass
"""),
            ("b.py", """
def caller():
    infinite()
"""),
        ]

        call_graph = {"b.caller": ["a.infinite"]}

        analysis = analyze_cross_file(files, call_graph)

        # Check file_results structure
        assert "a.py" in analysis["file_results"]
        assert "b.py" in analysis["file_results"]

        # Check a.py results
        a_results = analysis["file_results"]["a.py"]
        assert len(a_results) == 1
        assert a_results[0]["function_name"] == "infinite"
        assert a_results[0]["has_infinite_loop"] is True

        # Check b.py results
        b_results = analysis["file_results"]["b.py"]
        assert len(b_results) == 1
        assert b_results[0]["function_name"] == "caller"
        assert b_results[0]["calls_diverging"] is True
        assert a_results[0]["function_name"] in b_results[0]["diverging_callee"]
