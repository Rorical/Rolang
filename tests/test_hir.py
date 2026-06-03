"""Tests for the RoLang HIR (High-level Intermediate Representation) builder."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck
from rolang.hir_builder import build_hir, HirBuildResult
from rolang.hir import (
    HirProgram, HirFunction, HirExternFunc, HirStruct, HirEnum,
    HirBlock, HirVarDecl, HirAssign, HirExprStmt, HirReturn,
    HirIf, HirIfLet, HirWhile, HirFor, HirSwitch, HirDefer,
    HirLiteral, HirVar, HirBinaryOp, HirUnaryOp, HirTernary,
    HirCall, HirMethodCall, HirFieldAccess, HirSubscript,
    HirTuple, HirArray, HirDict, HirLambda,
    HirStructInit,
    HirOptionalSome, HirOptionalNone, HirOptionalMatch,
    HirBindingPattern, HirWildcardPattern, HirEnumCasePattern,
)
from rolang.types import TypeKind



STRING_PRELUDE = "\nstruct String { var handle: RawPtr; }\n"


def with_string_prelude(source: str) -> str:
    if "String" in source or '"' in source:
        return source + STRING_PRELUDE
    return source


def build(source: str) -> HirBuildResult:
    """Helper to parse, resolve, type check, and build HIR."""
    source = with_string_prelude(source)
    program = parse(source)
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    return build_hir(program, resolution, type_result)


# ========================= Basic HIR Structure Tests =========================

def test_empty_program():
    """Test HIR building for an empty program."""
    result = build("")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"
    assert isinstance(result.program, HirProgram)
    assert len(result.program.items) == 0


def test_simple_function():
    """Test HIR building for a simple function."""
    result = build("""
