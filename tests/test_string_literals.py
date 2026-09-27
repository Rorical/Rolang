"""String literal metadata must use the runtime's UTF-8 byte lengths."""
import subprocess

import pytest

from rolang.driver import CompileOptions, OptLevel, compile_source


@pytest.mark.parametrize('level', list(OptLevel))
def test_utf8_and_nul_literal_byte_lengths(tmp_path, level):
    path = tmp_path / 'strings.rl'
    path.write_text(r'''
def main() -> i32 {
    let s = "λ😀\0Z";
    if s.len() != 8 { return 1; }
    if s.byte_at(6) != 0 || s.byte_at(7) != 90 { return 2; }
    if !s.substring(2, 4).equals("😀") { return 3; }
    if !s.starts_with("λ") || !s.ends_with("Z") { return 4; }
    if s.concat("λ").len() != 10 { return 5; }
    if s.find_char(90, 0) != 7 { return 6; }
    if s.compare_to("λ😀\0Y") != 1 { return 7; }
    if "".len() != 0 || "abc\0".len() != 4 { return 8; }
    return 0;
}
''')
    binary = tmp_path / 'strings'
    result = compile_source(path, CompileOptions(opt_level=level, output_path=binary))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert (native.returncode, native.stdout, native.stderr) == (0, '', '')
