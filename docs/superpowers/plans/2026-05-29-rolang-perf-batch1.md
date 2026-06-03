# Rolang Performance Batch 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land seven low-risk, individually-measured codegen + runtime performance wins, closing the gap to native C on the `fib`/`mandelbrot`/`json` benchmarks without breaking the test suite.

**Architecture:** Compiler is Python (lark parser → resolver → checker → HIR → monomorphize → MIR → ARC insertion → LLVM IR via llvmlite → object file → link with the C runtime `rolang_rt.c`). Changes touch (a) LLVM module setup, (b) the retain/release codepath emitted as inline IR + a C slow path, (c) C build flags, (d) the cycle GC. Every change is gated by the existing pytest suite plus benchmark deltas.

**Tech Stack:** Python 3.11, llvmlite, pytest (+ pytest-xdist), C11 runtime, `uv` toolchain.

**Spec:** `docs/superpowers/specs/2026-05-29-rolang-perf-batch1-design.md`

---

## Conventions used by every task

- **Run the full test suite:** `uv run pytest -q` (must be green before *and* after each task; use `uv run pytest -q -n auto` to parallelize).
- **Run a single test:** `uv run pytest tests/test_codegen.py::test_name -v`
- **Benchmark command** (added in Task 0): `uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt <0|2|3> --out benchmarks/results/<file>.md`
- **Line numbers** in this plan reflect the repo at plan-writing time and may drift by a few lines; match on the quoted code, not the number.
- **Commit after every task** with the message shown in its final step.

---

## File Structure

**Created:**
- `benchmarks/results/baseline-2026-05-29.md` — captured baseline numbers (Task 0).
- `benchmarks/results/batch1.md` — running ledger of post-item deltas (appended each task).
- `src/rolang/acyclic.py` — pure acyclicity analysis over the type graph (Task 6).
- `tests/test_acyclic.py` — unit tests for the analysis (Task 6).

**Modified:**
- `benchmarks/runner.py` — add `--langs`, `--rolang-opt`, `--out` (Task 0).
- `src/rolang/codegen/object_file.py` — set `data_layout`, attach analysis passes, stop swallowing opt errors (Task 1).
- `src/rolang/runtime/rolang_rt.c` — `rt_obj_release_slow` split (Task 2), non-atomic RC under flag (Task 4), descriptor `acyclic` field + GC candidate skip (Task 6), adaptive GC threshold (Task 7), pool bins (Task 8).
- `src/rolang/codegen/runtime.py` — inline-IR retain/release (Task 3).
- `src/rolang/driver.py` — runtime build flags + `-DROLANG_SINGLE_THREADED` (Tasks 4, 5).
- `src/rolang/codegen/__init__.py` — descriptor `acyclic` field emission (Task 6).
- `src/rolang/codegen/types.py` — adapter feeding the acyclic analysis (Task 6).

---

## Task 0: Baseline tooling + capture

Make the benchmark runner selectable (languages, Rolang opt level) and able to write markdown, then capture the O0/O2/O3 Rolang+C baseline. Also confirm the O2/O3 LLVM optimizer is actually running today.

**Files:**
- Modify: `benchmarks/runner.py`
- Test: `tests/test_benchmark_runner.py` (create)
- Create: `benchmarks/results/baseline-2026-05-29.md`

- [ ] **Step 1: Write the failing test for the pure helpers**

Create `tests/test_benchmark_runner.py`:

```python
"""Tests for the benchmark runner's language/opt selection helpers."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "bench_runner", Path(__file__).resolve().parent.parent / "benchmarks" / "runner.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def test_select_langs_filters_by_name():
    selected = runner.select_langs(["C", "Rolang"])
    names = [l.name for l in selected]
    assert names == ["C", "Rolang"]


def test_select_langs_none_returns_all():
    assert len(runner.select_langs(None)) == len(runner.LANGS)


def test_select_langs_is_case_insensitive():
    assert [l.name for l in runner.select_langs(["rolang"])] == ["Rolang"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_benchmark_runner.py -v`
Expected: FAIL with `AttributeError: module 'bench_runner' has no attribute 'select_langs'`

- [ ] **Step 3: Implement `select_langs` + opt parameterization + markdown output in `runner.py`**

Add this helper near the `LANGS` list:

```python
def select_langs(names):
    """Return the subset of LANGS whose names match `names` (case-insensitive).
    `names=None` returns all languages, preserving LANGS order."""
    if not names:
        return list(LANGS)
    wanted = [n.strip().lower() for n in names]
    by_name = {l.name.lower(): l for l in LANGS}
    return [by_name[n] for n in wanted if n in by_name]
```

Parameterize `compile_rolang` to take an opt level (default keeps current behavior):

```python
def compile_rolang(src: Path, out: Path, opt: int = 3) -> bool:
    if not which("rolangc"):
        return False
    r = run_cmd(["rolangc", f"-O{opt}", "-o", str(out), str(src)], 60)
    return r.returncode == 0
```

Add a markdown writer:

```python
def write_markdown(path, all_results, rolang_opt):
    lines = [f"## Benchmark results (Rolang -O{rolang_opt})", ""]
    for bench_name, results in all_results.items():
        lines.append(f"### {bench_name}")
        lines.append("")
        lines.append("| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |")
        lines.append("|------|---------:|----------:|------------:|------------:|-------|")
        for r in sorted([x for x in results if x.success], key=lambda r: r.min_time):
            lines.append(
                f"| {r.name} | {r.min_time*1000:.2f} | {r.mean_time*1000:.2f} | "
                f"{r.median_time*1000:.2f} | {r.stddev_time*1000:.2f} | {r.mem_model} |"
            )
        lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")
```

