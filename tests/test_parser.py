"""Tests for the RoLang parser."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang import ast


def test_simple_function():
    """Test parsing a simple function."""
    source = """
def add(a: i32, b: i32) -> i32 {
    return a + b;
}
"""
    program = parse(source)
    assert isinstance(program, ast.Program)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.FuncDecl)

    func = program.items[0]
    assert func.name == "add"
    assert len(func.params) == 2
    assert func.params[0].internal_name == "a"
    assert func.params[1].internal_name == "b"


def test_struct_definition():
    """Test parsing a struct definition."""
    source = """
struct Point {
    var x: i32;
    var y: i32;
}
"""
    program = parse(source)
    assert isinstance(program, ast.Program)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.StructDecl)

    struct = program.items[0]
    assert struct.name == "Point"
    assert len(struct.members) == 2


def test_enum_definition():
    """Test parsing an enum definition."""
    source = """
enum Color {
    case red
    case green
    case blue
}
"""
    program = parse(source)
    assert isinstance(program, ast.Program)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.EnumDecl)

    enum = program.items[0]
    assert enum.name == "Color"


def test_generic_enum():
    """Test parsing a generic enum."""
    source = """
enum Option<T> {
    case none
    case some(T)
}
"""
    program = parse(source)
    assert isinstance(program, ast.Program)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.EnumDecl)

    enum = program.items[0]
    assert enum.name == "Option"
    assert len(enum.generic_params) == 1
    assert enum.generic_params[0].name == "T"


def test_protocol():
    """Test parsing a protocol definition."""
    source = """
protocol Show {
    def show() -> String;
}
"""
    program = parse(source)
    assert isinstance(program, ast.Program)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.ProtocolDecl)

    protocol = program.items[0]
    assert protocol.name == "Show"


def test_method_that_mutates_self():
    """A method can freely mutate `self` — no special modifier."""
    source = """
struct Counter {
    var value: i32;

    def increment() -> Void {
        self.value = self.value + 1;
    }
}
"""
    program = parse(source)
    struct = program.items[0]
    assert isinstance(struct, ast.StructDecl)
    method = struct.members[1]
    assert isinstance(method, ast.FuncDecl)
    assert method.name == "increment"


def test_variable_declaration():
    """Test parsing variable declarations."""
    source = """
def test() {
    let x: i32 = 42;
    var y = 10;
}
"""
    program = parse(source)
    func = program.items[0]
    assert isinstance(func.body, ast.Block)
    assert len(func.body.statements) == 2


def test_unsafe_block():
    """Test parsing unsafe blocks."""
    source = """
def test() {
    unsafe {
        let x = 1;
    }
}
"""
    program = parse(source)
    func = program.items[0]
    assert isinstance(func.body, ast.Block)
    block = func.body.statements[0]
    assert isinstance(block, ast.Block)
    assert block.is_unsafe


def test_if_statement():
    """Test parsing if statements."""
    source = """
def test() {
    if x > 0 {
        return 1;
    } else {
        return 0;
    }
}
"""
    program = parse(source)
    func = program.items[0]
    assert len(func.body.statements) == 1
    assert isinstance(func.body.statements[0], ast.IfStmt)


def test_while_loop():
    """Test parsing while loops."""
    source = """
def test() {
    while x > 0 {
        x = x - 1;
    }
}
"""
    program = parse(source)
    func = program.items[0]
    assert len(func.body.statements) == 1
    assert isinstance(func.body.statements[0], ast.WhileStmt)


def test_for_loop():
    """Test parsing for loops."""
    source = """
def test() {
    for i in items {
        let unused = i;
    }
}
"""
    program = parse(source)
    func = program.items[0]
    assert len(func.body.statements) == 1
    assert isinstance(func.body.statements[0], ast.ForStmt)


def test_switch_statement():
    """Test parsing switch statements."""
    source = """
def test() {
    switch value {
    case .none:
        return 0;
    case .some(let x):
        return x;
    }
}
"""
    program = parse(source)
    func = program.items[0]
    assert len(func.body.statements) == 1
    assert isinstance(func.body.statements[0], ast.SwitchStmt)


def test_expressions():
    """Test parsing various expressions."""
    source = """
def test() {
    let a = 1 + 2 * 3;
    let b = x && y || z;
    let c = a > b ? a : b;
    let d = opt ?? default_val;
}
"""
    program = parse(source)
    func = program.items[0]
    assert len(func.body.statements) == 4


def test_array_literal():
    """Test parsing array literals."""
    source = """
def test() {
    let arr = [1, 2, 3];
}
"""
    program = parse(source)
    func = program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, ast.VarDecl)
    assert isinstance(stmt.initializer, ast.ArrayLiteral)



def test_lambda():
    """Test parsing lambda expressions."""
    source = """
def test() {
    let f = { x: i32 in
        return x + 1;
    };
}
"""
    program = parse(source)
    func = program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, ast.VarDecl)
    assert isinstance(stmt.initializer, ast.Lambda)


def test_void_return_statement():
    """Test parsing an explicit return with no value."""
    source = """
def log() -> Void {
    return;
}
"""
    program = parse(source)
    func = program.items[0]
    stmt = func.body.statements[0]

    assert isinstance(stmt, ast.ReturnStmt)
    assert stmt.value is None


def test_extern_func():
    """Test parsing extern function declarations."""
    source = """
extern "C" def printf(fmt: RawPtr) -> i32;
"""
    program = parse(source)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.ExternFuncDecl)
    assert program.items[0].abi == "C"
    assert program.items[0].name == "printf"


def test_extension():
    """Test parsing extension declarations."""
    source = """
extension Point {
    def magnitude() -> f64 {
        return 0.0;
    }
}
"""
    program = parse(source)
    assert len(program.items) == 1
    assert isinstance(program.items[0], ast.ExtensionDecl)


def test_types():
    """Test parsing various type annotations."""
    source = """
def test(
    a: i32,
    b: [i32],
    c: [String: i32],
    d: Ref<Point>,
    e: Weak<Point>,
    f: Point?
) {
}
"""
    program = parse(source)
    func = program.items[0]
    assert len(func.params) == 6


if __name__ == "__main__":
    # Run all tests
    tests = [
        test_simple_function,
        test_struct_definition,
        test_enum_definition,
        test_generic_enum,
        test_protocol,
        test_variable_declaration,
        test_if_statement,
        test_while_loop,
        test_for_loop,
        test_switch_statement,
        test_expressions,
        test_array_literal,
        test_lambda,
        test_extern_func,
        test_extension,
        test_types,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}")
            print(f"      Error: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
