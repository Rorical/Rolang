# Async I/O and task control — 26 September 2026

The previously verified compiler fixes were committed as `3ee0d2a`
(`fix(compiler): validate callbacks and expose optimized outputs`). This document
covers the subsequent async implementation.

## Implemented

- `spawn async_call()` produces a managed `Task<T>` from synchronous or async
  code. Arguments are evaluated immediately and retained by the task frame.
- `await task` supports scalar, heap, generic, and Void results, including
  repeated awaits and aliased handles. Private child results transfer ownership;
  shared Task results remain owned by the handle and are copied/retained.
- `Task.cancel`, `done`, `cancelled`, async `wait`, and synchronous
  `wait_blocking` provide task control. Dropping the final managed handle cancels
  unfinished work. Exiting `main` cancels remaining scheduled work.
- `sleep` and `yield_now` use monotonic deadlines.
- `AsyncStream.read` and `write` suspend on socket readiness; writes handle
  partial progress and backpressure. `AsyncPipe.create` provides a connected
  full-duplex socket pair. FFI can transfer an existing stream socket through
  unsafe `AsyncStream.adopt`.
- Socket operations report positive POSIX errno values through `Result`.
  Descriptors remain owned until all pending operations release the stream.
- VS Code syntax highlighting recognizes `spawn`.

See [the language chapter](../book/ch16-async-await.md) and
[the executable example](../examples/async_io_test.rl) for usage.

## Scheduler and ownership

Generated await points register a dependency and return to the scheduler. A
waiting parent becomes runnable only when that dependency completes. This
removes nested scheduler joins from generated suspension points and avoids
repeatedly resuming blocked parents. Ready tasks rotate in FIFO order; the
scheduler polls I/O between turns and blocks in `poll` when only pending I/O or
timers remain. Self-await and dependency cycles produce diagnostics.

Native handles have separate caller and scheduler references. Active frames have
an additional ARC root for the scheduler. Cancellation cleans up owned child
awaits; borrowed source tasks remain independently owned. Frame retirement
releases both frame references. Task GC hooks trace frame/result references so
completed results can participate in collected cycles.

The work also fixes Void Task awaits attempting to spill a Void local into a
frame, and limits the synchronous spawn exemption to its outer call so that
nested argument expressions cannot bypass async-context checking.

## Validation

The full regression run passed **806 tests, no skips**, in 366.58 seconds.
After the final spawn-context check and MIR display adjustment, **12 focused
checks passed**, including the additional invalid-context regression. All eight
AddressSanitizer runs and all six output-stage checks described below passed.
The compiler's C runtime also passed `cc -Wall -Wextra -fsyntax-only` without
warnings; `git diff --check` is clean.

`tests/test_async_tasks.py` covers O0–O3 execution of:

- aliased handles, repeated heap-result awaits, generic/scalar/Void results,
  statically resolved async methods, and 600 concurrent tasks;
- cancellation before execution, cancellation while waiting for a child or
  socket read, status inspection, and cancelled-result diagnostics;
- timer waits, short reads, EOF, invalid/empty reads, and a one-megabyte transfer
  that exercises backpressure;
- repeated create/cancel/drop cycles, live task/object accounting, and GC of
  cycles through completed task results;
- invalid spawn operands and async expressions inside synchronous spawn arguments.

Eight additional native programs (four cases at O0 and O3) were independently
linked against an AddressSanitizer-instrumented runtime and executed successfully:
backpressure, parked-read cancellation, 600-task fanout, and cyclic task results.
These checks use `detect_leaks=0` on macOS; live task/object accounting in the
regression tests provides the separate ownership checks.

The example was emitted as optimized MIR, verified optimized LLVM, and assembly
at O0 and O3. MIR contains dependency registration and borrowed result extraction.
Both assembly products were independently assembled, linked, and executed with
exit status 0 and output `hello`.

Validation logs and emitted inspection artifacts for this session are under
`/private/tmp/rolang-async/`. Run the regression suite with:

```sh
UV_CACHE_DIR=/private/tmp/rolang-uv-cache .venv/bin/pytest -q -n 4
```

## Explicit limits

- Single-threaded cooperative execution; CPU loops must yield.
- POSIX socket/timer backend, tested on Apple Silicon macOS. Linux paths use
  POSIX poll and `MSG_NOSIGNAL` but have not been executed in this session.
- Cancellation drops suspended continuations without running pending `defer`
  blocks. Managed resources still receive ARC destruction. In-flight writes may
  already have sent a prefix; cancellation cannot undo it.
- Existing file and console APIs still block. No file worker pool, DNS,
  connect/listen/accept convenience APIs, or TLS is included.
- `poll` scales by scanning pending operations; no epoll/kqueue backend yet.
- Async closures and dynamic async protocol dispatch remain unsupported by
  `spawn`; use a statically resolved wrapper.
