"""File strings retain byte lengths, including embedded NULs."""
import subprocess

import pytest

from rolang.driver import CompileOptions, OptLevel, compile_source


FILE_SOURCE = r'''
import std.fs
import std.process

def main() -> i32 {
    let file = fs_open(argv(1), 0);
    unsafe { if (file as i64) == 0 { return 1; } }
    let line = fs_read_line(file);
    if !line.equals("λ\0A\n") || line.len() != 5 { return 2; }
    let rest = fs_read_all(file);
    if rest.len() != 8196 || rest.byte_at(4095) != 0 || rest.byte_at(8195) != 90 { return 3; }
    if fs_seek(file, 0, 0) != 0 || fs_tell(file) != 0 { return 4; }
    let all = fs_read_all(file);
    if all.len() != 8201 || !all.substring(0, 5).equals(line) { return 5; }
    fs_close(file);
    let bad = fs_open(argv(1) + "\0ignored", 1);
    unsafe { if (bad as i64) != 0 { fs_close(bad); return 6; } }
    return 0;
}
'''


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_file_readers_preserve_nul_bytes(tmp_path, level):
    source = tmp_path / 'bytes.rl'
    source.write_text(FILE_SOURCE)
    binary = tmp_path / 'bytes'
    result = compile_source(source, CompileOptions(opt_level=level, output_path=binary))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    data = 'λ\0A\n'.encode() + b'x' * 4095 + b'\0' + b'y' * 4099 + b'Z'
    path = tmp_path / 'data.bin'
    path.write_bytes(data)
    run = subprocess.run([str(binary), str(path)], capture_output=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (0, b'', b'')
    assert path.read_bytes() == data
