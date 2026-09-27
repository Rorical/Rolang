"""Contextual inference and enum payload bugs found by native differential tests."""
import pytest

from rolang.driver import OptLevel
from test_async_tasks import build_run


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
@pytest.mark.parametrize('source', [
    '''def identity<T>(value: T) -> T { return value; }
def main() -> i32 {
    let value: i32? = identity(42);
    guard let n = value else { return 1; } return n;
}''',
    '''struct Record { var n: i32; }
def identity<T>(value: T) -> T { return value; }
def main() -> i32 { return identity(Record { n: 42 }).n; }''',
    '''enum Choice<T> { case none; case some(T); }
def absent<T>(value: T) -> Choice<T> { return Choice.none; }
def main() -> i32 {
    let x: Choice<i32> = Choice.none;
    let called: Choice<i32> = Choice.none();
    switch called { case .some(_): return 3; case .none: {} }
    switch x { case .some(_): return 1; case .none: {} }
    return switch absent("text") { case .none: 42; case .some(_): 2; };
}''',
    '''enum Inner<T> { case value(T); case empty; }
enum Outer<T> { case wrap(Inner<T>); case other(T); }
def main() -> i32 {
    let x: Outer<i32> = Outer.wrap(Inner.value(42));
    let unused: Outer<String> = Outer.other("ignored");
    switch unused { case .other(_): {} case .wrap(_): return 1; }
    return switch x { case .wrap(.value(42)): 42; default: 2; };
}''',
])
def test_generic_context_and_payloads(tmp_path, level, source):
    run = build_run(tmp_path, source, level)
    assert (run.returncode, run.stdout, run.stderr) == (42, '', '')
