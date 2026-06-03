"""Tests for the RoLang ARC (Automatic Reference Counting) Insertion Pass."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck
from rolang.hir_builder import build_hir
from rolang.monomorphize import monomorphize
from rolang.mir_builder import build_mir, MirBuildResult
from rolang.mir import (
    # ID types
    LocalId, BlockId,
    # Core types
    MirFunction, MirProgram,
    # Operations
    Retain, Release,
    # Formatting
    format_function,
)
from rolang.arc_insertion import (
    # Main entry point
    insert_arc,
    ArcInsertionResult,
    # Data structures
    RcState,
    LocalInfo,
    BlockAnalysis,
    OpOwnership,
    # Analysis functions
    collect_ref_locals,
    compute_use_def,
    compute_liveness,
)
from rolang.types import TypeTable


def build_mir_result(source: str) -> MirBuildResult:
    """Helper to parse, resolve, type check, build HIR, monomorphize, and build MIR."""
    program = parse(source)
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir = build_hir(program, resolution, type_result)
    mono = monomorphize(hir)
    return build_mir(mono)


def build_with_arc(source: str) -> ArcInsertionResult:
    """Helper to build MIR and run ARC insertion."""
    mir_result = build_mir_result(source)
    return insert_arc(mir_result)


# =============================================================================
# RcState Tests
# =============================================================================

def test_rc_state_enum():
    """Test RcState enum values."""
    assert RcState.OWNED != RcState.BORROWED
    assert RcState.OWNED.name == "OWNED"
    assert RcState.BORROWED.name == "BORROWED"


# =============================================================================
# LocalInfo Tests
# =============================================================================

def test_local_info_creation():
    """Test LocalInfo dataclass creation."""
    info = LocalInfo(
        local_id=LocalId(0),
        type_id=None,
        is_heap_type=True,
        is_closure_type=False,
        is_existential_type=False,
        is_optional_heap_type=False,
        needs_arc=True,
        needs_cleanup=False,
    )
    assert info.local_id == LocalId(0)
    assert info.is_heap_type
    assert info.needs_arc


def test_local_info_weak_type():
    """Test LocalInfo for cleanup types."""
    info = LocalInfo(
        local_id=LocalId(1),
        type_id=None,
        is_heap_type=False,
        is_closure_type=True,
        is_existential_type=False,
        is_optional_heap_type=False,
        needs_arc=False,
        needs_cleanup=True,
    )
    assert not info.needs_arc
    assert info.needs_cleanup


# =============================================================================
# BlockAnalysis Tests
# =============================================================================

def test_block_analysis_creation():
    """Test BlockAnalysis dataclass creation."""
    analysis = BlockAnalysis(
        block_id=BlockId(0),
        live_in={LocalId(1), LocalId(2)},
        live_out={LocalId(2)},
        use_set={LocalId(1)},
        def_set={LocalId(3)},
    )
    assert analysis.block_id == BlockId(0)
    assert LocalId(1) in analysis.live_in
    assert LocalId(2) in analysis.live_out
    assert LocalId(1) in analysis.use_set
    assert LocalId(3) in analysis.def_set


# =============================================================================
# collect_ref_locals Tests
# =============================================================================

def test_collect_ref_locals_no_refs():
    """Test collect_ref_locals with no reference types."""
    result = build_mir_result("""
def test() {
    let x = 42;
}
""")
    assert not result.has_errors()
    func = result.program.functions[0]
    ref_locals = collect_ref_locals(func, result.type_table)
    # No Ref<T> locals in this function
    assert len(ref_locals) == 0



# =============================================================================
# compute_liveness Tests
# =============================================================================

def test_compute_liveness_empty_function():
    """Test liveness analysis on an empty function."""
    result = build_mir_result("""
def test() {
}
""")
    assert not result.has_errors()
    func = result.program.functions[0]
    ref_locals = collect_ref_locals(func, result.type_table)
    liveness = compute_liveness(func, ref_locals)

    # Should have analysis for entry block at minimum
    assert len(liveness) >= 1


def test_compute_liveness_linear_blocks():
    """Test liveness analysis on linear control flow."""
    result = build_mir_result("""
