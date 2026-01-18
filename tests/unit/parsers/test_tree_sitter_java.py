"""Tests for tree-sitter Java parser."""

import pytest
import tempfile
from pathlib import Path

# Skip all tests if tree-sitter-java not available
pytestmark = pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_java", reason="tree-sitter-java not installed"),
    reason="tree-sitter-java not available"
)


@pytest.fixture
def java_parser():
    """Create a Java parser."""
    from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
    return TreeSitterJavaParser()


class TestTreeSitterJavaParser:
    """Test TreeSitterJavaParser functionality."""

    def test_parser_initialization(self, java_parser):
        """Test parser can be initialized."""
        assert java_parser.language_name == "java"
        assert java_parser.adapter is not None

    def test_parse_simple_class(self, java_parser):
        """Test parsing a simple Java class."""
        source = '''public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
    }
}
'''
        tree = java_parser.adapter.parse(source)

        assert tree.node_type == "program"
        classes = tree.find_all("class_declaration")
        assert len(classes) == 1

    def test_parse_class_with_methods(self, java_parser):
        """Test parsing a class with methods."""
        source = '''public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public int subtract(int a, int b) {
        return a - b;
    }
}
'''
        tree = java_parser.adapter.parse(source)

        classes = tree.find_all("class_declaration")
        assert len(classes) == 1

    def test_extract_entities_from_class(self, java_parser):
        """Test entity extraction from Java class."""
        source = '''public class UserService {
    private List<User> users;

    public User getUser(String id) {
        return users.stream().filter(u -> u.getId().equals(id)).findFirst().orElse(null);
    }

    public void addUser(User user) {
        users.add(user);
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            # Should have: FileEntity, ClassEntity, 2x FunctionEntity
            entity_types = [e.__class__.__name__ for e in entities]
            assert "FileEntity" in entity_types
            assert "ClassEntity" in entity_types
            assert entity_types.count("FunctionEntity") == 2

            # Check class name
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "UserService"

            # Check method names
            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert func_names == {"getUser", "addUser"}
        finally:
            Path(temp_path).unlink()

    def test_extract_inheritance(self, java_parser):
        """Test inheritance extraction."""
        source = '''public class Animal {
    protected String name;
}

public class Dog extends Animal {
    public void bark() {
        System.out.println("Woof!");
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)
            relationships = java_parser.extract_relationships(tree, temp_path, entities)

            # Find INHERITS relationship
            inherits_rels = [r for r in relationships if r.rel_type.value == "INHERITS"]
            assert len(inherits_rels) == 1

            # Check inheritance target
            assert inherits_rels[0].target_id.endswith("Animal") or inherits_rels[0].target_id == "Animal"
        finally:
            Path(temp_path).unlink()

    def test_extract_interface_implementation(self, java_parser):
        """Test interface implementation extraction."""
        source = '''public interface Runnable {
    void run();
}

public class Task implements Runnable {
    @Override
    public void run() {
        System.out.println("Running task");
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)
            relationships = java_parser.extract_relationships(tree, temp_path, entities)

            # Find INHERITS relationship (implements creates INHERITS too)
            inherits_rels = [r for r in relationships if r.rel_type.value == "INHERITS"]
            assert len(inherits_rels) >= 1

            # Task should inherit from Runnable
            task_inherits = [r for r in inherits_rels if "Task" in r.source_id]
            assert len(task_inherits) >= 1
        finally:
            Path(temp_path).unlink()

    def test_extract_imports(self, java_parser):
        """Test import extraction."""
        source = '''import java.util.List;
import java.util.ArrayList;
import java.io.*;

public class Service {
    private List<String> items;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)
            relationships = java_parser.extract_relationships(tree, temp_path, entities)

            # Find IMPORTS relationships
            import_rels = [r for r in relationships if r.rel_type.value == "IMPORTS"]
            assert len(import_rels) >= 3

