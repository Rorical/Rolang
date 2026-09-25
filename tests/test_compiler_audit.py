"""Behavioral checks across optimization levels and compiler output formats."""
from pathlib import Path
import subprocess

from llvmlite import binding as llvm
import pytest

from rolang.driver import CompileOptions, EmitKind, OptLevel, compile_source


def compile_program(tmp_path, source, level, emit=EmitKind.EXECUTABLE):
    entry = tmp_path / 'main.rl'
    entry.write_text(source)
    output = tmp_path / ('program.' + emit.name.lower())
    result = compile_source(entry, CompileOptions(
        emit=emit, opt_level=level, output_path=output,
    ))
    assert result.success, '\n'.join(d.message for d in result.diagnostics.diagnostics)
    assert output.is_file()
    return result


PROGRAMS = {
    'destructor': ('''
import "io.rl"
struct Item { var value: i32; def __release__() -> Void { println("dropped"); } }
def main() -> i32 { let x = Item { value: 42 }; return x.value; }
''', 42, 'dropped\n'),
    'nan_comparisons': ('''
def main() -> i32 {
    let n: f64 = 0.0 / 0.0;
    if n == n { return 1; }
    if !(n != n) { return 2; }
    if !(n != 1.0) { return 3; }
    if !(1.0 != n) { return 4; }
    if n < 1.0 { return 5; }
    if n >= 1.0 { return 6; }
    let f: f32 = n as f32;
    if !(f != f) { return 7; }
    return 0;
}
''', 0, ''),
    'signed_division': ('''
def main() -> i32 {
    let n: i64 = -9223372036854775807 - 1;
    let d: i64 = -1;
    if n / d != n { return 1; }
    if n % d != 0 { return 2; }
    if -17 / 3 != -5 { return 3; }
    if -17 % 3 != -2 { return 4; }
    return 0;
}
''', 0, ''),
    'struct_alias': ('''
struct Point { var x: i32; }
def change(p: Point) -> i32 { p.x = 42; return p.x; }
def main() -> i32 {
    let a = Point { x: 1 };
    let b = a;
    let v = change(b);
    if a.x != v { return 1; }
    return 0;
}
''', 0, ''),
    'async': ('''
def square(n: i32) async -> i32 { return n * n; }
def main() async -> i32 { return await square(6); }
''', 36, ''),
    'dotted_stdlib': ('''
import std.io
def main() -> i32 { println("ok"); return 0; }
''', 0, 'ok\n'),
}


@pytest.mark.parametrize('level', list(OptLevel))
@pytest.mark.parametrize('name', PROGRAMS)
def test_program_across_optimization_levels(tmp_path, level, name):
    source, code, stdout = PROGRAMS[name]
    result = compile_program(tmp_path, source, level)
    run = subprocess.run([str(result.output_path)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (code, stdout, '')


@pytest.mark.parametrize('level', list(OptLevel))
@pytest.mark.parametrize('emit', [EmitKind.MIR, EmitKind.LLVM_IR, EmitKind.OBJECT])
def test_imported_generic_output_formats(tmp_path, level, emit):
    (tmp_path / 'helper.rl').write_text('pub def identity<T>(x: T) -> T { return x; }')
    result = compile_program(tmp_path, '''
import "helper.rl"
def main() -> i32 { return identity(42); }
''', level, emit)
    if emit == EmitKind.LLVM_IR:
        module = llvm.parse_assembly(result.output_content)
        module.verify()
        assert 'main' in result.output_content
    elif emit == EmitKind.MIR:
        assert 'main' in result.output_content
    else:
        # Link the emitted object independently, proving it contains imported
        # specializations and remains on disk after the driver returns.
        import rolang
        runtime = Path(rolang.__file__).parent / 'runtime' / 'rolang_rt.c'
        executable = tmp_path / 'linked'
        subprocess.run(['cc', str(result.output_path), str(runtime), '-o', str(executable)],
                       capture_output=True, check=True)
        run = subprocess.run([str(executable)], timeout=10)
        assert run.returncode == 42


@pytest.mark.parametrize('level', list(OptLevel))
def test_generic_destructor_and_discarded_values(tmp_path, level):
    result = compile_program(tmp_path, '''
import "io.rl"
struct Box<T> {
    var value: T;
    def __release__() -> Void { println("released"); }
}
def main() -> i32 {
    let b = Box<i32> { value: 9 };
    let unused = Box<i32> { value: 4 };
    return b.value;
}
''', level)
    run = subprocess.run([str(result.output_path)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (9, 'released\nreleased\n', '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_signed_division_all_widths(tmp_path, level):
    functions = []
    checks = []
    for bits in (8, 16, 32, 64):
        minimum = -(1 << (bits - 1))
        # Separate arguments keep both operations dynamic in unoptimized IR.
        functions.append(f'''
def divide{bits}(a: i{bits}, b: i{bits}) -> i{bits} {{ return a / b; }}
def remain{bits}(a: i{bits}, b: i{bits}) -> i{bits} {{ return a % b; }}
''')
        checks.append(f'''
let n{bits}: i{bits} = {minimum + 1} - 1;
if divide{bits}(n{bits}, -1 as i{bits}) != n{bits} {{ return 1; }}
if remain{bits}(n{bits}, -1 as i{bits}) != 0 {{ return 2; }}
if divide{bits}(-17 as i{bits}, 3 as i{bits}) != -5 {{ return 3; }}
if remain{bits}(-17 as i{bits}, 3 as i{bits}) != -2 {{ return 4; }}
''')
    result = compile_program(tmp_path, '\n'.join(functions) +
        'def main() -> i32 {\n' + '\n'.join(checks) + 'return 0; }', level)
    run = subprocess.run([str(result.output_path)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (0, '', '')


def test_std_namespace_without_member_is_diagnostic(tmp_path):
    entry = tmp_path / 'main.rl'
    entry.write_text('import std\ndef main() -> i32 { return 0; }')
    result = compile_source(entry)
    assert not result.success
    assert any('not found' in d.message for d in result.diagnostics.diagnostics)