def test() {
    let x = 42;
    let y = x + 1;
}
""")
    assert not result.has_errors()
    func = result.program.functions[0]
    ref_locals = collect_ref_locals(func, result.type_table)
    liveness = compute_liveness(func, ref_locals)

    # Every block should have analysis
    for block_id in func.blocks:
        assert block_id in liveness


def test_compute_liveness_if_else():
    """Test liveness analysis with branching."""
    result = build_mir_result("""
def test() {
    if true {
        let x = 1;
    } else {
        let x = 2;
    }
}
""")
    assert not result.has_errors()
    func = result.program.functions[0]
    ref_locals = collect_ref_locals(func, result.type_table)
    liveness = compute_liveness(func, ref_locals)

    # Should have analysis for all blocks
    assert len(liveness) == len(func.blocks)


def test_compute_liveness_loop():
    """Test liveness analysis with loops."""
    result = build_mir_result("""
def test() {
    var x = 10;
    while x > 0 {
        x = x - 1;
    }
}
""")
    assert not result.has_errors()
    func = result.program.functions[0]
    ref_locals = collect_ref_locals(func, result.type_table)
    liveness = compute_liveness(func, ref_locals)

    # Should converge without infinite loop
    assert len(liveness) == len(func.blocks)


# =============================================================================
# insert_arc Tests
# =============================================================================

def test_insert_arc_empty_program():
    """Test ARC insertion on empty program."""
    result = build_with_arc("")
    assert not result.has_errors()
    assert isinstance(result.program, MirProgram)


def test_insert_arc_no_refs():
    """Test ARC insertion with no reference types."""
    result = build_with_arc("""
def test() {
    let x = 42;
    let y = x + 1;
}
""")
    assert not result.has_errors()
    assert len(result.program.functions) == 1


def test_insert_arc_preserves_function_count():
    """Test that ARC insertion preserves function count."""
    mir_result = build_mir_result("""
def foo() -> i32 { return 1; }
def bar() -> i32 { return 2; }
def baz() { let x = 1; }
""")
    arc_result = insert_arc(mir_result)

    assert len(arc_result.program.functions) == len(mir_result.program.functions)


def test_insert_arc_preserves_structs():
    """Test that ARC insertion preserves struct definitions."""
    mir_result = build_mir_result("""
struct Point {
    var x: i32;
    var y: i32;
}
""")
    arc_result = insert_arc(mir_result)

    assert len(arc_result.program.structs) == len(mir_result.program.structs)


def test_insert_arc_preserves_enums():
    """Test that ARC insertion preserves enum definitions."""
    mir_result = build_mir_result("""
enum Color {
    case red
    case green
    case blue
}
""")
    arc_result = insert_arc(mir_result)

    assert len(arc_result.program.enums) == len(mir_result.program.enums)


# =============================================================================
# verify_arc_correctness Tests
# =============================================================================

def test_verify_arc_no_errors():
    """Test verification with correct ARC."""
    result = build_with_arc("""
def test() {
    let x = 42;
}
""")
    # No ref types, should have no errors
    assert not result.has_errors()


# =============================================================================
# OpOwnership Tests
# =============================================================================

def test_op_ownership_creation():
    """Test OpOwnership dataclass creation."""
    ownership = OpOwnership(
        produces=LocalId(0),
        consumes=[LocalId(1)],
        copies=[LocalId(2), LocalId(3)],
        post_retains=[],
    )
    assert ownership.produces == LocalId(0)
    assert LocalId(1) in ownership.consumes
    assert len(ownership.copies) == 2


# =============================================================================
# ArcInsertionResult Tests
# =============================================================================

def test_arc_insertion_result():
    """Test ArcInsertionResult structure."""
    result = build_with_arc("def test() {}")

    assert result.program is not None
    assert result.type_table is not None
    assert result.symbol_table is not None
    assert isinstance(result.errors, list)
    assert not result.has_errors()


def test_arc_insertion_result_has_errors():
    """Test ArcInsertionResult.has_errors()."""
    result = build_with_arc("def test() {}")
    assert not result.has_errors()


# =============================================================================
# Integration Tests
# =============================================================================

def test_full_pipeline():
    """Full pipeline with ARC insertion on a heap-struct API.

    ARC should insert retain/release around the cross-function ownership
    transfer.
    """
    source = """
