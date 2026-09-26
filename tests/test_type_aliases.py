"""Transparent type aliases resolve canonically across module boundaries."""
import pytest
from rolang.driver import CompileOptions, OptLevel, compile_source
from test_async_tasks import build_run


@pytest.mark.parametrize('level', list(OptLevel))
def test_aliases_forward_nested_and_static_calls(tmp_path, level):
    run = build_run(tmp_path, '''
import std.vec
typealias NodeId = Index;
typealias Index = i32;
typealias Nodes = Vec<NodeId>;
typealias Maybe = NodeId?;
typealias Callback = (NodeId) -> NodeId;
typealias Record = Item;
struct Item { var number: NodeId; }
def next(n: NodeId) -> Index { return n + 1; }
def main() -> i32 {
    let nodes: Nodes = Nodes.new();
    let callback: Callback = next;
    nodes.push(callback(4));
    let item = Record { number: nodes.get(0) };
    let optional: Maybe = item.number;
    if let value = optional { return value - 5; }
    return 9;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('source, message', [
    ('typealias A = A;', 'Cyclic type alias'),
    ('typealias A = B; typealias B = Vec<A>;', 'Cyclic type alias'),
    ('typealias A = Missing;', 'Undefined type'),
    ('typealias A = i32; def f(x: A<i32>) -> Void {}', 'does not accept generic arguments'),
    ('struct Hidden {} pub typealias Visible = Hidden;', 'non-public type'),
    ('typealias A = i32; typealias A = i64;', 'already defined'),
])
def test_invalid_aliases(tmp_path, source, message):
    path = tmp_path / 'main.rl'
    path.write_text(source + '\ndef main() -> i32 { return 0; }')
    result = compile_source(path, CompileOptions(output_path=tmp_path / 'program'))
    assert not result.success
    assert any(message in d.message for d in result.diagnostics.diagnostics), [d.message for d in result.diagnostics.diagnostics]


def test_public_alias_reexport_and_declaration_scope(tmp_path):
    (tmp_path / 'types.rl').write_text('pub typealias Id = i32; pub typealias List = Vec<Id>;')
    (tmp_path / 'exports.rl').write_text('pub import "types.rl";')
    run = build_run(tmp_path, '''
import "exports.rl" as Types
typealias Id = String;
def read(id: Types.Id) -> i32 { return id; }
def main() -> i32 {
    let list: Types.List = Vec<i32>.new();
    list.push(42);
    return read(list.get(0)) - 42;
}
''', OptLevel.O2)
    assert (run.returncode, run.stderr) == (0, '')


def test_aliases_in_native_module(tmp_path):
    from rolang.driver import EmitKind
    library = tmp_path / 'lib.rl'
    library.write_text('pub typealias Id = i32; pub def increment(n: Id) -> Id { return n + 1; }')
    result = compile_source(library, CompileOptions(emit=EmitKind.MODULE, output_path=tmp_path / 'lib.rlm'))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    library.unlink()
    run = build_run(tmp_path, '''
import "lib.rlm"
def main() -> i32 { let id: Id = 41; return increment(id) - 42; }
''', OptLevel.O2)
    assert (run.returncode, run.stderr) == (0, '')
