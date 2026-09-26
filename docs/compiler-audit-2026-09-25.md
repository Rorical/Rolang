# Compiler audit — 25 September 2026

## Changes

| Area | Reproduction before the fix | Resolution |
| --- | --- | --- |
| Scalar replacement | A scalar-only struct's `__release__` prints at O0/O1 but disappears at O2/O3. | Exclude types with destruction or trace hooks from scalar replacement and its supporting MIR inlining. |
| Object emission | `rolangc -c` fails with a multi-module error, even for a simple program with implicit core imports. | Resolve and merge modules once for every output format; specialize imported generics together. Remove the separate object path and its output deletion/options mutation. |
| Standard-library imports | `import std.io` looks for `std/std/io.rl` in the bundled library. | Strip the namespace prefix when resolving dotted standard-library imports against the bundle. Local and include-path precedence is preserved. |
| Floating-point comparison | `NaN != NaN` incorrectly returns false. | Emit LLVM `fcmp une` for inequality; retain ordered equality and ordering. |
| Signed division | Minimum i64 divided by -1 works at O0 on this machine but traps at optimized levels. | Replace the divisor with +1 for the overflow pair before emitting division/remainder, producing wrapping minimum/zero without LLVM poison. |

## Validation

Initial audit suite: **722 passed, 2 skipped** in 275.61 seconds, including 45 new regression cases. The two skips are existing toolchain integration decorators. In this sandbox, the run used `UV_CACHE_DIR=/private/tmp/rolang-uv-cache .venv/bin/pytest -q -n 4` because the default user cache is not writable.

`tests/test_compiler_audit.py` exercises all four optimization levels, including observable destruction, generic destructors, unused objects, NaN comparisons, signed division at every integer width, struct aliasing, async execution, dotted imports, and imported generics.

MIR and LLVM output are checked; LLVM is verified with LLVM's verifier. Emitted objects are independently linked with the runtime and executed. This checks the actual object artifact, including imported generic specializations and its lifetime after the driver returns.

All 24 additional manual runs compile the Fibonacci, closure, async, method, reference-mutation, and JSON-parser examples at O0–O3. Generated MIR, LLVM IR, and native object disassembly are inspected for scalar replacement, destructor preservation, unordered comparisons, and guarded division.

The six examples return 55, 15, 126, 30, 30, and 0 respectively, consistently at all four levels, with identical stdout per example. The JSON example prints a diagnostic tree without commas; this check establishes consistent execution, not JSON serialization compliance.

Native output confirms that an ordinary scalar `Point { x: 42 }` reduces to the following at O3:

```asm
mov w0, #0x2a
ret
```

The corresponding destructor-bearing object retains allocation and release code. Numeric LLVM output contains `fcmp une` and a `div.safe` operand selection before signed division.

Run the suite with:

```sh
uv sync --frozen
uv run pytest -q -n 4
```

## Language and tooling caveats

- Structs have reference semantics. `let` freezes a binding, not the object's mutable fields. Scalar replacement must preserve both aliasing and destruction.
- Numeric literals now use concrete parameter types, including narrow integer arguments and signed minima. Variables still require explicit casts when narrowing.
- Signed division truncates toward zero. Minimum divided by -1 wraps to minimum; its remainder is zero. Zero divisors still panic. NaN converts to integer zero, and compares unequal to every floating-point value.
- Cyclic garbage is reclaimed by a synchronous generational cycle collector. Collection can pause execution, and destruction of cyclic objects is delayed. The README's former pause-free claim was incorrect.
- At the initial audit, async execution lacked source-level spawn, cancellation, and an I/O event loop. These are implemented in the subsequent [async/task implementation](async-io-task-control.md); execution remains cooperative and single-threaded.
- `--emit mir` and `--emit llvm` retain their original stages. New `--emit mir-opt` includes async lowering, MIR optimization and ARC; `--emit llvm-opt` exposes verified backend-optimized LLVM IR; `--emit asm` emits assembly with the same target and optimization settings as object output.
- Plain source imports use unified object output. The subsequent [separate-module implementation](separate-modules.md) adds `.rlm` artifacts with native objects and generic source metadata; consumers link non-generic library code and instantiate generics locally.
- Package dependencies support paths, Git, and now [configured static registries](package-registries.md). Hosted registry operations and publishing remain future work.

