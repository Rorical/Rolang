"""Inspect and execute the outputs exposed by rolangc."""
import os
from pathlib import Path
import subprocess
import sys

from llvmlite import binding as llvm
import pytest

import rolang
from rolang.driver import CompileOptions, EmitKind, OptLevel, compile_source


@pytest.mark.parametrize('level', list(OptLevel))
@pytest.mark.parametrize('emit, suffix', [
    (EmitKind.MIR_OPTIMIZED, '.opt.mir'),
    (EmitKind.LLVM_OPTIMIZED, '.opt.ll'),
    (EmitKind.ASSEMBLY, '.s'),
])
def test_compiler_output_can_be_inspected_or_executed(tmp_path, level, emit, suffix):
    source = tmp_path / 'main.rl'
    source.write_text('''
struct Point { var x: i32; }
def main() -> i32 { let p = Point { x: 42 }; return p.x; }
''')
    result = compile_source(source, CompileOptions(emit=emit, opt_level=level))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    assert result.output_path == source.with_suffix(suffix)
    assert result.output_content == result.output_path.read_text()
    if emit == EmitKind.MIR_OPTIMIZED:
        assert 'main' in result.output_content
    elif emit == EmitKind.LLVM_OPTIMIZED:
        module = llvm.parse_assembly(result.output_content)
        module.verify()
        if level.value >= 2:
            main = str(module.get_function('__rolang_user_main'))
            assert 'ret i32 42' in main
    else:
        # Assemble/link exactly what the CLI emitted, and run the product.
        runtime = Path(rolang.__file__).parent / 'runtime' / 'rolang_rt.c'
        executable = tmp_path / 'program'
        subprocess.run([os.environ.get('CC', 'cc'), str(result.output_path),
                        str(runtime), '-o', str(executable)], check=True, capture_output=True)
        run = subprocess.run([str(executable)], capture_output=True, timeout=10)
        assert run.returncode == 42


@pytest.mark.parametrize('emit', ['mir-opt', 'llvm-opt', 'asm'])
def test_cli_prints_new_text_formats(tmp_path, emit):
    source = tmp_path / 'main.rl'
    source.write_text('def main() -> i32 { return 42; }')
    run = subprocess.run([sys.executable, '-m', 'rolang.cli', '--emit', emit,
                          '-O3', str(source)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert 'main' in run.stdout


def test_backend_target_error_is_diagnostic(tmp_path):
    source = tmp_path / 'main.rl'
    source.write_text('def main() -> i32 { return 0; }')
    result = compile_source(source, CompileOptions(
        emit=EmitKind.LLVM_OPTIMIZED, target_triple='invalid-unknown-none'))
    assert not result.success
    assert result.diagnostics.has_errors()


def test_text_output_io_error_is_diagnostic(tmp_path):
    source = tmp_path / 'main.rl'
    source.write_text('def main() -> i32 { return 0; }')
    result = compile_source(source, CompileOptions(
        emit=EmitKind.MIR_OPTIMIZED, output_path=tmp_path/'missing'/'output.mir'))
    assert not result.success
    assert result.diagnostics.has_errors()


def test_lowered_mir_exposes_async_state_machine(tmp_path):
    source = tmp_path / 'async.rl'
    source.write_text('def f() async -> i32 { return 42; } '
                      'def main() async -> i32 { return await f(); }')
    raw = compile_source(source, CompileOptions(emit=EmitKind.MIR))
    lowered = compile_source(source, CompileOptions(emit=EmitKind.MIR_OPTIMIZED))
    assert raw.success and lowered.success
    assert 'def f_resume(' not in raw.output_content
    assert 'def f_resume(' in lowered.output_content
