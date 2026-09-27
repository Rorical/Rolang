"""LLVM operand extension must preserve unsigned values and promoted arithmetic."""
import subprocess

import pytest

from rolang.driver import CompileOptions, OptLevel, compile_source


@pytest.mark.parametrize('level', list(OptLevel))
def test_mixed_integer_promotion_executes(tmp_path, level):
    source = tmp_path / 'promotion.rl'
    source.write_text('''
    def check(a: u8, b: i32, c: i64, signed: i8, big: u32) -> i32 {
        if a + b != 256 || b + a != 256 || a + c != 256 || c + a != 256 { return 1; }
        if a / -2 != -127 || a % -2 != 1 || -510 / a != -2 { return 2; }
        if a <= -1 || a < 0 || a == -1 || -1 >= a { return 3; }
        if a - b != 254 || b - a != -254 || a * b != 255 { return 4; }
        if signed + a != 254 || a + signed != 254 { return 5; }
        if signed >= a || a <= signed { return 6; }
        if big + -1 != 4294967294 || -1 + big != 4294967294 { return 7; }
        if big <= -1 || -1 >= big { return 8; }
        if (a >> c) != 127 || (a << c) != 254 { return 9; }
        return 0;
    }
    def main() -> i32 { return check(255, 1, 1, -1, 4294967295); }
    ''')
    binary = tmp_path / 'promotion'
    result = compile_source(source, CompileOptions(opt_level=level, output_path=binary))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (0, '', '')
