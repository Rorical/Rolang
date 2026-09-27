import subprocess
import pytest
from rolang.driver import OptLevel
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_writer_indentation_and_c_escape_roundtrip(tmp_path, level):
    run = build_run(tmp_path, r'''
import std.code_writer
import std.io
def main() -> i32 {
    let writer = CodeWriter.with_indent("  ");
    writer.line("#include <stdio.h>");
    writer.line("int main(void) {");
    writer.indent();
    let text = "a\0" + "7f?\"\\\n猫";
    writer.write("const char bytes[] = ");
    writer.write(c_quote(text));
    writer.line(";");
    writer.line("fwrite(bytes, 1, sizeof(bytes) - 1, stdout);\nreturn 0;");
    if !writer.dedent() || writer.dedent() { return 1; }
    writer.line("}");
    let saved = writer.to_string();
    writer.clear();
    writer.indent(); writer.line(""); writer.write("\nx"); writer.write("y");
    if !writer.to_string().equals("\n\n  xy") { return 2; }
    print(saved);
    return 0;
}
''', level)
    assert run.returncode == 0, run.stderr
    assert '\n  const char bytes[]' in run.stdout
    assert '\n  return 0;\n}\n' in run.stdout
    source = tmp_path/'generated.c'
    source.write_text(run.stdout)
    executable = tmp_path/'generated'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', str(source), '-o', str(executable)], check=True, capture_output=True, timeout=30)
    result = subprocess.run([str(executable)], capture_output=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout == 'a\0' .encode() + '7f?"\\\n猫'.encode()
