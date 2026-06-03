"""End-to-end executable tests for runtime-backed features."""

from __future__ import annotations

import subprocess
from pathlib import Path

from rolang.driver import CompileOptions, EmitKind, compile_source


def compile_and_run(tmp_path: Path, name: str, source: str, include_paths: list = None) -> int:
    source_path = tmp_path / f"{name}.rl"
    output_path = tmp_path / name
    source_path.write_text(source, encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=include_paths or [],
        ),
    )

    assert result.success
    assert result.output_path == output_path

    completed = subprocess.run([str(output_path)], check=False)
    return completed.returncode


def test_generic_static_constructor_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "generic_static_constructor",
        """
struct Box<T> {
    var value: T;

    static def new(value: T) -> Box<T> {
        return Box { value: value };
    }
}

def main() -> i32 {
    let b: Box<i32> = Box.new(42);
    return b.value;
}
""",
    )

    assert exit_code == 42


def test_generic_static_sizeof_uses_specialized_type(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "generic_static_sizeof",
        """
struct Box<T> {
    var elem_size: i32;

    static def new(value: T) -> Box<T> {
        return Box { elem_size: size_of(T) };
    }
}

def main() -> i32 {
    let b: Box<String> = Box.new("hello");
    if b.elem_size != 8 { return b.elem_size; }
    return 0;
}
""",
    )

    assert exit_code == 0


def test_generic_static_method_returns_different_type(tmp_path: Path):
    """Static generic method with return type != owner type handled correctly (Bug 1 regr)."""
    exit_code = compile_and_run(
        tmp_path,
        "static_other_type",
        """
struct Elem<T> {
    var value: T;

    static def make(value: T) -> Elem<T> {
        return Elem { value: value };
    }

    static def size_of() -> i32 {
        return size_of(T);
    }
}

def main() -> i32 {
    let a: Elem<String> = Elem.make("hello");
    let s: i32 = Elem<String>.size_of();
    if s != 8 { return s; }
    return 0;
}
""",
    )

    assert exit_code == 0


def test_legacy_struct_constructor_syntax_is_rejected(tmp_path: Path):
    source_path = tmp_path / "legacy_constructor.rl"
    output_path = tmp_path / "legacy_constructor"
    source_path.write_text(
        """
struct Point { var x: i32; }

def main() -> i32 {
    let p = Point(x: 1);
    return p.x;
}
""",
        encoding="utf-8",
    )

    result = compile_source(
        source_path,
        CompileOptions(emit=EmitKind.EXECUTABLE, output_path=output_path),
    )

    assert not result.success


def test_array_literal_index_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "array_index",
        """
def main() -> i32 {
    let xs = [10, 20, 30];
    return xs[1];
}
""",
    )

    assert exit_code == 20


def test_string_keyed_dictionary_lookup_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "dict_lookup",
        """
def main() -> i32 {
    let values = ["a": 10, "b": 20];
    if let value = values["b"] {
        return value;
    }
    return 7;
}
""",
    )

    assert exit_code == 20


def test_dictionary_missing_key_returns_none(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "dict_missing",
        """
def main() -> i32 {
    let values = ["a": 10, "b": 20];
    if let value = values["c"] {
        return value;
    }
    return 7;
}
""",
    )

    assert exit_code == 7


def test_collection_count_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "collection_count",
        """
def main() -> i32 {
    let xs = [10, 20, 30];
    let values = ["a": 1, "b": 2];
    return xs.len() + (values.len() as i32);
}
""",
    )

    assert exit_code == 5


def test_array_for_loop_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "array_for_loop",
        """
def main() -> i32 {
    var total = 0;
    for x in [1, 2, 3, 4] {
        total = total + x;
    }
    return total;
}
""",
    )

    assert exit_code == 10


def test_protocol_existential_method_dispatch_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "protocol_dispatch",
        """
protocol RuntimeValued {
    def value() -> i32;
}

struct RuntimeValuedBox {
    def value() -> i32 {
        return 42;
    }
}

def main() -> i32 {
    let p: any RuntimeValued = RuntimeValuedBox {};
    return p.value();
}
""",
    )

    assert exit_code == 42


def test_generic_function_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "generic_identity",
        """
def identity<T>(x: T) -> T {
    return x;
}

def main() -> i32 {
    return identity(42);
}
""",
    )

    assert exit_code == 42


def test_generic_function_multiple_instantiations_execute(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "generic_identity_multi",
        """
def identity<T>(x: T) -> T {
    return x;
}

def main() -> i32 {
    let n = identity(40);
    let ok = identity(true);
    if ok {
        return n + 2;
    }
    return 0;
}
""",
    )

    assert exit_code == 42


def test_generic_struct_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "generic_struct",
        """
struct RuntimeBox<T> {
    var value: T;
}

def main() -> i32 {
    let box = RuntimeBox<i32> { value: 42 };
    return box.value;
}
""",
    )

    assert exit_code == 42


def test_generic_struct_method_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "generic_struct_method",
        """
struct RuntimeMethodBox<T> {
    var value: T;

    def choose(x: T) -> T {
        return x;
    }
}

def main() -> i32 {
    let box = RuntimeMethodBox<i32> { value: 1 };
    return box.choose(42);
}
""",
    )

    assert exit_code == 42


def test_extension_method_can_mutate_self(tmp_path: Path):
    """Extension methods can freely update `self`."""
    exit_code = compile_and_run(
        tmp_path,
        "extension_mutate_self",
        """
struct RuntimePoint {
    var x: i64;
    var y: i64;
}

extension RuntimePoint {
    def add(other: RuntimePoint) -> Void {
        self.x = self.x + other.x;
        self.y = self.y + other.y;
    }
}

def main() -> i32 {
    var p1 = RuntimePoint { x: 3, y: 4 };
    let p2 = RuntimePoint { x: 1, y: 2 };
    p1.add(p2);
    return (p1.x + p1.y) as i32;
}
""",
    )

    assert exit_code == 10


def test_optional_value_wrap_in_let_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_let",
        """
def main() -> i32 {
    let x: i32? = 42;
    if let v = x { return v; }
    return 0;
}
""",
    )
    assert exit_code == 42


def test_optional_nil_literal_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_nil",
        """
def main() -> i32 {
    let x: i32? = nil;
    if let v = x { return v; }
    return 99;
}
""",
    )
    assert exit_code == 99


def test_optional_function_argument_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_arg",
        """
def take(x: i32?) -> i32 {
    if let v = x { return v; }
    return 0;
}
def main() -> i32 {
    take(42)
}
""",
    )
    assert exit_code == 42


def test_optional_int_widening_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_widen",
        """
def take(x: i64?) -> i64 {
    if let v = x { return v; }
    return 0;
}
def main() -> i32 {
    let a: i32 = 42;
    take(a) as i32
}
""",
    )
    assert exit_code == 42


def test_optional_struct_field_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_field",
        """
struct Box { var v: i32? }
def main() -> i32 {
    let b = Box { v: 42 };
    if let x = b.v { return x; }
    return 0;
}
""",
    )
    assert exit_code == 42


def test_optional_assignment_to_var_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_assign",
        """
def main() -> i32 {
    var x: i32? = nil;
    x = 42;
    if let v = x { return v; }
    return 0;
}
""",
    )
    assert exit_code == 42


def test_optional_return_value_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "opt_return",
        """
def maybe(b: Bool) -> i32? {
    if b { return 42; }
    return nil;
}
def main() -> i32 {
    if let v = maybe(true) { return v; }
    return 0;
}
""",
    )
    assert exit_code == 42


def test_enum_no_payload_constructor_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "enum_no_payload",
        """
enum Color {
    case red
    case green
    case blue
}

def main() -> i32 {
    let c = Color.green;
    switch c {
    case .red: return 1;
    case .green: return 2;
    case .blue: return 3;
    }
}
""",
    )
    assert exit_code == 2


def test_enum_payload_constructor_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "enum_with_payload",
        """
enum Either {
    case left(i32)
    case right
}

def main() -> i32 {
    let x = Either.left(42);
    switch x {
    case .left(let v): return v;
    case .right: return 0;
    }
}
""",
    )
    assert exit_code == 42


def test_generic_enum_constructor_inference_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "enum_generic_infer",
        """
enum Opt<T> {
    case none
    case some(T)
}

def main() -> i32 {
    let x = Opt.some(42);
    switch x {
    case .none: return 0;
    case .some(let v): return v;
    }
}
""",
    )
    assert exit_code == 42


def test_generic_enum_constructor_with_annotation_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "enum_generic_annot",
        """
enum Opt<T> {
    case none
    case some(T)
}

def main() -> i32 {
    let x: Opt<i32> = Opt.some(42);
    switch x {
    case .none: return 0;
    case .some(let v): return v;
    }
}
""",
    )
    assert exit_code == 42


def test_protocol_bound_generic_method_dispatch_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "proto_bound_method",
        """
protocol Show {
    def show() -> i32;
}

struct A {
    var v: i32
    def show() -> i32 { self.v }
}

def call_show<T: Show>(item: T) -> i32 {
    item.show()
}

def main() -> i32 {
    let a = A { v: 42 };
    call_show(a)
}
""",
    )
    assert exit_code == 42


