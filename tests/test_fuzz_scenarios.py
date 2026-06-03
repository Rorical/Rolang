"""Deterministic fuzz-style compiler/runtime scenarios.

These tests intentionally generate Rolang programs from fixed seeds. They are
not random in CI, but they cover many small combinations that are tedious to
write by hand and make any failure reproducible.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from rolang.driver import CompileOptions, EmitKind, compile_source


def _stdlib_path() -> list[Path]:
    return [Path(__file__).parent.parent / "src" / "rolang" / "std"]


def _compile_and_run(
    tmp_path: Path,
    name: str,
    source: str,
    *,
    include_stdlib: bool = False,
) -> subprocess.CompletedProcess:
    source_path = tmp_path / f"{name}.rl"
    output_path = tmp_path / name
    source_path.write_text(source, encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=_stdlib_path() if include_stdlib else [],
        ),
    )
    assert result.success, "\n".join(d.message for d in result.diagnostics.diagnostics)
    return subprocess.run([str(output_path)], check=False, capture_output=True, text=True)


def _gen_int_expr(rng: random.Random, depth: int) -> tuple[str, int]:
    if depth <= 0:
        value = rng.randint(0, 12)
        return str(value), value

    left_expr, left_value = _gen_int_expr(rng, depth - 1)
    right_expr, right_value = _gen_int_expr(rng, depth - 1)
    op = rng.choice(["+", "-", "*", "/", "%"])

    if op == "+":
        value = left_value + right_value
    elif op == "-":
        if left_value < right_value:
            left_expr, right_expr = right_expr, left_expr
            left_value, right_value = right_value, left_value
        value = left_value - right_value
    elif op == "*":
        if left_value * right_value > 100_000:
            op = "+"
            value = left_value + right_value
        else:
            value = left_value * right_value
    elif op == "/":
        if right_value == 0:
            right_expr = "1"
            right_value = 1
        value = left_value // right_value
    else:
        if right_value == 0:
            right_expr = "1"
            right_value = 1
        value = left_value % right_value

    return f"(({left_expr}) {op} ({right_expr}))", value


def test_fuzz_integer_expressions_execute(tmp_path: Path) -> None:
    rng = random.Random(0xA11CE)
    checks: list[str] = []

    for i in range(80):
        expr, expected = _gen_int_expr(rng, depth=3)
        checks.append(f"    if ({expr}) != {expected} {{ return {i + 1}; }}")

    source = "\n".join(["def main() -> i32 {"] + checks + ["    return 0;", "}"])
    completed = _compile_and_run(tmp_path, "fuzz_integer_exprs", source)

    assert completed.returncode == 0, completed.stderr


def test_fuzz_arrays_for_loops_and_indexing_execute(tmp_path: Path) -> None:
    rng = random.Random(0xA44A7)
    lines = ["def main() -> i32 {"]
    failure_code = 1

    for case in range(24):
        values = [rng.randint(0, 17) for _ in range(rng.randint(1, 7))]
        total = sum(values)
        weighted = sum(value * (index + 1) for index, value in enumerate(values))
        literal = ", ".join(str(value) for value in values)

        lines.extend(
            [
                f"    let arr_{case} = [{literal}];",
                f"    var sum_{case} = 0;",
                f"    for value_{case} in arr_{case} {{",
                f"        sum_{case} = sum_{case} + value_{case};",
                "    }",
                f"    if sum_{case} != {total} {{ return {failure_code}; }}",
                f"    if arr_{case}.len() != {len(values)} {{ return {failure_code + 1}; }}",
                f"    var weighted_{case} = 0;",
                f"    var index_{case}: i32 = 0;",
                f"    while index_{case} < arr_{case}.len() {{",
                f"        weighted_{case} = weighted_{case} + arr_{case}[index_{case}] * (index_{case} + 1);",
                f"        index_{case} = index_{case} + 1;",
                "    }",
                f"    if weighted_{case} != {weighted} {{ return {failure_code + 2}; }}",
            ]
        )
        failure_code += 3

    lines.extend(["    return 0;", "}"])
    completed = _compile_and_run(tmp_path, "fuzz_arrays", "\n".join(lines))

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("seed", [0x51, 0x52, 0x53, 0x54, 0x55])
def test_fuzz_linked_list_interleaved_operations_execute(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    model: list[int] = []
    lines = [
        'import "linked_list.rl"',
        "",
        "def main() -> i32 {",
        "    var xs: LinkedList<i32> = LinkedList<i32>.new();",
    ]
    failure_code = 1

    for step in range(45):
        op = rng.choice(["push_front", "push_back", "pop_front", "front", "len", "clear"])
        value = rng.randint(0, 90)

        if op == "push_front":
            model.insert(0, value)
            lines.append(f"    xs.push_front({value});")
        elif op == "push_back":
            model.append(value)
            lines.append(f"    xs.push_back({value});")
        elif op == "pop_front":
            expected = model.pop(0) if model else -999
            lines.extend(
                [
                    f"    let popped_{step} = xs.pop_front() ?? -999;",
                    f"    if popped_{step} != {expected} {{ return {failure_code}; }}",
                ]
            )
            failure_code += 1
        elif op == "front":
            expected = model[0] if model else -999
            lines.extend(
                [
                    f"    let front_{step} = xs.front() ?? -999;",
                    f"    if front_{step} != {expected} {{ return {failure_code}; }}",
                ]
            )
            failure_code += 1
        elif op == "len":
            lines.append(f"    if xs.len() != {len(model)} {{ return {failure_code}; }}")
            failure_code += 1
        else:
            model.clear()
            lines.append("    xs.clear();")

        lines.append(f"    if xs.len() != {len(model)} {{ return {failure_code}; }}")
        failure_code += 1
        expected_front = model[0] if model else -999
        lines.extend(
            [
                f"    let check_front_{step} = xs.front() ?? -999;",
                f"    if check_front_{step} != {expected_front} {{ return {failure_code}; }}",
            ]
        )
        failure_code += 1

    lines.extend(["    return 0;", "}"])
    completed = _compile_and_run(
        tmp_path,
        f"fuzz_linked_list_{seed}",
        "\n".join(lines),
        include_stdlib=True,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("name", "source", "expected_exit"),
    [
        (
            "named_constructor_order",
            """