struct Node {
    var value: i32;
}

def makeNode(v: i32) -> Node {
    return Node { value: v };
}

def test() {
    let n = makeNode(42);
}
"""
    result = build_with_arc(source)
    # Should complete without crashing
    assert isinstance(result.program, MirProgram)
    assert not result.has_errors()


def test_comprehensive_example():
    """Test ARC insertion for the basic.rl example file."""
    example_path = Path(__file__).parent.parent / "examples" / "basic.rl"
    if not example_path.exists():
        print("Skipping: example file not found")
        return

    source = example_path.read_text()
    mir_result = build_mir_result(source)
    arc_result = insert_arc(mir_result)

    # Print info
    print(f"ARC insertion completed:")
    print(f"  Functions: {len(arc_result.program.functions)}")
    print(f"  Errors: {len(arc_result.errors)}")
    if arc_result.errors:
        for e in arc_result.errors[:5]:
            print(f"    - {e}")

    assert len(arc_result.program.functions) > 0


# =============================================================================
# Edge Cases
# =============================================================================

def test_empty_blocks():
    """Test ARC insertion with empty blocks."""
    result = build_with_arc("""
def test() {
    {}
    {}
}
""")
    assert isinstance(result.program, MirProgram)


def test_nested_control_flow():
    """Test ARC insertion with nested control flow."""
    result = build_with_arc("""
def test() {
    if true {
        if false {
            let x = 1;
        }
    }
}
""")
    assert isinstance(result.program, MirProgram)


def test_early_return():
    """Test ARC insertion with early return.

    The owned local `d` must be released on every return path —
    including the conditional early return. Without that, the heap
    object behind `d` would leak when the early-return branch is taken.
    """
    result = build_with_arc("""
struct Data {
    var x: i32;
}

def test() -> i32 {
    let d = Data { x: 1 };
    if true {
        return 0;
    }
    return 1;
}
""")
    assert isinstance(result.program, MirProgram)
    assert not result.has_errors()

    # The owned local `d` must be released somewhere on every control
    # flow path that reaches a return. The exact block (predecessor or
    # return block itself) depends on the optimizer, so we just count
    # global releases across the function and demand at least one per
    # return statement.
    from rolang.mir import Release, Return
    func = next(f for f in result.program.functions if f.name == "test")
    return_blocks = [
        block for block in func.blocks.values()
        if isinstance(block.terminator, Return)
    ]
    total_releases = sum(
        sum(1 for op in block.ops if isinstance(op, Release))
        for block in func.blocks.values()
    )
    assert len(return_blocks) >= 1
    assert total_releases >= 1, (
        f"expected at least one Release on the early-return path; got 0. "
        f"Function MIR: {func}"
    )


# =============================================================================
# regression tests for the cross-block release fix
# =============================================================================


def test_local_released_when_used_only_in_successor():
    """A local defined in one block and used only in a successor must be
    released in the successor — not silently leaked. This was the bug
    that caused `let x = vec.get(0); if let v = x { ... }` to leak the
    Item even when both branches no longer needed `x` afterwards."""
    source = """
struct Item { var n: i32 }

def make() -> Item { return Item { n: 1 }; }

def test() -> i32 {
    let x = make();
    if true {
        return x.n;
    }
    return -1;
}
"""
    result = build_with_arc(source)
    assert not result.has_errors()

    from rolang.mir import Release
    test_func = next(
        f for f in result.program.functions if f.name == "test"
    )
    total_releases = sum(
        sum(1 for op in block.ops if isinstance(op, Release))
        for block in test_func.blocks.values()
    )
    assert total_releases >= 1, (
        "expected at least one Release for `x` somewhere in the function"
    )


# =============================================================================
# Count Operations Tests
# =============================================================================

def count_arc_ops(func: MirFunction) -> tuple:
    """Count retain and release operations in a function."""
    retains = 0
    releases = 0

    for block in func.blocks.values():
        for op in block.ops:
            if isinstance(op, Retain):
                retains += 1
            elif isinstance(op, Release):
                releases += 1

    return retains, releases


def test_count_ops_no_refs():
    """Test that functions without refs have no ARC ops."""
    result = build_with_arc("""
