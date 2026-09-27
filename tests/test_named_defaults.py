import pytest
from rolang.driver import OptLevel, CompileOptions, compile_source
from test_async_tasks import build_run


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_named_parameters_and_fresh_defaults(tmp_path, level):
    run = build_run(tmp_path, '''
struct State { var value: i32; }
def fresh(state: State = State { value: 0 }) -> State { return state; }
def emit(name: String, indent: i32 = 2) -> String { return f"{name}:{indent}"; }
def apply<T, U>(value: T, transform: (T) -> U) -> U { return transform(value); }
def labeled(to value: i32 = 42) -> i32 { return value; }
struct Writer { def emit(name: String, indent: i32 = 2) -> String { return emit(name, indent: indent); } }
def main() -> i32 {
    if !emit("node", indent: 4).equals("node:4") { return 1; }
    if !emit(name: "node").equals("node:2") { return 2; }
    if !Writer {}.emit("node", indent: 3).equals("node:3") { return 3; }
    if apply(value: 41, transform: { n in n + 1 }) != 42 { return 4; }
    if labeled() != 42 || labeled(to: 3) != 3 { return 5; }
    let a = fresh(); a.value = 9;
    if fresh().value != 0 || fresh(state: a).value != 9 { return 6; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('call', ['f(wrong: 1)', 'f(1, x: 2)', 'labeled(1)'])
def test_invalid_argument_labels(tmp_path, call):
    path = tmp_path/'main.rl'
    path.write_text('def f(x: i32) -> i32 { return x; } '
                    'def labeled(to x: i32) -> i32 { return x; } '
                    'def main() -> i32 { return '+call+'; }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any('label mismatch' in d.message or 'arguments' in d.message for d in result.diagnostics.diagnostics)


def test_defaults_cannot_reference_callee_parameters(tmp_path):
    path = tmp_path/'main.rl'
    path.write_text('def f(x: i32, y: i32 = x) -> i32 { return y; } '
                    'def main() -> i32 { return f(42); }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any("Undefined variable or function 'x'" in d.message for d in result.diagnostics.diagnostics)