def test_protocol_bound_generic_dispatch_multiple_types_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "proto_bound_multi",
        """
protocol HasValue {
    def value() -> i32;
}

struct A { var v: i32; def value() -> i32 { self.v } }
struct B { var w: i32; def value() -> i32 { self.w * 10 } }

def double<T: HasValue>(item: T) -> i32 {
    item.value() * 2
}

def main() -> i32 {
    let a = A { v: 21 };
    let b = B { w: 7 };
    double(a) + double(b)
}
""",
    )
    assert exit_code == 182


def test_protocol_bound_generic_property_access_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "proto_bound_property",
        """
protocol HasSize {
    var size: i32 { get };
}

struct SizedBox {
    var size: i32
}

def get_size<T: HasSize>(x: T) -> i32 {
    x.size
}

def main() -> i32 {
    let b = SizedBox { size: 42 };
    get_size(b)
}
""",
    )
    assert exit_code == 42


def test_generic_struct_constructor_inference_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "struct_generic_infer",
        """
struct GenBox<T> {
    var v: T
    def get() -> T { self.v }
}

def main() -> i32 {
    let b = GenBox { v: 42 };
    b.get()
}
""",
    )
    assert exit_code == 42


def test_generic_struct_constructor_two_params_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "struct_generic_two",
        """
struct GenPair<A, B> {
    var fst: A
    var snd: B
}

def main() -> i32 {
    let p = GenPair { fst: 10, snd: 32 };
    (p.fst + p.snd) as i32
}
""",
    )
    assert exit_code == 42


def test_generic_struct_unbound_param_errors(tmp_path: Path):
    """Generic struct with field types not constrained by args yields a clear error."""
    from rolang.driver import CompileOptions, EmitKind, compile_source

    source_path = tmp_path / "bad.rl"
    source_path.write_text(
        """
struct UnboundHolder<T> {
    var name: i32
}

def main() -> i32 {
    let h = UnboundHolder { name: 42 };
    h.name
}
""",
        encoding="utf-8",
    )
    result = compile_source(
        source_path,
        CompileOptions(emit=EmitKind.EXECUTABLE, output_path=tmp_path / "bad"),
    )
    assert not result.success
    msgs = "\n".join(d.message for d in result.diagnostics.diagnostics)
    assert "Cannot infer type parameter" in msgs
    assert "UnboundHolder" in msgs


def test_generic_struct_method_returns_field_executes(tmp_path: Path):
    """Method body that returns a field whose type is the generic param."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_field_return",
        """
struct GenFieldBox<T> {
    var v: T
    def fetch() -> T { self.v }
}
def main() -> i32 {
    let b = GenFieldBox { v: 42 };
    b.fetch()
}
""",
    )
    assert exit_code == 42


def test_generic_struct_nested_generic_field_executes(tmp_path: Path):
    """Generic struct with a field of another generic struct parameterized by T."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_nested_field",
        """
struct InnerGen<T> { var v: T }
struct WrapGen<U> {
    var inner: InnerGen<U>
    def unwrap() -> U { self.inner.v }
}
def main() -> i32 {
    let w = WrapGen { inner: InnerGen { v: 42 } };
    w.unwrap()
}
""",
    )
    assert exit_code == 42


def test_generic_struct_optional_field_executes(tmp_path: Path):
    """Generic struct with field of type T? - tests the substitution path."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_opt_field",
        """
struct OptBox<T> {
    var v: T?
    def or_default(d: T) -> T {
        if let x = self.v { return x; }
        return d;
    }
}
def main() -> i32 {
    let b = OptBox<i32> { v: 42 };
    b.or_default(0)
}
""",
    )
    assert exit_code == 42


def test_generic_struct_array_field_executes(tmp_path: Path):
    """Generic struct with field of type [T] - tests the array substitution path."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_arr_field",
        """
struct ArrBox<T> {
    var items: [T]
}
def main() -> i32 {
    let b = ArrBox<i32> { items: [10, 20, 12] };
    b.items[0] + b.items[1] + b.items[2]
}
""",
    )
    assert exit_code == 42


def test_generic_struct_method_with_self_in_if_executes(tmp_path: Path):
    """`self` referenced only inside an `if` body must still be discovered."""
    exit_code = compile_and_run(
        tmp_path,
        "self_in_if",
        """
struct GuardBox {
    var v: i32
    def get_pos() -> i32 {
        if self.v > 0 { return self.v; }
        return 0;
    }
}
def main() -> i32 {
    let b = GuardBox { v: 42 };
    b.get_pos()
}
""",
    )
    assert exit_code == 42


def test_generic_struct_with_optional_generic_field_inference_executes(tmp_path: Path):
    """Constructor inference unifies through Optional wrapping."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_opt_inner",
        """
struct InnerOG<T> { var v: T }
struct OuterOG<T> {
    var i: InnerOG<T>?
    def value(d: T) -> T {
        if let x = self.i { return x.v; }
        return d;
    }
}
def main() -> i32 {
    let o = OuterOG { i: InnerOG { v: 42 } };
    o.value(0)
}
""",
    )
    assert exit_code == 42


def test_generic_struct_with_array_inner_generic_executes(tmp_path: Path):
    """Generic struct field of type [Inner<T>] — nested-generic monomorphization."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_arr_inner",
        """
struct InnerAG<T> { var v: T }
struct OuterAG<T> {
    var items: [InnerAG<T>]
    def first_v() -> T { self.items[0].v }
}
def main() -> i32 {
    let o = OuterAG<i32> { items: [InnerAG { v: 42 }] };
    o.first_v()
}
""",
    )
    assert exit_code == 42


def test_generic_enum_method_executes(tmp_path: Path):
    """Generic enum methods get monomorphized and dispatched correctly."""
    exit_code = compile_and_run(
        tmp_path,
        "gen_enum_method",
        """
enum WrapMethod<T> {
    case some(T)
    case none
    def or_default(d: T) -> T {
        switch self {
        case .some(let x): return x;
        case .none: return d;
        }
    }
}
def main() -> i32 {
    let w = WrapMethod.some(42);
    w.or_default(0)
}
""",
    )
    assert exit_code == 42


def test_tuple_labeled_member_access_executes(tmp_path: Path):
    """`t.label` reads the correct element (previously always read element 0)."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_labeled",
        """
def main() -> i32 {
    let t = (a: 10, b: 32);
    t.a + t.b
}
""",
    )
    assert exit_code == 42


def test_tuple_subscript_int_literal_executes(tmp_path: Path):
    """`t[0]` and `t[1]` with integer literals — was treated as array index."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_subscript",
        """
def main() -> i32 {
    let t = (10, 32);
    t[0] + t[1]
}
""",
    )
    assert exit_code == 42


def test_tuple_dot_int_member_executes(tmp_path: Path):
    """`t.0`, `t.1` (Swift-style) — required new grammar production."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_dot_int",
        """
def main() -> i32 {
    let t = (10, 20, 12);
    t.0 + t.1 + t.2
}
""",
    )
    assert exit_code == 42


def test_tuple_mixed_label_and_dot_int_executes(tmp_path: Path):
    """Labeled tuples are also reachable by positional `.N`."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_mixed",
        """
def main() -> i32 {
    let t = (a: 10, b: 32);
    t.0 + t.1
}
""",
    )
    assert exit_code == 42


def test_float_literal_still_parses_after_tuple_dot_int(tmp_path: Path):
    """Adding `.INT` to member_suffix must not steal `3.14` from FLOAT."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_float_regression",
        """
def main() -> i32 {
    let x = 3.14;
    let y: i32 = x as i32;
    y
}
""",
    )
    assert exit_code == 3


def test_tuple_labeled_assignment_targets_correct_element_executes(tmp_path: Path):
    """`t.label = ...` must write the labeled element, not always index 0."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_assign_labeled",
        """
def main() -> i32 {
    var t = (a: 0, b: 100);
    t.b = 42;
    t.a + t.b
}
""",
    )
    assert exit_code == 42


def test_tuple_subscript_int_assignment_executes(tmp_path: Path):
    """`t[N] = ...` with an integer literal writes the right slot."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_assign_subscript",
        """
def main() -> i32 {
    var t = (0, 100);
    t[1] = 42;
    t[0] + t[1]
}
""",
    )
    assert exit_code == 42


def test_tuple_dot_int_assignment_executes(tmp_path: Path):
    """`t.N = ...` is now accepted on the lvalue side (grammar update)."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_assign_dot_int",
        """
def main() -> i32 {
    var t = (0, 100);
    t.1 = 42;
    t.0 + t.1
}
""",
    )
    assert exit_code == 42


def test_tuple_compound_assignment_executes(tmp_path: Path):
    """`+=` on a tuple element uses the correct slot."""
    exit_code = compile_and_run(
        tmp_path,
        "tup_assign_compound",
        """
def main() -> i32 {
    var t = (a: 10, b: 20);
    t.b += 12;
    t.a + t.b
}
""",
    )
    assert exit_code == 42


def test_enum_ctor_infers_from_return_type_executes(tmp_path: Path):
    """`return Result.ok(...)` infers unbound E from the function's return type."""
    exit_code = compile_and_run(
        tmp_path,
        "ctor_ret_type",
        """
enum CtorRes<T, E> {
    case ok(value: T)
    case err(error: E)
}

def make() -> CtorRes<i32, i32> {
    return CtorRes.ok(value: 42);
}

def main() -> i32 {
    let r = make();
    switch r {
    case .ok(let v): return v;
    case .err(let _): return -1;
    }
}
""",
    )
    assert exit_code == 42


