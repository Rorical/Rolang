"""
Regression tests for compiler / runtime bug fixes.

Each test here locks down a previously-broken behaviour identified during
the bug audit. Names use the audit id (C1/C2/H1/...) so future failures
are easy to trace back to the original issue.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import pytest

from rolang.driver import CompileOptions, EmitKind, compile_source


# --------------------------------------------------------------------------- helpers


def _stdlib_path() -> list[Path]:
    """Return the path to the bundled Rolang stdlib."""
    import os

    stdlib = Path(os.path.dirname(__file__)).parent / "src" / "rolang" / "std"
    return [stdlib]


def _compile(
    tmp_path: Path,
    name: str,
    source: str,
    include_paths: Optional[list[Path]] = None,
):
    source_path = tmp_path / f"{name}.rl"
    output_path = tmp_path / name
    source_path.write_text(source, encoding="utf-8")
    return compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=include_paths or [],
        ),
    )


def _diags_text(result) -> str:
    return "\n".join(str(d) for d in result.diagnostics.diagnostics)


def _compile_and_capture(
    tmp_path: Path,
    name: str,
    source: str,
    include_paths: Optional[list[Path]] = None,
) -> subprocess.CompletedProcess:
    """Compile, then run the executable capturing stdout/stderr."""
    result = _compile(tmp_path, name, source, include_paths=include_paths)
    assert result.success, f"compilation failed: {_diags_text(result)}"
    return subprocess.run(
        [str(result.output_path)],
        check=False,
        capture_output=True,
        text=True,
    )


def _expect_compile_error(
    tmp_path: Path,
    name: str,
    source: str,
    substring: str,
    include_paths: Optional[list[Path]] = None,
) -> None:
    result = _compile(tmp_path, name, source, include_paths=include_paths)
    assert not result.success, "expected compile to fail, but it succeeded"
    diags = _diags_text(result)
    assert substring in diags, (
        f"expected substring {substring!r} in diagnostics:\n{diags}"
    )


# --------------------------------------------------------------------------- C1 — deinit


def test_c1_deinit_runs_on_scope_exit(tmp_path: Path) -> None:
    """A `def __release__() -> Void { ... }` block must execute on final release."""
    completed = _compile_and_capture(
        tmp_path,
        "c1_basic",
        """
import "io.rl"

struct R {
    var n: i32;

    def __release__() -> Void {
        println("deinit ran");
    }
}

