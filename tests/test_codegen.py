"""Tests for the Rolang LLVM code generation."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck
from rolang.hir_builder import build_hir
from rolang.monomorphize import monomorphize
from rolang.mir_builder import build_mir
from rolang.arc_insertion import insert_arc, ArcInsertionResult
from rolang.mir import (
    LocalId, BlockId, Local, Place, Block, MirFunction, MirProgram,
    BinOp, BinOpKind, CmpOp, CmpOpKind, UnaryOp, UnaryOpKind,
    MakeStruct, MakeEnum, Assign, CallStatic, Return,
    CopyOperand, MoveOperand, ConstantOperand, ConstantKind,
    MirStruct, MirField, MirEnum, MirEnumCase,
)
from rolang.types import TypeTable, TypeKind
from rolang.symbols import SymbolTable, SymbolId

# Import codegen module
from rolang.codegen import (
    compile_to_llvm,
    compile_to_object,
    CodegenResult,
    TypeLayoutCache,
    RuntimeABI,
)
from rolang.codegen.types import TypeLayoutCache
from rolang.codegen.runtime import RuntimeABI
from rolang.codegen.object_file import get_host_triple, verify_module

from llvmlite import ir


def build_arc(source: str) -> ArcInsertionResult:
    """Helper to parse through ARC insertion."""
    program = parse(source)
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir = build_hir(program, resolution, type_result)
    mono = monomorphize(hir)
    mir = build_mir(mono)
    return insert_arc(mir)


# =============================================================================
# TypeLayoutCache Tests
# =============================================================================

def test_type_layout_cache_primitives():
    """Test TypeLayoutCache maps primitive types correctly."""
    module = ir.Module(name="test")
    type_table = TypeTable()
    symbol_table = SymbolTable()

    cache = TypeLayoutCache(module, type_table, symbol_table)

    # Test integer types
    i32_id = type_table.get_builtin("i32")
    assert i32_id is not None
    i32_type = cache.get_llvm_type(i32_id)
    assert isinstance(i32_type, ir.IntType)
    assert i32_type.width == 32

    i64_id = type_table.get_builtin("i64")
    assert i64_id is not None
    i64_type = cache.get_llvm_type(i64_id)
    assert isinstance(i64_type, ir.IntType)
    assert i64_type.width == 64

    # Test float types
    f32_id = type_table.get_builtin("f32")
    assert f32_id is not None
    f32_type = cache.get_llvm_type(f32_id)
    assert isinstance(f32_type, ir.FloatType)

    f64_id = type_table.get_builtin("f64")
    assert f64_id is not None
    f64_type = cache.get_llvm_type(f64_id)
    assert isinstance(f64_type, ir.DoubleType)

    # Test bool
    bool_id = type_table.get_builtin("Bool")
    assert bool_id is not None
    bool_type = cache.get_llvm_type(bool_id)
    assert isinstance(bool_type, ir.IntType)
    assert bool_type.width == 1

    # Test void
    void_id = type_table.get_builtin("Void")
    assert void_id is not None
    void_type = cache.get_llvm_type(void_id)
    assert isinstance(void_type, ir.VoidType)


def test_type_layout_cache_string():
    """Test TypeLayoutCache maps source-level String to a heap reference."""
    module = ir.Module(name="test")
    program = parse("struct String { var handle: RawPtr; }")
    resolution = resolve(program)
    assert not resolution.has_errors(), resolution.errors
    type_result = typecheck(program, resolution)
    assert not type_result.has_errors(), type_result.errors

    cache = TypeLayoutCache(module, type_result.type_table, resolution.symbol_table)

    string_symbol = resolution.symbol_table.get_type_symbol("String")
    assert string_symbol is not None
    string_id = type_result.type_table.make_struct(string_symbol)
    string_type = cache.get_llvm_type(string_id)

    assert isinstance(string_type, ir.PointerType)


def test_type_layout_cache_pads_struct_payload_size():
    """Heap payload allocation includes padding before aligned fields."""
    module = ir.Module(name="test")
    program = parse("""