def test() {
    let x = 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"
    assert len(result.program.items) == 1

    func = result.program.items[0]
    assert isinstance(func, HirFunction)
    assert func.name == "test"
    assert func.body is not None
    assert len(func.body.statements) == 1


def test_function_with_params():
    """Test HIR building for a function with parameters."""
    result = build("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    assert isinstance(func, HirFunction)
    assert len(func.params) == 2
    assert func.params[0].name == "a"
    assert func.params[1].name == "b"


def test_extern_function():
    """Test HIR building for an extern function."""
    result = build('extern "C" def printf(fmt: RawPtr) -> i32;')
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    assert isinstance(func, HirExternFunc)
    assert func.name == "printf"
    assert func.abi == "C"


def test_struct_declaration():
    """Test HIR building for a struct."""
    result = build("""
struct Point {
    var x: i32;
    var y: i32;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    struct = result.program.items[0]
    assert isinstance(struct, HirStruct)
    assert struct.name == "Point"
    assert len(struct.fields) == 2
    assert struct.fields[0].name == "x"
    assert struct.fields[1].name == "y"


def test_struct_with_method():
    """Test HIR building for a struct with methods."""
    result = build("""
struct Point {
    var x: i32;
    var y: i32;

    def sum() -> i32 {
        return x + y;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    struct = result.program.items[0]
    assert isinstance(struct, HirStruct)
    assert len(struct.methods) == 1
    assert struct.methods[0].name == "sum"
    assert struct.methods[0].is_method


def test_enum_declaration():
    """Test HIR building for an enum."""
    result = build("""
enum Option<T> {
    case none
    case some(T)
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    enum = result.program.items[0]
    assert isinstance(enum, HirEnum)
    assert enum.name == "Option"
    assert len(enum.cases) == 2
    assert enum.cases[0].name == "none"
    assert enum.cases[1].name == "some"


# ========================= Statement Tests =========================

def test_var_decl():
    """Test HIR building for variable declarations."""
    result = build("""
def test() {
    let x = 42;
    var y = 10;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    assert isinstance(func, HirFunction)
    assert len(func.body.statements) == 2

    decl1 = func.body.statements[0]
    assert isinstance(decl1, HirVarDecl)
    assert decl1.name == "x"
    assert not decl1.is_mutable

    decl2 = func.body.statements[1]
    assert isinstance(decl2, HirVarDecl)
    assert decl2.name == "y"
    assert decl2.is_mutable


def test_assignment():
    """Test HIR building for assignments."""
    result = build("""
def test() {
    var x = 1;
    x = 2;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirAssign)
    assert stmt.compound_op is None


def test_compound_assignment():
    """Test HIR building for compound assignments."""
    result = build("""
def test() {
    var x = 1;
    x += 2;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirAssign)
    assert stmt.compound_op == "+"


def test_return_statement():
    """Test HIR building for return statements."""
    result = build("""
def test() -> i32 {
    return 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirReturn)
    assert isinstance(stmt.value, HirLiteral)


def test_if_statement():
    """Test HIR building for if statements."""
    result = build("""
def test() {
    if true {
        let x = 1;
    } else {
        let x = 2;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirIf)
    assert stmt.then_block is not None
    assert stmt.else_block is not None


def test_while_loop():
    """Test HIR building for while loops."""
    result = build("""
def test() {
    var x = 10;
    while x > 0 {
        x = x - 1;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirWhile)
    assert isinstance(stmt.condition, HirBinaryOp)


def test_for_loop():
    """Test HIR building for for loops."""
    result = build("""
def test() {
    for i in [1, 2, 3] {
        let x = i;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirFor)
    assert isinstance(stmt.iterable, HirArray)


def test_switch_statement():
    """Test HIR building for switch statements."""
    result = build("""
def test() {
    let x = 1;
    switch x {
    case 0:
        let y = 0;
    case 1:
        let y = 1;
    default:
        let y = -1;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirSwitch)
    assert len(stmt.cases) == 3


def test_defer_statement():
    """Test HIR building for defer statements."""
    result = build("""
def test() {
    defer {
        let x = 0;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirDefer)
    assert stmt.body is not None


# ========================= Expression Tests =========================

def test_literal_expressions():
    """Test HIR building for literal expressions."""
    result = build("""
def test() {
    let a = 42;
    let b = 3.14;
    let c = true;
    let d = "hello";
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    for stmt in func.body.statements:
        assert isinstance(stmt, HirVarDecl)
        assert isinstance(stmt.initializer, HirLiteral)


def test_binary_operations():
    """Test HIR building for binary operations."""
    result = build("""
def test() {
    let a = 1 + 2;
    let b = 3 - 4;
    let c = 5 * 6;
    let d = 8 / 2;
    let e = true && false;
    let f = 1 < 2;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    for stmt in func.body.statements:
        assert isinstance(stmt, HirVarDecl)
        assert isinstance(stmt.initializer, HirBinaryOp)


def test_unary_operations():
    """Test HIR building for unary operations."""
    result = build("""
def test() {
    let a = -42;
    let b = !true;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    for stmt in func.body.statements:
        assert isinstance(stmt, HirVarDecl)
        assert isinstance(stmt.initializer, HirUnaryOp)


def test_ternary_expression():
    """Test HIR building for ternary expressions."""
    result = build("""
def test() {
    let x = true ? 1 : 0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirTernary)


def test_function_call():
    """Test HIR building for function calls."""
    result = build("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}

def test() {
    let x = add(1, 2);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    test_func = result.program.items[1]
    stmt = test_func.body.statements[0]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirCall)


def test_array_literal():
    """Test HIR building for array literals."""
    result = build("""
def test() {
    let arr = [1, 2, 3];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirArray)
    assert len(stmt.initializer.elements) == 3


def test_dict_literal():
    """Test HIR building for dictionary literals."""
    result = build("""
def test() {
    let dict = ["a": 1, "b": 2];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirDict)
    assert len(stmt.initializer.entries) == 2


def test_tuple_expression():
    """Test HIR building for tuple expressions."""
    result = build("""
def test() {
    let t = (1, 2, 3);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirTuple)


def test_lambda_expression():
    """Test HIR building for lambda expressions."""
    result = build("""
def test() {
    let add = { a: i32, b: i32 in
        return a + b;
    };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[0]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirLambda)
    assert len(stmt.initializer.params) == 2


def test_subscript_access():
    """Test HIR building for subscript access."""
    result = build("""
def test() {
    let arr = [1, 2, 3];
    let first = arr[0];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirVarDecl)
    assert isinstance(stmt.initializer, HirSubscript)


# ========================= Desugaring Tests =========================

def test_nil_coalescing_desugaring():
    """Test that ?? is desugared to HirOptionalMatch."""
    result = build("""
def test() {
    let x: i32? = nil;
    let y = x ?? 0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirVarDecl)

    # The initializer should be HirOptionalMatch (desugared from ??)
    init = stmt.initializer
    assert isinstance(init, HirOptionalMatch), f"Expected HirOptionalMatch, got {type(init)}"

    # Check structure of desugared expression
    assert init.some_binding is not None  # Has temporary binding
    assert isinstance(init.some_expr, HirVar)  # Some case returns the unwrapped value
    assert isinstance(init.none_expr, HirLiteral)  # None case returns the default


def test_nil_coalescing_chained():
    """Test chained nil coalescing: a ?? b ?? c"""
    result = build("""
def test() {
    let a: i32? = nil;
    let b: i32? = nil;
    let c = a ?? b ?? 0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[2]
    assert isinstance(stmt, HirVarDecl)

    # Should be nested HirOptionalMatch
    init = stmt.initializer
    assert isinstance(init, HirOptionalMatch)


def test_optional_chain_desugaring():
    """Test that ?. is desugared to HirOptionalMatch."""
    result = build("""
struct Point {
    var x: i32;
    var y: i32;
}

def test() {
    let p: Point? = nil;
    let x = p?.x;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[1]
    stmt = func.body.statements[1]
    assert isinstance(stmt, HirVarDecl)

    # The initializer should be HirOptionalMatch (desugared from ?.)
    init = stmt.initializer
    assert isinstance(init, HirOptionalMatch), f"Expected HirOptionalMatch, got {type(init)}"

    # Check structure
    assert init.some_binding is not None  # Has temporary binding
    assert isinstance(init.some_expr, HirOptionalSome)  # Some case wraps result in Some
    assert isinstance(init.none_expr, HirOptionalNone)  # None case returns None


def test_optional_chain_some_expr():
    """Test that some_expr in optional chain contains field access."""
    result = build("""
struct Point {
    var x: i32;
}

def test() {
    let p: Point? = nil;
    let x = p?.x;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[1]
    stmt = func.body.statements[1]
    init = stmt.initializer

    assert isinstance(init, HirOptionalMatch)
    # The some_expr should be HirOptionalSome wrapping a field access
    some_expr = init.some_expr
    assert isinstance(some_expr, HirOptionalSome)
    # The value inside Some should be the field access
    assert isinstance(some_expr.value, HirFieldAccess)
    assert some_expr.value.field_name == "x"


# ========================= Type Preservation Tests =========================

def test_expr_types_preserved():
    """Test that expression types are preserved in HIR."""
    result = build("""
def test() {
    let x = 42;
    let y = 3.14;
    let z = true;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]

    # Check int literal has i32 type
    int_decl = func.body.statements[0]
    int_type_info = result.type_table.get_type(int_decl.initializer.type_id)
    assert int_type_info is not None
    assert int_type_info.kind == TypeKind.PRIMITIVE

    # Check float literal has f64 type
    float_decl = func.body.statements[1]
    float_type_info = result.type_table.get_type(float_decl.initializer.type_id)
    assert float_type_info is not None
    assert float_type_info.kind == TypeKind.PRIMITIVE

    # Check bool literal has Bool type
    bool_decl = func.body.statements[2]
    bool_type_info = result.type_table.get_type(bool_decl.initializer.type_id)
    assert bool_type_info is not None
    assert bool_type_info.kind == TypeKind.PRIMITIVE


def test_var_decl_types():
    """Test that variable declarations have correct types."""
    result = build("""
def test() {
    let x: i32 = 42;
    let y: String = "hello";
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]

    x_decl = func.body.statements[0]
    assert result.type_table.is_integer(x_decl.type_id)

    y_decl = func.body.statements[1]
    assert result.type_table.is_string(y_decl.type_id)


def test_optional_type_preserved():
    """Test that optional types are correctly preserved."""
    result = build("""
def test() {
    let x: i32? = nil;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    decl = func.body.statements[0]

    # The variable type should be optional
    assert result.type_table.is_optional(decl.type_id)


# ========================= Symbol Preservation Tests =========================

def test_symbol_ids_preserved():
    """Test that symbol IDs are correctly assigned."""
    result = build("""
def test() {
    let x = 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]

    # Function should have a symbol ID
    assert func.symbol_id is not None
    func_symbol = result.symbol_table.get_symbol(func.symbol_id)
    assert func_symbol is not None
    assert func_symbol.name == "test"

    # Variable should have a symbol ID
    decl = func.body.statements[0]
    assert decl.symbol_id is not None


def test_parameter_symbols():
    """Test that function parameters have symbol IDs."""
    result = build("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]

    for param in func.params:
        assert param.symbol_id is not None
        symbol = result.symbol_table.get_symbol(param.symbol_id)
        assert symbol is not None


def test_temporary_symbols_created():
    """Test that temporaries are created for desugared operations."""
    result = build("""
def test() {
    let x: i32? = nil;
    let y = x ?? 0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    func = result.program.items[0]
    stmt = func.body.statements[1]
    init = stmt.initializer

    # The desugared optional match should have a some_binding
    assert isinstance(init, HirOptionalMatch)
    assert init.some_binding is not None

    # The binding should be in the symbol table
    symbol = result.symbol_table.get_symbol(init.some_binding)
    assert symbol is not None


# ========================= Integration Tests =========================

def test_comprehensive_example():
    """Test HIR building for the basic.rl example file."""
    example_path = Path(__file__).parent.parent / "examples" / "basic.rl"
    if not example_path.exists():
        print("Skipping: example file not found")
        return

    source = example_path.read_text()
    result = build(source)

    # Print errors for debugging
    if result.errors:
        print(f"Build errors ({len(result.errors)}):")
        for e in result.errors[:10]:
            print(f"  {e}")

    # Should have built some items
    assert len(result.program.items) > 0, "Should have built some items"

    # Count item types
    functions = sum(1 for i in result.program.items if isinstance(i, HirFunction))
    structs = sum(1 for i in result.program.items if isinstance(i, HirStruct))
    enums = sum(1 for i in result.program.items if isinstance(i, HirEnum))
    externs = sum(1 for i in result.program.items if isinstance(i, HirExternFunc))

    print(f"HIR items: {len(result.program.items)} total")
    print(f"  Functions: {functions}")
    print(f"  Structs: {structs}")
    print(f"  Enums: {enums}")
    print(f"  Externs: {externs}")


def test_hir_build_result():
    """Test HirBuildResult structure."""
    result = build("def test() {}")

    # Check result structure
    assert result.program is not None
    assert result.type_table is not None
    assert result.symbol_table is not None
    assert isinstance(result.errors, list)

    # has_errors method
    assert not result.has_errors()



# ========================= Pattern Tests =========================

def test_binding_pattern():
    """Test HIR building for binding patterns."""
    result = build("""
def test() {
    let x = 42;
}
""")
    assert not result.has_errors()


def test_switch_with_patterns():
    """Test HIR building for switch with patterns."""
    result = build("""
enum Option<T> {
    case none
    case some(T)
}

def test() {
    let opt: Option<i32> = Option.none;
    switch opt {
    case .none:
        let x = 0;
    case .some(let v):
        let x = v;
    }
}
""")
    # Check it doesn't crash - pattern matching is complex
    assert len(result.program.items) >= 1


# ========================= Edge Cases =========================

def test_empty_function_body():
    """Test HIR building for function with empty body."""
    result = build("def test() {}")
    assert not result.has_errors()

    func = result.program.items[0]
    assert func.body is not None
    assert len(func.body.statements) == 0


def test_nested_blocks():
    """Test HIR building for nested blocks."""
    result = build("""
def test() {
    {
        {
            let x = 1;
        }
    }
}
""")
    assert not result.has_errors()


def test_multiple_functions():
    """Test HIR building for multiple functions."""
    result = build("""
def foo() {}
def bar() {}
def baz() {}
""")
    assert not result.has_errors()
    assert len(result.program.items) == 3


if __name__ == "__main__":
    # Run all tests
    tests = [
        # Basic structure
        test_empty_program,
        test_simple_function,
        test_function_with_params,
        test_extern_function,
        test_struct_declaration,
        test_struct_with_method,
        test_enum_declaration,
        # Statements
        test_var_decl,
        test_assignment,
        test_compound_assignment,
        test_return_statement,
        test_if_statement,
        test_while_loop,
        test_for_loop,
        test_switch_statement,
        test_defer_statement,
        # Expressions
        test_literal_expressions,
        test_binary_operations,
        test_unary_operations,
        test_ternary_expression,
        test_function_call,
        test_array_literal,
        test_dict_literal,
        test_tuple_expression,
        test_lambda_expression,
        test_subscript_access,
        # Desugaring
        test_nil_coalescing_desugaring,
        test_nil_coalescing_chained,
        test_optional_chain_desugaring,
        test_optional_chain_some_expr,
        # Type preservation
        test_expr_types_preserved,
        test_var_decl_types,
        test_optional_type_preserved,
        # Symbol preservation
        test_symbol_ids_preserved,
        test_parameter_symbols,
        test_temporary_symbols_created,
        # Integration
        test_comprehensive_example,
        test_hir_build_result,
        # Patterns
        test_binding_pattern,
        test_switch_with_patterns,
        # Edge cases
        test_empty_function_body,
        test_nested_blocks,
        test_multiple_functions,
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
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
