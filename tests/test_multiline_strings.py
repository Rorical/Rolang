import pytest
from rolang.driver import OptLevel
from rolang.parser import parse
from lark.exceptions import LarkError
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_raw_multiline_and_interpolated_templates(tmp_path, level):
    run = build_run(tmp_path, r'''
import std.io
def main() -> i32 {
    let raw = r"C:\new\test\";
    if !raw.equals("C:\\new\\test\\") { return 1; }
    let code = """int main(void) {
    return 42;
}
""";
    let value = 42;
    let template = f"""int main(void) {{
    return {value};
}}
""";
    if !code.equals(template) { return 2; }
    let quoted = r""""quote" \n {literal}
line""";
    if !quoted.equals("\"quote\" \\n {literal}\nline") { return 3; }
    if !"""a\0b""".equals("a\0b") { return 4; }
    if !f"""{f"inner {value}"}""".equals("inner 42") { return 5; }
    print(template);
    return 0;
}
''', level)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'int main(void) {\n    return 42;\n}\n', '')

@pytest.mark.parametrize('text', ['r"unterminated', '"""unterminated', 'f"""{42}', 'f"""{ }"""'])
def test_unterminated_templates(text):
    with pytest.raises(LarkError):
        parse('def f() -> String { return '+text+'; }')