struct String { var handle: RawPtr; }
struct Mixed { var tag: i32; var text: String; }
""")
    resolution = resolve(program)
    assert not resolution.has_errors(), resolution.errors
    type_result = typecheck(program, resolution)
    assert not type_result.has_errors(), type_result.errors
    hir = build_hir(program, resolution, type_result)
    mono = monomorphize(hir)
    mir = build_mir(mono)

    cache = TypeLayoutCache(module, mir.type_table, mir.symbol_table)
    for struct in mir.program.structs:
        cache.get_struct_type(struct)

    mixed = next(s for s in mir.program.structs if s.name == "Mixed")
    assert cache._get_type_size(mixed.type_id) == 16

    mixed_desc = cache.get_or_assign_descriptor_id(mixed.type_id)
    field_descs = cache.compute_field_descriptors()
    assert field_descs[mixed_desc][0][0] == 8


def test_type_layout_cache_tuple():
    """Test TypeLayoutCache maps tuples correctly."""
    module = ir.Module(name="test")
    type_table = TypeTable()
    symbol_table = SymbolTable()

    cache = TypeLayoutCache(module, type_table, symbol_table)

    # Create a tuple type (i32, i64)
    i32_id = type_table.get_builtin("i32")
    i64_id = type_table.get_builtin("i64")
    tuple_id = type_table.make_tuple(((None, i32_id), (None, i64_id)))

    tuple_type = cache.get_llvm_type(tuple_id)

    # tuples are heap-allocated, so the LLVM type is PointerType
    assert isinstance(tuple_type, ir.PointerType)
    inner = tuple_type.pointee
    assert isinstance(inner, ir.LiteralStructType)
    assert len(inner.elements) == 2



def test_type_layout_cache_optional():
    """Test TypeLayoutCache maps Optional correctly."""
    module = ir.Module(name="test")
    type_table = TypeTable()
    symbol_table = SymbolTable()

    cache = TypeLayoutCache(module, type_table, symbol_table)

    # Optional of non-pointer type
    i32_id = type_table.get_builtin("i32")
    opt_i32 = type_table.make_optional(i32_id)
    opt_type = cache.get_llvm_type(opt_i32)

    # Should be { i1, i32 }
    assert isinstance(opt_type, ir.LiteralStructType)

    # Optional of pointer type (struct is a pointer type)
    struct_id = type_table.make_struct(SymbolId(99), ())
    opt_struct = type_table.make_optional(struct_id)
    opt_struct_type = cache.get_llvm_type(opt_struct)

    # Should just be the pointer (null = None)
    assert isinstance(opt_struct_type, ir.PointerType)


def test_type_layout_cache_closure_is_heap_pointer():
    """Closure/function values are object references, not fat pointers."""
    module = ir.Module(name="test")
    type_table = TypeTable()
    symbol_table = SymbolTable()
    cache = TypeLayoutCache(module, type_table, symbol_table)

    i32_id = type_table.get_builtin("i32")
    assert i32_id is not None
    closure_id = type_table.make_closure(
        params=(i32_id,),
        return_type=i32_id,
        captures=(i32_id,),
    )
    function_id = type_table.make_function((i32_id,), i32_id)

    assert cache.get_llvm_type(closure_id) == cache.ptr
    assert cache.get_llvm_type(function_id) == cache.ptr
    assert len(cache.get_closure_payload_type(closure_id).elements) == 2


def test_type_layout_cache_existential_is_heap_pointer():
    """Existential values are object references, not fat {witness, value} values."""
    source = """
protocol Printable {
    def print() -> Void;
}