Wire argparse into `main()` (replace the current `main()` signature/body top):

```python
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", help="comma-separated language names, e.g. C,Rolang")
    ap.add_argument("--rolang-opt", type=int, default=3, help="Rolang opt level (0-3)")
    ap.add_argument("--out", help="append results as markdown to this path")
    args = ap.parse_args()

    global LANGS
    LANGS = select_langs(args.langs.split(",")) if args.langs else LANGS
    runner_opt = args.rolang_opt
    # Bind the chosen opt level into compile_rolang via a wrapper on the Rolang entry.
    for l in LANGS:
        if l.name == "Rolang":
            l.compile = lambda src, out, _opt=runner_opt: compile_rolang(src, out, _opt)

    print("Rolang Benchmark Suite")
    print(f"  warmup={WARMUP_RUNS}, timed={TIMED_RUNS}, rolang_opt=O{runner_opt}")
    available = [l.name for l in LANGS if l.available()]
    print(f"  detected: {', '.join(available)}")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    all_results = {}
    for bench_name, verify in [("fib", EXPECTED_FIB), ("mandelbrot", None), ("json_parse", None)]:
        results = run_benchmark(bench_name, verify)
        all_results[bench_name] = results
        print_results(results, bench_name)

    if args.out:
        write_markdown(args.out, all_results, runner_opt)
        print(f"\nWrote {args.out}")
```

