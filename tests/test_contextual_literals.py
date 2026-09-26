"""Numeric literals use concrete parameter types without narrowing variables."""
import subprocess

import pytest

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck
from rolang.driver import CompileOptions, OptLevel, compile_source


def check(source):
    program = parse(source)
    return typecheck(program, resolve(program))


@pytest.mark.parametrize('type_name, value', [
    ('i8', '-128'), ('i8', '127'), ('u8', '255'),
    ('i16', '-32768'), ('i64', '-9223372036854775808'),
    ('u64', '18446744073709551615'), ('f32', '1.25'),
])
def test_function_argument_accepts_representable_literal(type_name, value):
    result = check(f'def f(x: {type_name}) -> {type_name} {{ return x; }} '
                   f'def main() -> Void {{ let x = f({value}); }}')
    assert not result.errors, result.errors


@pytest.mark.parametrize('type_name, value', [
    ('i8', '-129'), ('i8', '128'), ('u8', '-1'), ('u8', '256'),
    ('i64', '-9223372036854775809'),
])
def test_function_argument_rejects_out_of_range_literal(type_name, value):
    result = check(f'def f(x: {type_name}) -> Void {{ }} '
                   f'def main() -> Void {{ f({value}); }}')
    assert result.errors
    assert any('does not fit' in str(e) for e in result.errors)


def test_variables_are_not_implicitly_narrowed():
    result = check('def f(x: i8) -> Void {} def main() -> Void { let n: i32 = 1; f(n); }')
    assert result.errors


@pytest.mark.parametrize('level', list(OptLevel))
def test_contextual_literals_execute(tmp_path, level):
    source = tmp_path / 'main.rl'
    source.write_text('''
struct Limits {
    def narrow(x: i8) -> i8 { return x; }
}
enum Small { case value(i8); }
def narrow(x: i8) -> i8 { return x; }
def decimal(x: f32) -> f32 { return x; }
def wide(x: i64) -> i64 { return x; }
def main() -> i32 {
    let limit: i8 = -128;
    if narrow(-128) != limit { return 1; }
    let obj = Limits {};
    if obj.narrow(127) != 127 { return 2; }
    let fn = narrow;
    if fn(-128) != limit { return 3; }
    if decimal(1.25) != 1.25 { return 4; }
    if wide(-9223372036854775808) >= 0 { return 5; }
    let e = Small.value(-128);
    switch e { case .value(let x): if x != limit { return 6; } }
    return 0;
}
''')
    result = compile_source(source, CompileOptions(opt_level=level, output_path=tmp_path/'program'))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    run = subprocess.run([str(result.output_path)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stderr) == (0, '')


def test_cast_operand_keeps_its_own_type():
    result = check('def f(x: u8) -> Void {} def main() -> Void { f(-1 as u8); }')
    assert not result.errors, result.errors


@pytest.mark.parametrize('level', list(OptLevel))
def test_function_values_pass_return_and_release_heap_values(tmp_path, level):
    source = tmp_path / 'main.rl'
    source.write_text('''
import "io.rl"
def identity(s: String) -> String { return s; }
def apply(f: (String) -> String, s: String) -> String { return f(s); }
def get() -> (String) -> String { return identity; }
def notify() -> Void { return println("done"); }
extern "C" def rt_obj_live_count() -> i64;
def exercise() -> Void {
    let f = get();
    println(apply(f, "hello"));
    println(apply(identity, "world"));
    let g = notify;
    g();
}
def main() -> i32 {
    unsafe {
        let before = rt_obj_live_count();
        exercise();
        if rt_obj_live_count() != before { return 1; }
    }
    return 0;
}
''')
    result = compile_source(source, CompileOptions(opt_level=level, output_path=tmp_path/'program'))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    run = subprocess.run([str(result.output_path)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'hello\nworld\ndone\n', '')


@pytest.mark.parametrize('declaration, expected', [
    ('unsafe def f() -> i32 { return 1; }', 'Unsafe function values'),
    ('def f<T>(x: T) -> T { return x; }', 'Generic function values'),
    ('def f() async -> i32 { return 1; }', 'Async function values'),
])
def test_unsupported_function_values_have_diagnostics(tmp_path, declaration, expected):
    source = tmp_path / 'main.rl'
    source.write_text(declaration + '\ndef main() -> i32 { let value = f; return 0; }')
    result = compile_source(source, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any(expected in d.message for d in result.diagnostics.diagnostics)
