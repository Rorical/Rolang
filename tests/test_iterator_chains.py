import pytest
from rolang.driver import OptLevel
from test_async_tasks import build_run

@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_iterator_method_chains_and_generic_methods(tmp_path, level):
    run = build_run(tmp_path, '''
import std.iterator
struct Node { var name: String; var is_public: Bool; }
struct Box<T> { var value: T; def transform<U>(f: (T) -> U) -> U { return f(self.value); } }
def main() -> i32 {
    let nodes = Vec<Node>.new();
    nodes.push(Node { name: "private", is_public: false }); nodes.push(Node { name: "public", is_public: true });
    let names = nodes.iter().filter({ node in node.is_public }).map({ node in node.name }).collect();
    if names.len() != 1 || !names.get(0).equals("public") { return 1; }
    let n = Box<i32> { value: 42 }.transform({ x in f"{x}" });
    if !n.equals("42") { return 2; }
    let value = Box<String> { value: "abc" }.transform({ x in x.len() });
    if value != 3 { return 3; }
    if names.iter().map({ name in name.len() }).fold(0 as i64, { a, b in a + b }) != 6 { return 4; }
    for pair in names.iter().zip(names.iter()).enumerate() {
        if pair.index != 0 || !pair.value.first.equals(pair.value.second) { return 5; }
    }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')