Validation here targets the local Apple Silicon macOS host. It does not establish cross-platform correctness or a new performance ranking.

## Follow-up — 26 September: literals, function values, and output inspection

- Function, method, and enum payload arguments now provide numeric literal context. Signed minimum literals are range-checked as negative values; out-of-range literals remain errors. Explicit casts isolate their operand from the surrounding expected type.
- Named function values now lower through closure adapters. Regression programs store them, pass and return them, and exercise both heap-valued and Void returns. Async, generic, and unsafe bare function values produce diagnostics explaining the supported alternative.
- Object, assembly, and optimized LLVM output share target setup, verification, and backend optimization. Text-output write errors are reported as diagnostics.
- Both previously skipped project build/run tests are enabled.
- Fixed Void-valued return expressions, which previously produced invalid LLVM return instructions. Callback tests also check that live heap-object counts return to their baseline.

```sh
rolangc --emit mir-opt -O3 program.rl
rolangc --emit llvm-opt -O3 program.rl
rolangc --emit asm -O3 program.rl -o program.s
```

Final follow-up validation: **767 passed, no skips**, in 301.70 seconds on Apple Silicon macOS. This includes 43 focused literal/function-value/output checks and the two newly enabled project build/run tests. Emitted assembly is independently assembled, linked, and executed at O0–O3; LLVM output is verified; callback tests check observable output and heap-object reclamation.

The next implementation adds [asynchronous socket I/O and task control](async-io-task-control.md). The subsequent [module implementation](separate-modules.md) adds independent native objects and generic source metadata. The subsequent [registry implementation](package-registries.md) adds static registry consumption. Hosted publishing remains outstanding. The subsequent [incremental build implementation](incremental-builds.md) reuses unchanged native targets; finer-grained incremental compilation remains future work. Synchronous cycle collection remains an execution-time caveat.


## Follow-up — separate native modules

Added `rolangc --emit module` and `.rlm` imports with reusable native objects,
generic source metadata, stable native/type identities, and transitive dependency
linking. The implementation also preserves cycle-analysis optimizations for sparse
type IDs and diagnoses incompatible or conflicting artifacts. See
[separate modules](separate-modules.md) for usage and limitations.

Validation: 819 tests passed in the full suite, followed by 69 focused tests after
final adjustments. Seven module sanitizer scenarios passed, including async tasks,
heap values, destructors, and cyclic object reclamation at O0/O3.


## Follow-up — static package registries

Added configurable static registry consumption, stable version requirements,
transitive resolution with backtracking, verified archive caching, checksum pins,
and offline reuse of compatible locked graphs. Fixed name resolution for
symlinked package entry points and development-dependency installation for test
targets. See [package registries](package-registries.md) for setup and limitations.

Validation: **856 tests passed** in the full suite, followed by **72 focused
registry/toolchain tests** after final fixes. HTTP downloads were exercised with
a temporary loopback server; no external registry was deployed or published to.


## Follow-up — prerelease registry versions

Added SemVer prerelease/build-metadata parsing and ordering. Stable requirements
exclude prereleases unless explicitly opted into their version core. Registry
selection handles metadata variants deterministically and preserves exact locked
artifact identities. Extracted version handling from registry transport and
archive code into `toolchain/versions.py`.

Validation: **130 version/registry/toolchain tests passed**, including a compiled
consumer installed from a prerelease package and restored offline from its lock.


## Follow-up — incremental builds

Added content-addressed native target caching to project build/run/test workflows.
Validated import-search snapshots allow warm builds to skip parsing and all later
compiler passes. Fingerprints cover source/import/module inputs, project settings,
compiler/runtime implementation, and native toolchain configuration. Cache entries
restore missing or damaged outputs; diagnostics and failed compilations are not
cached. `--no-cache` bypasses reuse, and `rolang clean` removes cached artifacts.

See [incremental builds](incremental-builds.md) for granularity and limitations.

Validation: **155 incremental/toolchain/registry/output/module/import tests passed**
in 105.08 seconds. A small local example measured 2.608 seconds cold and 0.038
seconds warm; broader performance claims are not established by that measurement.

## TCP async follow-up

The async runtime now supports numeric IPv4/IPv6 TCP connect and listener/accept
operations. See [the async implementation notes](async-io-task-control.md).
Testing also fixed ARC release motion destroying an enum owner before retaining
its extracted payload, which previously closed listeners prematurely at O1–O3.