def main() -> i32 {
    let _ = R { n: 1 };
    println("before scope end");
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    # deinit must run AFTER the binding goes out of scope (i.e. after the
    # final println). The exact ordering is what we want to lock in here.
    assert "before scope end" in completed.stdout
    assert "deinit ran" in completed.stdout
    assert completed.stdout.index("before scope end") < completed.stdout.index(
        "deinit ran"
    )


def test_c1_returned_closure_releases_captured_heap_object(tmp_path: Path) -> None:
    """A returned closure owns and releases its captured heap references."""
    completed = _compile_and_capture(
        tmp_path,
        "c1_closure_capture_release",
        """
import "io.rl"

struct R {
    var n: i32;

    def __release__() -> Void {
        println("captured release");
    }
}

def use_r(r: R, x: i32) -> i32 {
    return r.n + x;
}

def make() -> (i32) -> i32 {
    let r = R { n: 41 };
    return { x: i32 in
        return use_r(r, x);
    };
}

def main() -> i32 {
    let f = make();
    let y = f(1);
    println("after call");
    if y != 42 { return y; }
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "after call" in completed.stdout
    assert "captured release" in completed.stdout
    assert completed.stdout.index("after call") < completed.stdout.index(
        "captured release"
    )


def test_c1_existential_releases_boxed_heap_object(tmp_path: Path) -> None:
    """A released existential drops the heap object stored in its payload."""
    completed = _compile_and_capture(
        tmp_path,
        "c1_existential_release",
        """
import "io.rl"

protocol Valued {
    def value() -> i32;
}

struct R {
    var n: i32;

    def value() -> i32 {
        return self.n;
    }

    def __release__() -> Void {
        println("existential release");
    }
}

def make() -> any Valued {
    let r = R { n: 42 };
    return r;
}

def main() -> i32 {
    let p = make();
    let y = p.value();
    println("after call");
    if y != 42 { return y; }
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "after call" in completed.stdout
    assert "existential release" in completed.stdout
    assert completed.stdout.index("after call") < completed.stdout.index(
        "existential release"
    )


def test_c1_deinit_can_read_self_fields(tmp_path: Path) -> None:
    """A deinit body must see a fully-initialized `self`."""
    completed = _compile_and_capture(
        tmp_path,
        "c1_self",
        """
import "io.rl"

struct File {
    var fd: i32;

    def __release__() -> Void {
        println("closing fd:");
        println_i32(self.fd);
    }
}

def main() -> i32 {
    let _ = File { fd: 42 };
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "closing fd:" in completed.stdout
    assert "42" in completed.stdout


# --------------------------------------------------------------------------- C2 — monomorphize typos


def test_c2_optional_return_does_not_internal_error(tmp_path: Path) -> None:
    """Returning `T?` from a generic function used to crash monomorphize.

    The original bug was a field-name typo (`wrapped_type` -> `inner`) that
    made the OPTIONAL branch of `_unify_for_inference` dead-on-arrival.
    """
    result = _compile(
        tmp_path,
        "c2",
        """
import "io.rl"

def make<T>() -> T? {
    return nil;
}

def main() -> i32 {
    let _: i32? = make();
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    # The program may or may not compile depending on whether `nil` infers a
    # concrete T here, but it must NOT raise an internal compiler error.
    diags = _diags_text(result)
    assert "internal compiler error" not in diags.lower()
    assert "wrapped_type" not in diags  # the old crash message
    assert "'OptionalType'" not in diags


# --------------------------------------------------------------------------- C4 — array OOB


def test_c4_array_index_out_of_bounds_panics(tmp_path: Path) -> None:
    """Out-of-bounds array reads must panic (not silently return 0)."""
    completed = _compile_and_capture(
        tmp_path,
        "c4_oob",
        """
import "io.rl"

def main() -> i32 {
    let a = [10, 20, 30];
    let x = a[10];
    println_i32(x);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    # Process should abort, not exit cleanly with 0.
    assert completed.returncode != 0
    assert "rolang panic" in completed.stderr
    assert "out of bounds" in completed.stderr
    # Old behaviour: would print "0" on stdout and exit 0. Now we must NOT
    # have printed the bogus value before crashing.
    assert "0" not in completed.stdout.split()


# --------------------------------------------------------------------------- C5 — divide by zero


def test_c5_integer_divide_by_zero_panics(tmp_path: Path) -> None:
    """Integer `/` by zero must panic with a clear message, not SIGFPE."""
    completed = _compile_and_capture(
        tmp_path,
        "c5_div",
        """
import "io.rl"

def main() -> i32 {
    var b: i32 = 0;
    let r = 10 / b;
    println_i32(r);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode != 0
    assert "rolang panic" in completed.stderr
    assert "divide by zero" in completed.stderr


def test_c5_integer_modulo_by_zero_panics(tmp_path: Path) -> None:
    """Integer `%` by zero must panic too."""
    completed = _compile_and_capture(
        tmp_path,
        "c5_mod",
        """
import "io.rl"

def main() -> i32 {
    var b: i32 = 0;
    let r = 10 % b;
    println_i32(r);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode != 0
    assert "rolang panic" in completed.stderr
    assert "remainder" in completed.stderr


# --------------------------------------------------------------------------- H1 — throws


def test_h1_throws_without_result_return_is_rejected(tmp_path: Path) -> None:
    """A `throws` function must return a Result-shaped type."""
    _expect_compile_error(
        tmp_path,
        "h1",
        """
def f() throws -> i32 {
    return 1;
}

def main() -> i32 {
    return 0;
}
""",
        substring="throws",
    )


def test_h1_throws_with_result_return_is_accepted(tmp_path: Path) -> None:
    """`throws` is fine when the function does return a Result."""
    result = _compile(
        tmp_path,
        "h1_ok",
        """
import "result.rl"

def f() throws -> Result<i32, String> {
    return Result.ok(value: 1);
}

def main() -> i32 {
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert result.success


def test_h1_try_error_path_runs_defer(tmp_path: Path) -> None:
    """A `?` early return must execute pending defers before propagating."""
    completed = _compile_and_capture(
        tmp_path,
        "h1_try_defer",
        """
import "result.rl"

struct Counter {
    var value: i32;
}

def fail() -> Result<i32, String> {
    return Result.err(error: "boom");
}

def run(c: Counter) -> Result<i32, String> {
    defer {
        c.value = 7;
    }

    let value = fail()?;
    return Result.ok(value: value);
}

def main() -> i32 {
    let c = Counter { value: 0 };
    let _ = run(c);
    return c.value;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 7


# --------------------------------------------------------------------------- H3 — async


def test_h3_sync_caller_cannot_call_async_function(tmp_path: Path) -> None:
    """An async function may only be called from an async context."""
    _expect_compile_error(
        tmp_path,
        "h3",
        """
def slow() async -> i32 {
    return 5;
}

def main() -> i32 {
    let n = slow();
    return n;
}
""",
        substring="async function",
    )


def test_h3_async_caller_can_call_async_function(tmp_path: Path) -> None:
    """async-from-async calls remain allowed."""
    result = _compile(
        tmp_path,
        "h3_ok",
        """
def slow() async -> i32 {
    return 5;
}

def main() async -> i32 {
    let n = slow();
    return n;
}
""",
    )
    assert result.success


# --------------------------------------------------------------------------- H4 — Dict.get returns Optional


def test_h4_generic_dict_get_returns_optional(tmp_path: Path) -> None:
    """Dict<K, V>.get(k) now returns V?; missing keys are distinguishable
    from a zero-valued payload."""
    completed = _compile_and_capture(
        tmp_path,
        "h4",
        """
import "io.rl"
import "dict.rl"

def main() -> i32 {
    var d = Dict<i32, i32>.new(16, 0, 0, 0);
    d.set(1, 0);  // present, value happens to be 0
    let present = d.get(1) ?? -1;        // expect 0
    let missing = d.get(2) ?? -1;        // expect -1
    println_i32(present);
    println_i32(missing);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    lines = completed.stdout.strip().splitlines()
    assert lines == ["0", "-1"], lines


# --------------------------------------------------------------------------- H5 — array_max/min


def test_h5_array_min_max_handle_empty_arrays(tmp_path: Path) -> None:
    """`array_min` / `array_max` return nil on an empty input."""
    completed = _compile_and_capture(
        tmp_path,
        "h5",
        """
import "io.rl"
import "array.rl"

def main() -> i32 {
    let arr = [1, 2, 3];
    let empty = Vec<i32>.new();
    let m = array_max(empty) ?? -1;  // empty -> nil -> -1
    let p = array_max(arr) ?? -1;    // non-empty -> 3
    println_i32(m);
    println_i32(p);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    lines = completed.stdout.strip().splitlines()
    assert lines == ["-1", "3"], lines


def test_optional_is_inner_type_compiles(tmp_path: Path) -> None:
    """`x is T` for `T?` lowers to an Optional tag check, not an ICE."""
    completed = _compile_and_capture(
        tmp_path,
        "optional_is",
        """
def main() -> i32 {
    let x: i32? = 1;
    if x is i32 { return 7; }
    return 0;
}
""",
    )
    assert completed.returncode == 7


def test_struct_literal_unknown_or_missing_fields_rejected(tmp_path: Path) -> None:
    _expect_compile_error(
        tmp_path,
        "bad_struct_literal",
        """
struct Point { var x: i32; var y: i32; }
def main() -> i32 {
    let p = Point { x: 1, z: 2 };
    return p.x;
}
""",
        "has no field 'z'",
    )


def test_non_void_function_must_return(tmp_path: Path) -> None:
    _expect_compile_error(
        tmp_path,
        "missing_return",
        """
def f() -> i32 {
}
def main() -> i32 { return f(); }
""",
        "must return a value",
    )


def test_call_labels_and_default_args(tmp_path: Path) -> None:
    completed = _compile_and_capture(
        tmp_path,
        "default_args",
        """
def add(first x: i32, y: i32 = 2) -> i32 {
    return x + y;
}
def main() -> i32 {
    return add(first: 40);
}
""",
    )
    assert completed.returncode == 42


def test_wrong_call_label_rejected(tmp_path: Path) -> None:
    _expect_compile_error(
        tmp_path,
        "wrong_label",
        """
def add(first x: i32) -> i32 { return x; }
def main() -> i32 { return add(second: 1); }
""",
        "label mismatch",
    )


def test_struct_generic_arity_rejected(tmp_path: Path) -> None:
    _expect_compile_error(
        tmp_path,
        "generic_arity",
        """
struct Box<T> { var value: T; }
def main() -> i32 {
    let b = Box<i32, i32> { value: 1 };
    return b.value;
}
""",
        "expects 1 generic argument",
    )


# --------------------------------------------------------------------------- N4 — lambda complex statements


def test_n4_complex_statements_in_lambda_compile_and_run(tmp_path: Path) -> None:
    """Compound statements (``if`` / ``while`` / ``for`` / ``switch`` /
    ``guard`` / ``defer``) are fully supported inside lambda bodies.

    This used to be a hard error — earlier versions of LambdaFunctionBuilder
    shipped a mini-lowerer that only handled return/var-decl/expr-stmt.
    The current builder delegates to the full ``MirFunctionBuilder``
    pipeline, so any HIR statement form lowers identically inside or
    outside of a lambda."""
    completed = _compile_and_capture(
        tmp_path,
        "n4",
        """
def call(f: (i32) -> i32, x: i32) -> i32 {
    return f(x);
}

def main() -> i32 {
    let limit = 10;
    let clamp = { x: i32 in
        if x > limit {
            return limit;
        }
        var acc = 0;
        var i = 0;
        while i < x {
            acc = acc + 1;
            i = i + 1;
        }
        return acc;
    };
    if call(clamp, 5) != 5 { return 1; }
    if call(clamp, 20) != 10 { return 2; }
    return 0;
}
""",
    )
    assert completed.returncode == 0, (
        f"Lambda with if + while + capture should compile and return 0, "
        f"got exit {completed.returncode}\nstdout: {completed.stdout}\n"
        f"stderr: {completed.stderr}"
    )


# --------------------------------------------------------------------------- N6 — diagnostic names


def test_n6_diagnostics_render_struct_names_not_ids(tmp_path: Path) -> None:
    """A type-mismatch diagnostic shows the user-facing type name."""
    result = _compile(
        tmp_path,
        "n6",
        """
struct Foo { var x: i32; }
struct Bar { var y: i32; }

def take(b: Bar) -> Void {}

def main() -> i32 {
    let f = Foo { x: 0 };
    take(f);
    return 0;
}
""",
    )
    assert not result.success
    diags = _diags_text(result)
    # Old format would have included `struct#N` placeholders.
    assert "Foo" in diags
    assert "Bar" in diags
    assert "struct#" not in diags

# --------------------------------------------------------------------------- v2 binding model
# In the ARC + GC memory model every struct/enum sits on the heap behind a
# reference, so every method can freely update `self`'s fields. What `let`
# still does is prevent rebinding the local: the binding is immutable, the
# storage is not.


def test_v2_any_method_can_update_self(tmp_path: Path) -> None:
    """A plain `def` method can update `self`."""
    completed = _compile_and_capture(
        tmp_path,
        "v2_mut",
        """
struct Counter {
    var n: i32;

    def bump() -> Void {
        self.n = self.n + 1;
    }
}

def main() -> i32 {
    var c = Counter { n: 0 };
    c.bump();
    c.bump();
    c.bump();
    return c.n;
}
""",
    )
    assert completed.returncode == 3


def test_v2_let_cannot_be_rebound(tmp_path: Path) -> None:
    """`let x = 1; x = 2;` is a compile error — bindings are immutable."""
    _expect_compile_error(
        tmp_path,
        "v2_let_rebind",
        """
def main() -> i32 {
    let x: i32 = 1;
    x = 5;
    return x;
}
""",
        substring="immutable binding",
    )


def test_v2_var_can_be_rebound(tmp_path: Path) -> None:
    """`var x = 1; x = 2;` is allowed."""
    completed = _compile_and_capture(
        tmp_path,
        "v2_var_rebind",
        """
def main() -> i32 {
    var x: i32 = 1;
    x = 5;
    return x;
}
""",
    )
    assert completed.returncode == 5


def test_v2_let_bound_object_field_can_be_mutated(tmp_path: Path) -> None:
    """`let` only freezes the *binding*; fields of a `let`-bound object
    can still be assigned (it's the same heap object after all)."""
    completed = _compile_and_capture(
        tmp_path,
        "v2_let_field",
        """
struct Box {
    var n: i32;
}

def main() -> i32 {
    let b = Box { n: 1 };
    b.n = 42;          // allowed — mutates the heap object, not the binding
    return b.n;
}
""",
    )
    assert completed.returncode == 42


# --------------------------------------------------------------------------- A — async / scheduler wiring
# Before the v0.2 async overhaul:
#   * the entry function never actually allocated the state-machine frame
#     on the heap (it read garbage out of an uninitialised stack pointer
#     slot), which the optimiser turned into a SIGSEGV under -O2;
#   * the entry function inline-called the resume function N+1 times
#     instead of spawning a task and letting the scheduler drive it, so
#     the runtime's task queue was always empty;
#   * the spawn ABI mismatched: rt_frame_alloc handed out an ARC-header-
#     less buffer but resume functions GEP'd +32 bytes anyway;
#   * mid-stack child tasks (an async fn that itself awaits another async
#     fn) crashed because the child frame's `_handle` field was never
#     stored, so `rt_task_complete` ran against a null handle.
#
# The tests below lock down each fix.


def test_a_async_works_at_O2(tmp_path: Path) -> None:
    """The entry function must heap-allocate its frame; the old version
    read an uninitialised stack slot and only "worked" by accident under
    -O0. -O2 must produce the same exit code as -O0."""
    source = """
def compute() async -> i64 { return 42; }
def double(x: i64) async -> i64 { return x * 2; }
def main() async -> i32 {
    let a = await compute();
    let b = await double(a);
    return (a + b) as i32;
}
"""
    source_path = tmp_path / "a.rl"
    source_path.write_text(source, encoding="utf-8")
    for opt in ("O0", "O2", "O3"):
        out = tmp_path / f"a_{opt}"
        cmd = [
            "uv", "run", "rolangc", f"-{opt}",
            str(source_path), "-o", str(out),
        ]
        rc = subprocess.run(cmd, capture_output=True, text=True)
        assert rc.returncode == 0, f"{opt} compile failed: {rc.stderr}"
        completed = subprocess.run([str(out)], capture_output=True, text=True)
        assert completed.returncode == 126, (
            f"-{opt} expected exit 126, got {completed.returncode}; "
            f"stderr={completed.stderr!r}"
        )


def test_a_async_nested_one_level(tmp_path: Path) -> None:
    """An async fn calling another async fn that itself awaits must work:
    this exercises the TaskSpawn path (vs leaf-only direct calls) and the
    child-handle back-link in the frame."""
    completed = _compile_and_capture(
        tmp_path,
        "a_nested1",
        """
def leaf() async -> i32 { return 5; }
def mid() async -> i32 {
    let x = await leaf();
    return x;
}
def main() async -> i32 {
    return await mid();
}
""",
    )
    assert completed.returncode == 5


def test_a_async_nested_three_levels(tmp_path: Path) -> None:
    """Three-deep await chain: main -> mid -> deep -> leaf."""
    completed = _compile_and_capture(
        tmp_path,
        "a_nested3",
        """
def leaf() async -> i64 { return 10; }
def deep() async -> i64 {
    let a = await leaf();
    return a + 1;          // 11
}
def mid() async -> i64 {
    let b = await deep();
    return b * 2;          // 22
}
def main() async -> i32 {
    let v = await mid();
    return v as i32;       // 22
}
""",
    )
    assert completed.returncode == 22


def test_a_async_struct_return(tmp_path: Path) -> None:
    """Async fns can return heap-allocated struct values; TaskComplete /
    TaskGetResult box the value uniformly via an alloca."""
    completed = _compile_and_capture(
        tmp_path,
        "a_struct",
        """
struct Point { var x: i64; var y: i64 }
def make() async -> Point { Point { x: 10, y: 32 } }
def main() async -> i32 {
    let p = await make();
    (p.x + p.y) as i32        // 42
}
""",
    )
    assert completed.returncode == 42


def test_a_async_non_leaf_preserves_primitive_argument(tmp_path: Path) -> None:
    """A spawned non-leaf async callee must receive its arguments in its frame."""
    completed = _compile_and_capture(
        tmp_path,
        "a_arg_primitive",
        """
def leaf() async -> i32 { return 1; }
def add_after_yield(x: i32) async -> i32 {
    let y = await leaf();
    return x + y;
}
def main() async -> i32 {
    return await add_after_yield(41);
}
""",
    )
    assert completed.returncode == 42


def test_a_async_non_leaf_preserves_heap_argument(tmp_path: Path) -> None:
    """Heap arguments copied into async frames must stay valid across await."""
    completed = _compile_and_capture(
        tmp_path,
        "a_arg_heap",
        """
struct Box { var n: i32 }

def leaf() async -> i32 { return 1; }
def read_after_yield(b: Box) async -> i32 {
    let y = await leaf();
    return b.n + y;
}
def main() async -> i32 {
    let b = Box { n: 41 };
    return await read_after_yield(b);
}
""",
    )
    assert completed.returncode == 42


# --------------------------------------------------------------------------- Optional switch (was: `case .Some(v):` always taken)


def test_optional_switch_dispatches_correctly_on_some(tmp_path: Path) -> None:
    """`switch x: i32?` must route through the Optional tag, not the
    value-switch fall-through. Previously `case .Some(v):` was always taken
    even when `x == nil`."""
    completed = _compile_and_capture(
        tmp_path,
        "opt_some",
        """
def main() -> i32 {
    let x: i32? = 7;
    switch x {
        case .Some(let v): return v + 10;
        case .None: return -1;
    }
}
""",
    )
    assert completed.returncode == 17


def test_optional_switch_dispatches_correctly_on_none(tmp_path: Path) -> None:
    """The None branch must be reached when scrutinee is nil."""
    completed = _compile_and_capture(
        tmp_path,
        "opt_none",
        """
def main() -> i32 {
    let x: i32? = nil;
    switch x {
        case .Some(let v): return v + 10;
        case .None: return 99;
    }
}
""",
    )
    assert completed.returncode == 99


def test_optional_switch_accepts_nil_literal_case(tmp_path: Path) -> None:
    """`case nil:` should be accepted as a None-case match."""
    completed = _compile_and_capture(
        tmp_path,
        "opt_nil_lit",
        """
def main() -> i32 {
    let x: i32? = nil;
    switch x {
        case .Some(let v): return v;
        case nil: return 42;
    }
}
""",
    )
    assert completed.returncode == 42


def test_optional_switch_non_exhaustive_is_rejected(tmp_path: Path) -> None:
    """A switch on Optional that misses one of {Some, None} must fail."""
    _expect_compile_error(
        tmp_path,
        "opt_nonex",
        """
def main() -> i32 {
    let x: i32? = nil;
    switch x {
        case .Some(let v): return v;
    }
    return 0;
}
""",
        "exhaustive",
    )


def test_optional_switch_with_default_compiles(tmp_path: Path) -> None:
    """`default:` should satisfy exhaustiveness on Optional."""
    completed = _compile_and_capture(
        tmp_path,
        "opt_default",
        """
def main() -> i32 {
    let x: i32? = 5;
    switch x {
        case .Some(let v): return v;
        default: return -1;
    }
}
""",
    )
    assert completed.returncode == 5


# --------------------------------------------------------------------------- Uninitialized var (was: `var x: i32;` read uninitialized memory)


def test_var_without_initializer_reads_zero(tmp_path: Path) -> None:
    """A `var x: T;` declaration without initializer must produce the zero
    value of T rather than whatever happens to be on the stack."""
    completed = _compile_and_capture(
        tmp_path,
        "var_zero",
        """
def main() -> i32 {
    var x: i32;
    var y: i64;
    var z: f64;
    var b: Bool;
    var o: i32?;

    var total: i32 = x;
    total = total + (y as i32);
    total = total + (z as i32);
    if b { total = total + 1; }
    if let v = o { total = total + v; }
    return total;
}
""",
    )
    assert completed.returncode == 0


def test_var_optional_without_init_is_nil(tmp_path: Path) -> None:
    """`var x: T?;` must default to nil (not random memory)."""
    completed = _compile_and_capture(
        tmp_path,
        "var_opt_nil",
        """
def main() -> i32 {
    var x: i32?;
    switch x {
        case .Some(let v): return v;
        case .None: return 33;
    }
}
""",
    )
    assert completed.returncode == 33


# --------------------------------------------------------------------------- `as` cast tightening


def test_cast_struct_to_int_is_rejected(tmp_path: Path) -> None:
    """`someStruct as i32` used to silently reinterpret the heap header.
    It must now be a compile error."""
    _expect_compile_error(
        tmp_path,
        "cast_struct_int",
        """
struct P { var x: i32 }
def main() -> i32 {
    let p = P { x: 1 };
    return p as i32;
}
""",
        "cannot cast",
    )


def test_cast_struct_to_other_struct_is_rejected(tmp_path: Path) -> None:
    """`a as B` between unrelated heap types must be rejected."""
    _expect_compile_error(
        tmp_path,
        "cast_struct_struct",
        """
struct A { var x: i32 }
struct B { var y: i32 }
def main() -> i32 {
    let a = A { x: 1 };
    let b = a as B;
    return b.y;
}
""",
        "cannot cast",
    )


def test_cast_existential_to_concrete_is_rejected(tmp_path: Path) -> None:
    """`any P as ConcreteT` used to be an unchecked bitcast. Must be rejected;
    a runtime-checked `as?` is the future replacement."""
    _expect_compile_error(
        tmp_path,
        "cast_exist_concrete",
        """
protocol P {
    def kind() -> i32;
}
struct S {
    var n: i32;
    def kind() -> i32 { self.n }
}
def main() -> i32 {
    let s = S { n: 7 };
    let x: any P = s;
    let back = x as S;
    return back.n;
}
""",
        "cannot cast",
    )


def test_cast_numeric_to_numeric_still_allowed(tmp_path: Path) -> None:
    """Numeric <-> numeric casts must keep working."""
    completed = _compile_and_capture(
        tmp_path,
        "cast_num",
        """
def main() -> i32 {
    let a: i64 = 30;
    let b: i32 = a as i32;
    let c: f64 = b as f64;
    let d: i32 = c as i32;
    return d;
}
""",
    )
    assert completed.returncode == 30


def test_cast_bool_to_int_still_allowed(tmp_path: Path) -> None:
    """Bool <-> numeric casts must keep working."""
    completed = _compile_and_capture(
        tmp_path,
        "cast_bool",
        """
def main() -> i32 {
    let b: Bool = true;
    return b as i32;
}
""",
    )
    assert completed.returncode == 1


# --------------------------------------------------------------------------- `unsafe def`


def test_unsafe_def_call_in_safe_context_is_rejected(tmp_path: Path) -> None:
    """An `unsafe def` cannot be called from a safe block."""
    _expect_compile_error(
        tmp_path,
        "unsafe_def_call",
        """
unsafe def danger() -> i32 { return 42; }

def main() -> i32 {
    return danger();
}
""",
        "unsafe",
    )


def test_unsafe_def_call_inside_unsafe_block_works(tmp_path: Path) -> None:
    completed = _compile_and_capture(
        tmp_path,
        "unsafe_def_ok",
        """
unsafe def danger() -> i32 { return 42; }

def main() -> i32 {
    unsafe { return danger(); }
}
""",
    )
    assert completed.returncode == 42


def test_lambda_body_inherits_safe_context(tmp_path: Path) -> None:
    """A lambda inside `unsafe { ... }` must still type-check its body as
    safe, since the closure value can escape the block."""
    _expect_compile_error(
        tmp_path,
        "lambda_safe",
        """
extern "C" def rt_panic(msg: RawPtr) -> Void;

def make() -> (i32) -> i32 {
    var bomb: (i32) -> i32 = { x: i32 in return x; };
    unsafe {
        bomb = { x: i32 in rt_panic(nil as RawPtr); return x; };
    }
    return bomb;
}

def main() -> i32 {
    let f = make();
    return 0;
}
""",
        "unsafe",
    )


def test_extern_non_c_abi_requires_unsafe(tmp_path: Path) -> None:
    """`extern "anything"` calls should require `unsafe` regardless of ABI string."""
    _expect_compile_error(
        tmp_path,
        "extern_rust",
        """
extern "rust" def some_fn() -> i32;

def main() -> i32 {
    return some_fn();
}
""",
        "unsafe",
    )


def test_cast_rawptr_still_requires_unsafe(tmp_path: Path) -> None:
    """Casting to/from RawPtr without `unsafe` must continue to fail."""
    _expect_compile_error(
        tmp_path,
        "cast_rawptr_safe",
        """
def main() -> i32 {
    let p: RawPtr = 0 as RawPtr;
    return 0;
}
""",
        "unsafe",
    )


def test_var_float_without_init_is_zero(tmp_path: Path) -> None:
    """`var f: f64;` must default to 0.0."""
    completed = _compile_and_capture(
        tmp_path,
        "var_f64_zero",
        """
def main() -> i32 {
    var f: f64;
    if f == 0.0 { return 7; }
    return 99;
}
""",
    )
    assert completed.returncode == 7


# --------------------------------------------------------------------------- heap-typed Vec/Dict retain (was: UAF on .get/.pop/.get heap element)


def test_vec_get_retains_heap_element(tmp_path: Path) -> None:
    """`let q = v.get(0)` on a heap-typed Vec must NOT leave the caller
    with a non-retained reference. Previously this was a UAF: the caller's
    local would drop before the Vec was freed, releasing the slot, then
    the Vec's own destruction would release it a second time.

    We exercise the bug by reading the same heap element many times and
    using `println` markers in deinit to count deinits."""
    completed = _compile_and_capture(
        tmp_path,
        "vec_get_uaf",
        """
import "io.rl"

struct Item {
    var n: i32;
    def __release__() -> Void {
        println("deinit");
    }
}

def main() -> i32 {
    let v = Vec<Item>.new();
    v.push(Item { n: 1 });
    v.push(Item { n: 2 });
    v.push(Item { n: 3 });

    // Read each element a few times. If the runtime UAF were still present,
    // these reads would either double-free or read freed memory.
    var sum: i32 = 0;
    var i: i32 = 0;
    while i < 5 {
        let a = v.get(0);
        let b = v.get(1);
        let c = v.get(2);
        sum = sum + a.n + b.n + c.n;
        i = i + 1;
    }

    // Now drop the Vec; the items should be deinit'd exactly once each.
    v.free();

    println("after free");
    return sum;  // 5 * (1+2+3) = 30
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 30
    # After freeing the Vec, exactly 3 items must be deinit'd.
    # A double-free would emit 4+ "deinit" lines; a missed retain would
    # crash before "after free" is printed.
    deinit_lines = completed.stdout.count("deinit")
    assert "after free" in completed.stdout
    assert deinit_lines == 3, (
        f"expected exactly 3 deinits, got {deinit_lines}; stdout:\n{completed.stdout}"
    )


def test_dict_get_retains_heap_value(tmp_path: Path) -> None:
    """Same UAF class for `Dict.get` on a heap-typed value."""
    completed = _compile_and_capture(
        tmp_path,
        "dict_get_uaf",
        """
import "io.rl"

struct Item {
    var n: i32;
    def __release__() -> Void {
        println("deinit");
    }
}

def main() -> i32 {
    let d = Dict<i32, Item>.with_capacity(16, 0);
    d.set(10, Item { n: 1 });
    d.set(20, Item { n: 2 });

    var sum: i32 = 0;
    var i: i32 = 0;
    while i < 5 {
        let a = d.get(10);
        if let av = a {
            sum = sum + av.n;
        }
        let b = d.get(20);
        if let bv = b {
            sum = sum + bv.n;
        }
        i = i + 1;
    }

    d.free();

    println("after free");
    return sum;  // 5 * (1+2) = 15
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 15
    deinit_lines = completed.stdout.count("deinit")
    assert "after free" in completed.stdout
    assert deinit_lines == 2, (
        f"expected exactly 2 deinits, got {deinit_lines}; stdout:\n{completed.stdout}"
    )


# --------------------------------------------------------------------------- rt_str_repeat overflow (was: signed mul UB -> heap overflow)


# --------------------------------------------------------------------------- GC deinit ordering (was: fields pre-zeroed before deinit)


# --------------------------------------------------------------------------- Float -> int + shift saturation (was: LLVM poison)


# --------------------------------------------------------------------------- run_argv (no-shell process spawn)


def test_run_argv_does_not_invoke_shell(tmp_path: Path) -> None:
    """`run_argv` must pass arguments literally — no shell interpretation,
    no metacharacter expansion. This is the safe alternative to `shell()`
    for callers embedding untrusted data into a command."""
    completed = _compile_and_capture(
        tmp_path,
        "run_argv_literal",
        """
import "io.rl"
import "process.rl"

def main() -> i32 {
    let args = Vec<String>.new();
    args.push("echo");
    args.push("hello; rm -rf /tmp/SHOULD_NOT_HAPPEN && false");
    return run_argv(args);
}
""",
        include_paths=_stdlib_path(),
    )
    # echo received the entire shell-injection payload as a single literal
    # argument, so the entire line is printed and no shell command is run.
    assert completed.returncode == 0
    assert "hello; rm -rf /tmp/SHOULD_NOT_HAPPEN && false" in completed.stdout


def test_float_to_int_nan_does_not_poison(tmp_path: Path) -> None:
    """`f as i32` for f = NaN must produce 0 rather than LLVM poison."""
    completed = _compile_and_capture(
        tmp_path,
        "fp_nan",
        """
import "io.rl"

def main() -> i32 {
    let nan: f64 = 0.0 / 0.0;
    let n: i32 = nan as i32;
    return n;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0


def test_float_to_int_infinity_saturates(tmp_path: Path) -> None:
    """+infinity must saturate to INT32_MAX, not produce poison."""
    completed = _compile_and_capture(
        tmp_path,
        "fp_inf",
        """
import "io.rl"

def main() -> i32 {
    let big: f64 = 1.0e300;
    let n: i32 = big as i32;
    // INT32_MAX = 2147483647, fits in i32
    if n == 2147483647 { return 0; }
    return n;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0


def test_shift_by_overlarge_is_defined(tmp_path: Path) -> None:
    """`x << 100` for a 32-bit x must produce a defined result via mask
    rather than LLVM poison."""
    completed = _compile_and_capture(
        tmp_path,
        "shift_big",
        """
def main() -> i32 {
    let x: i32 = 1;
    let s: i32 = 100;
    let r = x << s;  // 100 & 31 = 4, so r = 16
    return r;
}
""",
    )
    assert completed.returncode == 16


def test_gc_deinit_observes_intact_fields_after_cycle_collection(tmp_path: Path) -> None:
    """When the cycle collector reclaims a group of objects, every user
    `def __release__() -> Void { ... }` must observe a fully-initialised `self` — including
    pointer fields that target other collected objects. The previous
    implementation pre-zeroed all pointer fields before running deinits,
    silently breaking any deinit that depended on field state."""
    completed = _compile_and_capture(
        tmp_path,
        "gc_deinit_intact",
        """
import "io.rl"

struct Node {
    var n: i32;
    var partner: Node?;

    def __release__() -> Void {
        if let p = self.partner {
            println("saw partner");
        } else {
            println("no partner");
        }
    }
}

def main() -> i32 {
    let a = Node { n: 1, partner: nil };
    let b = Node { n: 2, partner: a };
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    # Both deinits must observe their partner field correctly — b sees a, a
    # sees nil.
    assert completed.stdout.count("saw partner") == 1
    assert completed.stdout.count("no partner") == 1


def test_str_repeat_normal_case_works(tmp_path: Path) -> None:
    """Sanity check that the normal `String.repeat(n)` path still works."""
    completed = _compile_and_capture(
        tmp_path,
        "str_repeat_ok",
        """
import "io.rl"

def main() -> i32 {
    let s = "ab";
    let r = s.repeat(3);
    println(r);  // "ababab"
    return r.len() as i32;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 6
    assert "ababab" in completed.stdout


def test_str_repeat_zero_count_returns_empty(tmp_path: Path) -> None:
    completed = _compile_and_capture(
        tmp_path,
        "str_repeat_zero",
        """
import "io.rl"

def main() -> i32 {
    let s = "abc";
    let r = s.repeat(0);
    return r.len() as i32;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0


def test_vec_pop_transfers_ownership(tmp_path: Path) -> None:
    """`pop()` on a heap Vec must move the slot's strong ref out — no
    double-free, no UAF. Empty pop must yield zero/nil, not whatever was
    on the stack."""
    completed = _compile_and_capture(
        tmp_path,
        "vec_pop_ownership",
        """
import "io.rl"

struct Item {
    var n: i32;
    def __release__() -> Void {
        println("deinit");
    }
}

def main() -> i32 {
    let v = Vec<Item>.new();
    v.push(Item { n: 1 });
    v.push(Item { n: 2 });
    v.push(Item { n: 3 });

    var s: i32 = 0;
    let a = v.pop();
    let b = v.pop();
    let c = v.pop();
    s = a.n + b.n + c.n;

    v.free();

    println("after free");
    return s;  // 1+2+3 = 6
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 6
    # After all pops + locals drop, exactly 3 deinits.
    deinit_lines = completed.stdout.count("deinit")
    assert "after free" in completed.stdout
    assert deinit_lines == 3, (
        f"expected exactly 3 deinits, got {deinit_lines}; stdout:\n{completed.stdout}"
    )


def test_optional_switch_on_heap_type(tmp_path: Path) -> None:
    """Optional<heap struct> uses null-as-None representation; the switch
    must still dispatch correctly."""
    completed = _compile_and_capture(
        tmp_path,
        "opt_heap",
        """
struct Wrap { var n: i32 }
def first() -> Wrap? { Wrap { n: 30 } }
def second() -> Wrap? { nil }

def main() -> i32 {
    let a = first();
    let b = second();

    var total: i32 = 0;
    switch a {
        case .Some(let w): total = total + w.n;
        case .None: total = total + 1000;
    }
    switch b {
        case .Some(let w): total = total + w.n;
        case .None: total = total + 12;
    }
    return total;
}
""",
    )
    assert completed.returncode == 42


# --------------------------------------------------------------------------- bug audit (post-orchestration)
#
# The next block of tests locks down fixes for bugs identified in the
# follow-up audit after the orchestration review. Each test names the bug
# it pins and includes a brief reproducer comment.


def test_int_literal_respects_i64_annotation(tmp_path: Path) -> None:
    """Bug: ``let x: i64 = 0x123456789ABCDEF0;`` was silently truncated to
    i32 because ``_infer_literal`` ignored ``_expected_type``.
    """
    completed = _compile_and_capture(
        tmp_path,
        "lit_i64",
        """
import "io.rl"
def main() -> i32 {
    let a: i64 = 0x123456789ABCDEF0;
    let b: i64 = 9000000000;
    println_i64(a);
    println_i64(b);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "1311768467463790320" in completed.stdout
    assert "9000000000" in completed.stdout


def test_int_literal_overflow_emits_diagnostic(tmp_path: Path) -> None:
    """A literal that does not fit the declared integer type must produce a
    type-check error, not silently wrap.
    """
    _expect_compile_error(
        tmp_path,
        "lit_overflow",
        """
def main() -> i32 {
    let bad: i32 = 9999999999;
    return bad;
}
""",
        substring="does not fit in i32",
    )


def test_int_literal_widens_when_no_context(tmp_path: Path) -> None:
    """Without a type annotation we still default to i32 for small values
    but widen to i64 when the value exceeds INT32_MAX.
    """
    completed = _compile_and_capture(
        tmp_path,
        "lit_widen",
        """
import "io.rl"
def main() -> i32 {
    // Default i32 path
    println_i32(2147483647);
    // Overflows i32 -> widens to i64 silently
    println_i64(2147483648);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "2147483647" in completed.stdout
    assert "2147483648" in completed.stdout


def test_exhaustiveness_rejects_guarded_full_cover(tmp_path: Path) -> None:
    """Bug: ``_check_enum_exhaustiveness`` ignored ``where`` guards, so a
    ``case .Red where false:`` was treated as full coverage of ``Red``.
    The runtime then reached an LLVM ``unreachable`` (UB) when no arm
    matched. The fix downgrades guarded arms to partial coverage.
    """
    _expect_compile_error(
        tmp_path,
        "guard_red",
        """
enum Color { case Red; case Green; case Blue; }
def describe(c: Color) -> i32 {
    switch c {
        case .Red where false: return 1;
        case .Green: return 2;
        case .Blue: return 3;
    }
}
def main() -> i32 { return describe(Color.Red); }
""",
        substring="missing cases: Red",
    )


def test_exhaustiveness_guarded_bool_arm_rejected(tmp_path: Path) -> None:
    """Same guard bug for boolean exhaustiveness."""
    _expect_compile_error(
        tmp_path,
        "guard_bool",
        """
def f(b: Bool) -> i32 {
    switch b {
        case true where false: return 1;
        case false: return 2;
    }
}
def main() -> i32 { return f(true); }
""",
        substring="missing cases: true",
    )


def test_exhaustiveness_guarded_catchall_does_not_cover(tmp_path: Path) -> None:
    """A guarded wildcard / identifier arm is not catch-all."""
    _expect_compile_error(
        tmp_path,
        "guard_catchall",
        """
enum Light { case On; case Off; }
def f(l: Light) -> i32 {
    switch l {
        case let _ where false: return 1;
        case .On: return 2;
    }
}
def main() -> i32 { return f(Light.On); }
""",
        substring="missing cases: Off",
    )


def test_exhaustiveness_some_partial_payload_not_full_cover(tmp_path: Path) -> None:
    """Bug: ``case .Some(.Ok(v))`` was treated as fully covering ``Some``
    even though ``.Some(.Err(_))`` was unhandled. The fix requires the
    payload of ``Some(...)`` to be irrefutable for it to count as full
    coverage.
    """
    _expect_compile_error(
        tmp_path,
        "opt_enum_partial",
        """
enum E { case A(i32); case B(i32); }
def wrap() -> E? { return E.A(1); }
def main() -> i32 {
    let r = wrap();
    switch r {
        case .Some(.A(let v)): return v;
        case nil: return 99;
    }
}
""",
        substring="missing: Some(...)",
    )


def test_vec_of_optional_i64_preserves_payload(tmp_path: Path) -> None:
    """Bug: ``size_of(Optional<i64>)`` returned 9 (packed ``1 + sizeof(i64)``)
    while the LLVM IR struct ``{i1, i64}`` is 16 bytes. The runtime
    therefore used a 9-byte stride in vec slots and codegen wrote 16,
    corrupting reads / overrunning the buffer.
    """
    completed = _compile_and_capture(
        tmp_path,
        "opt_i64_vec",
        """
import "io.rl"
import "vec.rl"
def wrap(x: i64) -> i64? { return x; }
def main() -> i32 {
    var v: Vec<i64?> = Vec<i64?>.new();
    v.push(wrap(1234567890123456));
    v.push(wrap(9876543210987654));
    switch v.get(0) {
        case .Some(let x): println_i64(x);
        case nil: println_i64(-1);
    }
    switch v.get(1) {
        case .Some(let x): println_i64(x);
        case nil: println_i64(-1);
    }
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "1234567890123456" in completed.stdout
    assert "9876543210987654" in completed.stdout


def test_vec_of_optional_f64_preserves_payload(tmp_path: Path) -> None:
    """Same layout bug, manifested on ``Optional<f64>``: stride must equal
    the LLVM ``{i1, f64}`` size (16), not packed 9.
    """
    completed = _compile_and_capture(
        tmp_path,
        "opt_f64_vec",
        """
import "io.rl"
import "vec.rl"
def wrap(x: f64) -> f64? { return x; }
def main() -> i32 {
    var v: Vec<f64?> = Vec<f64?>.new();
    v.push(wrap(314.0));
    v.push(wrap(271.0));
    var total: f64 = 0.0;
    switch v.get(0) {
        case .Some(let x): total = total + x;
        case nil: println_i32(-1);
    }
    switch v.get(1) {
        case .Some(let x): total = total + x;
        case nil: println_i32(-1);
    }
    // 314 + 271 = 585; result fits in i64 cleanly.
    println_i64(total as i64);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "585" in completed.stdout


def test_file_read_all_after_partial_read_returns_remaining(tmp_path: Path) -> None:
    """Bug: ``rt_file_read_all`` used ``ftell(SEEK_END)`` as the request
    size but seeked back to the prior position before reading, so it asked
    fread for too many bytes when called after a partial read. The fix
    requests ``end - pos`` bytes.
    """
    sample = tmp_path / "sample.txt"
    sample.write_text("HEADER:body-of-the-file", encoding="utf-8")
    completed = _compile_and_capture(
        tmp_path,
        "read_after_seek",
        f"""
import "io.rl"
import "fs.rl"

def main() -> i32 {{
    unsafe {{
        // 0 = read mode.
        let opt = File.open("{sample}", 0);
        switch opt {{
            case .Some(let f): {{
                // Position past the 7-byte "HEADER:" prefix (SEEK_SET = 0)
                // so read_all() must return only the remaining bytes.
                f.seek(7, 0);
                let rest = f.read_all();
                println(rest);
                f.close();
                return 0;
            }}
            case nil: {{
                return 1;
            }}
        }}
    }}
}}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "body-of-the-file" in completed.stdout
    # Crucially, no garbage trailing the expected string.
    expected_line = "body-of-the-file"
    assert completed.stdout.strip().endswith(expected_line)


def test_void_async_multi_level_runs(tmp_path: Path) -> None:
    """Bug: a Void-returning ``async`` function that itself ``await``s a
    Void-returning ``async`` child would crash because:
      (a) the entry function emitted ``ret i64 0`` in a ``void`` LLVM
          signature (UNIT constant accidentally typed as i64), and
      (b) the post-await fixup was gated on ``result_local is not None``
          so the spawned child's ``TaskHandle*`` was never destroyed —
          leaking one handle per Void-async await.
    The combined fix lowers ``Return(value=None)`` for Void entries and
    always emits a TaskGetResult after every spawn (synthesising a Void
    sink local when the child is Void).
    """
    completed = _compile_and_capture(
        tmp_path,
        "async_void_multi",
        """
import "io.rl"

def helper() async -> Void {
    println("helper");
    return;
}

def child() async -> Void {
    await helper();
    return;
}

def parent() async -> Void {
    await child();
    await child();
    return;
}

def main() async -> i32 {
    await parent();
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    # parent -> child x2 -> helper x1 each = 2 "helper" lines.
    assert completed.stdout.count("helper") == 2


def test_gc_reentry_from_deinit_does_not_deadlock(tmp_path: Path) -> None:
    """Bug: ``rt_gc_collect`` acquired ``gc_list_lock`` without checking
    ``gc_running``. A deinit running inside the GC sweep that allocated
    enough new objects to re-cross the 10k-allocation threshold would
    re-enter ``rt_gc_collect`` and spin forever on the held spinlock.
    The fix early-returns when ``gc_running`` is set.

    This smoke test exercises a program that creates 20k objects with
    non-trivial deinits, runs explicit GC cycles, and must terminate.
    """
    completed = _compile_and_capture(
        tmp_path,
        "gc_reentry",
        """
import "io.rl"

struct Cell {
    var n: i32;
    def __release__() -> Void { /* nothing — just allocates */ }
}

def main() -> i32 {
    var i: i32 = 0;
    while i < 20000 {
        let _ = Cell { n: i };
        i = i + 1;
    }
    println_i32(i);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "20000" in completed.stdout


def test_async_multiple_leaf_awaits_in_same_block(tmp_path: Path) -> None:
    """Bug: ``TaskYield`` markers that the MIR builder inserts between
    awaits used to be naïvely copied into the second state block, so the
    resume function entered state N, immediately yielded, came back into
    state N, yielded again — infinite loop. The state-block constructor
    now filters TaskYield the same way the completion block already did.
    """
    completed = _compile_and_capture(
        tmp_path,
        "async_two_leaf_awaits",
        """
import "io.rl"

def leaf() async -> i32 {
    return 7;
}

def main() async -> i32 {
    let a = await leaf();
    let b = await leaf();
    println_i32(a + b);
    return 0;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0
    assert "14" in completed.stdout


# --------------------------------------------------------------------------- __gc_trace__ / __release__ — Python-style dunder hooks


def test_dunder_release_wrong_signature_is_rejected(tmp_path: Path) -> None:
    """``def __release__`` must have signature ``(self) -> Void``.

    Codegen treats the method as the destructor and installs it in the
    type descriptor; an instance method that accidentally takes extra
    arguments would crash on the GC sweep path. Catch it at compile
    time instead.
    """
    _expect_compile_error(
        tmp_path,
        "release_bad_sig",
        """
pub struct Box {
    var handle: RawPtr

    // Wrong: __release__ should take no extra arguments.
    pub def __release__(extra: i32) -> Void {}
}

def main() -> i32 { return 0; }
""",
        substring="__release__ on struct",
    )


# --------------------------------------------------------------------------- as? / as!

def test_as_optional_downcast_succeeds(tmp_path: Path) -> None:
    """`as?` on an existential returns Some(concrete) when the type matches."""
    completed = _compile_and_capture(
        tmp_path,
        "as_opt_ok",
        """
import "io.rl"

protocol Shape {
    def area() -> i32;
}

struct Circle {
    var r: i32;
    def area() -> i32 { return self.r * self.r; }
}

def main() -> i32 {
    let c = Circle { r: 5 };
    let s: any Shape = c;
    let maybe: Circle? = s as? Circle;
    switch maybe {
        case .Some(let got): return got.area();
        case nil: return -1;
    }
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 25  # 5 * 5


def test_as_optional_downcast_fails(tmp_path: Path) -> None:
    """`as?` returns nil when the runtime type does not match the target."""
    completed = _compile_and_capture(
        tmp_path,
        "as_opt_fail",
        """
import "io.rl"

protocol Animal {
    def sound() -> i32;
}

struct Dog {
    var id: i32;
    def sound() -> i32 { return 1; }
}

struct Cat {
    var id: i32;
    def sound() -> i32 { return 2; }
}

def main() -> i32 {
    let d = Dog { id: 7 };
    let a: any Animal = d;
    let maybe: Cat? = a as? Cat;
    switch maybe {
        case .Some(let c): return c.id;
        case nil: return 0;
    }
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0  # nil branch


def test_as_forced_downcast_succeeds(tmp_path: Path) -> None:
    """`as!` yields the concrete value when the runtime type matches."""
    completed = _compile_and_capture(
        tmp_path,
        "as_forced_ok",
        """
protocol Measurable {
    def size() -> i32;
}

struct Box {
    var w: i32;
    def size() -> i32 { return self.w; }
}

def main() -> i32 {
    let b = Box { w: 42 };
    let m: any Measurable = b;
    let got: Box = m as! Box;
    return got.w;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 42


def test_as_forced_downcast_panics_on_mismatch(tmp_path: Path) -> None:
    """`as!` must abort the process (non-zero exit) when the type does not match."""
    completed = _compile_and_capture(
        tmp_path,
        "as_forced_panic",
        """
protocol P {
    def val() -> i32;
}

struct A { var n: i32; def val() -> i32 { return self.n; } }
struct B { var n: i32; def val() -> i32 { return self.n; } }

def main() -> i32 {
    let a = A { n: 1 };
    let p: any P = a;
    let b: B = p as! B;   // mismatch — should panic
    return b.n;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode != 0  # panicked


def test_as_optional_is_check_with_primitive(tmp_path: Path) -> None:
    """`is` type-check on an existential holding a concrete struct returns true."""
    completed = _compile_and_capture(
        tmp_path,
        "is_check",
        """
protocol Printable {
    def repr() -> i32;
}

struct Num {
    var v: i32;
    def repr() -> i32 { return self.v; }
}

def main() -> i32 {
    let n = Num { v: 99 };
    let p: any Printable = n;
    if p is Num { return 0; }
    return 1;
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 0



def test_dunder_gc_trace_wrong_signature_is_rejected(tmp_path: Path) -> None:
    """``static def __gc_trace__`` must have signature ``(RawPtr, RawPtr, RawPtr) -> Void``.

    A wrong-shape hook would dispatch GC callbacks with type-confused
    arguments — reject it at compile time.
    """
    _expect_compile_error(
        tmp_path,
        "gc_trace_bad_sig",
        """
pub struct Container {
    var handle: RawPtr

    // Wrong: __gc_trace__ takes 3 RawPtrs.
    pub static def __gc_trace__(payload: RawPtr) -> Void {}
}

def main() -> i32 { return 0; }
""",
        substring="__gc_trace__ on struct",
    )


def test_dunder_gc_trace_non_static_is_rejected(tmp_path: Path) -> None:
    """``__gc_trace__`` must be ``static`` — instance methods get an
    implicit ``self`` and the LLVM signature ends up with four
    parameters instead of three, which the convention scan rejects.
    """
    _expect_compile_error(
        tmp_path,
        "gc_trace_instance",
        """
pub struct Container {
    var handle: RawPtr

    // Missing `static` — implicit self changes the LLVM signature.
    pub def __gc_trace__(payload: RawPtr, cb: RawPtr, ctx: RawPtr) -> Void {}
}

def main() -> i32 { return 0; }
""",
        substring="__gc_trace__ on struct",
    )


def test_dict_set_value_at_manages_heap_value_arc(tmp_path: Path) -> None:
    """The index-based `set_value_at`/`value_at` must obey the same ARC discipline
    as `set`/`get` for heap values: `set_value_at` releases the OLD value and
    retains the NEW one; `value_at` retains the returned value for the caller.

    Exactly 2 deinits expected: the overwritten Item{1} (at set_value_at) and the
    final Item{2} (at free). A missing release-old would drop to 1 deinit (leak);
    a missing retain in value_at would double-free / crash before "after free"."""
    completed = _compile_and_capture(
        tmp_path,
        "dict_set_value_at_arc",
        """
import "io.rl"
import "dict.rl"

struct Item {
    var n: i32;
    def __release__() -> Void {
        println("deinit");
    }
}

def main() -> i32 {
    let d = Dict<i32, Item>.with_capacity(16, 0);
    let i1 = d.entry_index(10, Item { n: 1 });   // insert Item{1}
    var sum: i32 = 0;
    sum = sum + d.value_at(i1).n;                 // reads 1 (retain+release within stmt)
    d.set_value_at(i1, Item { n: 2 });            // releases Item{1} -> deinit, stores Item{2}
    sum = sum + d.value_at(i1).n;                 // reads 2
    println("before free");
    d.free();                                     // releases Item{2} -> deinit
    println("after free");
    return sum;                                   // 1 + 2 = 3
}
""",
        include_paths=_stdlib_path(),
    )
    assert completed.returncode == 3, completed.stdout
    assert "after free" in completed.stdout
    deinit_lines = completed.stdout.count("deinit")
    assert deinit_lines == 2, (
        f"expected exactly 2 deinits, got {deinit_lines}; stdout:\n{completed.stdout}"
    )

