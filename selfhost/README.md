# Rolang-written bootstrap compiler — self-parsing milestone

This directory starts the compiler rewrite in Rolang. The frontend is native
Rolang code: it reads source, lexes, parses, resolves local/function names, checks
types, and emits standalone C11. It does not invoke Python, Lark, or llvmlite.

**This is not full self-hosting yet.** The existing Python compiler builds this
compiler. Its frontend now parses every `.rl` file in this directory and exposes the syntax
tree as JSON. Its C backend cannot yet compile those files: lowering structs,
generics, imports, strings, and standard-library calls remains to be implemented. Here, “stage 0”
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

For C emission the native frontend takes two paths: input `.rl` and output C. C emission
needs no Python or C compiler on PATH. Compiling the resulting C requires a C11
compiler; the resulting program needs no Rolang runtime.

Exit codes: 0 for successful emission or parsing, 1 for a frontend diagnostic, and 2 for
usage/file errors. Diagnostics currently go to stdout and use `path:line:column`,
with one-based byte columns. Only the first frontend error is reported.
Existing output is left untouched on lexer/parser/type errors. Input/output paths
that resolve to the same path are rejected, including symlink aliases.

## Supported C subset

| Area | Supported |
|---|---|
| Functions | `def name(parameters) -> i32/Bool`, forward calls, recursion, mutual recursion |
| Entry point | Exactly one `def main() -> i32` |
| Variables | Initialized `let`/`var`, optional explicit `i32`/`Bool` annotation, lexical block scopes, assignment to `var` |
| Statements | `return`, `if { } else { }`, `while { }`, expression statements |
| Expressions | Decimal i32 integers, Boolean literals, variables, calls, parentheses |
| Operators | Unary `+ - !`; `* / % + -`; `< <= > >= == !=`; `&& ||` |
| Source | ASCII identifiers, whitespace, `//` and non-nested `/* */` comments; explicit statement semicolons |

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

## Self-parsing frontend

```sh
build/rolang-stage0 --parse selfhost/parser.rl > build/parser.ast.json
```

`--parse` reads one file and prints a deterministic JSON syntax tree. It does not
resolve imports or check types, names, assignment mutability, or return paths.
A library file does not need `main`. Successful parsing does **not** mean that a
program can be compiled by this backend.

The syntax supported beyond the C subset includes:

- Quoted strings (raw spelling retained), `nil`, member access, method calls,
  indexed access, struct literals with field labels, and numeric `as` syntax.
- Qualified named types, generic types and parameters, optional types, array and
  dictionary types, and function types.
- Imports and aliases, `pub` declarations, structs with stored fields and methods,
  static methods, generic functions, and transparent `typealias` declarations.
- `for`, `if let`, `break`, `continue`, `unsafe` and ordinary nested blocks,
  assignment through members/indices, bare `return`, and typed uninitialized `var`.

The JSON object contains `declarations`, `functions`, `expressions`, and
`statements`. Methods carry their owner name and declarations store method indices.
Function bodies and child expressions use indices into flat arrays; `-1` denotes
an absent child. Types are canonical strings. Tokens retain spelling, line and
byte column. Numeric node kinds are documented in `ast.rl`. This development
format may evolve with the rewrite.

Every source file in this directory is parsed at O0 and O3 and compared with the
Python frontend: declarations, field types, function signatures, and complete
expression/statement trees. This establishes self-parsing, not self-compilation.
The C backend checks the whole parsed tree and rejects unsupported constructs,
including unreachable ones, before publishing output.

## Structure

- `lexer.rl`: tokenization and source positions.
- `ast.rl`: declarations, types, functions, and flat expression/statement trees.
- `ast_json.rl`: deterministic syntax-tree serialization.
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
- run the native compiler with Python/backend tools absent from PATH;
- compare the expanded frontend against the Python syntax trees for its own
  sources and compound-type/operator fixtures;
- check malformed extended syntax, type/block nesting limits, source positions,
  JSON escaping, and explicit C-backend rejection.

## Deliberate limits and next milestones

The frontend still accepts a subset. Enums, protocols, extensions, constraints,
closures, labeled/default call parameters, collection literals, tuple types,
optional chaining, additional numeric literal forms, bitwise operators, async,
FFI declarations, and implicit returns are not implemented here. Struct fields
and statements require semicolons. `else if` must be written as `else { if ... }`.
Generic expression receivers currently use unqualified names (`Vec<T>.new()`).
The existing compiler continues to provide the complete language.

Expression/block/type nesting is limited to 128; source length is limited to the
scanner's signed 32-bit indexing range. Identifiers are ASCII; strings may contain
UTF-8. The backend remains C-only, with no ARC, MIR or LLVM generation.

Next steps toward actual self-compilation:

1. **Done:** expand the frontend to parse its own source, verified against the
   existing frontend.
2. Add semantic analysis and lowering for structs/enums, strings/collections,
   imports, and generic specialization.
3. Implement managed-object layouts, ownership lowering, and runtime linkage.
4. Make this compiler compile its own source; rebuild again with that executable.
5. Compare bootstrap generations and run the broader language regression suite.

A C backend and the existing C runtime can remain dependencies during those
bootstrap generations. Porting the package manager and language server is separate
from achieving compiler self-hosting.

## Validation

On Apple Silicon macOS, this milestone passed:

- **162 tests** in 115.10 seconds, including O0/O3 self-parsing, complete tree
  comparisons, differential program execution, and emitted-C UBSan checks.
- **86 AddressSanitizer checks** in 54.98 seconds, covering the expanded frontend,
  its own sources, malformed input, precedence, JSON output, and execution with
  Python/backend tools absent from PATH. The bootstrap runtime is instrumented;
  `detect_leaks=0` means these checks do not validate leaks.

See the test command above to reproduce the standard suite.

No existing compiler/runtime implementation is changed by this milestone; the
new compiler is entirely source-level Rolang code using the existing library.
