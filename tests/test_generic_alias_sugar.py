import pytest
from rolang.driver import OptLevel, CompileOptions, compile_source
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_generic_aliases_in_compiler_signatures(tmp_path, level):
    run = build_run(tmp_path, '''
import std.result
typealias ParseResult<T> = Result<T, String>;
typealias Option<T> = T?;
typealias List<T> = Vec<T>;
typealias Callback<T, U> = (T) -> U;
typealias Reversed<A, B> = (B, A);
def first<A, B>(pair: Reversed<A, B>) -> B { return pair.0; }
def run<T, U>(f: Callback<T, U>, value: T) -> U { return f(value); }
typealias Record = Item;
struct Item { var n: i32; static def new(n: i32) -> Record { return Item { n: n }; } }
def parse() -> ParseResult<i32> { return Result.ok(value: 42); }
def text() -> ParseResult<String> { let n = parse()?; return Result.ok(value: f"{n}"); }
def main() -> i32 {
    if first((42, "text")) != 42 { return 7; }
    if !run({ n in f"{n}" }, 42).equals("42") { return 8; }
    let list = List<i32>.new(); list.push(42);
    let f: Callback<i32, String> = { x in f"{x}" };
    let value: Option<String> = f(list.get(0));
    if !(value ?? "").equals("42") { return 1; }
    if Item.new(42).n != 42 { return 2; }
    switch text() { case .ok(let s): if !s.equals("42") { return 3; } case .err(let e): return 4; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('source, message', [
    ('typealias A<T> = A<T>;', 'Cyclic'),
    ('typealias A<T> = Vec<T>; def f(x: A) -> Void {}', 'expects 1'),
    ('typealias A<T> = Vec<T>; def f(x: A<i32, i64>) -> Void {}', 'expects 1'),
    ('struct Hidden {} pub typealias A<T> = (T, Hidden);', 'non-public'),
])
def test_invalid_generic_aliases(tmp_path, source, message):
    path = tmp_path/'main.rl'; path.write_text(source + '\ndef main() -> i32 { return 0; }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any(message in d.message for d in result.diagnostics.diagnostics), [d.message for d in result.diagnostics.diagnostics]


def test_generic_alias_and_method_in_native_module(tmp_path):
    from rolang.driver import EmitKind
    library = tmp_path/'lib.rl'
    library.write_text('''
pub typealias Callback<T, U> = (T) -> U;
pub struct Box<T> {
    pub var value: T;
    pub def map<U>(f: Callback<T, U>) -> U { return f(self.value); }
}
''')
    result = compile_source(library, CompileOptions(emit=EmitKind.MODULE, output_path=tmp_path/'lib.rlm'))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    library.unlink()
    run = build_run(tmp_path, '''
import "lib.rlm"
def main() -> i32 {
    let box = Box<i32> { value: 42 };
    let text = box.map({ value in f"{value}" });
    if !text.equals("42") { return 1; }
    return 0;
}
''', OptLevel.O2)
    assert (run.returncode, run.stderr) == (0, '')
