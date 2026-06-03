# Rolang Performance — Batch 1 (low-risk, high-ROI wins)

**Date:** 2026-05-29
**Status:** Design — awaiting review
**Scope:** Batch 1 of a staged performance effort. Measure between every item.

## Goal

Close the gap to native (C) on the existing `fib` / `mandelbrot` / `json` benchmarks
through a set of low-risk, individually-measurable codegen and runtime changes.
No change may regress a benchmark or break the test suite.

## Non-goals (explicitly deferred — each gets its own spec later)

- **Escape analysis → stack allocation** of non-escaping aggregates.
- **Cross-block ARC elimination** (ownership/borrow dataflow over the CFG).
- **FBIP-style in-place reuse** when rc == 1 at an update site.

These are memory-safety-critical and far larger than anything in Batch 1. They are
out of scope here and must not be started until Batch 1 is measured and merged.

## Measurement discipline (applies to every item)

1. Capture the **baseline** (Step 0) before any change.
2. After each item: run the full existing test suite (must stay green) **and** the
   three benchmarks at the relevant opt levels; record the delta vs. baseline in
   `benchmarks/results/batch1.md`.
3. Do not start item N+1 until item N is green + measured. If an item shows no win
   (or a regression), stop and reassess before continuing.

## Step 0 — Baseline (prerequisite, no code change)

- Build `fib`, `mandelbrot`, `json` at **O0, O2, O3**.
- Run via the existing `benchmarks/runner.py`, **Rolang + C only** (C is the native
  ceiling reference; skip Rust/Go/Java/Node/Python even if installed).
- Record min/mean/median/stddev per (benchmark, opt-level) to
  `benchmarks/results/baseline-2026-05-29.md` and commit it.
- **Also verify** whether O2/O3 actually run LLVM passes today: the optimizer block
  in `object_file.py` is wrapped in `except Exception: pass`, so a throwing pass
  would silently produce an unoptimized "optimized" build. Capture O0-vs-O3 `.ll` or
  disassembly to confirm passes are firing. This finding gates item #1's expected impact.

## Items (in order)

### 1. Module `data_layout` + modern pass setup + surface opt errors

**Why:** `codegen/__init__.py:86-88` sets `module.triple` but never sets
`module.data_layout`. Without it LLVM optimizes against a default/empty data layout
and stays conservative about alignment, struct packing, and pointer size, weakening
SROA, load/store forwarding, and vectorization. Separately, `object_file.py:104-114`
swallows all optimizer exceptions, so optimization may be silently absent.

**Approach:**
- In `object_file.py`, after creating the `target_machine`, set
  `llvm_module.data_layout = str(target_machine.target_data)` (and ensure
  `llvm_module.triple` is set to the target triple) **before** running passes.
- Attach target analysis passes to the pass manager
  (`target_machine.add_analysis_passes(pm)`) so passes have target info.
- Remove the blanket `except: pass` around the optimization run — let failures
  surface as a diagnostic (or at minimum log them). A broken optimizer must be loud.
- Keep using the existing `PassManagerBuilder` populate path; migrating to the
  new-PM pipeline string is optional polish, only if it measures better.

**Files:** `src/rolang/codegen/object_file.py`, `src/rolang/codegen/__init__.py`.
**Risk:** Low. **Verify:** O0-vs-O3 disassembly shows optimization; benchmarks; test suite.

### 2. Inline `rt_obj_retain` / `rt_obj_release` as LLVM IR (fast path)

**Why:** The generated object file is machine code, not LTO bitcode, so `-flto`
cannot inline the C `rt_obj_*` functions into generated code. Every retain/release
is therefore a real `call` into C — on the hottest operation in the language. Commit
`3f2aa8d` already established the technique (emitting `char_at` etc. as inline LLVM
IR with `alwaysinline`); apply it to retain/release.

**Approach (fast/slow split):**
- **retain** (`rolang_rt.c:466`) is null-check + increment. Emit the whole thing as
  inline IR: `if (ptr) { rc = load; store rc + 1; }`.
- **release** (`rolang_rt.c:530`) is null-check + `prev = rc--` + `if (prev == 1)`
  cold teardown (deinit, field release, GC unlink, free). Emit inline IR for the hot
  path only: null-check, decrement, compare; on `prev == 1` `call rt_obj_release_slow(ptr)`.
- Introduce `rt_obj_release_slow(void* ptr)` in `rolang_rt.c` = the current body of
  `rt_obj_release` from the `prev == 1` branch onward (no second decrement). The
  existing `rt_obj_release` remains as the exported, callable C entry point (used by
  the runtime itself, e.g. `obj_release_fields`, and as a fallback) and now simply
  does the decrement + tail-call to `rt_obj_release_slow`.
- The inlined IR must compute the header pointer the same way `OBJ_HEADER` does
  (payload − 32) and read/write the `rc` field at the same offset.

**Files:** `src/rolang/codegen/runtime.py` (or wherever `char_at` IR is emitted),
the retain/release emission sites, `src/rolang/runtime/rolang_rt.c`.
**Risk:** Medium — touches the codegen↔runtime ABI. **TDD-gated.**
**Verify:** Dedicated cycle/leak tests (objects with cycles still collected; no leaks
under a churn stress test); full test suite; benchmarks. The header-offset constant
(32) must be asserted to match `OBJ_HEADER_SIZE` so the two can't silently drift.

### 3. Non-atomic RC fast path under `-DROLANG_SINGLE_THREADED`