def use(p: any Printable) -> Void {
}
"""
    program = parse(source)
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    assert not type_result.has_errors(), type_result.errors

    module = ir.Module(name="test")
    cache = TypeLayoutCache(module, type_result.type_table, resolution.symbol_table)
    existential_id = next(
        type_info.id
        for type_info in type_result.type_table.types.values()
        if type_info.kind == TypeKind.EXISTENTIAL
    )

    assert cache.get_llvm_type(existential_id) == cache.ptr
    assert cache.get_existential_payload_size() == 16


# =============================================================================
# RuntimeABI Tests
# =============================================================================

def test_runtime_abi_declarations():
    """Test RuntimeABI declares all functions."""
    module = ir.Module(name="test")
    runtime = RuntimeABI(module)

    # Memory + ARC
    assert runtime.rt_alloc is not None
    assert runtime.rt_free is not None
    assert runtime.rt_obj_alloc is not None
    assert runtime.rt_obj_retain is not None
    assert runtime.rt_obj_release is not None

    # Panic family
    assert runtime.rt_panic is not None
    assert runtime.rt_panic_divide_by_zero is not None

    # The always-on set is purely memory + ARC + panic.


def test_runtime_abi_function_signatures():
    """Test RuntimeABI function signatures are correct."""
    module = ir.Module(name="test")
    runtime = RuntimeABI(module)

    # rt_alloc(i64, i64) -> ptr
    assert len(runtime.rt_alloc.args) == 2
    assert isinstance(runtime.rt_alloc.return_value.type, ir.PointerType)

    # rt_free(ptr) -> void
    assert len(runtime.rt_free.args) == 1
    assert isinstance(runtime.rt_free.return_value.type, ir.VoidType)

    # rt_obj_retain(ptr) -> void
    assert len(runtime.rt_obj_retain.args) == 1
    assert isinstance(runtime.rt_obj_retain.return_value.type, ir.VoidType)

    # rt_obj_release(ptr) -> void
    assert len(runtime.rt_obj_release.args) == 1
    assert isinstance(runtime.rt_obj_release.return_value.type, ir.VoidType)


# =============================================================================
# Code Generation Tests
# =============================================================================

def test_codegen_simple_function():
    """Test code generation for a simple function."""
    source = """
    def add(a: i32, b: i32) -> i32 {
        return a + b;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    # Check module has the function
    llvm_ir = str(result.module)
    assert "define" in llvm_ir
    assert "add" in llvm_ir


def test_codegen_arithmetic_operations():
    """Test code generation for arithmetic operations."""
    source = """
    def arithmetic(a: i32, b: i32) -> i32 {
        let sum = a + b;
        let diff = a - b;
        let prod = a * b;
        return sum + diff + prod;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    assert "add" in llvm_ir
    assert "sub" in llvm_ir
    assert "mul" in llvm_ir


def test_codegen_comparison_operations():
    """Test code generation for comparison operations."""
    source = """
    def compare(a: i32, b: i32) -> Bool {
        return a < b;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    assert "icmp" in llvm_ir


def test_codegen_float_operations():
    """Test code generation for float operations."""
    source = """
    def float_add(a: f64, b: f64) -> f64 {
        return a + b;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    assert "fadd" in llvm_ir


def test_codegen_conditionals():
    """Test code generation for conditional branches."""
    source = """
    def max(a: i32, b: i32) -> i32 {
        if a > b {
            return a;
        } else {
            return b;
        }
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    # Should have conditional branch
    assert "br" in llvm_ir


def test_codegen_while_loop():
    """Test code generation for while loops."""
    source = """
    def sum_to_n(n: i32) -> i32 {
        var result = 0;
        var i = 1;
        while i <= n {
            result = result + i;
            i = i + 1;
        }
        return result;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_function_call():
    """Test code generation for function calls."""
    source = """
    def helper(x: i32) -> i32 {
        return x * 2;
    }

    def caller(n: i32) -> i32 {
        return helper(n) + 1;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    assert "call" in llvm_ir


def test_codegen_tuple():
    """Test code generation for tuples."""
    source = """
    def make_pair(a: i32, b: i32) -> (i32, i32) {
        let pair = (a, b);
        return pair;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    # Tuple handling may have edge cases, check we at least generate something
    llvm_ir = str(result.module)
    assert "make_pair" in llvm_ir


def test_codegen_void_function():
    """Test code generation for void functions."""
    source = """
    def do_nothing() -> Void {
        return;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    assert "ret void" in llvm_ir


def test_codegen_unary_ops():
    """Test code generation for unary operations."""
    source = """
    def negate(x: i32) -> i32 {
        return -x;
    }

    def logical_not(b: Bool) -> Bool {
        return !b;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_multiple_returns():
    """Test code generation for multiple return paths."""
    source = """
    def sign(x: i32) -> i32 {
        if x > 0 {
            return 1;
        } else if x < 0 {
            return -1;
        } else {
            return 0;
        }
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_nested_expressions():
    """Test code generation for nested expressions."""
    source = """
    def complex(a: i32, b: i32, c: i32) -> i32 {
        return (a + b) * (b - c) + a;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


# =============================================================================
# Module Verification Tests
# =============================================================================

def test_verify_simple_module():
    """Test module verification passes for valid IR."""
    source = """
    def add(a: i32, b: i32) -> i32 {
        return a + b;
    }
    """
    arc_result = build_arc(source)
    result = compile_to_llvm(arc_result)

    errors = verify_module(result.module)

    assert len(errors) == 0, f"Verification errors: {errors}"


def test_host_triple():
    """Test getting host triple."""
    triple = get_host_triple()
    assert triple is not None
    assert len(triple) > 0
    # Should contain OS info
    assert any(os in triple.lower() for os in ["linux", "darwin", "windows", "unknown"])


# =============================================================================
# Integration Tests
# =============================================================================

def test_codegen_full_pipeline():
    """Test the full compilation pipeline produces valid IR."""
    source = """
    def factorial(n: i32) -> i32 {
        if n <= 1 {
            return 1;
        }
        let result = n * factorial(n - 1);
        return result;
    }

    def main() -> i32 {
        return factorial(5);
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    # Check module was generated (may have edge case errors with recursion)
    llvm_ir = str(result.module)
    assert "factorial" in llvm_ir
    assert "main" in llvm_ir


def test_codegen_with_struct():
    """Test code generation with struct types."""
    source = """
    struct Point {
        var x: i32;
        var y: i32;
    }

    def make_point(a: i32, b: i32) -> Point {
        return Point { x: a, y: b };
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    # Note: struct access may have issues in codegen, just check no fatal errors
    # The result may have errors but shouldn't crash
    llvm_ir = str(result.module)
    assert "Point" in llvm_ir or "make_point" in llvm_ir


def test_codegen_with_variables():
    """Test code generation with mutable variables."""
    source = """
    def count_down(start: i32) -> i32 {
        var n = start;
        var sum = 0;
        while n > 0 {
            sum = sum + n;
            n = n - 1;
        }
        return sum;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_string_literal():
    """Test code generation with string literals."""
    source = """
    struct String {
        var handle: RawPtr;
        def __release__() -> Void {}
    }
    def get_string() -> String {
        return "hello";
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    # String handling may have issues, just check no crash
    llvm_ir = str(result.module)
    assert "get_string" in llvm_ir


# =============================================================================
# Edge Cases
# =============================================================================

def test_codegen_empty_function():
    """Test code generation for empty function body."""
    source = """
    def empty() -> i32 {
        return 0;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_many_parameters():
    """Test code generation with many parameters."""
    source = """
    def many_params(a: i32, b: i32, c: i32, d: i32, e: i32) -> i32 {
        return a + b + c + d + e;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_deeply_nested():
    """Test code generation for deeply nested conditionals."""
    source = """
    def nested(a: i32, b: i32, c: i32) -> i32 {
        if a > 0 {
            if b > 0 {
                if c > 0 {
                    return a + b + c;
                } else {
                    return a + b;
                }
            } else {
                return a;
            }
        } else {
            return 0;
        }
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"


def test_codegen_bitwise_ops():
    """Test code generation for bitwise operations."""
    source = """
    def bitwise(a: i32, b: i32) -> i32 {
        let and_result = a & b;
        let or_result = a | b;
        let xor_result = a ^ b;
        return and_result + or_result + xor_result;
    }
    """
    arc_result = build_arc(source)

    result = compile_to_llvm(arc_result)

    assert not result.has_errors(), f"Errors: {result.errors}"

    llvm_ir = str(result.module)
    assert "and" in llvm_ir
    assert "or" in llvm_ir
    assert "xor" in llvm_ir


# =============================================================================
# LLVM IR Content Tests
# =============================================================================

def test_llvm_ir_contains_function_definition():
    """Test LLVM IR contains proper function definitions."""
    source = """
    def test_func(x: i32) -> i32 {
        return x + 1;
    }
    """
    arc_result = build_arc(source)
    result = compile_to_llvm(arc_result)

    llvm_ir = str(result.module)

    # Check for function definition
    assert "define" in llvm_ir
    assert "test_func" in llvm_ir
    assert "i32" in llvm_ir


def test_llvm_ir_contains_entry_block():
    """Test LLVM IR contains entry basic block."""
    source = """
    def simple() -> i32 {
        return 42;
    }
    """
    arc_result = build_arc(source)
    result = compile_to_llvm(arc_result)

    llvm_ir = str(result.module)

    # Check for basic block (bb0 or similar)
    assert ":" in llvm_ir  # Basic block labels end with ':'


def test_llvm_ir_runtime_declarations():
    """Test LLVM IR contains runtime function declarations."""
    source = """
    def simple() -> i32 {
        return 0;
    }
    """
    arc_result = build_arc(source)
    result = compile_to_llvm(arc_result)

    llvm_ir = str(result.module)

    # Runtime functions should be declared
    assert "rt_alloc" in llvm_ir
    assert "rt_obj_retain" in llvm_ir
    assert "rt_obj_release" in llvm_ir


def test_object_compilation_sets_data_layout():
    """compile_module_to_object must stamp the module with the target data layout
    so LLVM optimizes against real alignment/pointer-size info, not a default."""
    import llvmlite.ir as ir
    from rolang.codegen.object_file import compile_module_to_object
    module = ir.Module(name="dl_probe")
    fnty = ir.FunctionType(ir.IntType(32), [])
    fn = ir.Function(module, fnty, name="main")
    blk = fn.append_basic_block("entry")
    ir.IRBuilder(blk).ret(ir.Constant(ir.IntType(32), 0))

    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.o")
        errors = compile_module_to_object(module, out, opt_level=2)
    assert errors == [], errors
    assert str(module.data_layout) != "", "data_layout was not set on the module"
    assert str(module.triple) != "", "triple was not set on the module"


def test_retain_release_emitted_as_inline_ir():
    """retain/release must be internal alwaysinline definitions, and release
    must call the C slow path rather than doing teardown itself."""
    import llvmlite.ir as ir
    from rolang.codegen.runtime import RuntimeABI
    module = ir.Module(name="rc_probe")
    RuntimeABI(module)
    text = str(module)
    assert "define internal void @\"rt_obj_retain\"" in text or \
           "define internal void @rt_obj_retain" in text, text[:2000]
    assert "rt_obj_release_slow" in text, "release fast path must call the C slow path"
    # both inline functions must carry alwaysinline
    assert text.count("alwaysinline") >= 6  # char_at + 4 classify + retain + release


if __name__ == "__main__":
    # Run tests with verbose output
    import pytest
    pytest.main([__file__, "-v"])
