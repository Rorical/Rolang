# Rolang-written bootstrap compiler — first milestone

This directory starts the compiler rewrite in Rolang. The frontend is native
Rolang code: it reads source, lexes, parses, resolves local/function names, checks
types, and emits standalone C11. It does not invoke Python, Lark, or llvmlite.

**This is not full self-hosting yet.** The existing Python compiler builds this
compiler. Its accepted language subset cannot yet compile its own source, which
uses structs, generics, imports, strings, and the standard library. Here, “stage 0”
names this initial subset compiler, not a successfully self-rebuilt compiler.

## Build and run

From the repository root, using the existing development environment:

```sh
mkdir -p build
.venv/bin/python -m rolang -O3 selfhost/main.rl -o build/rolang-stage0
build/rolang-stage0 selfhost/examples/fibonacci.rl build/fibonacci.c
cc -std=c11 -O3 build/fibonacci.c -o build/fibonacci
build/fibonacci
# Exit status: 88 (sum of Fibonacci values for 0 through 9).
```

The native frontend takes exactly two paths: input `.rl` and output C. C emission
needs no Python or C compiler on PATH. Compiling the resulting C requires a C11
compiler; the resulting program needs no Rolang runtime.

Exit codes: 0 for successful emission, 1 for a frontend diagnostic, and 2 for
usage/file errors. Diagnostics currently go to stdout and use `path:line:column`,
with one-based ASCII byte columns. Only the first frontend error is reported.
Existing output is left untouched on lexer/parser/type errors. Input/output paths
that resolve to the same path are rejected, including symlink aliases.

## Supported subset

| Area | Supported |
|---|---|
| Functions | `def name(parameters) -> i32/Bool`, forward calls, recursion, mutual recursion |
| Entry point | Exactly one `def main() -> i32` |
| Variables | Initialized `let`/`var`, optional explicit `i32`/`Bool` annotation, lexical block scopes, assignment to `var` |
| Statements | `return`, `if { } else { }`, `while { }`, expression statements |
| Expressions | Decimal i32 integers, Boolean literals, variables, calls, parentheses |
| Operators | Unary `+ - !`; `* / % + -`; `< <= > >= == !=`; `&& ||` |
| Source | ASCII identifiers, whitespace, `//` comments; explicit statement semicolons |

Function arguments are immutable. Conditions require Bool. Arguments, assignments,
annotations, and return values are checked for type compatibility. Parameters and
locals cannot be duplicated in the same scope; nested blocks may shadow names.
Every function must have a provable return on every path; a loop alone is not
accepted as proof of a return.

Integer arithmetic follows the existing compiler's wrapping i32 behavior,
including `INT32_MIN / -1` and `% -1`. Out-of-range literals are rejected. Division
by zero terminates the generated program with an error. The C backend avoids
signed-overflow undefined behavior, emits explicit temporaries for left-to-right
evaluation, preserves Boolean short-circuiting, and mangles all source names.

## Structure

- `lexer.rl`: tokenization and source positions.
- `ast.rl`: functions and flat, indexed expression/statement trees.
- `parser.rl`: recursive-descent statements and precedence-climbing expressions.
- `backend.rl`: name resolution, type checking, return checks, and C emission.
- `main.rl`: command-line and file I/O.

Syntax-tree indices avoid cycles and permit later arena-style representations.
Semantic checking and emission share a pass in this first milestone; the generated
text is published only if the entire program validates.

## Tests

```sh
UV_CACHE_DIR=/private/tmp/rolang-uv-cache .venv/bin/pytest -q tests/test_selfhost.py
```

The tests build the Rolang-written compiler at O0 and O3, then:

- compare generated programs with the existing compiler for arithmetic, control
  flow, recursion, scopes, and Boolean behavior;
- compile emitted C at both O0 and O3;
- exercise seeded random arithmetic and signed boundary cases;
- check generated arithmetic with UndefinedBehaviorSanitizer;
- reject malformed syntax, missing names, bad types, duplicate bindings,
  incorrect calls, and unsupported constructs;
- check output preservation, deterministic emission, file failures, and symlinks;
- run a 300-function frontend workload;
- run the native compiler with Python/backend tools absent from PATH.

## Deliberate limits and next milestones

The frontend accepts a strict subset. Imports, structs/enums, generics, aliases,
closures, strings, collections, protocols, async, FFI, additional numeric types,
and ARC code generation are not implemented here. The existing compiler continues
to provide all those features. Expression/block nesting is limited to 128; source
length is limited to the scanner's signed 32-bit indexing range. This first backend
is C-only; it does not emit MIR or LLVM.

Next steps toward actual self-compilation:

1. Expand the lexer/parser to represent the constructs used by these source files.
2. Add structs/enums, strings/collections, imports, and generic specialization.
3. Implement managed-object layouts, ownership lowering, and runtime linkage.
4. Make this compiler compile its own source; rebuild again with that executable.
5. Compare bootstrap generations and run the broader language regression suite.

A C backend and the existing C runtime can remain dependencies during those
bootstrap generations. Porting the package manager and language server is separate
from achieving compiler self-hosting.

## Validation recorded for this milestone

On Apple Silicon macOS:

- **78 tests passed** in 94.02 seconds, including emitted-C UBSan checks.
- **54 AddressSanitizer checks passed** in 35.90 seconds with the bootstrap
  runtime instrumented at O0/O3 (`detect_leaks=0`). These cover malformed input,
  random arithmetic, the 300-function workload, and execution without Python.

No existing compiler/runtime implementation was changed for this milestone; the
new compiler is entirely source-level Rolang code using the existing library.
