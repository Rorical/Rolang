// Async Test - exercises the async state-machine lowering and runtime
// task scheduler. The trick is that `main` must itself be `async` to
// kick the scheduler into life; awaiting `compute()` from a sync `main`
// would never drive the queue.

def compute() async -> i64 {
    return 42;
}

def double(x: i64) async -> i64 {
    return x * 2;
}

def main() async -> i32 {
    let a = await compute();      // 42
    let b = await double(a);      // 84
    return (a + b) as i32;        // 126
}
