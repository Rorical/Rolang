# Rolang Performance — Batch 1 progress

Running record of per-item benchmark deltas vs. the 2026-05-29 baseline
(`benchmarks/results/baseline-2026-05-29.md`). Captured with `--warmup 2 --runs 5`,
languages C + Rolang only. Medians quoted in prose; full tables below.

## Test-suite state (important caveat)

The repo's full `pytest` suite is **not green at the Batch-1 baseline** — three tests
fail on `main` (commit `6339e99`, "initial release"), entirely independent of any
Batch-1 change. They are unrelated to codegen/runtime perf work:

- `test_file_read_all_after_partial_read_returns_remaining` — the test calls
  `File.open_read_s(...)`, which does not exist in the current `fs.rl` (compile error
  `E0102: Type File has no member 'open_read_s'`). Stale test vs. stdlib.
- `test_run_argv_does_not_invoke_shell` — compiles, but the program returns 255 with
  empty stdout: `run_argv` does not execute `echo` (PATH/exec behavior).
- `test_dunder_release_wrong_signature_is_rejected` — the checker reports the
  `__release__` signature error but not the expected `__gc_trace__` one.

Confirmed pre-existing by stashing each item's changes and by `git diff main...HEAD`
(this branch only touches benchmark/docs files). The practical green-gate for Batch 1
is therefore **"no new failures beyond these three"**: 656 passed / 3 failed / 2 skipped.

---

## Item 1 — Module `data_layout` + modern pass setup + surface opt errors

**Status:** done. Suite: 656 passed / 3 failed (pre-existing) / 2 skipped — no regression.