def test_enum_ctor_infers_from_let_annotation_executes(tmp_path: Path):
    """Same idea, driven by a `let : T = ...` annotation."""
    exit_code = compile_and_run(
        tmp_path,
        "ctor_let_annot",
        """
enum CtorRes2<T, E> {
    case ok(value: T)
    case err(error: E)
}

def main() -> i32 {
    let r: CtorRes2<i32, i32> = CtorRes2.ok(value: 42);
    switch r {
    case .ok(let v): return v;
    case .err(let _): return -1;
    }
}
""",
    )
    assert exit_code == 42


def test_string_constants_unique_across_functions_executes(tmp_path: Path):
    """Two functions emitting string literals must not collide on `.str.0`."""
    exit_code = compile_and_run(
        tmp_path,
        "str_collision",
        """
def first() -> String { "hello" }
def second() -> String { "world" }
def main() -> i32 {
    let _ = first();
    let _ = second();
    42
}
""",
    )
    assert exit_code == 42


def test_repeated_struct_name_across_compilations_does_not_collide(tmp_path: Path):
    """Two back-to-back compilations using the same struct name with different
    bodies must not interfere via llvmlite's process-global LLVM context."""
    # First compilation: struct CollideBox { v: i32 }
    exit_a = compile_and_run(
        tmp_path,
        "collide_a",
        """
struct CollideBox { var v: i32 }
def main() -> i32 {
    let b = CollideBox { v: 42 };
    b.v
}
""",
    )
    assert exit_a == 42

    # Second compilation in the same process: same name, totally different body.
    exit_b = compile_and_run(
        tmp_path,
        "collide_b",
        """
struct CollideBox { var first: i64; var second: i64 }
def main() -> i32 {
    let b = CollideBox { first: 10, second: 32 };
    (b.first + b.second) as i32
}
""",
    )
    assert exit_b == 42


def test_extension_protocol_conformance_static_dispatch_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "ext_proto_static",
        """
protocol ShowExt {
    def show() -> i32;
}

struct ExtA { var v: i32 }

extension ExtA: ShowExt {
    def show() -> i32 { self.v }
}

def call_show<T: ShowExt>(x: T) -> i32 { x.show() }

def main() -> i32 {
    let a = ExtA { v: 42 };
    call_show(a)
}
""",
    )
    assert exit_code == 42


def test_extension_protocol_conformance_dynamic_dispatch_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "ext_proto_dyn",
        """
protocol ShowDyn {
    def show() -> i32;
}

struct DynA { var v: i32 }

extension DynA: ShowDyn {
    def show() -> i32 { self.v }
}

def call_dyn(x: any ShowDyn) -> i32 { x.show() }

def main() -> i32 {
    let a = DynA { v: 42 };
    call_dyn(a)
}
""",
    )
    assert exit_code == 42


def test_extension_multiple_conformances_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "ext_multi_proto",
        """
protocol MultiShow { def show() -> i32; }
protocol MultiTag { def tag() -> i32; }

struct MultiA { var v: i32 }

extension MultiA: MultiShow, MultiTag {
    def show() -> i32 { self.v }
    def tag() -> i32 { self.v + 1 }
}

def call_show(x: any MultiShow) -> i32 { x.show() }
def call_tag(x: any MultiTag) -> i32 { x.tag() }

def main() -> i32 {
    let a = MultiA { v: 41 };
    call_show(a) + call_tag(a) - 41
}
""",
    )
    assert exit_code == 42


def test_extension_missing_conformance_method_errors(tmp_path: Path):
    """`extension X: P` whose body lacks a P-required method should error."""
    from rolang.driver import CompileOptions, EmitKind, compile_source

    source_path = tmp_path / "bad_ext.rl"
    source_path.write_text(
        """
protocol BadShow {
    def show() -> i32;
}

struct BadA { var v: i32 }

extension BadA: BadShow {
    def other() -> i32 { 0 }
}

def main() -> i32 { 0 }
""",
        encoding="utf-8",
    )
    result = compile_source(
        source_path,
        CompileOptions(emit=EmitKind.EXECUTABLE, output_path=tmp_path / "bad_ext"),
    )
    assert not result.success
    msgs = "\n".join(d.message for d in result.diagnostics.diagnostics)
    assert "does not conform" in msgs
    assert "show" in msgs


def test_extension_conformance_to_non_protocol_errors(tmp_path: Path):
    """`extension X: Y` where Y isn't a protocol should error."""
    from rolang.driver import CompileOptions, EmitKind, compile_source

    source_path = tmp_path / "bad_conf.rl"
    source_path.write_text(
        """
struct StructA { var v: i32 }
struct StructB { var w: i32 }

extension StructA: StructB {
    def f() -> i32 { 0 }
}

def main() -> i32 { 0 }
""",
        encoding="utf-8",
    )
    result = compile_source(
        source_path,
        CompileOptions(emit=EmitKind.EXECUTABLE, output_path=tmp_path / "bad_conf"),
    )
    assert not result.success
    msgs = "\n".join(d.message for d in result.diagnostics.diagnostics)
    assert "is not a protocol" in msgs


def test_async_simple_await_executes(tmp_path: Path):
    """Async functions compile to state machines; await extracts the result."""
    exit_code = compile_and_run(
        tmp_path,
        "async_simple",
        """
def fetch() async -> i64 {
    42
}

def main() async -> i32 {
    let x = await fetch();
    x as i32
}
""",
    )
    assert exit_code == 42


def test_async_chained_awaits_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "async_chain",
        """
def step1() async -> i64 { 10 }
def step2(x: i64) async -> i64 { x + 32 }

def main() async -> i32 {
    let a = await step1();
    let b = await step2(a);
    b as i32
}
""",
    )
    assert exit_code == 42


def test_async_optional_return_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "async_opt",
        """
def fetch() async -> i32? {
    return 42;
}

def main() async -> i32 {
    if let v = await fetch() {
        return v;
    }
    return 0;
}
""",
    )
    assert exit_code == 42


def test_async_struct_return_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "async_struct",
        """
struct AsyncPoint { var x: i64; var y: i64 }
def make() async -> AsyncPoint { AsyncPoint { x: 10, y: 32 } }
def main() async -> i32 {
    let p = await make();
    (p.x + p.y) as i32
}
""",
    )
    assert exit_code == 42


def test_gc_preserves_objects_reachable_from_survivors(tmp_path: Path):
    """Cycle GC must not collect an object reachable through a live object."""
    exit_code = compile_and_run(
        tmp_path,
        "gc_survivor_edge",
        """
extern "C" def rt_gc_collect() -> Void;

struct Node { var next: Node?; var value: i32 }

def main() -> i32 {
    var root = Node { next: nil, value: 1 };
    root.next = Node { next: nil, value: 2 };

    var i = 0;
    while i < 10005 {
        let tmp = Node { next: nil, value: i };
        i = i + 1;
    }

    unsafe { rt_gc_collect(); }

    if let child = root.next {
        return child.value;
    }
    return 99;
}
""",
    )
    assert exit_code == 2


def test_callwitness_for_loop_empty_executes(tmp_path: Path):
    """For-loop exercises CallWitness(__iter__) + CallWitness(__next__) with immediate nil."""
    exit_code = compile_and_run(
        tmp_path,
        "callwitness_for_loop_empty",
        """
struct EmptyIter {
    def __iter__() -> EmptyIter {
        return self;
    }
    
    def __next__() -> i32? {
        return nil;
    }
}

def main() -> i32 {
    var total = 0;
    for x in EmptyIter {} {
        total = total + x;
    }
    return total;
}
""",
    )
    assert exit_code == 0


def test_protocol_bound_in_generic_struct_method_executes(tmp_path: Path):
    exit_code = compile_and_run(
        tmp_path,
        "proto_bound_struct_method",
        """
protocol Show {
    def show() -> i32;
}

struct A { var v: i32; def show() -> i32 { self.v } }

struct Container<T: Show> {
    var item: T
    def get_show() -> i32 { self.item.show() }
}

def main() -> i32 {
    let c = Container<A> { item: A { v: 42 } };
    c.get_show()
}
""",
    )
    assert exit_code == 42


# ========================= Import Tests =========================

def test_import_function_direct_executes(tmp_path: Path):
    """Test importing a function from another file (unqualified access)."""
    (tmp_path / "math.rl").write_text("""
pub def add(a: i32, b: i32) -> i32 {
    return a + b;
}
""")

    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl"

def main() -> i32 {
    return add(10, 32);
}
""")
    assert exit_code == 42


def test_import_multiple_functions_executes(tmp_path: Path):
    """Test importing multiple functions from another file."""
    (tmp_path / "calc.rl").write_text("""
pub def square(x: i32) -> i32 {
    return x * x;
}

pub def cube(x: i32) -> i32 {
    return x * x * x;
}
""")

    exit_code = compile_and_run(tmp_path, "main", """
import "calc.rl"

def main() -> i32 {
    var a = square(3);
    var b = cube(2);
    return a + b;
}
""")
    assert exit_code == 17  # 9 + 8


def test_import_transitive_executes(tmp_path: Path):
    """Test transitive imports: A imports B which imports C."""
    (tmp_path / "c.rl").write_text("""
pub def value() -> i32 {
    return 100;
}
""")
    (tmp_path / "b.rl").write_text("""
import "c.rl"

pub def wrap() -> i32 {
    return value() + 1;
}
""")

    exit_code = compile_and_run(tmp_path, "main", """
import "b.rl"

