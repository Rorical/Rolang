"""Compiler-oriented collections, text assembly, and identifier interning."""
import pytest
from rolang.driver import OptLevel
from test_async_tasks import build_run


@pytest.mark.parametrize('level', list(OptLevel))
def test_dictionary_removal_snapshots_and_reuse(tmp_path, level):
    run = build_run(tmp_path, '''
import std.dict
import std.set
import std.iter
struct Value { var number: i32; }
def main() -> i32 {
    let d = Dict<i32, Value>.with_capacity(2, 0);
    var i = 0;
    while i < 500 { d.set(i, Value { number: i }); i = i + 1; }
    let entries = d.entries();
    let keys = d.keys();
    let values = d.values();
    i = 0;
    while i < 500 {
        if let value = d.remove(i) {
            if value.number != i { return 1; }
        } else { return 2; }
        if d.contains(i) { return 3; }
        if i == 0 && d.keys().get(0) != 1 { return 15; }
        var j = i + 1;
        while j < 500 {
            if let remaining = d.get(j) {
                if remaining.number != j { return 4; }
            } else { return 5; }
            j = j + 1;
        }
        i = i + 1;
    }
    if d.len() != 0 { return 6; }
    if let missing = d.remove(0) { return 7; }
    i = 0;
    while i < 500 {
        if entries.get(i).key != i { return 8; }
        if entries.get(i).value.number != i { return 9; }
        if keys.get(i) != i || values.get(i).number != i { return 10; }
        d.set(i, values.get(i));
        i = i + 1;
    }
    d.clear();
    if d.len() != 0 { return 11; }
    d.set(7, Value { number: 7 });
    for key in dict_keys(d) { if key != 7 { return 12; } }
    let set = set_string_new();
    set.add("name");
    let snapshot = set.values();
    if !set.remove("name") || set.remove("name") { return 13; }
    set.add("other");
    set.clear();
    if !set.is_empty() || !snapshot.get(0).equals("name") { return 14; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_builder_and_interner(tmp_path, level):
    run = build_run(tmp_path, '''
import std.string_builder
import std.interner
import std.io
typealias SymbolId = i32;
typealias Names = Vec<String>;
def main() -> i32 {
    let builder = StringBuilder.new();
    let alias = builder;
    if builder.len() != 0 || !builder.to_string().is_empty() { return 1; }
    var i = 0;
    while i < 10000 { alias.append("ab"); i = i + 1; }
    let original = builder.to_string();
    if original.len() != 20000 { return 2; }
    builder.clear();
    builder.append_line("name");
    builder.append_byte(0 as u8);
    builder.append("end");
    let text = builder.to_string();
    if text.len() != 9 || text.byte_at(5) != 0 { return 3; }
    if original.len() != 20000 { return 4; }
    let names = StringInterner.new();
    let first: SymbolId = names.intern("alpha");
    if first != 0 || names.intern("al" + "pha") != first { return 5; }
    if names.intern("beta") != 1 || names.len() != 2 { return 6; }
    if let absent = names.lookup("missing") { return 7; }
    if let absent = names.resolve(-1) { return 8; }
    if let absent = names.resolve(2) { return 9; }
    if let found = names.resolve(first) {
        if !found.equals("alpha") { return 10; }
    } else { return 11; }
    let saved: Names = Vec<String>.new();
    saved.push(original);
    println(saved.len().to_string());
    return 0;
}
''', level)
    assert (run.returncode, run.stdout, run.stderr) == (0, '1\n', '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_collection_ownership_and_interner_growth(tmp_path, level):
    run = build_run(tmp_path, '''
import std.dict
import std.interner
struct State { var drops: i32; }
struct Item {
    var state: State;
    pub def __release__() -> Void { self.state.drops = self.state.drops + 1; }
}
def exercise(state: State) -> Void {
    let d = Dict<String, Item>.with_capacity(2, 1);
    var i = 0;
    while i < 64 {
        d.set(i.to_string(), Item { state: state });
        i = i + 1;
    }
    let snapshot = d.entries();
    for entry in snapshot { d.remove(entry.key); }
    d.clear();
}
def add_one(d: Dict<String, Item>, state: State) -> Void {
    d.set("a", Item { state: state });
}
def main() -> i32 {
    let state = State { drops: 0 };
    exercise(state);
    if state.drops != 64 { return 1; }
    let d = Dict<String, Item>.with_capacity(2, 1);
    add_one(d, state);
    d.clear();
    if state.drops != 65 { return 2; }
    if let old = d.remove("absent") { return 3; }
    let names = StringInterner.new();
    var i = 0;
    while i < 2000 {
        if names.intern(i.to_string()) != i { return 4; }
        i = i + 1;
    }
    i = 0;
    while i < 2000 {
        if names.intern(i.to_string()) != i { return 5; }
        if let text = names.resolve(i) {
            if !text.equals(i.to_string()) { return 6; }
        } else { return 7; }
        i = i + 1;
    }
    if names.len() != 2000 { return 8; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_nested_struct_field_survives_scalar_replacement(tmp_path, level):
    run = build_run(tmp_path, '''
struct Span { var start: i32; var end: i32; }
struct Token { var span: Span; var text: String; }
def main() -> i32 {
    let token = Token { span: Span { start: 3, end: 7 }, text: "name" };
    let span = token.span;
    span.end = 9;
    if token.span.end != 9 { return 1; }
    return token.span.end - token.span.start - 6;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_hash_map_structural_keys_and_collisions(tmp_path, level):
    run = build_run(tmp_path, '''
import std.hash_map
struct Key { var owner: i32; var name: String; }
def hash(key: Key) -> i64 { return key.owner as i64; }
def equal(a: Key, b: Key) -> Bool {
    return a.owner == b.owner && a.name.equals(b.name);
}
def main() -> i32 {
    let map = HashMap<Key, String>.new(hash, equal);
    var i = 0;
    while i < 100 {
        map.set(Key { owner: i % 3, name: i.to_string() }, "value" + i.to_string());
        i = i + 1;
    }
    if map.len() != 100 { return 1; }
    let snapshot = map.entries();
    map.set(Key { owner: 0, name: "0" }, "changed");
    if map.len() != 100 { return 2; }
    if let value = map.get(Key { owner: 0, name: "0" }) {
        if !value.equals("changed") { return 3; }
    } else { return 4; }
    i = 0;
    while i < 100 {
        let key = Key { owner: i % 3, name: i.to_string() };
        if let removed = map.remove(key) {
            if i != 0 && !removed.equals("value" + i.to_string()) { return 5; }
        } else { return 6; }
        if map.contains(key) { return 7; }
        i = i + 1;
    }
    if !map.is_empty() { return 8; }
    for entry in snapshot {
        if !entry.value.equals("value" + entry.key.name) { return 9; }
    }
    map.set(Key { owner: 5, name: "again" }, "new");
    map.clear();
    if !map.is_empty() { return 10; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_compiler_frontend_example(tmp_path, level):
    from pathlib import Path
    source = (Path(__file__).parent.parent / 'examples' / 'compiler_frontend.rl').read_text()
    run = build_run(tmp_path, source, level)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'let: 1\nanswer: 1\ninput: 2\n', '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_hash_map_captured_callbacks(tmp_path, level):
    run = build_run(tmp_path, '''
import std.hash_map
struct Salt { var value: i64; }
def make_map() -> HashMap<i32, String> {
    let salt = Salt { value: 17 };
    let hash = { key: i32 in return (key as i64) + salt.value; };
    let equal = { a: i32, b: i32 in return a == b; };
    return HashMap<i32, String>.new(hash, equal);
}
def main() -> i32 {
    let map = make_map();
    map.set(7, "seven");
    if let result = map.get(7) { if !result.equals("seven") { return 1; } }
    else { return 2; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')
