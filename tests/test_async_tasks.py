"""Executable async task/IO regressions across every optimization level."""
import subprocess

import pytest

from rolang.driver import CompileOptions, OptLevel, compile_source


def build_run(tmp_path, source, level, timeout=10):
    path = tmp_path / 'main.rl'
    path.write_text(source)
    result = compile_source(path, CompileOptions(opt_level=level, output_path=tmp_path/'program'))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    return subprocess.run([str(result.output_path)], capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize('level', list(OptLevel))
def test_spawn_results_repeated_await_and_fairness(tmp_path, level):
    run = build_run(tmp_path, '''
import std.task
import std.io
struct State { var value: i32; }
def worker(state: State, n: i32) async -> String {
    var i = 0;
    while i < 4 {
        state.value = state.value + 1;
        await yield_now();
        i = i + 1;
    }
    return "result" + n.to_string();
}
def main() async -> i32 {
    let state = State { value: 0 };
    let a = spawn worker(state, 1);
    let alias = a;
    let b = spawn worker(state, 2);
    println(await a);
    println(await alias);
    println(await b);
    if state.value != 8 { return 1; }
    if !a.done() { return 2; }
    if a.cancel() { return 3; }
    if !(await a.wait()) { return 4; }
    return 0;
}
''', level)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'result1\nresult1\nresult2\n', '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_cancellation_before_start_and_while_waiting(tmp_path, level):
    run = build_run(tmp_path, '''
import std.task
struct State { var value: i32; }
def child(state: State) async -> i32 {
    state.value = state.value + 1;
    await sleep(60000);
    state.value = 99;
    return 10;
}
def parent(state: State) async -> i32 { return await child(state); }
def main() async -> i32 {
    let state = State { value: 0 };
    let first = spawn child(state);
    if !first.cancel() { return 1; }
    if first.cancel() { return 2; }
    if await first.wait() { return 3; }
    if state.value != 0 { return 4; }
    let second = spawn parent(state);
    await sleep(5);
    if state.value != 1 { return 5; }
    if !second.cancel() { return 6; }
    if await second.wait() { return 7; }
    if !second.cancelled() { return 8; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_socket_io_timer_and_eof(tmp_path, level):
    run = build_run(tmp_path, '''
import std.async_io
import std.result
import std.task
import std.io
def writer(stream: AsyncStream) async -> i32 {
    await sleep(5);
    let result = await stream.write("hello");
    switch result { case .ok(let n): if n != 5 { return 1; } case .err(let e): return e; }
    return stream.shutdown_write();
}
def main() async -> i32 {
    if let pair = AsyncPipe.create() {
    let task = spawn writer(pair.second);
    var text = "";
    while true {
        let result = await pair.first.read(2);
        switch result {
            case .ok(let chunk):
                if chunk.len() == 0 { break; }
                text = text + chunk;
            case .err(let e): return e;
        }
    }
    println(text);
    return await task;
    }
    return 99;
}
''', level)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'hello\n', '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_task_and_frame_ownership(tmp_path, level):
    run = build_run(tmp_path, '''
import std.task
extern "C" def rt_scheduler_run() -> Void;
extern "C" def rt_task_live_count() -> i64;
extern "C" def rt_obj_live_count() -> i64;
def work(s: String) async -> String { await sleep(0); return s; }
def cancelled(s: String) async -> Void { await sleep(60000); }
def exercise() -> Void {
    let task = spawn work("owned");
    task.wait_blocking();
    let drop = spawn cancelled("drop");
}
def main() -> i32 {
    unsafe {
        let before = rt_obj_live_count();
        var i = 0;
        while i < 100 {
            exercise();
            rt_scheduler_run();
            i = i + 1;
        }
        if rt_task_live_count() != 0 { return 1; }
        if rt_obj_live_count() != before { return 2; }
    }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('source, diagnostic', [
    ('def main() -> i32 { let t = spawn 1; return 0; }', 'requires an async function call'),
    ('import std.task\ndef f() async -> i32 { return 1; } '
     'def g(x: i32) async -> i32 { return x; } '
     'def main() -> i32 { let t = spawn g(await f()); return 0; }',
     "'await' can only be used inside an async function"),
    ('import std.task\ndef f() -> i32 { return 1; } def main() -> i32 { let t = spawn f(); return 0; }', 'requires an async function call'),
    ('def f() async -> i32 { return 1; } def main() -> i32 { let t = spawn f(); return 0; }', 'Import std.task'),
])
def test_spawn_diagnostics(tmp_path, source, diagnostic):
    path = tmp_path/'main.rl'
    path.write_text(source)
    result = compile_source(path, CompileOptions(output_path=tmp_path/'program'))
    assert not result.success
    assert any(diagnostic in d.message for d in result.diagnostics.diagnostics)


@pytest.mark.parametrize('level', list(OptLevel))
def test_backpressure_and_read_errors(tmp_path, level):
    run = build_run(tmp_path, '''
import std.async_io
import std.result
import std.task
def writer(stream: AsyncStream) async -> i32 {
    let data = "x".repeat(1048576);
    let result = await stream.write(data);
    stream.shutdown_write();
    switch result { case .ok(let n): return n; case .err(let e): return -e; }
}
def main() async -> i32 {
    if let pair = AsyncPipe.create() {
        let bad = await pair.first.read(-1);
        if !is_err(bad) { return 1; }
        let empty = await pair.first.read(0);
        switch empty { case .ok(let s): if s.len() != 0 { return 2; } case .err(let e): return 3; }
        let task = spawn writer(pair.second);
        await sleep(5);
        var count: i64 = 0;
        while true {
            let r = await pair.first.read(4096);
            switch r {
                case .ok(let s):
                    if s.len() == 0 { break; }
                    if s.char_at(0) != 120 { return 4; }
                    count = count + s.len();
                case .err(let e): return 5;
            }
        }
        if (await task) != 1048576 { return 6; }
        if count != 1048576 { return 7; }
        return 0;
    }
    return 8;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_cancel_parked_io_releases_descriptors_and_frames(tmp_path, level):
    run = build_run(tmp_path, '''
import std.async_io
import std.result
import std.task
extern "C" def rt_task_live_count() -> i64;
extern "C" def rt_obj_live_count() -> i64;
extern "C" def rt_scheduler_run() -> Void;
def waiting(stream: AsyncStream) async -> Void {
    let data = await stream.read(10);
}
def exercise() async -> Void {
    if let pair = AsyncPipe.create() {
        let reader = spawn waiting(pair.first);
        await sleep(1);
        reader.cancel();
        await reader.wait();
    }
}
def cycle() -> Void {
    let t = spawn exercise();
    t.wait_blocking();
}
def main() -> i32 {
    unsafe {
        let baseline = rt_obj_live_count();
        var i = 0;
        while i < 50 { cycle(); i = i + 1; }
        rt_scheduler_run();
        if rt_task_live_count() != 0 { return 1; }
        if rt_obj_live_count() != baseline { return 2; }
    }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_many_tasks_and_generic_scalar_results(tmp_path, level):
    run = build_run(tmp_path, '''
import std.task
import std.vec
def identity<T>(value: T) async -> T { await yield_now(); return value; }
def nothing() async -> Void { await sleep(-1); }
struct Worker {
    def answer(n: i32) async -> i32 { return n; }
}
def main() async -> i32 {
    let tasks = Vec<Task<i32>>.new();
    var i = 0;
    while i < 600 { tasks.push(spawn identity(i)); i = i + 1; }
    i = 0;
    while i < 600 {
        let task = tasks.get(i);
        if (await task) != i { return 1; }
        i = i + 1;
    }
    let wide = spawn identity(9223372036854775807);
    if (await wide) != 9223372036854775807 { return 2; }
    let float = spawn identity(1.25);
    if (await float) != 1.25 { return 3; }
    let flag = spawn identity(true);
    if !(await flag) { return 4; }
    let done = spawn nothing();
    await done;
    await done;
    let worker = Worker {};
    let method = spawn worker.answer(42);
    if (await method) != 42 { return 5; }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_cancelled_await_reports_panic(tmp_path, level):
    run = build_run(tmp_path, '''
import std.task
def work() async -> i32 { return 1; }
def main() async -> i32 {
    let task = spawn work();
    task.cancel();
    return await task;
}
''', level)
    assert run.returncode != 0
    assert 'await of cancelled task' in run.stderr


@pytest.mark.parametrize('level', list(OptLevel))
def test_completed_task_result_cycles_are_collected(tmp_path, level):
    run = build_run(tmp_path, '''
import std.task
extern "C" def rt_gc_collect() -> Void;
extern "C" def rt_obj_live_count() -> i64;
extern "C" def rt_task_live_count() -> i64;
struct Box { var task: Task<Box>?; }
struct Churn { var n: i32; def __release__() -> Void {} }
def make() async -> Box { return Box { task: nil }; }
def exercise() async -> Void {
    let task = spawn make();
    let box = await task;
    box.task = task;
}
def cycle() -> Void {
    let driver = spawn exercise();
    driver.wait_blocking();
}
def main() -> i32 {
    unsafe {
        let baseline = rt_obj_live_count();
        cycle();
        // The collector runs after its allocation threshold, including on
        // explicit requests. Keep these allocations observable via a hook.
        var i = 0;
        while i < 12000 { let churn = Churn { n: i }; i = i + 1; }
        rt_gc_collect();
        if rt_task_live_count() != 0 { return 1; }
        if rt_obj_live_count() != baseline { return 2; }
    }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')
