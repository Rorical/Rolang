import pytest
from rolang.driver import OptLevel
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_lazy_iterator_composition_and_lifetime(tmp_path, level):
    run = build_run(tmp_path, '''
import std.iterator
struct State { var calls: i32; }
def make(state: State) -> Iter<String> {
    let values = Vec<i32>.new(); values.push(1); values.push(2); values.push(3); values.push(4);
    let mapped = iter_map(iter_vec(values), { n: i32 in state.calls = state.calls + 1; return n + 10; });
    let filtered = iter_filter(mapped, { n: i32 in return n % 2 == 0; });
    return iter_map(iter_take(filtered, 1), { n: i32 in return n.to_string(); });
}
def add(a: i32, b: i32) -> i32 { return a + b; }
def main() -> i32 {
    let state = State { calls: 0 };
    let source = make(state);
    if state.calls != 0 { return 1; }
    let result = iter_collect(source);
    if state.calls != 2 || result.len() != 1 || !result.get(0).equals("12") { return 2; }
    if let extra = source.__next__() { return 3; }
    if state.calls != 2 { return 4; }
    let values = Vec<i32>.new(); values.push(20); values.push(22);
    let names = Vec<String>.new(); names.push("a"); names.push("b"); names.push("c");
    var count = 0;
    for entry in iter_enumerate(iter_zip(iter_vec(values), iter_vec(names))) {
        if entry.index != (count as i64) { return 5; }
        if entry.value.first != values.get(count) { return 6; }
        if !entry.value.second.equals(names.get(count)) { return 7; }
        count = count + 1;
    }
    if count != 2 { return 8; }
    if iter_fold(iter_vec(values), 0, add) != 42 { return 9; }
    let untouched = make(state);
    if iter_collect(iter_take(untouched, 0)).len() != 0 || state.calls != 2 { return 10; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_custom_source_exhaustion_and_optional_elements(tmp_path, level):
    run = build_run(tmp_path, '''
import std.iterator
struct Source {
    var calls: i32;
    def next() -> String? {
        self.calls = self.calls + 1;
        if self.calls < 3 { return self.calls.to_string(); }
        if self.calls == 3 { return nil; }
        return "must not resume";
    }
}
def main() -> i32 {
    let source = Source { calls: 0 };
    let iterator = iter_from({ return source.next(); });
    let alias = iterator;
    if !(iterator.__next__() ?? "").equals("1") { return 1; }
    if !(alias.__next__() ?? "").equals("2") { return 2; }
    if let extra = iterator.__next__() { return 3; }
    if let extra = alias.__next__() { return 4; }
    if source.calls != 3 { return 5; }
    let values = Vec<i32?>.new(); values.push(nil); values.push(42);
    let collected = iter_collect(iter_vec(values));
    if collected.len() != 2 { return 6; }
    if let unexpected = collected.get(0) { return 7; }
    if (collected.get(1) ?? 0) != 42 { return 8; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_compiler_ergonomics_example(tmp_path, level):
    from pathlib import Path
    import subprocess
    source = (Path(__file__).parent.parent/'examples/compiler_ergonomics.rl').read_text()
    run = build_run(tmp_path, source, level)
    assert (run.returncode, run.stderr) == (0, '')
    path = tmp_path/'output.c'
    path.write_text(run.stdout)
    executable = tmp_path/'output'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', str(path), '-o', str(executable)], check=True, capture_output=True, timeout=30)
    assert subprocess.run([str(executable)], timeout=10).returncode == 42