(Leave the existing summary-plot block after this if present; it is unaffected.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_benchmark_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Ensure `rolangc` is runnable, then capture the baseline**

Run:
```bash
uv sync
uv run rolangc --help >/dev/null && echo "rolangc OK"
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 0 --out benchmarks/results/baseline-2026-05-29.md
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 2 --out benchmarks/results/baseline-2026-05-29.md
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/baseline-2026-05-29.md
```
Expected: three appended sections (O0, O2, O3) with Rolang + C rows for fib/mandelbrot/json.

- [ ] **Step 6: Confirm the O2/O3 LLVM optimizer is actually running today**

Run:
```bash
uv run rolangc -O0 -o /tmp/fib_o0 --emit=llvm-ir benchmarks/fib/fib.rl 2>/dev/null || uv run rolangc -O0 --emit-llvm -o /tmp/fib_o0.ll benchmarks/fib/fib.rl
uv run rolangc -O3 --emit-llvm -o /tmp/fib_o3.ll benchmarks/fib/fib.rl
diff <(wc -l </tmp/fib_o0.ll) <(wc -l </tmp/fib_o3.ll); echo "compare the two .ll files"
```
(Use whatever emit flag `rolangc --help` documents for LLVM IR.) Record in `baseline-2026-05-29.md` a one-line note: **"O3 IR differs from O0: yes/no"**. If they are identical, the `except: pass` in `object_file.py` is swallowing a real failure — Task 1 becomes critical.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/runner.py tests/test_benchmark_runner.py benchmarks/results/baseline-2026-05-29.md
git commit -m "perf(bench): selectable runner (langs/opt/markdown) + captured baseline"
```

---

## Task 1: Module `data_layout` + analysis passes + surface optimizer errors

**Files:**
- Modify: `src/rolang/codegen/object_file.py` (the `compile_module_to_object` optimizer block, ~lines 100-118)
- Test: `tests/test_codegen.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_codegen.py`:

```python
def test_object_compilation_sets_data_layout():
    """compile_module_to_object must stamp the module with the target data layout
    so LLVM optimizes against real alignment/pointer-size info, not a default."""
    import llvmlite.ir as ir
    from rolang.codegen.object_file import compile_module_to_object, initialize_llvm
    initialize_llvm()
    module = ir.Module(name="dl_probe")
    fnty = ir.FunctionType(ir.IntType(32), [])
    fn = ir.Function(module, fnty, name="main")
    blk = fn.append_basic_block("entry")
    ir.IRBuilder(blk).ret(ir.Constant(ir.IntType(32), 0))

    import tempfile, os
    out = os.path.join(tempfile.mkdtemp(), "out.o")
    errors = compile_module_to_object(module, out, opt_level=2)
    assert errors == [], errors
    assert str(module.data_layout) != "", "data_layout was not set on the module"
```

(If `initialize_llvm` has a different name, use the init function already called at the top of `object_file.py`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_codegen.py::test_object_compilation_sets_data_layout -v`
Expected: FAIL — `data_layout was not set on the module` (it is currently never set).

- [ ] **Step 3: Implement — set data layout, attach analysis passes, surface errors**

In `object_file.py`, right after `target_machine` is created and before the optimizer block, add:

```python
        # Stamp the module with the target's data layout + triple so LLVM
        # optimizes against real alignment/pointer/struct-packing info.
        llvm_module.triple = triple
        llvm_module.data_layout = str(target_machine.target_data)
```

Replace the optimizer block:

```python
        if opt_level > 0:
            try:
                pmb = llvm.create_pass_manager_builder()
                pmb.opt_level = opt_level
                pm = llvm.create_module_pass_manager()
                pmb.populate(pm)
                pm.run(llvm_module)
            except Exception as e:
                # Optimization failure is not fatal
                pass
```

with:

```python
        if opt_level > 0:
            try:
                pmb = llvm.create_pass_manager_builder()
                pmb.opt_level = opt_level
                pm = llvm.create_module_pass_manager()
                # Give passes target-specific analysis (TTI etc.) so cost models
                # and target-aware transforms actually fire.
                target_machine.add_analysis_passes(pm)
                pmb.populate(pm)
                pm.run(llvm_module)
            except Exception as e:
                # A broken optimizer must be loud — never silently ship -O0 code
                # under an -O2/-O3 flag.
                errors.append(f"LLVM optimization failed at opt_level={opt_level}: {e}")
                return errors
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_codegen.py::test_object_compilation_sets_data_layout -v`
Expected: PASS

- [ ] **Step 5: Run the full suite + benchmarks**

Run:
```bash
uv run pytest -q
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 2 --out benchmarks/results/batch1.md
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/batch1.md
```
Expected: suite green; record the O2/O3 deltas vs. baseline. If Task 0 Step 6 showed identical O0/O3 IR, expect a large jump here.

- [ ] **Step 6: Commit**

```bash
git add src/rolang/codegen/object_file.py tests/test_codegen.py benchmarks/results/batch1.md
git commit -m "perf(codegen): set module data_layout + target analysis passes; surface opt errors"
```

---

## Task 2: Split `rt_obj_release` cold path into `rt_obj_release_slow` (C only)

This is a pure refactor of the C runtime — no behavior change — so the next task can inline only the hot path and call this for `rc==0` teardown. Keeping one teardown implementation avoids drift.

**Files:**
- Modify: `src/rolang/runtime/rolang_rt.c` (`rt_obj_release`, ~lines 530-590; the `prev == 1` branch)
- Test: `tests/test_runtime_execution.py` (existing tests already exercise release/teardown; we rely on them)

- [ ] **Step 1: Add a static assertion that `rc` is at offset 0 (the inline IR will assume this)**

Near the `ObjHeader` typedef (~line 270 in `rolang_rt.c`), add:

```c
_Static_assert(offsetof(ObjHeader, rc) == 0,
    "inline retain/release IR assumes rc is the first header field (offset 0)");
```

Ensure `#include <stddef.h>` is present (for `offsetof`); add it near the top includes if missing.

- [ ] **Step 2: Extract the teardown into `rt_obj_release_slow`**

Add a forward declaration near the existing `void rt_obj_release(void* ptr);` (~line 461):

```c
/* Cold path: rc has just reached 0. Runs deinit, handles resurrection,
 * releases pointer fields, unlinks from the GC list, and frees. */
void rt_obj_release_slow(void* ptr);
```

Replace the body of `rt_obj_release` so that, after the decrement, it tail-calls the slow path:

```c
void rt_obj_release(void* ptr) {
    if (ptr == NULL) {
        return;
    }
    ObjHeader* h = OBJ_HEADER(ptr);
    int64_t prev;
#if defined(__GNUC__) || defined(__clang__)
    prev = __atomic_fetch_sub(&h->rc, 1, __ATOMIC_ACQ_REL);
#else
    prev = (h->rc)--;
#endif
    if (prev == 1) {
        rt_obj_release_slow(ptr);
    }
}
```

Define `rt_obj_release_slow` immediately after, containing **exactly** the code that previously ran inside the old `if (prev == 1) { ... }` block (the PIN/deinit/resurrection-check/field-release/unlink/free sequence) — but with **no decrement** (the caller already decremented). It begins:

```c
void rt_obj_release_slow(void* ptr) {
    ObjHeader* h = OBJ_HEADER(ptr);
    /* rc just reached 0. Order: deinit (object still valid) → resurrection
     * check → release fields → unlink from GC list → free. */
    TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
    if (desc != NULL && desc->deinit_fn != NULL) {
        const int64_t PIN = (int64_t)0x4000000000000000LL;
        __atomic_store_n(&h->rc, PIN, __ATOMIC_RELAXED);
        desc->deinit_fn(ptr);
        int64_t cur = __atomic_load_n(&h->rc, __ATOMIC_RELAXED);
        int64_t extra = cur - PIN;
        if (extra > 0) {
            __atomic_store_n(&h->rc, extra, __ATOMIC_RELAXED);
            return;
        }
        __atomic_store_n(&h->rc, 0, __ATOMIC_RELAXED);
    }
    /* ... remainder of the original teardown: obj_release_fields(h);
       gc_list_remove(h); pool-or-free deallocation ... (move verbatim) ... */
}
```

Move the remaining teardown statements (everything after the resurrection block in the original function) into `rt_obj_release_slow` verbatim.

- [ ] **Step 3: Build the runtime + run the suite**

Run: `uv run pytest -q`
Expected: PASS (the existing ARC/runtime-execution tests cover deinit, resurrection, cycles, and ordinary frees — all must stay green, confirming the refactor is behavior-preserving).

- [ ] **Step 4: Commit**

```bash
git add src/rolang/runtime/rolang_rt.c
git commit -m "refactor(runtime): split rt_obj_release cold path into rt_obj_release_slow"
```

---

## Task 3: Inline retain/release as LLVM IR (fast path)

Emit `rt_obj_retain`/`rt_obj_release` as internal `alwaysinline` IR in the generated module (same technique as `rt_string_char_at`), so they are no longer real `call`s into C. The release fast path calls `rt_obj_release_slow` only on `rc==0`. The IR uses plain (non-atomic) loads/stores — valid because the runtime is single-threaded (formalized in Task 4).

**Files:**
- Modify: `src/rolang/codegen/runtime.py` (`_declare_obj_functions`, ~line 193)
- Test: `tests/test_codegen.py` (append) + reliance on `tests/test_arc.py`, `tests/test_runtime_execution.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_codegen.py`:

```python
def test_retain_release_emitted_as_inline_ir():
    """retain/release must be internal alwaysinline definitions, and release
    must call the C slow path rather than doing teardown itself."""
    import llvmlite.ir as ir
    from rolang.codegen.runtime import RuntimeABI
    module = ir.Module(name="rc_probe")
    RuntimeABI(module)
    text = str(module)
    assert "define internal void @\"rt_obj_retain\"" in text or \
           "define internal void @rt_obj_retain" in text, text[:2000]
    assert "rt_obj_release_slow" in text, "release fast path must call the C slow path"
    # both inline functions must carry alwaysinline
    assert text.count("alwaysinline") >= 6  # char_at + 4 classify + retain + release
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_codegen.py::test_retain_release_emitted_as_inline_ir -v`
Expected: FAIL — retain/release are currently plain external declarations; `rt_obj_release_slow` is absent from the module.

- [ ] **Step 3: Implement inline retain/release in `runtime.py`**

In `_declare_obj_functions`, **replace** these two external declarations:

```python
        # void rt_obj_retain(void* ptr)
        obj_retain_type = ir.FunctionType(self.void, [self.ptr])
        self.rt_obj_retain = ir.Function(self.module, obj_retain_type, name="rt_obj_retain")

        # void rt_obj_release(void* ptr)
        obj_release_type = ir.FunctionType(self.void, [self.ptr])
        self.rt_obj_release = ir.Function(self.module, obj_release_type, name="rt_obj_release")
```

with calls to two new builders:

```python
        # Inlinable retain/release (replaces the extern C calls). The C
        # functions of the same name still exist for the runtime's own
        # internal use; `internal` linkage keeps these module-private.
        self._declare_inline_obj_retain()
        self._declare_inline_obj_release()
```

Add the two methods to the class (model on `_declare_inline_char_at`):

```python
    def _declare_inline_obj_retain(self) -> None:
        """rc is the first header field (offset 0). retain = null-check + rc++."""
        fnty = ir.FunctionType(self.void, [self.ptr])
        func = ir.Function(self.module, fnty, name="rt_obj_retain")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")
        self.rt_obj_retain = func

        entry = func.append_basic_block(name="entry")
        b = ir.IRBuilder(entry)
        ptr = func.args[0]
        is_null = b.icmp_signed("==", b.ptrtoint(ptr, self.i64), ir.Constant(self.i64, 0))
        do = func.append_basic_block(name="do")
        done = func.append_basic_block(name="done")
        b.cbranch(is_null, done, do)

        b.position_at_end(do)
        rc_ptr = b.bitcast(ptr, ir.PointerType(self.i64), name="rc_ptr")
        rc = b.load(rc_ptr, name="rc")
        b.store(b.add(rc, ir.Constant(self.i64, 1), name="rc_inc"), rc_ptr)
        b.branch(done)

        b.position_at_end(done)
        b.ret_void()

    def _declare_inline_obj_release(self) -> None:
        """release fast path: null-check + rc--; on prev==1 call rt_obj_release_slow."""
        # extern C slow path
        slow_ty = ir.FunctionType(self.void, [self.ptr])
        self.rt_obj_release_slow = ir.Function(
            self.module, slow_ty, name="rt_obj_release_slow"
        )

        fnty = ir.FunctionType(self.void, [self.ptr])
        func = ir.Function(self.module, fnty, name="rt_obj_release")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")
        self.rt_obj_release = func

        entry = func.append_basic_block(name="entry")
        b = ir.IRBuilder(entry)
        ptr = func.args[0]
        is_null = b.icmp_signed("==", b.ptrtoint(ptr, self.i64), ir.Constant(self.i64, 0))
        do = func.append_basic_block(name="do")
        done = func.append_basic_block(name="done")
        b.cbranch(is_null, done, do)

        b.position_at_end(do)
        rc_ptr = b.bitcast(ptr, ir.PointerType(self.i64), name="rc_ptr")
        rc = b.load(rc_ptr, name="rc")
        b.store(b.sub(rc, ir.Constant(self.i64, 1), name="rc_dec"), rc_ptr)
        was_one = b.icmp_signed("==", rc, ir.Constant(self.i64, 1), name="was_one")
        slow = func.append_basic_block(name="slow")
        b.cbranch(was_one, slow, done)

        b.position_at_end(slow)
        b.call(self.rt_obj_release_slow, [ptr])
        b.branch(done)

        b.position_at_end(done)
        b.ret_void()
```

`emit_obj_retain`/`emit_obj_release` (lines 257-267) are unchanged — they still call `self.rt_obj_retain`/`self.rt_obj_release`, which now point at the inline functions.

- [ ] **Step 4: Run the targeted test, then the ARC + runtime suites**

Run:
```bash
uv run pytest tests/test_codegen.py::test_retain_release_emitted_as_inline_ir -v
uv run pytest tests/test_arc.py tests/test_runtime_execution.py -v
uv run pytest -q
```
Expected: all PASS. The ARC/runtime tests (cycles, deinit, resurrection, ordinary frees) are the real safety net for this change — if any fail, the header offset or fast/slow split is wrong; do not proceed.

- [ ] **Step 5: Benchmark**

Run:
```bash
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/batch1.md
```
Expected: `fib`/`json` (retain/release-heavy) improve; record the delta.

- [ ] **Step 6: Commit**

```bash
git add src/rolang/codegen/runtime.py tests/test_codegen.py benchmarks/results/batch1.md
git commit -m "perf(codegen): emit rt_obj_retain/release as inline LLVM IR with C slow path"
```

---

## Task 4: Non-atomic RC fast path under `-DROLANG_SINGLE_THREADED`

Make the C retain/release (used by the runtime internally) match the inline IR's non-atomic behavior, gated behind a default-on flag so it is a one-line revert if a threaded runtime ever lands.

**Files:**
- Modify: `src/rolang/runtime/rolang_rt.c` (`rt_obj_retain` ~line 466, `rt_obj_release` ~line 530, near the `rc` field comment ~line 264)
- Modify: `src/rolang/driver.py` (`_compile_runtime`, ~line 882)
- Test: rely on `tests/test_arc.py`, `tests/test_runtime_execution.py`

- [ ] **Step 1: Gate the C retain/release decrement/increment on the flag**

In `rt_obj_retain`, replace:

```c
#if defined(__GNUC__) || defined(__clang__)
    __atomic_fetch_add(refcount, 1, __ATOMIC_RELAXED);
#else
    (*refcount)++;
#endif
```

with:

```c
#if defined(ROLANG_SINGLE_THREADED)
    (*refcount)++;   /* single-threaded runtime: atomics are pure overhead */
#elif defined(__GNUC__) || defined(__clang__)
    __atomic_fetch_add(refcount, 1, __ATOMIC_RELAXED);
#else
    (*refcount)++;
#endif
```

In `rt_obj_release`, replace the decrement:

```c
#if defined(__GNUC__) || defined(__clang__)
    prev = __atomic_fetch_sub(&h->rc, 1, __ATOMIC_ACQ_REL);
#else
    prev = (h->rc)--;
#endif
```

with:

```c
#if defined(ROLANG_SINGLE_THREADED)
    prev = (h->rc)--;
#elif defined(__GNUC__) || defined(__clang__)
    prev = __atomic_fetch_sub(&h->rc, 1, __ATOMIC_ACQ_REL);
#else
    prev = (h->rc)--;
#endif
```

Add a comment by the `rc` field in `ObjHeader` (~line 264):

```c
    int64_t           rc;   /* refcount. Non-atomic under ROLANG_SINGLE_THREADED
                             * (default). Undefine that flag to restore atomics
                             * if/when a multi-threaded runtime is added. */
```

- [ ] **Step 2: Pass the flag from the driver**

In `_compile_runtime` (`driver.py` ~line 882), after `f"-O{self.options.opt_level.value}",` is appended, add:

```python
        compile_cmd.append("-DROLANG_SINGLE_THREADED")
```

- [ ] **Step 3: Run the safety suites**

Run:
```bash
uv run pytest tests/test_arc.py tests/test_runtime_execution.py -q
uv run pytest -q
```
Expected: PASS. (These exercise refcount correctness end-to-end; green here means the non-atomic counter is sound for the single-threaded runtime.)

- [ ] **Step 4: Benchmark**

Run: `uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/batch1.md`
Expected: small additional gain on RC-heavy benchmarks; record delta.

- [ ] **Step 5: Commit**

```bash
git add src/rolang/runtime/rolang_rt.c src/rolang/driver.py benchmarks/results/batch1.md
git commit -m "perf(runtime): non-atomic refcount fast path under ROLANG_SINGLE_THREADED"
```

---

## Task 5: Portable C build flags for the runtime

**Files:**
- Modify: `src/rolang/driver.py` (`_compile_runtime` ~line 882)

- [ ] **Step 1: Build the runtime at -O3 + add portable flags**

In `_compile_runtime`, change the optimization flag and add portable flags. Replace:

```python
        compile_cmd = [
            cc,
            "-c",
            str(runtime_path),
            "-o", str(runtime_obj),
            f"-O{self.options.opt_level.value}",
        ]
        if self.options.opt_level.value >= 2:
            compile_cmd.append("-flto")
```

with:

```python
        # The runtime is small, hot, and shared by every program — there is no
        # debugging reason to ship it unoptimized once the program is past O0.
        rt_opt = 3 if self.options.opt_level.value >= 1 else 0
        compile_cmd = [
            cc,
            "-c",
            str(runtime_path),
            "-o", str(runtime_obj),
            f"-O{rt_opt}",
            "-DROLANG_SINGLE_THREADED",
        ]
        if rt_opt >= 1:
            # Portable, distribution-safe (no -march=native).
            compile_cmd += ["-fno-semantic-interposition", "-fvisibility=hidden"]
        if self.options.opt_level.value >= 2:
            compile_cmd.append("-flto")
```

(This supersedes the `-DROLANG_SINGLE_THREADED` line added in Task 4 Step 2 — remove that now-duplicate append so the flag appears once.)

- [ ] **Step 2: Run the suite + benchmarks**

Run:
```bash
uv run pytest -q
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 2 --out benchmarks/results/batch1.md
```
Expected: green; record delta.

- [ ] **Step 3: Commit**

```bash
git add src/rolang/driver.py benchmarks/results/batch1.md
git commit -m "perf(driver): build runtime at -O3 with portable flags (no -march=native)"
```

---

## Task 6: Acyclic-type GC skip

Compute, per type, whether an instance can ever be part of a reference cycle. Acyclic-typed objects are excluded from the cycle-GC candidate set (they are still fully refcounted and freed immediately by `rt_obj_release`). A wrongly-acyclic type can only *leak a cycle* — never corrupt memory — and cycle tests guard against that.

**Files:**
- Create: `src/rolang/acyclic.py`, `tests/test_acyclic.py`
- Modify: `src/rolang/codegen/types.py` (adapter), `src/rolang/codegen/__init__.py` (descriptor emission ~line 839/869), `src/rolang/runtime/rolang_rt.c` (struct ~line 313, candidate loop ~line 880)

### 6a — Pure analysis + unit tests

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_acyclic.py`:

```python
from rolang.acyclic import cyclic_capable_ids


def test_self_loop_is_cyclic():
    # 0 -> 0
    assert cyclic_capable_ids(num_ids=1, edges={0: [0]}, conservative=set()) == {0}


def test_two_cycle_is_cyclic():
    # 0 -> 1 -> 0
    assert cyclic_capable_ids(num_ids=2, edges={0: [1], 1: [0]}, conservative=set()) == {0, 1}


def test_acyclic_chain():
    # 0 -> 1 -> 2 (no back edge): none can reach themselves
    assert cyclic_capable_ids(num_ids=3, edges={0: [1], 1: [2]}, conservative=set()) == set()


def test_conservative_node_is_cyclic_and_taints_reachers():
    # 1 is conservative (points-to-unknown). 0 -> 1 means 0 can reach back to itself
    # through the unknown node, so both are cyclic-capable.
    result = cyclic_capable_ids(num_ids=2, edges={0: [1]}, conservative={1})
    assert result == {0, 1}


def test_node_pointing_into_cycle_but_not_reachable_is_acyclic():
    # 0 -> 1 -> 2 -> 1 : the 1<->2 cycle is cyclic, but 0 cannot reach 0.
    result = cyclic_capable_ids(num_ids=3, edges={0: [1], 1: [2], 2: [1]}, conservative=set())
    assert result == {1, 2}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_acyclic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rolang.acyclic'`

- [ ] **Step 3: Implement `src/rolang/acyclic.py`**

```python
"""Type-level cycle analysis for the GC.

A heap type T is *cyclic-capable* iff an instance of T can be part of a
reference cycle — equivalently, iff T can reach itself in the may-point-to
graph over descriptor ids. Types that point to "unknown" targets
(existentials, closures, containers whose element walk the GC can't see
statically) are `conservative`: modeled as pointing to a synthetic TOP node
that points to everything, so any type reaching a conservative node becomes
cyclic-capable. Everything not cyclic-capable is acyclic and safe to skip in
the cycle collector.
"""
from typing import Dict, List, Set


def cyclic_capable_ids(num_ids: int, edges: Dict[int, List[int]],
                       conservative: Set[int]) -> Set[int]:
    TOP = num_ids  # synthetic node id
    adj: Dict[int, List[int]] = {i: list(edges.get(i, [])) for i in range(num_ids)}
    for c in conservative:
        adj.setdefault(c, []).append(TOP)
    # TOP may point to anything (including itself).
    adj[TOP] = list(range(num_ids)) + [TOP]

    # A node is cyclic-capable iff it can reach itself via >=1 edge.
    def reaches_self(start: int) -> bool:
        stack = list(adj.get(start, []))
        seen: Set[int] = set()
        while stack:
            n = stack.pop()
            if n == start:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj.get(n, []))
        return False

    return {i for i in range(num_ids) if reaches_self(i)}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_acyclic.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rolang/acyclic.py tests/test_acyclic.py
git commit -m "feat(gc): pure type-level cyclic-capability analysis"
```

### 6b — Adapter from the type graph

- [ ] **Step 6: Add `acyclic_descriptor_ids` to `TypeLayoutCache` (types.py)**

Add a method that builds `edges`/`conservative` from the descriptor graph and returns the set of **acyclic** descriptor ids. Use the already-computed field-descriptor map and existing `TypeKind`/`get_type` APIs:

```python
    def acyclic_descriptor_ids(self, type_table, type_to_trace) -> set:
        """Return descriptor ids whose type can never be part of a cycle.

        `field_desc_map` (from compute_field_descriptors) gives heap-field edges
        desc_id -> field_type_descriptor_id. Existentials, closures, and any
        type with a registered trace_fn (containers) are conservative: their
        element/capture set isn't statically walkable here, so they may point
        anywhere.
        """
        from rolang.acyclic import cyclic_capable_ids
        from rolang.types import TypeKind

        field_desc_map = self.compute_field_descriptors()
        num = self.get_descriptor_count()
        desc_to_type = {did: tid for tid, did in self._descriptor_ids.items()}

        edges = {}
        conservative = set()
        for did in range(num):
            edges[did] = [fd[1] for fd in field_desc_map.get(did, [])]  # field_type_descriptor_id
            tid = desc_to_type.get(did)
            if tid is None:
                conservative.add(did)
                continue
            info = type_table.get_type(tid)
            if info is not None and info.kind in (TypeKind.EXISTENTIAL, TypeKind.CLOSURE):
                conservative.add(did)
            if tid in type_to_trace:  # containers (Vec/Dict/user __gc_trace__)
                conservative.add(did)

        cyclic = cyclic_capable_ids(num_ids=num, edges=edges, conservative=conservative)
        return set(range(num)) - cyclic
```

(`field_desc_map[did]` entries are `(offset, field_type_descriptor_id, case_tag)`; index `[1]` is the target descriptor id. Confirm this tuple shape against `compute_field_descriptors` and adjust the index if it differs.)

- [ ] **Step 7: No standalone test here** — 6b is covered by the integration test in 6e. Proceed.

### 6c — Add the `acyclic` field to the descriptor (both sides, in sync)

- [ ] **Step 8: Append `acyclic` to the C struct**

In `rolang_rt.c` (~line 313), change:

```c
typedef struct {
    uint64_t        type_id;
    int64_t         payload_size;
    int32_t         field_count;
    int32_t         fields_start;
    DeinitFn        deinit_fn;
    GCTraceFn       trace_fn;
} TypeDescriptor;
```

to add a trailing field (append-only, so existing offsets don't shift):

```c
typedef struct {
    uint64_t        type_id;
    int64_t         payload_size;
    int32_t         field_count;
    int32_t         fields_start;
    DeinitFn        deinit_fn;
    GCTraceFn       trace_fn;
    int32_t         acyclic;       /* 1 = instances can never be in a cycle */
} TypeDescriptor;
```

- [ ] **Step 9: Append `acyclic` to the LLVM descriptor in `codegen/__init__.py`**

At ~line 839 change:

```python
    desc_type = ir.LiteralStructType([i64, i64, i32, i32, ptr_t, ptr_t])
```

to:

```python
    desc_type = ir.LiteralStructType([i64, i64, i32, i32, ptr_t, ptr_t, i32])
```

Compute the acyclic set once before the loop (after `type_to_trace` is built, ~line 800):

```python
    acyclic_ids = type_cache.acyclic_descriptor_ids(type_table, type_to_trace)
```

And append the field to each entry (~line 869), changing the `ir.Constant(desc_type, [...])` list to include the flag as the final element:

```python
        descriptors.append(ir.Constant(desc_type, [
            ir.Constant(i64, desc_id),
            ir.Constant(i64, payload_size),
            ir.Constant(i32, field_count),
            ir.Constant(i32, field_desc_offset),
            deinit_const,
            trace_const,
            ir.Constant(i32, 1 if desc_id in acyclic_ids else 0),
        ]))
```

Update the comment at line 836-837 to list the new `i32 acyclic` field.

### 6d — Skip acyclic objects in GC candidate building

- [ ] **Step 10: Guard the candidate loop in `rt_gc_collect`**

In `rolang_rt.c`, in the candidate-building `while` loop (~lines 879-891), change:

```c
    while (obj != NULL && gc_candidate_count < gc_candidates_capacity) {
        if (obj->rc > 0) {
```

to:

```c
    while (obj != NULL && gc_candidate_count < gc_candidates_capacity) {
        TypeDescriptor* od = rt_get_type_descriptor(obj->type_id);
        if (obj->rc > 0 && !(od != NULL && od->acyclic)) {
```

(Acyclic objects can never be in a cycle, so excluding them from candidates is correct: references *from* them to candidates are then treated as external — keeping those candidates alive — and the acyclic objects themselves are freed by refcounting, never by the collector.)

### 6e — Integration tests + verify

- [ ] **Step 11: Write failing integration tests**

Append to `tests/test_arc.py` (follow that file's existing compile-and-run helper; the pseudocode below shows intent — adapt to the file's actual harness):

```python
def test_recursive_cycle_still_collected(run_program):
    """A self-referential type is cyclic-capable: its cycles must still be
    collected by the GC (acyclic optimization must NOT touch it)."""
    src = """
    struct Node { var next: Node?; var tag: i32; }
    def main() -> i32 {
        var a = Node { next: none, tag: 1 };
        var b = Node { next: none, tag: 2 };
        a.next = some(b); b.next = some(a);   // cycle
        return 0;
    }
    """
    # Run with a low GC threshold / forced collect; assert no leak via the
    # runtime's alloc/live accounting that test_arc already uses.
    assert run_program(src).exit_code == 0

def test_flat_record_marked_acyclic(emit_descriptors):
    """A struct with only non-recursive fields must be flagged acyclic."""
    src = "struct Point { var x: i32; var y: i32; }\n" \
          "def main() -> i32 { var p = Point{ x: 1, y: 2 }; return p.x; }"
    descs = emit_descriptors(src)  # helper that returns parsed RT_TYPE_DESCRIPTORS
    assert descs.for_type("Point").acyclic == 1
```

If `test_arc.py` has no descriptor-inspection helper, assert acyclicity at the unit level instead by constructing the type graph and calling `cyclic_capable_ids` (already covered in 6a) and limit the integration test to the cycle-still-collected case, which is the safety-critical one.

- [ ] **Step 12: Run to verify failure, implement (6b/6c/6d already done), verify pass**

Run: `uv run pytest tests/test_arc.py -v`
Expected: the cycle-still-collected test PASSES (proves the optimization didn't break cycle collection); the acyclic-flag test PASSES once 6c/6b are wired.

- [ ] **Step 13: Full suite + benchmark**

Run:
```bash
uv run pytest -q
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/batch1.md
```
Expected: green; `json` (many short-lived acyclic records) should benefit; record delta.

- [ ] **Step 14: Commit**

```bash
git add src/rolang/codegen/types.py src/rolang/codegen/__init__.py src/rolang/runtime/rolang_rt.c tests/test_arc.py benchmarks/results/batch1.md
git commit -m "perf(gc): skip acyclic types in the cycle collector candidate set"
```

---

## Task 7: Adaptive GC threshold

Scale the gap between collections with the live-set size instead of a hardcoded 10,000.

**Files:**
- Modify: `src/rolang/runtime/rolang_rt.c` (threshold check ~line 828; the `gc_last_collect_count` update ~line 1153)

- [ ] **Step 1: Introduce an adaptive gap variable**

Near `static int64_t gc_alloc_counter = 0;` (~line 348), add:

```c
#define GC_MIN_GAP   10000
#define GC_MAX_GAP   2000000
#define GC_GROWTH    2
static int64_t gc_next_gap = GC_MIN_GAP;
```

Change the early-return check (~line 828) from:

```c
    if (gc_alloc_counter - gc_last_collect_count < 10000) {
        return;
    }
```

to:

```c
    if (gc_alloc_counter - gc_last_collect_count < gc_next_gap) {
        return;
    }
```

- [ ] **Step 2: Recompute the gap after each collection from the surviving live set**

The collection already knows `live_count` (objects pre-collection) and frees some. After collection completes and before releasing the lock (near the `gc_last_collect_count = gc_alloc_counter;` update ~line 1153), add:

```c
    /* Adapt: next gap grows with the surviving live set so allocation-heavy
     * programs don't pay frequent full scans, with a floor and a cap. */
    int64_t survivors = (int64_t)gc_candidate_count;  /* candidates not collected this pass */
    int64_t gap = survivors * GC_GROWTH;
    if (gap < GC_MIN_GAP) gap = GC_MIN_GAP;
    if (gap > GC_MAX_GAP) gap = GC_MAX_GAP;
    gc_next_gap = gap;
```

(If `gc_candidate_count` is reset before this point, capture the survivor count into a local right after Step-2-of-collection's accounting instead; the requirement is "approximate live objects that remain after this collection.")

- [ ] **Step 3: Full suite + a churn stress check**

Run:
```bash
uv run pytest -q
uv run pytest tests/test_arc.py tests/test_runtime_execution.py -v
```
Expected: green. The existing cycle/leak tests confirm collections still happen and free cycles under the adaptive schedule.

- [ ] **Step 4: Benchmark**

Run: `uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/batch1.md`
Expected: allocation-heavy benchmarks improve or hold; record delta.

- [ ] **Step 5: Commit**

```bash
git add src/rolang/runtime/rolang_rt.c benchmarks/results/batch1.md
git commit -m "perf(gc): adaptive collection threshold scaled by live-set size"
```

---

## Task 8: Pool-bin tuning (data-driven)

Fit the pool size classes to the benchmarks' real allocation-size distribution.

**Files:**
- Modify: `src/rolang/runtime/rolang_rt.c` (the size-class table + `pool_obj_alloc` bin selection, ~lines 60-100)

- [ ] **Step 1: Add temporary instrumentation**

In `pool_obj_alloc` (and the `rt_alloc` fallback for oversized objects), add a guarded histogram, e.g.:

```c
#ifdef ROLANG_POOL_PROFILE
    /* total = header + payload; record into coarse buckets and atexit-print */
    extern void rt_pool_profile_record(size_t total);
    rt_pool_profile_record(total);
#endif
```

Implement `rt_pool_profile_record` to bump a static array of counters keyed by `total` rounded to 16-byte buckets and register an `atexit` printer (guarded by the same macro). Keep it entirely behind `ROLANG_POOL_PROFILE` so normal builds are unaffected.

- [ ] **Step 2: Capture the histogram**

Run (compile the runtime with the profile flag via the env override the driver respects, or a one-off):
```bash
CC="cc -DROLANG_POOL_PROFILE" uv run python benchmarks/runner.py --langs Rolang --rolang-opt 3
```
Record the printed size histogram for fib/mandelbrot/json.

- [ ] **Step 3: Adjust the size classes to match**

Edit the size-class array (currently `48,64,96,128,192,256`) so the bins land on the most common observed sizes (keep 6 bins unless the data clearly argues for a different count; document the chosen sizes in a comment citing the histogram).

- [ ] **Step 4: Remove instrumentation, run suite + benchmark**

Remove the `ROLANG_POOL_PROFILE` blocks (or leave them, fully macro-guarded and off by default — prefer leaving them for future tuning, clearly commented).

Run:
```bash
uv run pytest -q
uv run python benchmarks/runner.py --langs C,Rolang --rolang-opt 3 --out benchmarks/results/batch1.md
```
Expected: green; record delta.

- [ ] **Step 5: Commit**

```bash
git add src/rolang/runtime/rolang_rt.c benchmarks/results/batch1.md
git commit -m "perf(runtime): tune object-pool size classes to benchmark allocation profile"
```

---

## Final: Batch 1 summary

- [ ] **Step 1: Write the summary**

Append to `benchmarks/results/batch1.md` a table of baseline → post-batch min times per (benchmark, opt-level), Rolang vs. C, and a one-line verdict per item (moved the needle / negligible / regressed-and-reverted). This informs whether the deferred heavy passes (escape analysis, cross-block ARC elimination, FBIP reuse) are worth their cost.

- [ ] **Step 2: Commit + finish the branch**

```bash
git add benchmarks/results/batch1.md
git commit -m "docs(bench): Batch 1 performance summary"
```

Then use **superpowers:finishing-a-development-branch** to decide merge/PR for `perf/batch1`.

---

## Self-review notes (addressed)

- **Spec coverage:** Step 0 = baseline + the "is the optimizer even running" check; Tasks 1–8 map 1:1 to spec items #1–#7 (item #2 spans Tasks 2+3; item #3 = Task 4; build-flags item = Task 5). Heavy passes explicitly deferred (Final section).
- **Type/name consistency:** `rt_obj_release_slow` defined in Task 2, declared in the module in Task 3, called from the inline IR in Task 3 — consistent. `acyclic` field appended to both the C struct (Task 6 Step 8) and the LLVM `desc_type` (Step 9) and read in the GC loop (Step 10) and emitted from `acyclic_descriptor_ids` (Step 6) using `cyclic_capable_ids` (Step 3) — consistent signatures throughout.
- **Known verify-points flagged for the implementer:** the `field_desc_map` tuple index for the target descriptor id (Step 6), the exact `rolangc` LLVM-IR emit flag (Step 6 of Task 0), and the `test_arc.py` harness shape (Task 6 Step 11) — each notes how to confirm against the real code.
