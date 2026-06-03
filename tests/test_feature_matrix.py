"""Unified feature tests across the compiler pipeline.

These tests intentionally cover features by compiler stage. A feature listed at
LLVM stage is expected to lower all the way to LLVM IR. A feature listed at MIR
stage is currently front-end/mid-end covered but not yet promised by codegen or
runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Optional

import pytest

from rolang.arc_insertion import insert_arc
from rolang.codegen import compile_to_llvm
from rolang.hir_builder import build_hir
from rolang.mir import BoxExistential, MirBuildResult
from rolang.mir_builder import build_mir
from rolang.monomorphize import monomorphize
from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck


class Stage(IntEnum):
    PARSE = 1
    RESOLVE = 2
    TYPECHECK = 3
    HIR = 4
    MIR = 5
    ARC = 6
    LLVM = 7


@dataclass(frozen=True)
class FeatureCase:
    name: str
    source: str
    stage: Stage
    verify: Optional[Callable[[PipelineResult], None]] = None


@dataclass
class PipelineResult:
    program: object | None = None
    resolution: object | None = None
    typecheck: object | None = None
    hir: object | None = None
    mono: object | None = None
    mir: MirBuildResult | None = None
    arc: object | None = None
    llvm: object | None = None


STRING_PRELUDE = "\nstruct String { var handle: RawPtr; }\n"

# Minimal Vec/Dict definitions so `[...]` and `[k: v]` literals (now
# sugar for these std structs) type-check without pulling in the real
# stdlib. The method bodies are stubs; tests that hit Stage.LLVM only
# need the shape, not the runtime semantics.
COLLECTIONS_PRELUDE = """
struct Vec<T> {
    var handle: RawPtr;
    pub static def with_capacity(capacity: i32) -> Vec<T> {
        var v: Vec<T>;
        return v;
    }
    pub def push(value: T) -> Void { }
    pub def get(index: i32) -> T {
        var out: T;
        return out;
    }
    pub def len() -> i32 { return 0; }
}
struct Dict<K, V> {
    var handle: RawPtr;
    pub static def with_capacity(capacity: i32, key_kind: i32) -> Dict<K, V> {
        var d: Dict<K, V>;
        return d;
    }
    pub def set(key: K, value: V) -> Void { }
    pub def get(key: K) -> V? {
        var out: V;
        return out;
    }
    pub def len() -> i64 { return 0; }
}
"""


def with_string_prelude(source: str) -> str:
    if "String" in source or '"' in source:
        return source + STRING_PRELUDE
    return source


def with_collections_prelude(source: str) -> str:
    needs_collections = (
        "[" in source
        or "Vec<" in source
        or "Dict<" in source
    )
    if needs_collections:
        return source + COLLECTIONS_PRELUDE
    return source


def run_pipeline(source: str, stage: Stage) -> PipelineResult:
    result = PipelineResult()
    source = with_string_prelude(source)
    source = with_collections_prelude(source)

    result.program = parse(source)
    if stage == Stage.PARSE:
        return result

    result.resolution = resolve(result.program)
    assert not result.resolution.has_errors(), result.resolution.errors
    if stage == Stage.RESOLVE:
        return result

    result.typecheck = typecheck(result.program, result.resolution)
    assert not result.typecheck.has_errors(), result.typecheck.errors
    if stage == Stage.TYPECHECK:
        return result

    result.hir = build_hir(result.program, result.resolution, result.typecheck)
    assert not result.hir.has_errors(), result.hir.errors
    if stage == Stage.HIR:
        return result

    result.mono = monomorphize(result.hir)
    assert not result.mono.has_errors(), result.mono.errors

    result.mir = build_mir(result.mono)
    assert not result.mir.has_errors(), result.mir.errors
    if stage == Stage.MIR:
        return result

    result.arc = insert_arc(result.mir)
    assert not result.arc.has_errors(), result.arc.errors
    if stage == Stage.ARC:
        return result

    result.llvm = compile_to_llvm(result.arc, module_name="feature_matrix")
    assert not result.llvm.has_errors(), result.llvm.errors
    assert str(result.llvm.module)
    return result


def assert_has_box_existential(result: PipelineResult) -> None:
    assert result.mir is not None
    assert any(
        isinstance(op, BoxExistential)
        for func in result.mir.program.functions
        for block in func.blocks.values()
        for op in block.ops
    )


FEATURES = [
    FeatureCase(
        name="arithmetic_functions",
        stage=Stage.LLVM,
        source="""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}

def main() -> i32 {
    return add(20, 22);
}
""",
    ),
    FeatureCase(
        name="struct_init_and_fields",
        stage=Stage.LLVM,
        source="""
