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
- Async execution remains cooperative and single-threaded, with no source-level spawn, cancellation, or I/O event loop. Those require language/runtime features beyond this corrective refactor.
- `--emit mir` and `--emit llvm` retain their original stages. New `--emit mir-opt` includes async lowering, MIR optimization and ARC; `--emit llvm-opt` exposes verified backend-optimized LLVM IR; `--emit asm` emits assembly with the same target and optimization settings as object output.
- Object output is one unified translation unit containing the entry and its dependencies. This does not implement independent module compilation or reusable generic metadata; separately emitted objects with shared dependencies can contain duplicate symbols.
- Package dependencies support paths and Git; a package registry remains unsupported.

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

Remaining architectural work is unchanged: asynchronous I/O and task spawning/cancellation, independent module compilation with generic metadata, and a package registry. Synchronous cycle collection remains an execution-time caveat. These require separate language/runtime or service designs; this follow-up does not implement them.
