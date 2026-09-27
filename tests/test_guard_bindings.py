import pytest
from rolang.driver import OptLevel, CompileOptions, compile_source
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_guard_bindings_scope_cleanup_and_loops(tmp_path, level):
    run = build_run(tmp_path, '''
struct State { var reads: i32; var cleanup: i32; }
def read(s: State) -> i32? { s.reads = s.reads + 1; if s.reads == 1 { return 42; } return nil; }
def render(s: State) -> String? {
    defer { s.cleanup = s.cleanup + 1; }
    guard let n = read(s) else { return nil; }
    return f"{n}";
}
def main() -> i32 {
    let s = State { reads: 0, cleanup: 0 };
    if !(render(s) ?? "").equals("42") { return 1; }
    if let unexpected = render(s) { return 2; }
    if s.reads != 2 || s.cleanup != 2 { return 3; }
    let items = Vec<i32?>.new(); items.push(nil); items.push(42);
    var sum = 0;
    for item in items { guard let n = item else { continue; } sum = sum + n; }
    return sum - 42;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('body', [
    'let x: i32? = 1; guard let n = x else {} return n;',
    'let x: i32? = 1; guard let n = x else { return n; } return n;',
    'guard let n = 42 else { return 0; } return n;',
])
def test_invalid_guard_binding(tmp_path, body):
    path = tmp_path/'main.rl'; path.write_text('def main() -> i32 { '+body+' }')
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
