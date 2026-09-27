import pytest
from rolang.driver import CompileOptions, OptLevel, compile_source
from rolang.parser import parse
from lark.exceptions import LarkError
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_interpolation_values_order_escaping_and_nesting(tmp_path, level):
    run = build_run(tmp_path, r'''
import std.io
struct State { var n: i32; }
struct Name { var text: String; def to_string() -> String { return self.text; } }
def next(s: State) -> i32 { s.n = s.n + 1; return s.n; }
def main() -> i32 {
    let state = State { n: 0 };
    println(f"  {{{next(state)}}} {next(state)} {next(state)}  ");
    println(f"{true}/{false}: {Name { text: "猫" }} {f"nested {42}"}");
    let max: u64 = 18446744073709551615;
    println(f"{max} {255 as u8} {-7 as i8} {3.5 as f32}");
    println(f"// unchanged text /* yes */\n\"quote\" \\ {{literal}}");
    println("{ordinary}");
    let binary = f"a\0{42}z";
    if binary.len() != 5 || binary.byte_at(1) != 0 { return 1; }
    if !f"".is_empty() { return 2; }
    if state.n != 3 { return 3; }
    return 0;
}
''', level)
    assert run.returncode == 0, run.stderr
    assert run.stdout == ('  {1} 2 3  \ntrue/false: 猫 nested 42\n'
                          '18446744073709551615 255 -7 3.5\n'
                          '// unchanged text /* yes */\n"quote" \\ {literal}\n{ordinary}\n')

@pytest.mark.parametrize('template', ['f"{"', 'f"}"', 'f"{}"', 'f"{1 +}"'])
def test_interpolation_malformed_templates(template):
    with pytest.raises(LarkError):
        parse('def main() -> String { return ' + template + '; }')

def test_interpolation_reports_missing_conversion(tmp_path):
    path = tmp_path/'main.rl'
    path.write_text('struct Item {}\ndef main() -> i32 { let x = f"{Item {}}"; return 0; }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any('to_string' in d.message for d in result.diagnostics.diagnostics)

def test_interpolation_rejects_non_string_conversion(tmp_path):
    path = tmp_path/'main.rl'
    path.write_text('struct Item { def to_string() -> i32 { return 1; } }\ndef main() -> i32 { let x = f"{Item {}}"; return 0; }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any('return String' in d.message for d in result.diagnostics.diagnostics)

@pytest.mark.parametrize('text', [' ', '  ', '\t', '\n', '// comment', '/* comment */'])
def test_interpolation_literal_trivia_preserved(tmp_path, text):
    # Compare parser output of a text-only template, including ignored-token forms.
    node = parse('def f() -> String { return f"' + text + '"; }').items[0].body.statements[0].value
    assert node.value == text
