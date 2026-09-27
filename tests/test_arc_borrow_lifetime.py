import pytest
from rolang.driver import OptLevel
from test_async_tasks import build_run


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_stored_alias_survives_single_direct_call(tmp_path, level):
    run = build_run(tmp_path, '''
struct Scanner { var tokens: Vec<i32>; }
struct Result { var tokens: Vec<i32>; var error: String; }
def scan() -> Result {
    let tokens = Vec<i32>.new();
    let scanner = Scanner { tokens: tokens };
    scanner.tokens.push(41);
    tokens.push(42);
    return Result { tokens: tokens, error: "" };
}
def main() -> i32 {
    let result = scan();
    if result.tokens.len() != 2 { return 1; }
    return result.tokens.get(1) - 42;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_borrowed_field_survives_owner_mutation_in_call(tmp_path, level):
    run = build_run(tmp_path, '''
struct Holder { var values: Vec<String>; }
def replace(values: Vec<String>, owner: Holder) -> String {
    owner.values = Vec<String>.new();
    return values.get(0);
}
def main() -> i32 {
    let owner = Holder { values: Vec<String>.new() };
    owner.values.push("kept");
    if !replace(owner.values, owner).equals("kept") { return 1; }
    return owner.values.len();
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')
