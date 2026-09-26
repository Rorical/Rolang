"""Native Rolang-written bootstrap compiler: differential and diagnostic tests."""
from pathlib import Path
import random
import subprocess

import pytest

from rolang.driver import CompileOptions, OptLevel, compile_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module', params=[OptLevel.O0, OptLevel.O3], ids=['bootstrap-O0', 'bootstrap-O3'])
def bootstrap(request, tmp_path_factory):
    folder = tmp_path_factory.mktemp('bootstrap')
    compiler = folder / 'rolang-stage0'
    result = compile_source(ROOT / 'selfhost' / 'main.rl', CompileOptions(
        opt_level=request.param, output_path=compiler))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    return compiler


def emit(bootstrap, tmp_path, source):
    path = tmp_path / 'program.rl'
    output = tmp_path / 'program.c'
    path.write_text(source)
    result = subprocess.run([str(bootstrap), str(path), str(output)],
                            capture_output=True, text=True, timeout=15)
    return result, path, output


def execute_c(path, level, flags=()):
    exe = path.with_suffix('.exe')
    result = subprocess.run(['cc', '-std=c11', f'-O{level}', *flags, str(path), '-o', str(exe)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)


PROGRAMS = [
    ('def main() -> i32 { return 42; }', 42),
    ('''// forward call and recursion
    def main() -> i32 {
        var sum = 0; var i = 0;
        while i < 10 { sum = sum + fib(i); i = i + 1; }
        return sum;
    }
    def fib(n: i32) -> i32 { if n < 2 { return n; } return fib(n-1) + fib(n-2); }
    ''', 88),
    ('''def even(n: i32) -> Bool { if n == 0 { return true; } return odd(n-1); }
    def odd(n: i32) -> Bool { if n == 0 { return false; } return even(n-1); }
    def main() -> i32 {
        let x = 9;
        if even(8) && !odd(8) { let x: i32 = 17; if x == 17 { return 3; } }
        return x;
    }''', 3),
    ('''def explode() -> Bool { return 1 / 0 == 0; }
    def main() -> i32 {
        if false && explode() { return 1; }
        if true || explode() { return 11; }
        return 2;
    }''', 11),
    ('''def main() -> i32 {
        let max: i32 = 2147483647;
        let min: i32 = -2147483648;
        if max + 1 != min { return 1; }
        if min - 1 != max { return 2; }
        if min / -1 != min { return 3; }
        if min % -1 != 0 { return 4; }
        if -min != min { return 5; }
        if 65536 * 65536 != 0 { return 6; }
        if -7 / 3 != -2 || -7 % 3 != -1 { return 7; }
        return 0;
    }''', 0),
    ('''def main() -> i32 {
        var done: Bool = false; var n = 0;
        while !done { n = n + 1; if n >= 5 { done = true; } }
        if (2 + 3 * 4 == 14) && (20 / 2 / 2 == 5) { return n; } else { return 99; }
    }''', 5),
    ('def main() -> i32 { let switch = 7; let int32_t = 5; return switch + int32_t; }', 12),
]


@pytest.mark.parametrize('source, expected', PROGRAMS)
def test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected):
    result, path, output = emit(bootstrap, tmp_path, source)
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')
    # The emitted program has no dependency on the Rolang compiler/runtime.
    for level in (0, 3):
        native = execute_c(output, level)
        assert (native.returncode, native.stdout, native.stderr) == (expected, '', '')
    reference = tmp_path / 'reference'
    compiled = compile_source(path, CompileOptions(opt_level=OptLevel.O2, output_path=reference))
    assert compiled.success, [d.message for d in compiled.diagnostics.diagnostics]
    native = subprocess.run([str(reference)], capture_output=True, text=True, timeout=10)
    assert (native.returncode, native.stdout, native.stderr) == (expected, '', '')


@pytest.mark.parametrize('source, diagnostic', [
    ('', 'missing main'),
    ('def main() -> i32 { return ; }', 'expected expression'),
    ('def main() -> i32 { return 1;', "expected '}'"),
    ('def main() -> i32 { return unknown; }', 'unknown variable'),
    ('def main() -> i32 { return missing(); }', 'unknown function'),
    ('def main() -> i32 { let n = 1; n = 2; return n; }', 'cannot assign to let'),
    ('def main() -> i32 { let n = 1; let n = 2; return n; }', 'duplicate local'),
    ('def f(n: i32, n: i32) -> i32 { return n; } def main() -> i32 { return 0; }', 'duplicate parameter'),
    ('def main() -> i32 { return 0; } def main() -> i32 { return 1; }', 'duplicate function'),
    ('def main(n: i32) -> i32 { return n; }', 'main must have signature'),
    ('def main() -> Bool { return true; }', 'main must have signature'),
    ('def main() -> i32 { if true { return 1; } }', 'return on every path'),
    ('def main() -> i32 { if 1 { return 1; } return 0; }', 'expected Bool'),
    ('def main() -> i32 { return true; }', 'expected i32'),
    ('def main() -> i32 { let x: Bool = 3; return 0; }', 'expected Bool'),
    ('def f(n: Bool) -> i32 { return 0; } def main() -> i32 { return f(3); }', 'expected Bool'),
    ('def f(n: i32) -> i32 { return n; } def main() -> i32 { return f(); }', 'wrong argument count'),
    ('def main() -> i32 { return 2147483648; }', 'out of i32 range'),
    ('def main() -> i32 { return 999999999999999999999999999; }', 'out of i32 range'),
    ('def main() -> i64 { return 0; }', 'only i32 and Bool'),
    ('def main() -> i32 { return "text"; }', 'unsupported character'),
    ('import std.io\ndef main() -> i32 { return 0; }', 'unsupported character'),
    ('def main() -> i32 { return ' + '(' * 150 + '0' + ')' * 150 + '; }', 'nesting limit'),
    ('def main() -> i32 { return ' + '+'.join(['1'] * 150) + '; }', 'nesting limit'),
])
def test_errors_preserve_output(bootstrap, tmp_path, source, diagnostic):
    output = tmp_path / 'program.c'
    output.write_text('existing output')
    result, _, _ = emit(bootstrap, tmp_path, source)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert diagnostic in result.stdout
    assert ':1:' in result.stdout or ':2:' in result.stdout
    assert output.read_text() == 'existing output'


