# Rolang Performance — Batch 2

Follow-on optimizations after Batch 1. Captured with the editable `rolangc` tool
(`uv tool install --editable`), `--warmup 3 --runs 15`, C + Rolang, medians quoted in
prose, mins in tables (least noise-sensitive). See `batch1.md` for the baseline story and
the benchmark-tooling caveats.

## Item 1 — Trigger cycle-GC at allocation, not before every statement

**Status:** done. **json_parse 25.6× → 1.37× C (~18.7× faster).**

### Root cause (found by profiling, not guessing)

Batch 1 left `json_parse` at ~26× C and pointed at "string/method dispatch." Disassembly +
LLVM-IR inspection of the hot parse loop showed the truth: `String.char_at`, `peek`,
`advance` are **all inlined** (the loop is a single `cmpb (%rax,%r13,1)` byte-load + `incl`
for `pos++`). What remained was a **`rt_gc_collect()` call emitted before every basic block**
(`arc_insertion.py`, one `GCCheck()` per block) — ~5 indirect `call *%r15` per byte **in a
loop that allocates nothing**. Each call just did `if (counter - last < gap) return;`.

A controlled experiment (suppress all GC polls) took json_parse 802 ms → 43 ms, proving the
poll was ~95% of its runtime.

### The fix

The cycle collector can only have new work after an allocation, and the allocation counter
(`gc_alloc_counter`) only advances inside `rt_obj_alloc`. So a poll in non-allocating code is
**provably dead**. Moved the trigger to the one place allocations happen:

- `rolang_rt.c` `rt_obj_alloc`: after `gc_list_add(h)`, inline-check
  `if (!gc_running && gc_alloc_counter - gc_last_collect_count >= gc_next_gap) rt_gc_collect();`
- `arc_insertion.py`: removed the per-basic-block `GCCheck()` insertion.

All typed-object allocations funnel through `rt_obj_alloc`, so coverage is complete by
construction (no per-op enumeration, no drift risk). Non-allocating hot loops now run with
zero GC overhead; allocation-heavy loops still poll once per allocation.

**Safety** (this changes GC *timing* — now fires mid-allocation rather than at block entry):
the just-allocated object is rc=1 with zeroed pointer fields and the list lock is released, so
it's a safe collection point; codegen emits field-value retains *before* the alloc op and
field stores *after* `rt_obj_alloc` returns, so no object graph is ever torn at an allocation
point; the `!gc_running` guard prevents re-entrant collection from a deinit's allocation.
Validated by the 14 GC/cycle/deinit tests, the full suite (666 passed / 2 skipped), and an
independent adversarial review (ABI/UAF/re-entrancy/leak — VERDICT SHIP).

### Measured (clean A/B, O3, mins; stash = pre-fix per-block poll)

| Benchmark | Pre-fix | Post-fix | Δ |
|-----------|--------:|---------:|---:|
| json_parse | 799.6 ms (25.6× C) | **42.8 ms (1.37× C)** | **−94.6%, ~18.7× faster** |
| mandelbrot | 187.4 ms (1.07× C) | 187.8 ms (1.07× C) | flat |
| fib | 33.3 ms (1.48× C) | 38.5 ms (1.72× C) | −15% (see note) |

**fib note:** fib allocates nothing and the fix only *removes* its per-block poll calls, so it
cannot be semantically slower. The regression is a **code-layout/alignment artifact** —
removing the poll calls globally shifted every function's address, and fib's ~29M-call hot
recursion (a tiny function) landed on a worse alignment. Confirmed by disassembly: fib's only
remaining `call` is its own recursion; the body is strictly smaller than before. Chasing it
(alignment flags) is fragile and out of scope; fib remains ~1.7× C.

**Read:** the largest single win since Batch-1 Item 1. The "string optimization" the Batch-1
summary guessed at was a red herring — strings were already inlined; the cost was a dead GC
poll. json is now within ~1.4× of C, in line with fib/mandelbrot.

---

## Item 2 — Memory/compute-heavy benchmarks (suite expansion)