def main() -> i32 {
    return wrap();
}
""")
    assert exit_code == 101


def test_import_aliased_executes(tmp_path: Path):
    """Test aliased import with namespace access (as Name)."""
    (tmp_path / "math.rl").write_text("""
pub def add(a: i32, b: i32) -> i32 {
    return a + b;
}

pub def square(x: i32) -> i32 {
    return x * x;
}
""")

    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl" as m

def main() -> i32 {
    var a = m.square(5);
    return m.add(a, 17);
}
""")
    assert exit_code == 42  # 25 + 17


def test_import_aliased_executes_via_method(tmp_path: Path):
    """Test aliased import calling a method on module-namespaced type (dedup copy)."""
    (tmp_path / "math.rl").write_text("""
pub def add(a: i32, b: i32) -> i32 {
    return a + b;
}

pub def square(x: i32) -> i32 {
    return x * x;
}
""")

    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl" as m

def main() -> i32 {
    var a = m.square(5);
    return m.add(a, 17);
}
""")
    assert exit_code == 42  # 25 + 17



# ========================= Visibility Tests =========================

def test_visibility_pub_importable(tmp_path: Path):
    """Test that pub functions are accessible from importing modules."""
    (tmp_path / "lib.rl").write_text("""
pub def answer() -> i32 {
    return 42;
}
""")

    exit_code = compile_and_run(tmp_path, "main", """
import "lib.rl"

def main() -> i32 {
    return answer();
}
""")
    assert exit_code == 42


def test_visibility_internal_not_importable(tmp_path: Path):
    """Test that internal functions are not accessible from other modules."""
    (tmp_path / "lib.rl").write_text("""
def hidden() -> i32 {
    return 42;
}
""")

    source_path = tmp_path / "main.rl"
    source_path.write_text("""
import "lib.rl"

def main() -> i32 {
    return hidden();
}
""", encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=tmp_path / "main",
        ),
    )
    assert not result.success
    error_msgs = [d.message for d in result.diagnostics.diagnostics]
    assert any("hidden" in msg for msg in error_msgs)


def test_visibility_private_not_importable(tmp_path: Path):
    """Test that private functions are not accessible from other modules."""
    (tmp_path / "lib.rl").write_text("""
private def secret() -> i32 {
    return 42;
}
""")

    source_path = tmp_path / "main.rl"
    source_path.write_text("""
import "lib.rl"

def main() -> i32 {
    return secret();
}
""", encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=tmp_path / "main",
        ),
    )
    assert not result.success
    error_msgs = [d.message for d in result.diagnostics.diagnostics]
    assert any("secret" in msg for msg in error_msgs)


# ========================= Operator Overloading Tests =========================

def test_operator_overloading_add_executes(tmp_path: Path):
    """Test operator overloading for + on a custom struct."""
    exit_code = compile_and_run(tmp_path, "main", """
struct Point {
    var x: i32;
    var y: i32;
}

extension Point {
    pub def __add__(other: Point) -> Point {
        return Point { x: self.x + other.x, y: self.y + other.y };
    }
}

def main() -> i32 {
    let p1 = Point { x: 10, y: 20 };
    let p2 = Point { x: 5, y: 7 };
    let p3 = p1 + p2;
    return p3.x + p3.y;
}
""")
    assert exit_code == 42  # (10+5) + (20+7) = 42


def test_operator_overloading_eq_executes(tmp_path: Path):
    """Test operator overloading for == on a custom struct."""
    exit_code = compile_and_run(tmp_path, "main", """
struct Box {
    var value: i32;
}

extension Box {
    pub def __eq__(other: Box) -> Bool {
        return self.value == other.value;
    }
}

def main() -> i32 {
    let a = Box { value: 5 };
    let b = Box { value: 5 };
    let c = Box { value: 7 };
    if a == b {
        if a == c {
            return 1;
        }
        return 0;
    }
    return 2;
}
""")
    assert exit_code == 0


# ========================= Pattern Matching Exhaustiveness Tests =========================

def test_exhaustive_enum_switch_passes(tmp_path: Path):
    """Test that exhaustive enum switches compile."""
    exit_code = compile_and_run(tmp_path, "main", """
enum Color {
    case red;
    case green;
    case blue;
}

def main() -> i32 {
    let c = Color.red;
    switch c {
        case .red: return 0;
        case .green: return 1;
        case .blue: return 2;
    }
    return 3;
}
""")
    assert exit_code == 0


def test_non_exhaustive_enum_switch_fails(tmp_path: Path):
    """Test that non-exhaustive enum switches fail to compile."""
    source_path = tmp_path / "main.rl"
    source_path.write_text("""
enum Color {
    case red;
    case green;
    case blue;
}

def main() -> i32 {
    let c = Color.red;
    switch c {
        case .red: return 0;
        case .green: return 1;
    }
    return 3;
}
""", encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=tmp_path / "main",
        ),
    )
    assert not result.success
    error_msgs = [d.message for d in result.diagnostics.diagnostics]
    assert any("blue" in msg for msg in error_msgs)


def test_exhaustive_bool_switch_passes(tmp_path: Path):
    """Test that exhaustive Bool switches compile."""
    exit_code = compile_and_run(tmp_path, "main", """
def main() -> i32 {
    let b = true;
    switch b {
        case true: return 0;
        case false: return 1;
    }
    return 2;
}
""")
    assert exit_code == 0


def test_non_exhaustive_bool_switch_fails(tmp_path: Path):
    """Test that non-exhaustive Bool switches fail to compile."""
    source_path = tmp_path / "main.rl"
    source_path.write_text("""
def main() -> i32 {
    let b = true;
    switch b {
        case true: return 0;
    }
    return 2;
}
""", encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=tmp_path / "main",
        ),
    )
    assert not result.success
    error_msgs = [d.message for d in result.diagnostics.diagnostics]
    assert any("false" in msg for msg in error_msgs)


def test_default_case_makes_switch_exhaustive(tmp_path: Path):
    """Test that a default case makes any switch exhaustive."""
    exit_code = compile_and_run(tmp_path, "main", """
enum Color {
    case red;
    case green;
    case blue;
}

def main() -> i32 {
    let c = Color.red;
    switch c {
        case .red: return 0;
        default: return 1;
    }
}
""")
    assert exit_code == 0


# ========================= Generic Collections Tests =========================

def test_generic_vec_i32_push_get_executes(tmp_path: Path):
    """Test generic Vec<i32> push and get."""
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"

def main() -> i32 {
    var v = Vec<i32>.new();
    v.push(10);
    v.push(20);
    v.push(30);
    return v.get(0) + v.get(1) + v.get(2);
}
""", include_paths=_stdlib_path())
    assert exit_code == 60


def test_generic_vec_i64_push_get_executes(tmp_path: Path):
    """Test generic Vec<i64> push and get."""
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"

def main() -> i32 {
    var v = Vec<i64>.new();
    v.push(10);
    v.push(20);
    v.push(30);
    let sum = v.get(0) + v.get(1) + v.get(2);
    return sum as i32;
}
""", include_paths=_stdlib_path())
    assert exit_code == 60


def test_generic_vec_set_executes(tmp_path: Path):
    """Test generic Vec<i32> set."""
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"

def main() -> i32 {
    var v = Vec<i32>.new();
    v.push(10);
    v.push(20);
    v.set(0, 100);
    return v.get(0) + v.get(1);
}
""", include_paths=_stdlib_path())
    assert exit_code == 120


def test_generic_vec_pop_executes(tmp_path: Path):
    """Test generic Vec<i32> pop."""
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"

def main() -> i32 {
    var v = Vec<i32>.new();
    v.push(10);
    v.push(20);
    v.push(30);
    let last = v.pop();
    return last + v.len();
}
""", include_paths=_stdlib_path())
    assert exit_code == 32  # 30 + 2


def test_generic_vec_struct_executes(tmp_path: Path):
    """Test generic Vec with a custom struct type."""
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"

struct Point {
    var x: i32;
    var y: i32;
}

def main() -> i32 {
    var v: Vec<Point> = Vec<Point>.new();
    let p1 = Point { x: 10, y: 20 };
    let p2 = Point { x: 5, y: 7 };
    v.push(p1);
    v.push(p2);
    let q = v.get(0);
    return q.x + q.y;
}
""", include_paths=_stdlib_path())
    assert exit_code == 30


def test_generic_dict_i32_i32_executes(tmp_path: Path):
    """Test generic Dict<i32, i32>. `.get` now returns V?, use ?? for fallback."""
    exit_code = compile_and_run(tmp_path, "main", """
import "dict.rl"

def main() -> i32 {
    var d = Dict<i32, i32>.new(16, 0, 0, 0);
    d.set(1, 10);
    d.set(2, 20);
    d.set(3, 5);
    let sum = (d.get(1) ?? 0) + (d.get(2) ?? 0) + (d.get(3) ?? 0);
    return sum;
}
""", include_paths=_stdlib_path())
    assert exit_code == 35  # 10 + 20 + 5


def test_generic_dict_string_i32_executes(tmp_path: Path):
    """Test generic Dict<String, i32> with String keys."""
    exit_code = compile_and_run(tmp_path, "main", """
import "dict.rl"

def main() -> i32 {
    var d = Dict<String, i32>.new(16, 1, 0, 0);
    d.set("a", 5);
    d.set("b", 10);
    d.set("c", 7);
    let sum = (d.get("a") ?? 0) + (d.get("b") ?? 0) + (d.get("c") ?? 0);
    return sum;
}
""", include_paths=_stdlib_path())
    assert exit_code == 22  # 5 + 10 + 7


