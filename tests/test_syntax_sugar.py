import pytest
from rolang.driver import OptLevel, CompileOptions, compile_source
from test_async_tasks import build_run
LEVELS = [OptLevel.O0, OptLevel.O3]

@pytest.mark.parametrize('level', LEVELS)
def test_contextual_lambdas_and_expression_bodies(tmp_path, level):
    run = build_run(tmp_path, '''
import std.collections
struct Node { var name: String; var public: Bool; }
def apply<T, U>(f: (T) -> U, value: T) -> U { return f(value); }
def main() -> i32 {
    let nodes = Vec<Node>.new(); nodes.push(Node { name: "alpha", public: true });
    let names = map_vec(nodes, { node in node.name });
    if !join_strings(names, ",").equals("alpha") { return 1; }
    if apply({ x in x + 1 }, 41) != 42 { return 2; }
    let f: (i32) -> i32 = { x in x * 2 };
    if f(21) != 42 { return 3; }
    let lift: (i32) -> i32? = { x in x };
    if (lift(42) ?? 0) != 42 { return 4; }
    let optional: (i32?) -> String? = { x in let value = x?; f"{value}" };
    if !(optional(42) ?? "").equals("42") { return 5; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')