**Status:** done. Added three benchmarks far heavier than the existing three, each stressing
a runtime subsystem the old suite never exercised. They produce deterministic integer output
(checksums) that matches byte-for-byte between the C reference and Rolang, so correctness is
verified on every run (registered with expected values in `runner.py`).

| Benchmark | Stresses (previously untested) | Workload |
|-----------|--------------------------------|----------|
| `binary_trees` | typed-object **pool + ARC + GC** at scale | CLBG-style: build/free millions of `struct Tree` nodes (maxDepth 14), recursive checksum |
| `nbody` | **Vec<f64>** indexing + float compute | 1000 bodies, 20 steps of O(N²) gravitation; LCG init + shared Newton `sqrt` for bit-identical f64 |
| `word_freq` | **Dict<String,i64>** + string hashing | 2M tokens over a 2000-word vocab into a hashmap; order-independent count checksum |

**Baseline (C + Rolang, O3, `--warmup 2 --runs 5`, min):**

| Benchmark | C | Rolang | ×C |
|-----------|--:|-------:|---:|
| fib | 23.9 ms | 38.8 ms | 1.6× |
| mandelbrot | 176.4 ms | 188.6 ms | 1.1× |
| json_parse | 32.1 ms | 44.9 ms | 1.4× |
| **binary_trees** | 63.9 ms | 1.48 s | **23.2×** |
| **nbody** | 806 ms | 2.49 s | **3.1×** |
| **word_freq** | 3.8 ms | 432 ms | **113×** |

**Read — these expose where Rolang is actually far from C** (the old three are all ~1.1–1.6×):
- **binary_trees 23×** — typed-object allocation + ARC retain/release + cycle-GC is ~23× a C
  `malloc`/`free` churn. This is the real cost of the ARC model and the first benchmark that
  measures it; it's the natural target for the deferred memory passes (escape analysis →
  stack-allocate non-escaping nodes; FBIP reuse). It also validates the new alloc-triggered GC
  under millions of allocations.
- **nbody 3.1×** — `Vec` bounds-checks per access + no SIMD/auto-vectorization on the force loop.
  Targets: bounds-check elision, vectorization.
- **word_freq 113×** — `Dict<String,i64>` + string hashing/equality. *Caveat:* the C reference
  uses a flat count array (the vocabulary is bounded), so 113× is an unfair lower bound, not an
  apples-to-apples hashmap comparison — its value is exercising Rolang's real Dict/String path
  and tracking it across versions, not the absolute ratio. Still, it flags Dict/String as a
  heavy cost center. (The author also hit several real Rolang codegen bugs working around the
  Dict-key-iteration path — see commit notes; out of scope here.)

These three give future ARC/alloc/string work a workload that can actually demonstrate a win,
which Batch 1 explicitly called for (the old suite could not measure the pool, ARC at scale,
GC under load, containers, or string throughput).

---

## Item 3 — O(1) GC-list removal (fixes an O(n²) free path)

**Status:** done. **Fixes a real O(n²) scalability bug** in the runtime's free path. Does
**not** move the binary_trees benchmark (see why below) — kept on its own merits.

`rt_obj_release` unlinked every freed object from `gc_object_list` via `gc_list_remove`,
which was an **O(n) linear scan** from the head → freeing N objects is **O(N²)**. Made the
list **doubly-linked** so removal is O(1). To avoid an ABI change, the reserved `_pad` slot
in `ObjHeader` (offset 16) became the `prev` pointer — the header stays 32 bytes, so **no
codegen/payload-offset change** (the alternative, growing to 40 bytes, would touch ~16
hardcoded `+32` offsets). The pool metadata `_pad` used to hold is recovered at free from
the type descriptor (`total = header + desc->payload_size`; `from_pool = total ≤ 256`, made
exact by always pooling small objects). The relied-on invariant — alloc-time `payload_size`
== `desc->payload_size` — was verified empirically (0 mismatches across the benchmarks + 15
examples covering closures/async/existentials/protocols/strings/Dict/Vec) and a
`-DROLANG_CHECK_PAYLOAD` probe is kept to re-check it.