**Why:** The runtime is single-threaded (`rolang_rt.c:1685` comment; multi-threading
is described as a "future" runtime). retain/release use `__atomic_fetch_add` /
`__atomic_fetch_sub` (lines 474, 539) which compile to `lock`-prefixed RMWs on x86 —
a full barrier on the hottest operation, for zero benefit today.

**Approach:**
- Define `ROLANG_SINGLE_THREADED` (default on) in the runtime build.
- Under that flag, retain/release (both the C functions and the inline IR from #2)
  use plain non-atomic load/add/store on `rc`.
- Keep the GC's `gc_list_lock` and the lock-free pool CAS untouched for now — those
  are about a future multi-threaded runtime and are not on the per-op hot path in the
  same way; revisit only if they measure.
- One-line revert path: undefining the flag restores atomics. Document this in the
  runtime header near the rc field.

**Files:** `src/rolang/runtime/rolang_rt.c`, `src/rolang/driver.py` (add the `-D` to
the runtime compile command), the inline-IR emission from #2.
**Risk:** Low (single-threaded confirmed). **Verify:** full test suite; benchmarks.

### 4. Portable C build flags for the runtime

**Why:** `_compile_runtime` / `_link_object_files` (`driver.py:~885`, `~923`) pass
only `-O{level}` and `-flto` at O2+. Safe, portable flags are left on the table.

**Approach:**
- Add to the runtime compile: `-fno-semantic-interposition`, `-fvisibility=hidden`.
- Build the **runtime at `-O3` regardless of program opt-level** (it is small, hot,
  and shared by every program; there is no debugging reason to ship it unoptimized
  once the program is past O0). Keep the program's own opt-level as chosen.
- **No `-march=native` by default** — it breaks distributable binaries. Optionally
  expose `--target-cpu <cpu>` (and pass through to both the LLVM target machine and
  the C compiler) for users who want machine-specific builds; default unset.

**Files:** `src/rolang/driver.py` (and `CompileOptions` if adding `--target-cpu`).
**Risk:** Low. **Verify:** binaries still run on the test suite; benchmarks.

### 5. Acyclic-type GC skip

**Why:** The cycle GC scans all live objects on the registry every collection. Types
that can never participate in a reference cycle never need cycle detection and waste
scan time (and registry-link overhead) on every collection.

**Approach:**
- In the compiler, compute per-type "can this type ever be part of a cycle?" via
  reachability over field types, **including through enum cases, containers
  (`Vec`/`Dict`/etc.), and closure captures**. A type is *acyclic* only if no path
  from it can reach itself; be conservative — when in doubt, mark cyclic.
- Add an `acyclic` flag to the emitted `TypeDescriptor`.
- In the runtime, objects of acyclic types are **not registered on the GC list**
  (or are skipped when building the candidate set). They are still fully reference
  counted and freed immediately by `rt_obj_release` — only the cycle collector ignores
  them.
**Risk:** Medium but **never unsafe** — a wrongly-acyclic type leaks cycles (caught by
tests), it cannot corrupt memory.
**Files:** new analysis in the compiler (likely near `layout.py` / type-descriptor
emission), `codegen` descriptor emission, `src/rolang/runtime/rolang_rt.c`.
**Verify:** existing cycle tests still collect cycles; a test that an acyclic type is
never placed on the GC registry; benchmarks (json builds many short-lived acyclic
records — expected to benefit).

### 6. Adaptive GC threshold

**Why:** `rolang_rt.c:828` hardcodes a 10,000-allocation gap between collections with
a stop-the-world full-heap scan. Allocation-heavy programs pay frequent full scans
regardless of live-set size.

**Approach:** After each collection, set the next threshold proportional to the
surviving live-set size, e.g. `next_gap = max(MIN_GAP, live_count * GROWTH)` with a
sane floor (the current 10k as `MIN_GAP`) and a cap. This mirrors generational-style
heuristics without changing the algorithm.
**Files:** `src/rolang/runtime/rolang_rt.c`.
**Risk:** Low-medium (changes GC timing). **Verify:** leak/cycle tests; a churn stress
test confirming memory does not grow unbounded; benchmarks.

### 7. Pool-bin tuning (data-driven)

**Why:** The pool's six size classes (48/64/96/128/192/256) were chosen a priori. The
real allocation-size distribution of the benchmarks may not match.

**Approach:** Add temporary instrumentation to record the histogram of `rt_alloc` /
pool request sizes while running the three benchmarks (from Step 0 data if captured,
else a one-off instrumented run). Adjust the size classes to fit the real
distribution; remove the instrumentation before merge.
**Files:** `src/rolang/runtime/rolang_rt.c`.
**Risk:** Low. **Verify:** benchmarks; test suite.

## Ordering rationale

#1 is the cheapest and may unlock the most (if the optimizer is silently off). #2+#3
are one logical change to the retain/release codepath and the biggest structural win
in Batch 1. #4 is trivial. #5/#6/#7 are runtime-side and independent of the codegen
items, sequenced last because their wins are workload-dependent and best judged once
the codegen items have shifted the baseline.

## Definition of done for Batch 1

- All seven items merged, each with a recorded benchmark delta and a green test suite.
- `benchmarks/results/batch1.md` summarizes baseline → post-batch numbers per
  (benchmark, opt-level), Rolang vs. C.
- A short note on which items moved the needle and which didn't, to inform whether the
  deferred heavy passes are worth their cost.