struct Triple {
    var a: i32;
    var b: i32;
    var c: i32;
}

def main() -> i32 {
    let t = Triple { c: 3, a: 1, b: 2 };
    return t.a * 100 + t.b * 10 + t.c;
}
""",
            123,
        ),
        (
            "generic_recursive_optional_constructor_order",
            """
struct Node<T> {
    var next: Node<T>?;
    var value: T;
}

def main() -> i32 {
    let tail: Node<i32> = Node { value: 7, next: nil };
    let head: Node<i32> = Node { value: 5, next: tail };

    if head.value != 5 { return 1; }
    if (head.next?.value ?? 0) != 7 { return 2; }
    return head.value + (head.next?.value ?? 0);
}
""",
            12,
        ),
        (
            "optional_heap_field_reassignment",
            """
struct Box {
    var value: i32;
}

struct Holder {
    var item: Box?;
}

def main() -> i32 {
    let first = Box { value: 10 };
    let second = Box { value: 32 };
    var holder = Holder { item: first };
    if (holder.item?.value ?? 0) != 10 { return 1; }
    holder.item = second;
    if (holder.item?.value ?? 0) != 32 { return 2; }
    holder.item = nil;
    return holder.item?.value ?? 42;
}
""",
            42,
        ),
    ],
)
def test_handwritten_compiler_stress_scenarios_execute(
    tmp_path: Path,
    name: str,
    source: str,
    expected_exit: int,
) -> None:
    completed = _compile_and_run(tmp_path, name, source)

    assert completed.returncode == expected_exit, completed.stderr