def test_random_expression_differential(bootstrap, tmp_path):
    rng = random.Random(2741)
    def expression(depth):
        if depth == 0:
            value = rng.randrange(0, 50000)
            return str(value), value
        left, a = expression(depth - 1)
        right, b = expression(depth - 1)
        op = rng.choice(['+', '-', '*'])
        value = {'+': a + b, '-': a - b, '*': a * b}[op] & 0xffffffff
        if value >= 0x80000000:
            value -= 0x100000000
        return '(' + left + op + right + ')', value
    # Check full signed values rather than only the low byte of exit status.
    values = [expression(3) for _ in range(30)]
    source = 'def main() -> i32 {' + ''.join(
        f'if {text} != {value} {{ return {index + 1}; }}'
        for index, (text, value) in enumerate(values)) + 'return 0; }'
    result, path, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0, result.stdout
    reference = tmp_path / 'reference'
    compiled = compile_source(path, CompileOptions(opt_level=OptLevel.O3, output_path=reference))
    assert compiled.success, [d.message for d in compiled.diagnostics.diagnostics]
    expected = subprocess.run([str(reference)], capture_output=True, timeout=10).returncode
    for level in (0, 3):
        assert execute_c(output, level).returncode == expected


def test_usage_and_io_failures(bootstrap, tmp_path):
    result = subprocess.run([str(bootstrap)], capture_output=True, text=True)
    assert result.returncode == 2 and 'usage:' in result.stdout
    result = subprocess.run([str(bootstrap), str(tmp_path / 'missing.rl'), str(tmp_path / 'out.c')], capture_output=True, text=True)
    assert result.returncode == 2 and 'cannot open input' in result.stdout
    path = tmp_path / 'main.rl'
    source = 'def main() -> i32 { return 0; }'
    path.write_text(source)
    result = subprocess.run([str(bootstrap), str(path), str(path)], capture_output=True, text=True)
    assert result.returncode == 2 and path.read_text() == source
    result = subprocess.run([str(bootstrap), str(path), str(tmp_path / 'missing' / 'out.c')], capture_output=True, text=True)
    assert result.returncode == 2 and 'cannot open output' in result.stdout


def test_emission_is_deterministic(bootstrap, tmp_path):
    source = PROGRAMS[1][0]
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0
    first = output.read_bytes()
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0 and output.read_bytes() == first


def test_runs_without_python_or_backend_tools_on_path(bootstrap, tmp_path):
    import os
    source = tmp_path / 'main.rl'
    source.write_text('def main() -> i32 { return 17; }')
    output = tmp_path / 'out.c'
    env = dict(os.environ, PATH=str(tmp_path / 'no-tools'), PYTHONPATH='')
    result = subprocess.run([str(bootstrap), str(source), str(output)], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert execute_c(output, 3).returncode == 17


def test_large_frontend_workload(bootstrap, tmp_path):
    source = '\n'.join(f'def f{i}(n: i32) -> i32 {{ let x = n + {i}; if x < 0 {{ return 0; }} return x; }}' for i in range(300))
    source += '\ndef main() -> i32 { return f299(1) - 201; }'
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert execute_c(output, 3).returncode == 99


def test_symlink_output_cannot_overwrite_input(bootstrap, tmp_path):
    source = tmp_path / 'main.rl'
    original = 'def main() -> i32 { return 0; }'
    source.write_text(original)
    alias = tmp_path / 'alias.c'
    alias.symlink_to(source)
    result = subprocess.run([str(bootstrap), str(source), str(alias)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert source.read_text() == original


def test_divide_by_zero_is_runtime_error(bootstrap, tmp_path):
    result, _, output = emit(bootstrap, tmp_path, 'def main() -> i32 { return 1 / 0; }')
    assert result.returncode == 0
    native = execute_c(output, 3)
    assert native.returncode != 0 and 'division by zero' in native.stderr


def test_generated_arithmetic_has_no_c_undefined_behavior(bootstrap, tmp_path):
    result, _, output = emit(bootstrap, tmp_path, PROGRAMS[4][0])
    assert result.returncode == 0
    native = execute_c(output, 3, ('-fsanitize=undefined', '-fno-sanitize-recover=all'))
    assert (native.returncode, native.stderr) == (0, '')