            # Check import targets
            import_targets = {r.target_id for r in import_rels}
            assert any("java.util.List" in t for t in import_targets)
            assert any("java.util.ArrayList" in t for t in import_targets)
        finally:
            Path(temp_path).unlink()

    def test_extract_interface(self, java_parser):
        """Test interface extraction."""
        source = '''public interface Repository<T> {
    T findById(String id);
    List<T> findAll();
    void save(T entity);
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            # Interface should be extracted as ClassEntity
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "Repository"
        finally:
            Path(temp_path).unlink()

    def test_extract_enum(self, java_parser):
        """Test enum extraction."""
        source = '''public enum Status {
    PENDING,
    ACTIVE,
    COMPLETED;

    public boolean isFinished() {
        return this == COMPLETED;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            # Enum should be extracted as ClassEntity
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "Status"

            # Should have method
            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            assert len(func_entities) == 1
            assert func_entities[0].name == "isFinished"
        finally:
            Path(temp_path).unlink()

    def test_complexity_calculation(self, java_parser):
        """Test cyclomatic complexity calculation."""
        source = '''public class ComplexClass {
    public String complexMethod(int x) {
        if (x > 0) {
            if (x > 10) {
                return "big";
            } else {
                return "medium";
            }
        } else if (x < 0) {
            return "negative";
        }
        return "zero";
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            assert len(func_entities) == 1

            # Base complexity (1) + 3 if statements + 1 else
            assert func_entities[0].complexity >= 3
        finally:
            Path(temp_path).unlink()


class TestJavadocExtraction:
    """Tests for Javadoc comment extraction."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_javadoc_on_method(self, java_parser):
        """Test Javadoc extraction from method."""
        source = '''public class Calculator {
    /**
     * Calculates the sum of two numbers.
     * @param a First number
     * @param b Second number
     * @return The sum of a and b
     */
    public int add(int a, int b) {
        return a + b;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'add'), None)
            assert func is not None
            assert func.docstring is not None
            assert "Calculates the sum" in func.docstring
            assert "@param a" in func.docstring
            assert "@return" in func.docstring
        finally:
            Path(temp_path).unlink()

    def test_javadoc_on_class(self, java_parser):
        """Test Javadoc extraction from class."""
        source = '''/**
 * Represents a user in the system.
 * @author John Doe
 * @version 1.0
 */
public class User {
    private String name;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            cls = next((e for e in entities if e.name == 'User'), None)
            assert cls is not None
            assert cls.docstring is not None
            assert "Represents a user" in cls.docstring
        finally:
            Path(temp_path).unlink()

    def test_no_javadoc_returns_none(self, java_parser):
        """Test that methods without Javadoc return None for docstring."""
        source = '''public class NoDoc {
    public int noDoc(int x) {
        return x * 2;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'noDoc'), None)
            assert func is not None
            assert func.docstring is None
        finally:
            Path(temp_path).unlink()

    def test_regular_comment_not_javadoc(self, java_parser):
        """Test that regular comments are not treated as Javadoc."""
        source = '''public class RegularComment {
    // This is a regular comment
    public int regularComment(int x) {
        return x;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'regularComment'), None)
            assert func is not None
            # Regular // comments should not be extracted as docstrings
            assert func.docstring is None
        finally:
            Path(temp_path).unlink()


class TestAnnotationExtraction:
    """Tests for Java annotation extraction."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_override_annotation(self, java_parser):
        """Test @Override annotation extraction."""
        source = '''public class Child extends Parent {
    @Override
    public void method() {
        // implementation
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'method'), None)
            assert func is not None
            assert "Override" in func.decorators
        finally:
            Path(temp_path).unlink()

    def test_multiple_annotations(self, java_parser):
        """Test multiple annotations extraction."""
        source = '''public class Service {
    @Deprecated
    @SuppressWarnings("unchecked")
    public void oldMethod() {
        // deprecated implementation
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'oldMethod'), None)
            assert func is not None
            assert "Deprecated" in func.decorators
        finally:
            Path(temp_path).unlink()


class TestAsyncDetection:
    """Tests for async/reactive pattern detection."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_completable_future_detected_as_async(self, java_parser):
        """Test that CompletableFuture return type is detected as async."""
        source = '''import java.util.concurrent.CompletableFuture;

public class AsyncService {
    public CompletableFuture<String> fetchAsync() {
        return CompletableFuture.supplyAsync(() -> "result");
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'fetchAsync'), None)
            assert func is not None
            assert func.is_async is True
        finally:
            Path(temp_path).unlink()

    def test_void_not_async(self, java_parser):
        """Test that void methods are not detected as async."""
        source = '''public class SyncService {
    public void doSync() {
        System.out.println("sync");
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'doSync'), None)
            assert func is not None
            assert func.is_async is False
        finally:
            Path(temp_path).unlink()


class TestMethodCallExtraction:
    """Tests for method call extraction."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_simple_method_call(self, java_parser):
        """Test simple method call extraction."""
        source = '''public class Main {
    public void process() {
        System.out.println("hello");
        doSomething();
    }

    private void doSomething() {
        // implementation
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)
            relationships = java_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            # Should detect println or doSomething
            assert len(called_names) > 0
        finally:
            Path(temp_path).unlink()

    def test_chained_method_calls(self, java_parser):
        """Test chained method call extraction."""
        source = '''import java.util.List;

public class Processor {
    public void process(List<String> items) {
        items.stream().filter(s -> s.length() > 0).map(String::toUpperCase).forEach(System.out::println);
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)
            relationships = java_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            # Should detect some of the chained methods
            assert len(called_names) >= 1
        finally:
            Path(temp_path).unlink()


class TestConstructorExtraction:
    """Tests for constructor extraction."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_constructor_extracted(self, java_parser):
        """Test that constructors are extracted as methods."""
        source = '''public class Person {
    private String name;

    public Person(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}

            # Should have constructor and getter
            assert "Person" in func_names  # Constructor
            assert "getName" in func_names  # Method
        finally:
            Path(temp_path).unlink()


class TestReturnTypeDetection:
    """Tests for return statement detection."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_void_method_no_return(self, java_parser):
        """Test void method has_return is False."""
        source = '''public class VoidMethods {
    public void noReturn() {
        System.out.println("no return");
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'noReturn'), None)
            assert func is not None
            assert func.has_return is False
        finally:
            Path(temp_path).unlink()

    def test_method_with_return(self, java_parser):
        """Test method with return statement has_return is True."""
        source = '''public class ReturnMethods {
    public int withReturn(int x) {
        return x * 2;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'withReturn'), None)
            assert func is not None
            assert func.has_return is True
        finally:
            Path(temp_path).unlink()


class TestNestingLevelTracking:
    """Tests for nesting level calculation."""

    @pytest.fixture
    def java_parser(self):
        """Create a Java parser."""
        from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
        return TreeSitterJavaParser()

    def test_top_level_class(self, java_parser):
        """Test that top-level class has nesting level 0."""
        source = '''public class TopLevel {
    public void method() {}
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            cls = next((e for e in entities if e.name == 'TopLevel'), None)
            assert cls is not None
            assert cls.nesting_level == 0
        finally:
            Path(temp_path).unlink()

    def test_multiple_top_level_classes(self, java_parser):
        """Test multiple top-level classes all have nesting level 0."""
        source = '''class First {
    void foo() {}
}

class Second {
    void bar() {}
}

class Third {
    void baz() {}
}
'''
        with tempfile.NamedTemporaryFile(suffix='.java', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = java_parser.parse(temp_path)
            entities = java_parser.extract_entities(tree, temp_path)

            classes = [e for e in entities if e.__class__.__name__ == 'ClassEntity']
            assert len(classes) == 3

            for cls in classes:
                assert cls.nesting_level == 0, f"Class {cls.name} should have nesting level 0"
        finally:
            Path(temp_path).unlink()