**Scaling test** (build one tree of depth D, free at scope end — the *non-LIFO* "build/parse
a large structure, process it, then drop it" pattern):

| nodes | before | after |
|------:|-------:|------:|
| 65,535 | 27.75 s | **0.01 s** |
| 131,071 | 123.12 s | **0.03 s** |
| 524,287 | (≈O(n²), ~min) | **0.16 s** |

O(n²) → O(n) — ~2800× at 65K nodes.

**Why binary_trees the benchmark is unmoved (still 23× C):** it frees trees in **LIFO order**
(`let t = make(d); check(t)` — each tree dropped immediately while its nodes are still at the
*head* of the GC list), where even the old head-scan was effectively O(1). So binary_trees
was never free-bound; its 23× is **allocation + ARC + traversal**. This fix targets the
common pattern (persistent structure freed non-LIFO) that the benchmark happens not to hit —
a real bug, just orthogonal to this benchmark. binary_trees' actual bottleneck remains to be
profiled/optimized separately.

**Validated:** full suite 667 passed / 2 skipped; 24 GC/cycle/deinit/dict tests; independent
adversarial review of the doubly-linked invariants, Phase-5d rebuild, mid-collection removal,
and pool-bin recovery — VERDICT SHIP. Contained to `rolang_rt.c`.

---

## Item 4 — ARC-optimization memory leak (the real binary_trees cause)

**Status:** done. **Correctness fix.** binary_trees **23.0× → 3.30× C** (peak RSS 280MB → 7MB).

The deep-dive into binary_trees (chasing a "GC is 83% of runtime" reading) bottomed out at
the actual cause: a **memory leak in the ARC-optimization pass**, not the GC. The GC was
merely scanning the leaked, ever-growing heap.

**Root cause:** `arc_optimization._eliminate_borrowed_single_use` (runs at `opt>=1`) is meant
to drop the retain/release of a *borrowed* field read (`ExtractField`) used once as a call
argument — but it never checked the local's defining op. So it also matched **owned** values:
`let t = make(); f(t)` lowers to `_3 = _5` (an owned copy whose retain sits on the source
`_5`, not `_3`). The pass removed `_3`'s *release* while no `Retain(_3)` existed to remove →
every such object's refcount stayed permanently elevated → leak. **Any program passing an
owned reference value once to a function leaked it at -O1+.** The suite missed it because it
(a) checks output, not memory, and (b) compiles at **-O0**, where the ARC-opt pass never runs
— so a `-O3` regression test was added (`rt_obj_live_count` introspection).

**Fix:** only eliminate when a `Retain` on *this exact local* exists (a genuine balancing
pair). Conservative — eliminates fewer ops, never more.

**Final O3 benchmarks (min, vs C):**

| Benchmark | Before this batch | After | Note |
|-----------|------------------:|------:|------|
| fib | 1.71× | 1.71× | no allocation |
| mandelbrot | 1.07× | 1.07× | — |
| json_parse | 1.36× | 1.36× | only 2 allocs |
| **binary_trees** | **23.0×** | **3.30×** | leak fixed (was the dominant cost) |
| nbody | 3.07× | 3.07× | Vec/float; no leak pattern |
| word_freq | 113× | **201×** | *correctly slower* — see below |

**Read:** binary_trees is the headline — the leak fix took it to the ~0.25s GC-off ceiling.
**word_freq got slower (113× → 201×) and this is expected:** it was *free-riding on the bug*
— the wrongly-elided releases meant it did less ARC work on its temporary `String` keys. The
fix restores the necessary releases (correct refcounting) at the cost of that work. (Its C
baseline is a flat array, so the absolute ratio is not apples-to-apples; word_freq's residual
~140MB working set and absolute cost are a separate follow-up, unrelated to this correctness
fix.) The generational GC (Item 3) is validated but did not move binary_trees — the leak did.

---

## Item 5 — Phantom out-param default-init (the real word_freq cause)

**Status:** done. **Correctness fix.** word_freq **201× → 26× C** (712 ms → 129 ms);
`Vec<String>.get` in isolation **617 ms → 17 ms (36×)**.

The word_freq deep-dive (chasing "the Dict must be slow") bottomed out, like binary_trees
before it, at a **memory leak** — this time a codegen one, and *not* in the Dict at all.

### Root cause (found by cost decomposition, not guessing)

A controlled A/B decomposition of the 2M-token loop (min of 9, O3) attributed the cost:

| stage | time | |
|-------|-----:|--|
| empty loop (LCG + word-id only) | 5 ms | floor |
| + `keys.get(w)` (`Vec<String>` index) | **617 ms** | **82% of runtime** |
| + 1 dict `get` | 688 ms | |
| + 1 dict `set` (full baseline) | 746 ms | |

The Dict was ~17%; `Vec<String>.get` was 82% — ~306 ns per array read. The IR showed why:
`var out: T;` (a heap-typed local with no initializer) is default-initialized by
`mir_builder._emit_default_init` to a **fresh `rt_obj_alloc` zero object**. Every container
accessor uses the out-param idiom

```
pub def get(index: i32) -> T { var out: T; rt_gvec_get(self.handle, index, out as RawPtr); return out; }
```

so `rt_gvec_get` `memcpy`-overwrites the `out` slot **without releasing the phantom** — orphaning
one ~48-byte object **per call**. 2M leaked headers (~96 MB — exactly the "~140 MB working set"
flagged in Item 4) bloat the GC list, so every alloc-triggered poll re-scans them. (Affects every
`Vec<Heap>.get`/`.pop`, `Dict<_,Heap>.get`, and `DictIter` — not just word_freq.)

### The fix (and a rejected first attempt)

The default-init phantom is **genuinely needed** for two patterns — `var n: Node; n.v = 7`
(declare-then-mutate needs field storage) and `var s: String; s.len()` (read-the-default returns
"") — both verified to still work. So a blanket NULL default is unsafe.

A first attempt (runtime: make the out-param FFIs release `*out` before overwriting, symmetric
with `rt_gvec_set`) stopped the leak but **failed 3 existing deinit-count tests**: freeing the
phantom runs the user struct's `__release__`. Those tests were silently *passing because of the
leak* (phantoms were never freed → never deinit'd). That ruled out the runtime layer — the
phantom must not exist at all.

**Fix: a targeted MIR pass** (`mir_outparam_init.elide_outparam_default_init`, before ARC
insertion, all opt levels). It replaces the phantom `AllocObj` with a `NIL` init for locals used
*purely as out-params* — the first reference after the default-init, scanning straight-line in the
same block, is taking the local's address (`L as RawPtr`) into a call, with no read in between.
NULL is safe (the call initializes the slot before any read; an FFI-untouched path releases NULL
as a no-op — never a leak, never a phantom deinit). Locals read or field-mutated as genuine
defaults don't match and keep the phantom. Needed one supporting codegen tweak: `ConstantKind.NIL`
now types its null to the slot's pointer type (was always generic `i8*`), so it can store into a
`String*`/`Node*` slot — unchanged for the pre-existing RawPtr/`nil` cases.

**Final O3 benchmarks (min, vs C):**

| Benchmark | Before this item | After | Note |
|-----------|-----------------:|------:|------|
| fib | 1.6× | 1.6× | no containers |
| mandelbrot | 1.07× | 1.05× | no containers |
| json_parse | 1.36× | ~1.3× | marginal (heap-value Dict.get) |
| binary_trees | 3.30× | 3.30× | struct build; unaffected |
| nbody | 3.07× | 3.07× | `Vec<f64>` is primitive (no phantom) |
| **word_freq** | **201×** | **26×** | phantom elided |

**Validated:** full suite **669 passed / 2 skipped** (the 3 deinit tests now pass *correctly* —
no phantom to deinit); a `-O3` regression test (`test_out_param_accessors_do_not_leak_at_O3`,
TDD-gated: fails on the pre-fix runtime, passes after); all 6 benchmarks byte-identical Rolang vs
C; an independent adversarial soundness review (domination, FFI-untouched paths, ARC interaction,
pattern-match robustness, the NIL codegen change) ran ASan on the transformed paths — **VERDICT
SHIP**. The Dict is now word_freq's bottleneck (2 probes/token: `get` then `set`); a single
get-or-insert primitive would roughly halve it — a clean follow-up.

### Two separate pre-existing bugs surfaced (NOT word_freq's hot path; flagged, not fixed)

1. **Inline string-literal arguments leak.** `f("x")` (a literal passed *directly* as a call arg)
   leaks one object per call — the literal is never bound to a MIR local, so ARC insertion has
   nowhere to attach its release. A call-result arg (`f(s.substring(..))`) is released correctly.
   Reproduces with the out-param pass disabled. word_freq pre-builds its keys, so its hot loop is
   unaffected.
2. **`DictIter`/`dict_keys` use-after-free.** `dict_keys(d)` copies only the raw `handle: RawPtr`
   into the iterator and holds no ARC reference to the `Dict`; if the source `Dict` is released
   while iterating, `rt_dict_key_copy` reads a freed handle (ASan-confirmed; reproduces with the
   pass disabled). word_freq keeps `counts` alive across its checksum loop, so it is unaffected.

*(Both follow-ups subsequently fixed: the literal-arg leak by materializing string literals into a
temp local in `_lower_literal`; the DictIter UAF by making `DictIter<K,V>` hold the `Dict` by value
like `VecIter`.)*

---

## Item 6 — Single-probe Dict read-modify-write (`entry_index`)

**Status:** done. **word_freq 26× → ~17× C (~1.5× faster).**

With the phantom-alloc leak (Item 5) gone, the Dict became word_freq's actual bottleneck — and
the cost was structural, not micro: the insert-or-increment idiom

```
let existing = counts.get(key) ?? 0;   // hash + probe #1
counts.set(key, existing + 1);         // hash + probe #2
```

does **two** full hash+probes per token for one logical update. Cost decomposition (interleaved,
min) put a single dict get ≈ a single set ≈ ~45 ns/token, and word_freq paid both.

**New primitive (general, not word_freq-specific):** a single-probe read-modify-write, exposed as
three `Dict<K,V>` methods over three C functions:

- `entry_index(key, default) -> i64` (`rt_dict_entry_index`): probe-or-insert in **one** hash+probe,
  returning the entry's *stable array index* (inserting `default` if absent).
- `value_at(index) -> V` (`rt_dict_get_at`) and `set_value_at(index, value)` (`rt_dict_set_at`):
  **O(1), hash-free** access by that index.

```
let i = counts.entry_index(key, 0);
counts.set_value_at(i, counts.value_at(i) + 1);   // one probe, then two O(1) ops
```

Indices are stable (entries are append-only, resize preserves order in place, the dict has no
remove), valid until the next mutation. The C functions mirror `rt_dict_set`/`get`'s ARC discipline
exactly: `entry_index` retains key+value on insert, `set_value_at` releases-old/retains-new,
`value_at` retains for the caller.

**Measured (interleaved, min):** word_freq **190 ms → 127 ms (1.49×)**; the dict portion (the
single probe + two O(1) index ops) replaces two probes. Output byte-identical (5308871524000000).
word_freq.rl now uses the new idiom.

**Validated:** full suite (all pass); two new tests — `test_dict_entry_index_single_probe_rmw`
(index stability, get-or-insert default, agreement with hashed `get`, survives an inserting resize)
and `test_dict_set_value_at_manages_heap_value_arc` (exactly-N-deinits for a `Dict<i32, Item>`,
proving `set_value_at` releases-old/retains-new and `value_at` retains for the caller); independent
adversarial ARC review (insert path, index stability, get_at/set_at refcount timelines, out-param
NULL interaction, bounds/null) with `rt_obj_live_count` leak checks at -O0 and -O3 — **VERDICT SHIP**.

**Read:** the Dict was never the bottleneck the original word_freq write-up guessed — first it was
the phantom-alloc leak (Item 5, 201×→26×), and only after that did the genuine two-probe redundancy
surface. Hash caching (keys reused ~1000×) was considered but deferred: the keys are 2–3 bytes, so
the FNV loop is a small part of each probe (the String-object indirection + memcmp dominate), and
the win would be marginal.