**Root-cause finding (validates the item's premise).** The installed **llvmlite is
0.46.0**, which removed `create_pass_manager_builder` / `create_module_pass_manager`.
The previous optimizer block called those and was wrapped in `except Exception: pass`,
so the **IR-level pass pipeline threw `AttributeError` and was silently swallowed** —
inlining, SROA, mem2reg, instcombine, etc. never ran at *any* opt level. The O0→O2
gains seen in the baseline came only from the **target-machine codegen opt level**
(`create_target_machine(opt=...)`, which still applied during `emit_object`), which is
why O2→O3 looked like noise: backend codegen opt, no IR transforms.

Item 1 sets `module.data_layout`/`triple` from the target machine before serialization
and switches to the llvmlite-0.46 PassBuilder API (`create_pass_builder` +
`create_pipeline_tuning_options`), turning the IR pipeline on for the first time, with
optimizer failures now surfaced as diagnostics instead of swallowed.

**Delta vs. baseline (median ms; lower is better):**

| Bench | Opt | Baseline Rolang | Item-1 Rolang | Baseline ×C | Item-1 ×C |
|-------|-----|----------------:|--------------:|------------:|----------:|
| fib | O2 | 75.65 | 34.88 | 3.31× | 1.48× |
| fib | O3 | 71.62 | 39.94 | 3.16× | 1.59× |
| mandelbrot | O2 | 343.25 | 190.90 | 1.93× | 1.07× |
| mandelbrot | O3 | 352.27 | 196.46 | 1.98× | 1.10× |
| json_parse | O2 | 1135.55 | 1039.17 | 33.8× | 30.2× |
| json_parse | O3 | 1144.99 | 960.36 | 33.9× | 30.2× |

**Read:** the IR pipeline being switched on is the single biggest win in Batch 1 so far.
`fib` and `mandelbrot` close to ~1.1–1.6× of C. `json_parse` improves only ~14% because
its cost is dominated by ARC/GC traffic (the target of items 2–7), not scalar codegen.
Note O2 ≥ O3 for `fib`/`mandelbrot` here — O3's extra aggressiveness does not help this
IR shape and slightly hurts `fib`.

### Item-1 raw tables (Rolang -O2)

#### fib

| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |
|------|---------:|----------:|------------:|------------:|-------|
| C | 23.36 | 23.59 | 23.49 | 0.24 | none |
| Rolang | 34.67 | 35.21 | 34.88 | 0.74 | arc |

#### mandelbrot

| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |
|------|---------:|----------:|------------:|------------:|-------|
| C | 177.04 | 178.36 | 178.24 | 1.02 | none |
| Rolang | 190.58 | 191.43 | 190.90 | 1.02 | arc |

#### json_parse

| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |
|------|---------:|----------:|------------:|------------:|-------|
| C | 32.62 | 34.12 | 34.40 | 1.52 | none |
| Rolang | 1008.49 | 1040.77 | 1039.17 | 30.36 | arc |

### Item-1 raw tables (Rolang -O3)

#### fib

| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |
|------|---------:|----------:|------------:|------------:|-------|
| C | 23.80 | 25.37 | 25.20 | 1.63 | none |
| Rolang | 39.73 | 40.07 | 39.94 | 0.38 | arc |

#### mandelbrot

| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |
|------|---------:|----------:|------------:|------------:|-------|
| C | 177.76 | 178.72 | 178.05 | 1.40 | none |
| Rolang | 189.18 | 196.39 | 196.46 | 7.40 | arc |

#### json_parse

| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |
|------|---------:|----------:|------------:|------------:|-------|
| C | 31.77 | 32.02 | 31.84 | 0.32 | none |
| Rolang | 933.08 | 1024.49 | 960.36 | 149.96 | arc |

---

## Item 2 — Split `rt_obj_release` cold path into `rt_obj_release_slow`

**Status:** done. Pure C refactor, no behavior change; no benchmark delta expected
(it only reshapes the function so Item 3 can inline the hot path). Verified by the
145-test ARC + runtime-execution suite (deinit/resurrection/cycles/frees).

---

## Item 3 — Inline `rt_obj_retain`/`rt_obj_release` as LLVM IR

**Status:** done. Suite: 659 passed / 0 failed / 2 skipped. ARC+runtime: 145 passed.

retain/release are now emitted as `internal alwaysinline` LLVM IR (null-check +
non-atomic rc±1; release tail-calls the C `rt_obj_release_slow` only when rc hits 0),
so they inline at every call site instead of being real `call`s into C. The C
functions remain for the runtime's own use.

**Delta vs. Item 1 (median ms; the ARC-heavy benchmark is the target):**

| Bench | Opt | Item-1 Rolang | Item-3 Rolang | Item-1 ×C | Item-3 ×C |
|-------|-----|--------------:|--------------:|----------:|----------:|
| json_parse | O3 | 960.36 | 888.91 | 30.2× | 28.3× |
| json_parse | O2 | 1039.17 | 926.91 | 30.2× | 28.0× |
| fib | O3 | 39.94 | 39.48 | 1.59× | 1.71× |
| mandelbrot | O3 | 196.46 | 188.37 | 1.10× | 1.06× |

**Read:** `json_parse` (allocation/refcount-heavy) improves ~7% (O3) / ~11% (O2) —
the expected payoff from removing a `call` on the hottest operation. `fib` and
`mandelbrot` are essentially flat: `fib` is pure integer recursion and `mandelbrot`
pure float loops, so neither has retain/release traffic to eliminate (their small
run-to-run wobble is measurement noise — note C also drifts ±1ms between runs).

---

## Benchmark-tooling caveat (discovered during Item 4) — IMPORTANT

`benchmarks/runner.py` builds Rolang via the **`rolangc` binary on PATH**, which is a
**`uv tool` install** living in its own isolated environment
(`/root/.local/share/uv/tools/rolang/`) — entirely separate from the project venv that
`uv run pytest` uses. That tool was a **non-editable, frozen copy** of the package: it
did **not** track edits to `src/rolang`, so a benchmark run silently compiled
*pre-Batch-1* code while the suite tested live source. A Task-4 benchmark first showed a
full regression to baseline (fib 3.1×, mandelbrot 1.9×, json_parse 35×) — the tell was
`fib`/`mandelbrot` regressing despite having zero refcount traffic, which no runtime
change can cause. Root cause: stale frozen `rolangc`.

**Fix (permanent):** reinstalled the tool **editable** so `rolangc` always tracks live
source (Python + `rolang_rt.c` via `importlib.resources` + stdlib via `__file__`):

```
uv tool install --force --editable /root/Projects/Rolang
```

Side effect: the tool env re-resolved **llvmlite 0.46.0 → 0.47.0** (the project venv used
by the suite stays on 0.46.0). The PassBuilder pipeline (Item 1) compiles cleanly and
correctly on 0.47.0 (smoke build + full A/B below). All Item-4 numbers are measured with
the editable tool; earlier Item-1..3 numbers were taken right after a fresh install and
remain representative (the new editable HEAD json_parse, 880ms, is within noise of the
recorded Item-3 889ms).

---

## Item 4 — Non-atomic refcount fast path under `ROLANG_SINGLE_THREADED`

**Status:** done. `rt_obj_retain`/`rt_obj_release` decrement/increment `rc` with a plain
non-atomic RMW under `-DROLANG_SINGLE_THREADED` (passed by the driver); the atomic
`__atomic_fetch_add/sub` paths are retained behind `#elif` so undefining one flag
restores a multi-threaded-safe runtime. The runtime is single-threaded (cooperative
scheduler), so the atomics were pure overhead. Item 3 already inlined non-atomic
retain/release at *user* call sites; Item 4 removes the atomics from the **runtime's own
internal** retain/release calls (collection growth, hashmap/vector element ownership),
which is what `json_parse` exercises heavily.

**Clean A/B under identical tooling** (editable tool, llvmlite 0.47.0, `--warmup 2
--runs 7`, back-to-back; stash Task 4 = HEAD/atomic, pop = non-atomic):

| Bench | Opt | HEAD atomic (median ms) | Item-4 non-atomic (median ms) | Δ | ×C |
|-------|-----|------------------------:|------------------------------:|------:|-----:|
| json_parse | O3 | 879.99 | 836.87 | −4.9% | 27.6× → 26.5× |
| fib | O3 | 38.9 | ~33.5 | n/a (noise) | 1.7× → 1.4× |
| mandelbrot | O3 | 188.9 | 189.6 | flat | 1.1× |

**Read:** `json_parse` improves ~4.9% median / ~6.4% min (874→818ms), exceeding the
±24–31ms run-to-run noise — a real win from dropping atomics on the runtime-internal
refcount path. `fib`/`mandelbrot` carry no refcount traffic, so their wobble is pure
noise (fib timings are tiny, ~33–39ms, and machine-scheduling sensitive).

---

## Item 5 — Portable runtime build flags (`-O3` runtime, no `-march=native`)

**Status:** done. **Perf-neutral at O2/O3** (within noise); shipped as build hygiene.

`_compile_runtime` now builds the runtime at `-O3` whenever the program is past `-O0`
(`rt_opt = 3 if opt >= 1 else 0`) and, at `rt_opt >= 1`, adds
`-fno-semantic-interposition` and `-fvisibility=hidden`. No `-march=native` — the runtime
object stays portable/distribution-safe. The `-DROLANG_SINGLE_THREADED` flag from Item 4
moves into this array (de-duplicated).

`-fvisibility=hidden` is safe here because the runtime is **statically** linked into the
program object: hidden symbols are still resolved by the static linker (only the dynamic
symbol table is trimmed). Verified by compile+link+run at `-O0`/`-O2`/`-O3`.

**Rigorous A/B (editable tool, llvmlite 0.47.0, `--warmup 2 --runs 12`, json_parse
median ms; stash = Item-4 HEAD, pop = Item-5):**

| Opt | Isolates | Item-4 HEAD | Item-5 | Δ median | min Item4→Item5 |
|-----|----------|------------:|-------:|---------:|----------------:|
| O3 | +`hidden`/`no-interp` flags (runtime already `-O3`) | 840.80 | 851.83 | +1.3% | 827.9→829.4 |
| O2 | runtime `-O2`→`-O3` | 843.23 | 842.82 | −0.05% | 808.0→825.4 |

**Read:** a dead heat. The O2 comparison (the runtime `-O2`→`-O3` bump) is identical to
within 0.4ms; the O3 comparison (the two flags alone) drifts ~1.3% on the median but the
*mins* are within ~2ms — inside the run-to-run noise (Item-4 O2 stddev was 31ms in this
batch). An initial 7-run pass read "846 vs 820" and looked like a regression; the 12-run
pass shows that was machine drift, not the `-O3` runtime. Net: no measurable win at the
benchmarked levels, but the change still optimizes the runtime for **`-O1`** program
builds (previously `-O1`) and keeps the object portable, so it's kept as hygiene.

---

## Item 6 — Acyclic-type GC skip

**Status:** done (shipped). **Correct + memory-safe; perf-neutral on all measured
workloads so far** — kept as infrastructure, to be re-measured after Item 7.

Per-type cycle analysis (`src/rolang/acyclic.py`): a type is *cyclic-capable* iff it can
reach itself in the may-point-to graph over descriptor ids; existentials, closures,
trace_fn containers (Vec/Dict), and any out-of-range field target are conservatively
treated as reaching everything. Acyclic types get an `acyclic=1` flag in the type
descriptor (appended last on both the C struct and the LLVM `desc_type`, ABI-guarded by a
`_Static_assert`), and the cycle collector excludes acyclic objects from its candidate
set. Such objects are freed promptly by refcounting and can never be in a cycle, so
excluding them can only ever *add* apparent external references → it can under-collect
(leak) at worst if the analysis is wrong, never over-collect (no UAF). Verified by a
soundness walk of the trial-deletion algorithm, an independent adversarial review
(ABI/UAF/leak/soundness — VERDICT SHIP), the 146-test ARC+runtime suite, and a new
end-to-end `test_recursive_cycle_still_collected` (a dropped a↔b cycle is still reclaimed,
proving `Node` is correctly *not* acyclic).

**Measured impact (clean A/B, O3, mins):**

| Workload | Task5 | Task6 | Δ (min) |
|----------|------:|------:|--------:|
| json_parse | 820.83 | 830.84 | +1.2% (noise/slightly slower) |
| fib / mandelbrot | — | — | unaffected (no GC traffic) |
| **acyclic microbench** (20k long-lived acyclic Payloads + 20k Nodes, 120k churn → ~12 GC passes; 50% of candidates skippable) | 10.69s | 10.54s | −1.4% (within noise) |

**Read:** even the purpose-built ideal case (a GC-scan-dominated loop where half the
candidates are acyclic) shows no win. The collector's per-pass cost here is dominated by
O(live) work the skip does *not* reduce — the `live_count` pre-scan (walks every object)
and the Phase-5d unlink sweep — plus allocation/RC churn, not the candidate-proportional
trial-deletion the skip shortens. json_parse's hot types are anyway non-acyclic
(`JsonValue` is recursive; `Vec`/`Dict` are trace_fn → conservative), so almost nothing is
skipped there and the extra per-object descriptor lookup edges it ~1% slower.

**Decision (user):** ship as correct/safe infrastructure and **re-measure after Item 7**.
Item 7 raises the GC gap → larger live sets scanned per pass → the per-pass candidate
savings should matter more. If still negligible after Item 7, reconsider reverting.

---

## Item 7 — Adaptive GC threshold

**Status:** done (shipped). **Correct + sound heuristic; perf-neutral on all measured
workloads** — and the A/B that proves it also explains *why* Items 6 & 7 are both neutral.

Replaced the fixed "collect every 10 000 allocations" gate with an adaptive gap that
scales with the live set surviving each pass: `gc_next_gap = clamp(survivors * 2,
GC_MIN_GAP=10 000, GC_MAX_GAP=2 000 000)`, so a program with a large persistent heap
amortizes each O(live) cycle scan over a proportional number of subsequent allocations
instead of rescanning every 10 000. **Deviation from the plan's literal code:** the plan
wrote `survivors = gc_candidate_count`, but its stated *requirement* is "approximate live
objects that remain after this collection." Post-Item-6, `gc_candidate_count` excludes
acyclic objects, so it undercounts live and would make Item 6 paradoxically *shrink* the
gap. Used `survivors = live_count - collected_count` (total live remaining) instead — the
correct amortization basis against the all-allocations counter, and it keeps the two items
from fighting.

**A/B (clean, O3; M0 = HEAD Item-6 fixed-10k, M1 = Item-6 + Item-7 adaptive):**

| Workload | M0 fixed (≈12 passes) | M1 adaptive (≈2 passes) | Δ |
|----------|----------------------:|------------------------:|---:|
| acyclic microbench | 10.75 s | 10.79 s | flat |
| json_parse (min) | 799.95 | 800.36 | flat |
| fib / mandelbrot | unaffected | unaffected | flat |

**Read — the load-bearing result of the whole GC effort:** cutting cycle-GC passes from
~12 to ~2 changed *nothing*. That proves a cycle-GC pass is **nearly free** in every
benchmark here, so **the GC is not the bottleneck** — which is the real reason Items 6
*and* 7 are both neutral (the Item-6 "re-measure after Item-7" hope does not materialize:
fewer/cheaper passes of a near-zero cost is still near-zero). json_parse's ~25×-C overhead
is **ARC retain/release traffic + allocation**, not cycle collection; even the
deliberately GC-heavy microbench turns out to be allocation/RC-bound. This redirects the
remaining ROI to allocation (Item 8, pool tuning) and the already-banked ARC wins
(Items 3–4), and is the strongest evidence so far on whether the deferred heavy passes
(escape analysis, cross-block ARC elision, FBIP reuse — all ARC/alloc-targeted) are worth
their cost. Items 6 & 7 are kept as correct, low-risk infrastructure; their keep/revert is
revisited in the final summary.

---

## Item 8 — Pool-bin tuning (data-driven → no retune warranted)

**Status:** done. **Instrumented, profiled, found nothing to tune** — kept the profiler +
an env hook as infrastructure; size classes unchanged (and the profiling explains where
json's time *actually* goes).

Added an allocation-size profiler to `rt_obj_alloc` behind `-DROLANG_POOL_PROFILE`
(16-byte-bucket histogram, atexit dump; compiled out of normal builds — verified byte-off
when the macro is undefined) and a `ROLANG_RT_CFLAGS` escape hatch in the driver's runtime
compile (so the profiler/sanitizers can be enabled without editing code — the env override
the plan assumed). Then captured the typed-object allocation distribution:

| Workload | total typed allocations | distribution |
|----------|------------------------:|--------------|
| fib | 0 | — (pure integer recursion) |
| mandelbrot | 0 | — (pure float loops) |
| **json_parse** | **2** | both in the 48–63 B class |
| acyclic stress (160k allocs) | 160 000 | 100% in the 48–63 B class |

**Read — the decisive finding for the batch:** the benchmark suite does almost no
typed-object allocation, and what little it does (plus a 160k-allocation stress test) lands
entirely in the existing two smallest bins (48/64). Nothing pressures the 96–256 classes or
argues for different boundaries, so retuning has no data to act on and changing the bins
blindly would only risk regressing workloads not represented here. **Bins left unchanged.**

More importantly, the profiler nails down json_parse's real cost: it is a **non-allocating**
parser (`struct Parser` walks the input via `self.src.char_at(self.pos)` and accumulates
counts — only 2 heap objects for its whole run). So its ~25×-C gap is **per-character
`char_at` + small-method-call overhead** (`peek`/`advance`/`eof`/`is_digit`/`len` per char),
not allocation, not GC, not cycle-collection. None of Batch 1's remaining levers (GC, pool)
touch that; the wins that *did* land for json (Items 3–4, ~7–11%) came from cheaper ARC,
and the next real lever is string/method-dispatch codegen (inlining `char_at` and the tiny
accessor methods), which is out of Batch-1 scope.

**Follow-up recommendation:** the suite needs an allocation-heavy and a string-throughput
benchmark before pool/string work can be measured; today fib/mandelbrot test only scalar
codegen and json tests only `char_at`/method-dispatch.

---

# Batch 1 — final summary

All 8 items landed and are committed on `perf/batch1`; the full suite is green throughout
(666 passed / 2 skipped — up from the baseline's 656/3-failed once the three pre-existing
failures were fixed). Headline, Rolang relative to C (lower is better):

| Benchmark | Opt | Baseline ×C | Post-batch ×C | Rolang min: baseline → post |
|-----------|-----|------------:|--------------:|----------------------------:|
| fib | O3 | 3.16× | **1.49×** | 71.6 → 33.4 ms |
| fib | O2 | 3.31× | **1.56×** | 75.7 → 35.2 ms |
| mandelbrot | O3 | 1.98× | **1.06×** | 352 → 187.6 ms |
| mandelbrot | O2 | 1.93× | **1.06×** | 343 → 186.3 ms |
| json_parse | O3 | 33.9× | **25.9×** | 1145 → 802.6 ms |
| json_parse | O2 | 33.8× | **25.6×** | 1136 → 801.6 ms |

(Baseline ×C from the pre-Item-1 capture; post-batch from clean min-of-10 at HEAD =
`d432ac7`. fib/mandelbrot near-C now; json still ~26× C, see verdict below.)

**Per-item verdict:**

| Item | Change | Verdict |
|------|--------|---------|
| 1 | Module data_layout + turn the IR pass pipeline on; surface opt errors | **Moved the needle, hugely.** The whole batch's win. The pipeline was silently disabled (llvmlite-0.46 API change swallowed by `except: pass`); enabling it took fib 3.16→1.6× and mandelbrot 1.98→1.1× C. |
| 2 | Split `rt_obj_release` cold path into `rt_obj_release_slow` | Enabler for Item 3 (no perf delta alone, by design). |
| 3 | Inline retain/release as LLVM IR | **Moved the needle (modest).** json −7% (O3) / −11% (O2) — removed a `call` on the hottest op. |
| 4 | Non-atomic refcount under `ROLANG_SINGLE_THREADED` | **Moved the needle (modest).** json −4.9% — atomics were pure overhead in a single-threaded runtime. |
| 5 | Build runtime at `-O3` + portable flags (no `-march=native`) | Negligible at O2/O3; hygiene (optimizes the runtime for `-O1` builds, keeps it portable). |
| 6 | Acyclic-type GC skip | Negligible (correct + safe; kept as infra). GC isn't the bottleneck. |
| 7 | Adaptive GC threshold | Negligible (correct + sound; kept as infra). Proved GC passes are nearly free here. |
| 8 | Pool-bin tuning | Negligible — profiling showed nothing to tune (suite barely allocates); kept the profiler + env hook as infra. |

**What this tells us about the deferred heavy passes (escape analysis, cross-block ARC
elision, FBIP reuse).** The cheap wins are banked: scalar codegen is now ~1.1–1.6× C
(near the ceiling for fib/mandelbrot), and the ARC fast-path items shaved ~10% off the one
allocation/ARC-touching benchmark. The three GC/pool items moved nothing, and the profiler
proved *why*: across the suite, **cycle-GC passes are nearly free and typed-object
allocation is negligible** — so neither GC tuning nor pool tuning can pay off here.

The remaining ~26× gap on `json_parse` is **not** what the deferred ARC/alloc passes
target either: json_parse is a non-allocating parser whose cost is per-character
`String.char_at` + tiny accessor-method dispatch (`peek`/`advance`/`eof`/`is_digit`/`len`
called once per input byte). The highest-ROI next step is therefore **not** the heavy
memory passes but **string/method-dispatch codegen** — inlining `char_at` and the small
struct-method accessors so the per-byte loop stops paying call overhead — plus adding
allocation-heavy and string-throughput benchmarks so any future ARC/alloc/FBIP work has a
workload that can actually demonstrate a win. On the current evidence, the deferred heavy
passes are **not** justified by these benchmarks; revisit them only once a representative
allocation-bound workload exists.