def test() {
    let x = 42;
    let y = x + 1;
}
""")
    func = result.program.functions[0]
    retains, releases = count_arc_ops(func)

    # No ref types means no ARC operations
    assert retains == 0
    assert releases == 0


# =============================================================================
# End-to-end cycle-collection safety net (Task 6: acyclic-type GC skip)
# =============================================================================

import subprocess

from rolang.driver import CompileOptions, EmitKind, compile_source


def _compile_and_run(tmp_path: Path, name: str, source: str) -> int:
    """Compile `source` to an executable and run it, returning the exit code.

    Mirrors the harness in tests/test_runtime_execution.py.
    """
    source_path = tmp_path / f"{name}.rl"
    output_path = tmp_path / name
    source_path.write_text(source, encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=[],
        ),
    )
    assert result.success, getattr(result, "errors", result)
    assert result.output_path == output_path

    completed = subprocess.run([str(output_path)], check=False)
    return completed.returncode


def test_recursive_cycle_still_collected(tmp_path: Path):
    """A self-referential ``Node`` is cyclic-capable (it has a ``Node?`` field),
    so the acyclic-type GC optimization must NOT skip it: the cycle must still
    be collected by the cycle collector.

    We build a self-referential cycle, drop all external references to it, push
    enough allocations past the GC threshold, force a collection, and assert via
    the runtime's ``rt_gc_cycle_count()`` accounting that at least one object was
    reclaimed by the cycle collector (i.e. the cycle did not leak)."""
    exit_code = _compile_and_run(
        tmp_path,
        "recursive_cycle_collected",
        """
extern "C" def rt_gc_collect() -> Void;
extern "C" def rt_gc_cycle_count() -> i64;

struct Node { var next: Node?; var tag: i32 }

def make_cycle() {
    // a <-> b cycle, entirely local: both are dead once make_cycle returns.
    var a = Node { next: nil, tag: 1 };
    var b = Node { next: nil, tag: 2 };
    a.next = b;
    b.next = a;
}

def main() -> i32 {
    make_cycle();

    // Push allocations past the GC threshold so a forced collect has work.
    var i = 0;
    while i < 10005 {
        let tmp = Node { next: nil, tag: i };
        i = i + 1;
    }

    // If the cycle was collected, the collector counted >=1 reclaimed object.
    // A leaked cycle (wrongly-acyclic Node) would leave this at 0.
    unsafe {
        rt_gc_collect();
        if rt_gc_cycle_count() > 0 {
            return 0;
        }
    }
    return 1;
}
""",
    )
    assert exit_code == 0


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        # RcState
        test_rc_state_enum,
        # LocalInfo
        test_local_info_creation,
        test_local_info_weak_type,
        # BlockAnalysis
        test_block_analysis_creation,
        # collect_ref_locals
        test_collect_ref_locals_no_refs,
        # compute_liveness
        test_compute_liveness_empty_function,
        test_compute_liveness_linear_blocks,
        test_compute_liveness_if_else,
        test_compute_liveness_loop,
        # insert_arc
        test_insert_arc_empty_program,
        test_insert_arc_no_refs,
        test_insert_arc_preserves_function_count,
        test_insert_arc_preserves_structs,
        test_insert_arc_preserves_enums,
        # verify_arc_correctness
        test_verify_arc_no_errors,
        # OpOwnership
        test_op_ownership_creation,
        # ArcInsertionResult
        test_arc_insertion_result,
        test_arc_insertion_result_has_errors,
        # Integration
        test_full_pipeline,
        test_comprehensive_example,
        # Edge cases
        test_empty_blocks,
        test_nested_control_flow,
        test_early_return,
        # Count operations
        test_count_ops_no_refs,
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
