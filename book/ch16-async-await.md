# Chapter 16: Async/Await

Rolang has built-in support for asynchronous programming through the `async` and `await` keywords. Async functions compile to cooperative state machines driven by a single-threaded task scheduler in the runtime.

## Why Async?

Async is useful for structuring concurrent work — multiple tasks that can make progress independently. Instead of blocking while waiting for one task to complete, the scheduler can run another.

Rolang's async model is *cooperative* and *single-threaded*: there are no OS threads, no preemption, and no parallel execution. Tasks voluntarily yield at `await` points, letting the scheduler run other tasks.

## Declaring an Async Function

Add the `async` keyword before the return type:

```rolang
def load_data() async -> String {
    return "result";
}
```

## Calling an Async Function

Async functions must be called with `await` from within another async function:

```rolang
def process() async -> i32 {
    let data = await load_data();
    return data.len() as i32;
}
```

Calling an async function from a non-async context is a compile-time error. The `async` annotation propagates up the call stack.

## The Async Entry Point

The `main` function may be declared `async`. This starts the scheduler and drives all tasks to completion before the program exits:

```rolang
def main() async -> i32 {
    let result = await process();
    return result;
}
```

## Chaining Awaits

Multiple `await` expressions execute sequentially within a function. The scheduler may run other tasks between them:

```rolang
def fetch_a() async -> i32 { return 1; }
def fetch_b() async -> i32 { return 2; }
def fetch_c() async -> i32 { return 3; }

def combine() async -> i32 {
    let a = await fetch_a();
    let b = await fetch_b();
    let c = await fetch_c();
    return a + b + c;   // 6
}
```

## How It Works

Each `async` function is compiled into three pieces:

1. An **entry function** (keeps the original name) that allocates a state-machine frame on the heap and registers it with the task scheduler.
2. A **resume function** (`FuncName_resume`) that the scheduler calls repeatedly to advance the state machine.
3. A **frame struct** (`FuncName_Frame`) that holds `state`, all locals that must survive across `await` points, and a task handle.

When `await` is reached, the resume function saves its state to the frame, pushes itself back onto the scheduler's task queue, and returns. The next time it is called, it picks up exactly where it left off.

## Task Scheduling

The runtime maintains a FIFO task queue:

- `rt_task_spawn`: push a new task onto the queue
- `rt_task_yield`: push the current task and run the next one
- `rt_task_join(handle)`: spin the queue until a specific task completes

The entry function runs `rt_task_join` on its own handle, so `await func()` in non-main code blocks until that specific task completes before continuing.

## Cooperative Yielding

Because scheduling is cooperative, a task that never reaches an `await` will never yield. An infinite loop without `await` inside an async function will starve all other tasks:

```rolang
// Bad — this never yields:
def runaway() async -> Void {
    while true { }
}

// Good — yield on every iteration (if needed):
def polling() async -> Void {
    while true {
        let status = await check_status();
        if status { break; }
    }
}
```

## Async with Result

Async functions combine naturally with `Result` for error propagation:

```rolang
import "result.rl"

def fetch_user(id: i32) async -> Result<String, String> {
    if id <= 0 {
        return Result.err(error: "invalid id");
    }
    return Result.ok(value: "user_" + id.to_string());
}

def load_profile(id: i32) async -> Result<String, String> {
    let user = try (await fetch_user(id));
    return Result.ok(value: "profile of " + user);
}
```

## Current Limitations

- **Single-threaded only.** No OS thread parallelism.
- **No I/O event loop.** There is no integration with `epoll`, `kqueue`, or timers. `await` only interleaves tasks already on the queue.
- **No `spawn` keyword yet.** Every `await` immediately joins its task. True concurrent execution (fire and forget) is not yet available at the source level.
- **No cancellation.** Tasks cannot be cancelled once started.

## A Complete Example

```rolang
import "io.rl"

def square(n: i64) async -> i64 {
    return n * n;
}

def sum_of_squares(a: i64, b: i64) async -> i64 {
    let sq_a = await square(a);
    let sq_b = await square(b);
    return sq_a + sq_b;
}

def main() async -> i32 {
    let result = await sum_of_squares(3, 4);   // 9 + 16 = 25
    println_i64(result);
    return 0;
}
```

## Summary

- Declare async functions with `async` before the return type: `def f() async -> T`
- Call async functions with `await`: `let x = await f()`
- `async` is contagious — calling an async function from a non-async context is a compile error
- `main` may be `async`; it starts the scheduler and drives all tasks to completion
- The runtime is single-threaded and cooperative — tasks yield only at `await` points
- Combine with `Result` for async operations that can fail