def test_generic_dict_i32_i64_executes(tmp_path: Path):
    """Test generic Dict<i32, i64>."""
    exit_code = compile_and_run(tmp_path, "main", """
import "dict.rl"

def main() -> i32 {
    var d = Dict<i32, i64>.new(16, 0, 0, 0);
    d.set(1, 5);
    d.set(2, 10);
    d.set(3, 7);
    let sum = (d.get(1) ?? 0) + (d.get(2) ?? 0) + (d.get(3) ?? 0);
    return sum as i32;
}
""", include_paths=_stdlib_path())
    assert exit_code == 22  # 5 + 10 + 7


def test_generic_dict_contains_executes(tmp_path: Path):
    """Test Dict contains method."""
    exit_code = compile_and_run(tmp_path, "main", """
import "dict.rl"

def main() -> i32 {
    var d = Dict<i32, i32>.new(16, 0, 0, 0);
    d.set(1, 10);
    if d.contains(1) {
        return 1;
    }
    if d.contains(99) {
        return 2;
    }
    return 3;
}
""", include_paths=_stdlib_path())
    assert exit_code == 1


def test_generic_dict_overwrite_executes(tmp_path: Path):
    """Test Dict overwriting existing key."""
    exit_code = compile_and_run(tmp_path, "main", """
import "dict.rl"

def main() -> i32 {
    var d = Dict<i32, i32>.new(16, 0, 0, 0);
    d.set(1, 10);
    d.set(1, 25);
    return d.get(1) ?? 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 25


# ========================= Standard Library Tests =========================

def _stdlib_path() -> list:
    """Return the path to the Rolang stdlib."""
    import os
    stdlib = Path(os.path.dirname(__file__)).parent / "src" / "rolang" / "std"
    return [stdlib]


def test_stdlib_math_abs_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl"

def main() -> i32 {
    if abs_i32(-42) != 42 { return 1; }
    if abs_i32(0) != 0 { return 1; }
    if abs_i32(7) != 7 { return 1; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_math_min_max_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl"

def main() -> i32 {
    if min_i32(3, 7) != 3 { return 1; }
    if max_i32(3, 7) != 7 { return 1; }
    if min_i64(100, 200) != 100 { return 1; }
    if max_i64(100, 200) != 200 { return 1; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_math_pow_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl"

def main() -> i32 {
    if pow_i32(2, 3) != 8 { return 1; }
    if pow_i32(5, 0) != 1 { return 1; }
    if pow_i32(3, 4) != 81 { return 1; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_test_assertions_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "test.rl"

def main() -> i32 {
    var r = assert_eq_i32(42, 42);
    if r != 0 { return r; }
    r = assert_eq_i64(100, 100);
    if r != 0 { return r; }
    r = assert_true(true);
    if r != 0 { return r; }
    r = assert_false(false);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_combined_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "math.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_eq_i32(pow_i32(2, 3), 8);
    if r != 0 { return r; }
    r = assert_eq_i32(max_i32(min_i32(10, 5), 0), 5);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_io_println_executes(tmp_path: Path):
    """Verify io module compiles and runs (output is verified manually)."""
    exit_code = compile_and_run(tmp_path, "main", """
import "io.rl"

def main() -> i32 {
    println("Hello from stdlib io!");
    println_i32(42);
    println_i64(100);
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_io_with_math_executes(tmp_path: Path):
    """Combined io + math test."""
    exit_code = compile_and_run(tmp_path, "main", """
import "io.rl"
import "math.rl"

def main() -> i32 {
    print("max(3, 7) = ");
    println_i32(max_i32(3, 7));
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_len_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_eq_i64("hello".len(), 5);
    if r != 0 { return r; }
    r = assert_eq_i64("".len(), 0);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_equals_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("abc".equals("abc"));
    if r != 0 { return r; }
    r = assert_false("abc".equals("xyz"));
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_contains_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("hello".contains("ell"));
    if r != 0 { return r; }
    r = assert_false("hello".contains("world"));
    if r != 0 { return r; }
    r = assert_true("file.rl".starts_with("file"));
    if r != 0 { return r; }
    r = assert_true("file.rl".ends_with(".rl"));
    if r != 0 { return r; }
    r = assert_false("file.rl".ends_with(".rs"));
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_compare_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"

def main() -> i32 {
    if "a".compare_to("b") >= 0 { return 1; }
    if "b".compare_to("a") <= 0 { return 1; }
    if "abc".compare_to("abc") != 0 { return 1; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_extension_methods_executes(tmp_path: Path):
    """Test String extension methods (s.len(), s.ends_with(), etc.) in a single file."""
    exit_code = compile_and_run(tmp_path, "main", """
extension String {
    def len() -> i64 {
        unsafe { return rt_string_len(self); }
    }
    def ends_with(suffix: String) -> Bool {
        unsafe { return rt_string_ends_with(self, suffix) != 0; }
    }
    def starts_with(prefix: String) -> Bool {
        unsafe { return rt_string_starts_with(self, prefix) != 0; }
    }
    def contains(needle: String) -> Bool {
        unsafe { return rt_string_contains(self, needle) != 0; }
    }
    def is_empty() -> Bool {
        unsafe { return rt_string_is_empty(self) != 0; }
    }
}

extern "C" def rt_string_len(s: String) -> i64;
extern "C" def rt_string_is_empty(s: String) -> i64;
extern "C" def rt_string_contains(haystack: String, needle: String) -> i32;
extern "C" def rt_string_starts_with(s: String, prefix: String) -> i32;
extern "C" def rt_string_ends_with(s: String, suffix: String) -> i32;

import "test.rl"

def main() -> i32 {
    var s = "hello.rl";

    var r = assert_eq_i64(s.len(), 8);
    if r != 0 { return r; }
    r = assert_true(s.ends_with(".rl"));
    if r != 0 { return r; }
    r = assert_true(s.starts_with("hello"));
    if r != 0 { return r; }
    r = assert_true(s.contains("llo"));
    if r != 0 { return r; }
    r = assert_false(s.is_empty());
    if r != 0 { return r; }
    r = assert_true("".is_empty());
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_numeric_extension_methods_executes(tmp_path: Path):
    """Test numeric extension methods (x.abs(), x.min(), x.pow(), etc.)."""
    exit_code = compile_and_run(tmp_path, "main", """
extension i32 {
    def abs() -> i32 {
        if self < 0 { return -self; }
        return self;
    }
    def min(other: i32) -> i32 {
        if self < other { return self; }
        return other;
    }
    def max(other: i32) -> i32 {
        if self > other { return self; }
        return other;
    }
    def pow(exp: i32) -> i32 {
        var result = 1;
        var e = exp;
        while e > 0 {
            result = result * self;
            e = e - 1;
        }
        return result;
    }
    def is_positive() -> Bool { return self > 0; }
    def is_negative() -> Bool { return self < 0; }
    def is_zero() -> Bool { return self == 0; }
    def clamp(low: i32, high: i32) -> i32 {
        if self < low { return low; }
        if self > high { return high; }
        return self;
    }
}

import "test.rl"

def main() -> i32 {
    var r = assert_eq_i32((-5).abs(), 5);
    if r != 0 { return r; }
    r = assert_eq_i32(3.min(7), 3);
    if r != 0 { return r; }
    r = assert_eq_i32(3.max(7), 7);
    if r != 0 { return r; }
    r = assert_eq_i32(2.pow(4), 16);
    if r != 0 { return r; }
    r = assert_true(5.is_positive());
    if r != 0 { return r; }
    r = assert_true((-3).is_negative());
    if r != 0 { return r; }
    r = assert_true(0.is_zero());
    if r != 0 { return r; }
    r = assert_eq_i32(5.clamp(10, 20), 10);
    if r != 0 { return r; }
    r = assert_eq_i32(15.clamp(10, 20), 15);
    if r != 0 { return r; }
    r = assert_eq_i32(25.clamp(10, 20), 20);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_concat_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("ab".concat("cd").equals("abcd"));
    if r != 0 { return r; }
    r = assert_true("".concat("xyz").equals("xyz"));
    if r != 0 { return r; }
    r = assert_eq_i64("a".concat("bc").len(), 3);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_int_to_string_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    let x: i64 = 42;
    var r = assert_true(x.to_string().equals("42"));
    if r != 0 { return r; }
    let y: i64 = -7;
    r = assert_true(y.to_string().equals("-7"));
    if r != 0 { return r; }
    let z: i64 = 0;
    r = assert_true(z.to_string().equals("0"));
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_repeat_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("ha".repeat(3).equals("hahaha"));
    if r != 0 { return r; }
    r = assert_true("x".repeat(1).equals("x"));
    if r != 0 { return r; }
    r = assert_eq_i64("ab".repeat(4).len(), 8);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_array_utils_executes(tmp_path: Path):
    """`array_*` helpers now operate directly on Vec<i32> — no explicit
    length argument because the vector tracks its own length."""
    exit_code = compile_and_run(tmp_path, "main", """
import "array.rl"
import "test.rl"

def main() -> i32 {
    let arr = [3, 1, 4, 1, 5];

    var r = assert_eq_i32(array_sum(arr), 14);
    if r != 0 { return r; }
    r = assert_true(array_contains(arr, 4));
    if r != 0 { return r; }
    r = assert_false(array_contains(arr, 9));
    if r != 0 { return r; }
    r = assert_eq_i32(array_find(arr, 5), 4);
    if r != 0 { return r; }
    r = assert_eq_i32(array_find(arr, 9), -1);
    if r != 0 { return r; }
    r = assert_eq_i32(array_count(arr, 1), 2);
    if r != 0 { return r; }
    r = assert_eq_i32(array_min(arr) ?? 0, 1);
    if r != 0 { return r; }
    r = assert_eq_i32(array_max(arr) ?? 0, 5);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_where_constraint_struct_executes(tmp_path: Path):
    """Test where T: Show on struct — method dispatch on type variable."""
    exit_code = compile_and_run(tmp_path, "main", """
protocol Show { def show() -> i32; }
struct A { var v: i32; def show() -> i32 { self.v } }

struct Container<T> where T: Show {
    var item: T;
    def get_show() -> i32 { return self.item.show(); }
}

def main() -> i32 {
    let c = Container<A> { item: A { v: 42 } };
    return c.get_show();
}
""")
    assert exit_code == 42


def test_where_constraint_inline_both_work_executes(tmp_path: Path):
    """Test that both where T:S and inline <T:S> work side by side."""
    exit_code = compile_and_run(tmp_path, "main", """
protocol Val { def val() -> i32; }
struct X { var n: i32; def val() -> i32 { self.n } }
struct Y { var m: i32; def val() -> i32 { self.m } }

struct WithWhere<T> where T: Val {
    var inner: T;
    def get() -> i32 { return self.inner.val(); }
}

struct WithInline<T: Val> {
    var inner: T;
    def get() -> i32 { return self.inner.val(); }
}

def main() -> i32 {
    let a = WithWhere<X> { inner: X { n: 10 } };
    let b = WithInline<Y> { inner: Y { m: 32 } };
    return a.get() + b.get();
}
""")
    assert exit_code == 42


def test_where_constraint_enforcement_errors(tmp_path: Path):
    """Test that where T: Show rejects non-conforming types at compile time."""
    source_path = tmp_path / "main.rl"
    output_path = tmp_path / "main"
    source_path.write_text("""
protocol Show { def show() -> i32; }
struct NoShow { var x: i32; }

struct Container<T> where T: Show {
    var item: T;
}

def main() -> i32 {
    let c = Container<NoShow> { item: NoShow { x: 1 } };
    return 1;
}
""")
    from rolang.driver import CompileOptions, EmitKind, compile_source
    result = compile_source(source_path, CompileOptions(emit=EmitKind.EXECUTABLE, output_path=output_path))
    assert not result.success, "Should fail: NoShow doesn't conform to Show"


def test_stdlib_string_char_at_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_eq_i32("abc".char_at(0), 97);
    if r != 0 { return r; }
    r = assert_eq_i32("abc".char_at(2), 99);
    if r != 0 { return r; }
    r = assert_eq_i32("abc".char_at(5), -1);
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_substring_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("hello".substring(1, 3).equals("ell"));
    if r != 0 { return r; }
    r = assert_true("abc".substring(0, 2).equals("ab"));
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_trim_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("  hi  ".trim().equals("hi"));
    if r != 0 { return r; }
    r = assert_true("abc".trim().equals("abc"));
    if r != 0 { return r; }
    r = assert_true("   ".trim().equals(""));
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_string_replace_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "string.rl"
import "test.rl"

def main() -> i32 {
    var r = assert_true("hello world".replace("world", "rolang").equals("hello rolang"));
    if r != 0 { return r; }
    r = assert_true("a,b,c".replace(",", "-").equals("a-b-c"));
    if r != 0 { return r; }
    r = assert_true("abc".replace("x", "y").equals("abc"));
    if r != 0 { return r; }
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_vec_i32_push_get_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"
import "test.rl"

def main() -> i32 {
    var v = Vec<i32>.new();
    v.push(42);
    v.push(99);
    var r = assert_eq_i32(v.len(), 2);
    if r != 0 { return r; }
    r = assert_eq_i32(v.get(0), 42);
    if r != 0 { return r; }
    r = assert_eq_i32(v.get(1), 99);
    if r != 0 { return r; }
    v.free();
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


def test_stdlib_vec_i64_push_get_executes(tmp_path: Path):
    exit_code = compile_and_run(tmp_path, "main", """
import "vec.rl"
import "test.rl"

def main() -> i32 {
    var v = Vec<i64>.new();
    v.push(100);
    v.push(200);
    var r = assert_eq_i64(v.get(0), 100);
    if r != 0 { return r; }
    r = assert_eq_i64(v.get(1), 200);
    if r != 0 { return r; }
    v.free();
    return 0;
}
""", include_paths=_stdlib_path())
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Async lowering regression tests (branches, switch, nested await)
# ---------------------------------------------------------------------------

def test_async_with_if_else_branches_runs(tmp_path: Path):
    """Awaits inside branches lower into per-segment state-machine entries.

    The previous lowering pass hard-errored on any await outside the
    entry block. The current ``_build_resume`` splits each block at its
    awaits and adds one ``frame.state`` case per resume target, so this
    program now compiles and runs end-to-end.
    """
    exit_code = compile_and_run(
        tmp_path,
        "async_if_branch",
        """
def get_a() async -> i64 { 10 }
def get_b() async -> i64 { 20 }

def main() async -> i32 {
    let a = await get_a();
    if a > 5 {
        return (await get_b()) as i32;
    }
    return 0;
}
""",
    )
    assert exit_code == 20


def test_async_nested_await_in_branches_runs(tmp_path: Path):
    """Multiple awaits across different blocks survive lowering."""
    exit_code = compile_and_run(
        tmp_path,
        "async_nested_branch",
        """
def get_a() async -> i64 { 10 }
def get_b() async -> i64 { 20 }
def get_c() async -> i64 { 30 }

def main() async -> i32 {
    let v = await get_a();
    if v > 5 {
        let extra = await get_b();
        return (extra + (await get_c())) as i32;
    }
    return 0;
}
""",
    )
    assert exit_code == 50


def test_async_cond_return(tmp_path: Path):
    """Async function with conditional return using if."""
    exit_code = compile_and_run(
        tmp_path,
        "async_cond",
        """
def fetch() async -> i64 { 42 }

def main() async -> i32 {
    let v = await fetch();
    if v > 100 {
        return 1;
    }
    return v as i32;
}
""",
    )
    assert exit_code == 42


# ---------------------------------------------------------------------------
# Multi-module generic import regression tests
# ---------------------------------------------------------------------------

def test_cross_module_generic_function_import(tmp_path: Path):
    """Generic function defined in one module, called from another.

    Tests that generic monomorphization works across module boundaries
    when functions (not types) are imported.
    """
    lib_path = tmp_path / "lib"
    lib_path.mkdir()
    (lib_path / "math.rl").write_text("""
pub def double(x: i64) -> i64 {
    return x + x;
}
""")
    (lib_path / "ops.rl").write_text("""
import "lib/math.rl"

pub def triple(x: i64) -> i64 {
    return x + double(x);
}
""")
    exit_code = compile_and_run(
        tmp_path,
        "chained_mod",
        """
import "lib/ops.rl"

def main() -> i32 {
    return triple(14) as i32;
}
""",
        include_paths=[tmp_path],
    )
    assert exit_code == 42


def test_dict_string_key_iteration(tmp_path: Path):
    """Regression: `for k in dict_keys(d)` over a Dict<String, _> crashed
    codegen. The witness call `DictIter<String>::__next__` resolved via the
    *generic* symbol name to `DictIter___next__` (which doesn't exist), then a
    loose suffix-search fallback picked a wrong `i32?`-returning `__next__`
    (Range/CharIter), producing `cannot store {i1, i32} to %String**`. Fixed by
    mangling the witness type's args so it resolves to the monomorphized
    `DictIter_S112___next__`. This also exercises correct String-key iteration
    (no garbage): the two keys' lengths (2 + 3) sum to the exit code."""
    exit_code = compile_and_run(
        tmp_path,
        "dict_string_key_iter",
        """
import "dict.rl"
import "iter.rl"

def main() -> i32 {
    var d = dict_string_i64_new();
    d.set("ab", 1);
    d.set("cde", 2);
    var total: i32 = 0;
    for k in dict_keys(d) {
        total = total + (k.len() as i32);
    }
    return total;
}
""",
    )
    assert exit_code == 5


def test_owned_value_passed_to_function_is_not_leaked_at_O3(tmp_path: Path):
    """Regression: the ARC-optimization pass (opt>=1) wrongly deleted the
    release of an OWNED value used once as a call argument (it treated any
    single-use call-arg local as a borrowed read, but `let t = make(); f(t)`
    lowers to `_3 = _5` whose retain is on the source `_5`, so removing `_3`'s
    release left every object's refcount permanently elevated -> a leak).

    The whole suite compiles at -O0 (OptLevel.O0 default), so the ARC-opt pass
    is otherwise never exercised at runtime; this forces -O3 and asserts objects
    allocated then dropped each iteration are freed (rt_obj_live_count stays near
    baseline, not ~50000)."""
    from rolang.driver import OptLevel

    source = """
extern "C" def rt_obj_live_count() -> i64;

struct Node { var next: Node?; var v: i32 }

def consume(n: Node) -> i32 { return n.v; }

def main() -> i32 {
    var i: i64 = 0;
    var acc: i64 = 0;
    while i < 50000 {
        let t = Node { next: nil, v: i as i32 };
        acc = acc + (consume(t) as i64);
        i = i + 1;
    }
    var live: i64 = 1000000;
    unsafe { live = rt_obj_live_count(); }
    if live > 1000 { return 1; }
    return 0;
}
"""
    source_path = tmp_path / "owned_arg_no_leak.rl"
    output_path = tmp_path / "owned_arg_no_leak"
    source_path.write_text(source, encoding="utf-8")
    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=[],
            opt_level=OptLevel.O3,
        ),
    )
    assert result.success, getattr(result, "errors", result)
    completed = subprocess.run([str(output_path)], check=False)
    assert completed.returncode == 0, "owned value passed to a function leaked at -O3"


def test_out_param_accessors_do_not_leak_at_O3(tmp_path: Path):
    """Regression: container out-param accessors leaked one object per call.

    `Vec<T>.get`/`pop`, `Dict<K,V>.get`, and `DictIter.__next__` use the
    `var out: T; rt_*(... out as RawPtr)` idiom. codegen default-initialized
    `var out: T;` for a heap T to a *fresh* `rt_obj_alloc` object; the runtime
    FFI then `memcpy`-overwrote that slot WITHOUT releasing it, orphaning the
    default object on every call. With the dict/vec value living a long time
    (e.g. `Vec<String>` indexed in a hot loop) this leaked ~1 object/iteration
    and bloated the GC list, making `word_freq` ~5.5x slower than necessary.

    Fix: a MIR pass (`mir_outparam_init.elide_outparam_default_init`) replaces
    the phantom alloc with a NULL init for locals used purely as out-params
    (address taken into a call before any read), so no object is ever created
    to leak. This forces -O3 (the suite is -O0, where ARC behaviour differs)
    and asserts the live object count stays near baseline rather than growing
    with the loop. No string literals appear inside the loops (a separate,
    unrelated literal-arg leak would otherwise contaminate the count)."""
    from rolang.driver import OptLevel

    source = """
import "vec.rl"
import "dict.rl"
import "string.rl"

extern "C" def rt_obj_live_count() -> i64;

struct Box { var v: i32 }

def main() -> i32 {
    // Pre-build keys once (no literals inside the hot loops below).
    var keys = Vec<String>.with_capacity(4);
    keys.push("alpha");
    keys.push("beta");

    var d = dict_string_i64_new();
    var bx = Dict<String, Box>.with_capacity(8, 1);
    var n: i32 = 0;
    while n < 2 {
        d.set(keys.get(n), n as i64);
        bx.set(keys.get(n), Box { v: n });
        n = n + 1;
    }

    var acc: i64 = 0;
    var i: i64 = 0;
    while i < 50000 {
        let k = keys.get((i % 2) as i32);      // Vec<String>.get
        acc = acc + k.len();
        acc = acc + (d.get(k) ?? 0 as i64);    // Dict<String,i64>.get (primitive value)
        let b = bx.get(k);                     // Dict<String,Box>.get (HEAP value)
        i = i + 1;
    }

    // Vec<String>.pop move-out churn (elem held across the loop; no literals).
    var stack = Vec<String>.with_capacity(4);
    let elem = keys.get(0);
    i = 0;
    while i < 50000 {
        stack.push(elem);
        let p = stack.pop();
        acc = acc + p.len();
        i = i + 1;
    }

    var live: i64 = 1000000;
    unsafe { live = rt_obj_live_count(); }
    if live > 1000 { return 1; }
    if acc < 0 { return 2; }  // keep `acc` (and the accessor calls) live
    return 0;
}
"""
    source_path = tmp_path / "outparam_no_leak.rl"
    output_path = tmp_path / "outparam_no_leak"
    source_path.write_text(source, encoding="utf-8")
    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=[],
            opt_level=OptLevel.O3,
        ),
    )
    assert result.success, getattr(result, "errors", result)
    completed = subprocess.run([str(output_path)], check=False)
    assert completed.returncode == 0, "container out-param accessor leaked at -O3"


def test_inline_primitive_vec_access_is_correct(tmp_path: Path):
    """Primitive `Vec<T>.get/set/len` are inlined in codegen (no opaque
    `rt_gvec_get/set` call) so tight numeric loops stay in registers instead of
    spilling — this cut nbody from 3.0x to 1.13x C. The inline path must be
    bit-for-bit equivalent to the FFI path: identical results at -O0 (inline IR
    emitted, unoptimized) and -O3 (mem2reg/LICM/regalloc applied), correct
    element-size scaling for both 8-byte (f64/i64) and 4-byte (i32) elements,
    and HEAP elements (Vec<String>) still falling back to the runtime accessor
    so ARC retain/release is preserved."""
    from rolang.driver import OptLevel

    source = """
import "vec.rl"
import "string.rl"

def main() -> i32 {
    // Vec<f64>: inline get/set/len, 8-byte elements.
    var xs = Vec<f64>.with_capacity(8);
    var i: i32 = 0;
    while i < 8 {
        xs.push((i as f64) * 1.5);
        i = i + 1;
    }
    xs.set(3, 100.0);                  // 4.5 -> 100.0
    var fsum: f64 = 0.0;
    i = 0;
    while i < xs.len() {               // inline len()
        fsum = fsum + xs.get(i);       // inline get
        i = i + 1;
    }
    if (fsum as i64) != 137 { return 1; }   // 0+1.5+3+100+6+7.5+9+10.5 = 137.5

    // Vec<i64>: 8-byte ints.
    var ys = Vec<i64>.with_capacity(4);
    ys.push(10); ys.push(20); ys.push(30);
    ys.set(1, 99);
    var isum: i64 = 0;
    var j: i32 = 0;
    while j < ys.len() { isum = isum + ys.get(j); j = j + 1; }
    if isum != 139 { return 2; }

    // Vec<i32>: 4-byte element-size scaling.
    var zs = Vec<i32>.with_capacity(4);
    zs.push(100); zs.push(200); zs.push(300);
    zs.set(0, 1);
    var ksum: i32 = 0;
    j = 0;
    while j < zs.len() { ksum = ksum + zs.get(j); j = j + 1; }
    if ksum != 501 { return 4; }

    // Vec<String>: HEAP element — must fall back to rt_gvec_get (ARC-correct).
    var ss = Vec<String>.with_capacity(2);
    ss.push("ab"); ss.push("cde");
    if ss.get(0).len() + ss.get(1).len() != 5 { return 3; }

    return 0;
}
"""
    source_path = tmp_path / "inline_vec.rl"
    source_path.write_text(source, encoding="utf-8")
    # Equivalent across opt levels: -O0 emits the inline IR unoptimized, -O3
    # promotes/hoists it. Both must produce exit 0.
    for opt in (OptLevel.O0, OptLevel.O3):
        output_path = tmp_path / f"inline_vec_{opt.name}"
        result = compile_source(
            source_path,
            CompileOptions(
                emit=EmitKind.EXECUTABLE,
                output_path=output_path,
                include_paths=[],
                opt_level=opt,
            ),
        )
        assert result.success, getattr(result, "errors", result)
        completed = subprocess.run([str(output_path)], check=False)
        assert completed.returncode == 0, (
            f"inline primitive Vec access wrong at {opt.name}: "
            f"exit {completed.returncode}"
        )


def test_inline_vec_get_still_bounds_checks(tmp_path: Path):
    """The inlined fast path must keep Rolang's bounds check: an out-of-range
    `Vec<i64>.get` panics (aborts) rather than reading out of bounds."""
    from rolang.driver import OptLevel

    source = """
import "vec.rl"

def main() -> i32 {
    var xs = Vec<i64>.with_capacity(2);
    xs.push(7);
    let v = xs.get(5);   // out of bounds -> must panic
    return v as i32;
}
"""
    source_path = tmp_path / "inline_vec_oob.rl"
    output_path = tmp_path / "inline_vec_oob"
    source_path.write_text(source, encoding="utf-8")
    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=[],
            opt_level=OptLevel.O3,
        ),
    )
    assert result.success, getattr(result, "errors", result)
    completed = subprocess.run([str(output_path)], check=False)
    assert completed.returncode != 0, "out-of-bounds inline Vec.get did not panic"


def test_inline_primitive_dict_value_access_is_correct(tmp_path: Path):
    """`Dict<K,V>.value_at/set_value_at` (the O(1) hash-free index accessors used
    by the word_freq counter loop) are inlined in codegen for PRIMITIVE value
    types — same opaque-call elimination as Vec, dropping ~20% off word_freq.
    Must be equivalent at -O0 and -O3, replicate the runtime's lenient
    out-of-range behaviour (value_at zero-fills, set_value_at no-ops), and fall
    back to the runtime accessor for HEAP value types so ARC is preserved."""
    from rolang.driver import OptLevel

    source = """
import "dict.rl"
import "string.rl"

def main() -> i32 {
    // Primitive value (i64): inlined value_at / set_value_at.
    var d = dict_string_i64_new();
    let ia = d.entry_index("apple", 0 as i64);
    let ib = d.entry_index("berry", 0 as i64);
    d.set_value_at(ia, d.value_at(ia) + 100 as i64);
    d.set_value_at(ib, d.value_at(ib) + 7 as i64);
    d.set_value_at(ia, d.value_at(ia) + 1 as i64);
    if d.value_at(ia) != 101 { return 1; }
    if d.value_at(ib) != 7 { return 2; }
    // Lenient on out-of-range: value_at returns 0 (no panic), matching runtime.
    if d.value_at(123456 as i64) != 0 { return 3; }

    // Heap value type must fall back to the runtime accessor (ARC-correct).
    var s = Dict<String, String>.with_capacity(8, 1);
    s.set("k", "hello");
    let si = s.entry_index("k", "");
    if s.value_at(si).len() != 5 { return 4; }

    return 0;
}
"""
    source_path = tmp_path / "inline_dict.rl"
    source_path.write_text(source, encoding="utf-8")
    for opt in (OptLevel.O0, OptLevel.O3):
        output_path = tmp_path / f"inline_dict_{opt.name}"
        result = compile_source(
            source_path,
            CompileOptions(
                emit=EmitKind.EXECUTABLE,
                output_path=output_path,
                include_paths=[],
                opt_level=opt,
            ),
        )
        assert result.success, getattr(result, "errors", result)
        completed = subprocess.run([str(output_path)], check=False)
        assert completed.returncode == 0, (
            f"inline primitive Dict value access wrong at {opt.name}: "
            f"exit {completed.returncode}"
        )


def test_per_type_release_fields_is_correct(tmp_path: Path):
    """Final-release field cleanup now goes through a codegen-generated per-type
    `release_fields(payload)` (constant offsets) instead of the generic
    descriptor walk. It must be exactly equivalent: release every heap field of
    recursive structs (no leak), run a coexisting user `__release__` first while
    self is still valid, and — for enums — release only the *active* case's
    payload fields (case-tag filtering). Checked at -O0 and -O3 via the live
    object count after heavy alloc/drop churn."""
    from rolang.driver import OptLevel

    source = """
import "io.rl"

extern "C" def rt_obj_live_count() -> i64;

struct Leaf {
    var v: i32;
    // A user deinit must still run (first, observing a valid self) alongside
    // the generated field-release path.
    def __release__() -> Void {
        if self.v < 0 { println("unreachable"); }
    }
}

// Recursive struct with two heap pointer fields (one a struct with a deinit).
struct Node {
    var leaf: Leaf?;
    var next: Node?;
}

// Enum carrying heap payloads in distinct cases -> case-tag-filtered release.
enum Shape {
    case circle(Leaf)
    case pair(Leaf, Leaf)
    case empty
}

def build(n: i32) -> Node? {
    if n == 0 { return nil; }
    return Node { leaf: Leaf { v: n }, next: build(n - 1) };
}

def main() -> i32 {
    var i: i32 = 0;
    while i < 2000 {
        let _ = build(10);                 // 10 Nodes + 10 Leaves, then dropped
        i = i + 1;
    }
    var j: i32 = 0;
    while j < 2000 {
        let _ = Shape.circle(Leaf { v: 1 });
        let _ = Shape.pair(Leaf { v: 2 }, Leaf { v: 3 });
        let _ = Shape.empty;
        j = j + 1;
    }
    var live: i64 = 999999;
    unsafe { live = rt_obj_live_count(); }
    if live > 200 { return 1; }            // leak guard: everything must free
    return 0;
}
"""
    source_path = tmp_path / "release_fields.rl"
    source_path.write_text(source, encoding="utf-8")
    for opt in (OptLevel.O0, OptLevel.O3):
        output_path = tmp_path / f"release_fields_{opt.name}"
        result = compile_source(
            source_path,
            CompileOptions(
                emit=EmitKind.EXECUTABLE,
                output_path=output_path,
                include_paths=[],
                opt_level=opt,
            ),
        )
        assert result.success, getattr(result, "errors", result)
        completed = subprocess.run([str(output_path)], check=False)
        assert completed.returncode == 0, (
            f"per-type release_fields leaked/misbehaved at {opt.name}: "
            f"exit {completed.returncode}"
        )


def test_dict_iter_keeps_dict_alive(tmp_path: Path):
    """Regression: `DictIter` held only `handle: RawPtr` (no strong reference to
    the Dict), so iterating a transiently-alive dict — `for k in dict_keys(make())`
    — read freed memory once the source dict was released (use-after-free). Fixed
    by making `DictIter<K, V>` hold the `Dict<K, V>` by value, like `VecIter`.

    The dict from `make_dict()` is a temporary; the loop body churns the allocator
    so any freed-and-reused dict storage would corrupt the keys. With the bug the
    key lengths come out wrong (18); with the fix the iterator keeps the dict alive
    and the lengths sum to 2+3+4 = 9. Reproduces at both -O0 and -O3."""
    exit_code = compile_and_run(
        tmp_path,
        "dict_iter_uaf",
        """
import "dict.rl"
import "iter.rl"

def make_dict() -> Dict<String, i64> {
    var d = dict_string_i64_new();
    d.set("ab", 1); d.set("cde", 2); d.set("fghi", 3);
    return d;
}

def main() -> i32 {
    var total: i32 = 0;
    for k in dict_keys(make_dict()) {
        // Churn the allocator: if the temporary dict were freed (the UAF), this
        // would reuse its storage and corrupt the keys read below.
        var junk = dict_string_i64_new();
        junk.set("zzzzzzzzzzzzzzzz", 999);
        junk.set("yyyyyyyyyyyyyyyy", 888);
        total = total + (k.len() as i32);
    }
    return total;  // 2 + 3 + 4 = 9
}
""",
    )
    assert exit_code == 9


def test_string_literal_borrowed_arg_not_leaked(tmp_path: Path):
    """Regression: a string literal passed as a *borrowed* operand leaked.

    A string literal lowers to a freshly-allocated owned `String` heap object.
    When bound (`let s = "x"`) ARC balances it, but when passed inline as a
    borrowed argument / receiver (`f("x")`, `"x".len()`, `a + "b"`) it was never
    materialized into a MIR local, so ARC had nowhere to attach a release and the
    object leaked once per evaluation. Fixed by materializing string literals into
    a temp local in `_lower_literal` so ARC tracks them like any owned temporary.

    Loops 50000 times over each borrowed position and asserts the live object
    count stays near baseline (the bug leaked ~150000). Reproduces at -O0."""
    exit_code = compile_and_run(
        tmp_path,
        "strlit_borrow_no_leak",
        """
import "string.rl"

extern "C" def rt_obj_live_count() -> i64;

def take(s: String) -> i64 { return s.len(); }

def main() -> i32 {
    var acc: i64 = 0;
    var i: i64 = 0;
    while i < 50000 {
        acc = acc + take("x");        // borrowed call argument
        acc = acc + "yz".len();       // borrowed method receiver
        let z = "a" + "b";            // borrowed operands of `+`
        acc = acc + z.len();
        i = i + 1;
    }
    var live: i64 = 1000000;
    unsafe { live = rt_obj_live_count(); }
    if live > 1000 { return 1; }
    if acc < 0 { return 2; }  // keep `acc` (and the literal evaluations) live
    return 0;
}
""",
    )
    assert exit_code == 0, "string literal in a borrowed position leaked"


def test_dict_entry_index_single_probe_rmw(tmp_path: Path):
    """`entry_index` + `value_at`/`set_value_at` give single-probe read-modify-
    write (the dict-as-counter primitive). Verifies: insert-with-default, that an
    existing key returns the SAME stable index (no duplicate), O(1) index get/set
    agree with hashed `get`, and index stability across an inserting resize."""
    exit_code = compile_and_run(
        tmp_path,
        "dict_entry_index",
        """
import "dict.rl"
import "string.rl"
import "vec.rl"

def make_key(n: i64) -> String {
    let alpha = "0123456789";
    var x = n; var s = "k";
    while x > 0 { s = s + alpha.substring((x % 10) as i32, 1); x = x / 10; }
    return s;
}

def main() -> i32 {
    var keys = Vec<String>.with_capacity(4);
    keys.push("a"); keys.push("b"); keys.push("c");

    var d = dict_string_i64_new();
    var ok: i32 = 0;

    // Insert "a" with default 0, then increment to 1 (single-probe RMW).
    let ia = d.entry_index(keys.get(0), 0 as i64);
    d.set_value_at(ia, d.value_at(ia) + 1 as i64);
    let ib = d.entry_index(keys.get(1), 0 as i64);
    d.set_value_at(ib, d.value_at(ib) + 1 as i64);
    // Re-entry of "a" must return the same index and see the current value.
    let ia2 = d.entry_index(keys.get(0), 0 as i64);
    d.set_value_at(ia2, d.value_at(ia2) + 1 as i64);

    if ia == ia2 { ok = ok + 1; }                            // stable index
    if d.value_at(ia) == 2 { ok = ok + 1; }                  // a incremented twice
    if d.value_at(ib) == 1 { ok = ok + 1; }                  // b once
    if (d.get(keys.get(0)) ?? 0) == 2 { ok = ok + 1; }       // hashed get agrees
    if d.len() == 2 { ok = ok + 1; }                         // exactly two keys

    // get-or-insert default: absent "c" gets default 9.
    let ic = d.entry_index(keys.get(2), 9 as i64);
    if d.value_at(ic) == 9 { ok = ok + 1; }
    if d.len() == 3 { ok = ok + 1; }

    // Index stability across many inserts (forces a resize); re-check "a".
    var n: i64 = 0;
    while n < 200 { d.entry_index(make_key(n), n); n = n + 1; }
    if d.value_at(ia) == 2 { ok = ok + 1; }                  // ia still valid post-resize

    return ok;  // expect 8
}
""",
    )
    assert exit_code == 8