struct FeatureMatrixPoint {
    var x: i32;
    var y: i32;
}

def main() -> i32 {
    let p = FeatureMatrixPoint { x: 3, y: 4 };
    return p.x + p.y;
}
""",
    ),
    FeatureCase(
        name="enum_switch",
        stage=Stage.LLVM,
        source="""
enum Choice {
    case one(i32);
    case two(i32);
}

def value(c: Choice) -> i32 {
    switch c {
    case .one(let x):
        return x;
    case .two(let y):
        return y;
    }
}
""",
    ),
    FeatureCase(
        name="optional_if_let",
        stage=Stage.LLVM,
        source="""
def value(x: i32?) -> i32 {
    if let y = x {
        return y;
    }
    return 0;
}
""",
    ),
    FeatureCase(
        name="arrays",
        stage=Stage.LLVM,
        source="""
def first() -> i32 {
    let xs = [1, 2, 3];
    return xs[0];
}
""",
    ),
    FeatureCase(
        name="dictionaries",
        stage=Stage.LLVM,
        source="""
def lookup() -> i32 {
    let values = ["a": 1, "b": 2];
    if let value = values["a"] {
        return value;
    }
    return 0;
}
""",
    ),
    FeatureCase(
        name="collection_count",
        stage=Stage.LLVM,
        source="""
def count_all() -> i32 {
    let xs = [1, 2, 3];
    let values = ["a": 1, "b": 2];
    return xs.len() + (values.len() as i32);
}
""",
    ),
    FeatureCase(
        name="array_for_loop",
        stage=Stage.LLVM,
        source="""
def sum_all() -> i32 {
    var total = 0;
    for x in [1, 2, 3] {
        total = total + x;
    }
    return total;
}
""",
    ),
    FeatureCase(
        name="closures",
        stage=Stage.LLVM,
        source="""
def main() -> i32 {
    let base = 41;
    let f = { x: i32 in
        return x + base;
    };
    return f(1);
}
""",
    ),
    FeatureCase(
        name="generic_function",
        stage=Stage.LLVM,
        source="""
def identity<T>(x: T) -> T {
    return x;
}

def main() -> i32 {
    return identity(42);
}
""",
    ),
    FeatureCase(
        name="generic_struct",
        stage=Stage.LLVM,
        source="""
struct GenericBox<T> {
    var value: T;
}

def main() -> i32 {
    let box = GenericBox<i32> { value: 42 };
    return box.value;
}
""",
    ),
    FeatureCase(
        name="generic_struct_method",
        stage=Stage.LLVM,
        source="""
struct GenericMethodBox<T> {
    var value: T;

    def choose(x: T) -> T {
        return x;
    }
}

def main() -> i32 {
    let box = GenericMethodBox<i32> { value: 1 };
    return box.choose(42);
}
""",
    ),
    FeatureCase(
        name="extension_method_mutates_self",
        stage=Stage.LLVM,
        source="""
struct MatrixPoint {
    var x: i64;
    var y: i64;
}

extension MatrixPoint {
    def add(other: MatrixPoint) -> Void {
        self.x = self.x + other.x;
        self.y = self.y + other.y;
    }
}

def main() -> i32 {
    var p1 = MatrixPoint { x: 3, y: 4 };
    let p2 = MatrixPoint { x: 1, y: 2 };
    p1.add(p2);
    return (p1.x + p1.y) as i32;
}
""",
    ),
    FeatureCase(
        name="protocol_existential_assignment",
        stage=Stage.LLVM,
        verify=assert_has_box_existential,
        source="""
protocol Printable {
    def print() -> Void;
}

struct Box {
    def print() -> Void {
    }
}

def use() -> Void {
    let p: any Printable = Box {};
}
""",
    ),
    FeatureCase(
        name="protocol_existential_method_dispatch",
        stage=Stage.LLVM,
        verify=assert_has_box_existential,
        source="""
protocol Valued {
    def value() -> i32;
}

struct ValuedBox {
    def value() -> i32 {
        return 42;
    }
}

def use() -> i32 {
    let p: any Valued = ValuedBox {};
    return p.value();
}
""",
    ),
    FeatureCase(
        name="explicit_void_return",
        stage=Stage.LLVM,
        source="""
def log() -> Void {
    return;
}
""",
    ),
]


@pytest.mark.parametrize("case", FEATURES, ids=lambda case: case.name)
def test_feature_pipeline_matrix(case: FeatureCase):
    result = run_pipeline(case.source, case.stage)
    if case.verify:
        case.verify(result)
