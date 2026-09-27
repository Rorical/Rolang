"""Compiler-writing features exercised through the Python/LLVM compiler."""
import pytest
from rolang.driver import CompileOptions, OptLevel, compile_source
from test_async_tasks import build_run

LEVELS = [OptLevel.O0, OptLevel.O3]

@pytest.mark.parametrize('level', LEVELS)
@pytest.mark.parametrize('operator', ['?', 'try'])
def test_result_propagation_changes_success_type(tmp_path, level, operator):
    call = 'read(c)?' if operator == '?' else 'try read(c)'
    run = build_run(tmp_path, '''
import std.result
struct Counter { var calls: i32; var cleanups: i32; }
def read(c: Counter) -> Result<i32, String> {
    c.calls = c.calls + 1;
    if c.calls == 1 { return Result.ok(value: 42); }
    return Result.err(error: "invalid token");
}
def parse(c: Counter) -> Result<String, String> {
    defer { c.cleanups = c.cleanups + 1; }
    let n = CALL;
    return Result.ok(value: n.to_string());
}
def main() -> i32 {
    let c = Counter { calls: 0, cleanups: 0 };
    switch parse(c) {
        case .ok(let s): if !s.equals("42") { return 1; }
        case .err(let e): return 2;
    }
    switch parse(c) {
        case .ok(let s): return 3;
        case .err(let e): if !e.equals("invalid token") { return 4; }
    }
    if c.calls != 2 || c.cleanups != 2 { return 5; }
    return 0;
}
'''.replace('CALL', call), level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('level', LEVELS)
def test_result_case_order_and_generic_parameter_order(tmp_path, level):
    run = build_run(tmp_path, '''
enum Parsed<E, T> { case err(error: E); case ok(value: T); }
enum Output { case ok(value: i64); case err(error: String); }
def token(fail: Bool) -> Parsed<String, i32> {
    if fail { return Parsed<String, i32>.err(error: "bad"); }
    return Parsed<String, i32>.ok(value: 42);
}
def parse(fail: Bool) -> Output {
    let n = token(fail)?;
    return Output.ok(value: n as i64);
}
def main() -> i32 {
    switch parse(false) {
        case .ok(let n): if n != 42 { return 1; }
        case .err(let e): return 2;
    }
    switch parse(true) {
        case .ok(let n): return 3;
        case .err(let e): if !e.equals("bad") { return 4; }
    }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('source', [
    'def f() -> i32 { return 42?; }',
    'enum R { case ok(i32); case err(String); case other; } def f(r: R) -> R { let x = r?; return r; }',
    'enum R { case ok(i32, i32); case err(String); } def f(r: R) -> R { let x = r?; return r; }',
    'enum R { case ok(i32); case err(String); } enum S { case other; } def f(r: R) -> S { let x = r?; return S.other; }',
    'enum R { case ok(i32); case err(String); } enum S { case ok(i32); case err(i32); } def f(r: R) -> S { let x = r?; return S.ok(1); }',
])
def test_invalid_propagation_diagnostic(tmp_path, source):
    path = tmp_path / 'main.rl'
    path.write_text(source + '\ndef main() -> i32 { return 0; }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any('Result type' in d.message or 'propagate error type' in d.message
               for d in result.diagnostics.diagnostics)

@pytest.mark.parametrize('level', LEVELS)
def test_generic_ast_enum_and_pattern_matching(tmp_path, level):
    run = build_run(tmp_path, '''
enum Expr<T> {
    case literal(value: T);
    case pair(left: Expr<T>?, right: Expr<T>?);
}
def sum(expr: Expr<i32>?) -> i32 {
    if let node = expr {
        switch node {
            case .literal(let n) where n < 0: return 0;
            case .literal(let n): return n;
            case .pair(let left, let right): return sum(left) + sum(right);
        }
    }
    return 0;
}
def identity<T>(value: T) -> T { return value; }
def main() -> i32 {
    let a = Expr<i32>.literal(value: 40);
    let b = Expr<i32>.literal(value: 2);
    let root = Expr<i32>.pair(left: a, right: b);
    return sum(identity(root)) - 42;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('level', LEVELS)
def test_optional_propagation_and_nested_patterns(tmp_path, level):
    run = build_run(tmp_path, '''
struct State { var calls: i32; var cleanup: i32; }
def read(s: State) -> i32? {
    s.calls = s.calls + 1;
    if s.calls == 1 { return 42; }
    return nil;
}
def text(s: State) -> String? {
    defer { s.cleanup = s.cleanup + 1; }
    let n = read(s)?;
    return n.to_string();
}
enum Inner { case number(i32); }
enum Outer { case empty; case nested(Inner); }
def classify(value: Outer) -> i32 {
    switch value {
        case .nested(.number(7)): return 7;
        case .nested(.number(let n)) where n > 0: return n;
        default: return 0;
    }
}
def main() -> i32 {
    let s = State { calls: 0, cleanup: 0 };
    if !(text(s) ?? "").equals("42") { return 1; }
    if let value = text(s) { return 2; }
    if s.calls != 2 || s.cleanup != 2 { return 3; }
    if classify(Outer.empty) != 0 { return 4; }
    if classify(Outer.nested(Inner.number(9))) != 9 { return 5; }
    if classify(Outer.nested(Inner.number(7))) != 7 { return 6; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

