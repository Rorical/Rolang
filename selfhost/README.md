# Rolang-written bootstrap compiler — vector code generation milestone

This directory starts the compiler rewrite in Rolang. The frontend is native
Rolang code: it reads source, lexes, parses, resolves names, checks types, and
emits standalone C11 for the supported subset. It does not invoke Python, Lark,
or llvmlite.

**This is not full self-hosting yet.** The existing Python compiler builds this
compiler. Its frontend parses every `.rl` file in this directory and exposes the
syntax tree as JSON. Its C backend cannot yet compile those files: generic
specialization, imports, dictionaries, optional values, and some standard-library
calls remain to be implemented. Non-generic structs, methods, core strings, and
typed vectors now emit C. Here, “stage 0”
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
| Functions | `def name(parameters) -> i32/i64/Bool/String/Struct/Void`, forward calls, recursion, mutual recursion |
| Entry point | Exactly one `def main() -> i32` |
| Variables | Initialized `let`/`var`, optional explicit `i32`/`i64`/`Bool`/`String`/struct annotation, lexical block scopes, assignment to `var` |
| Statements | `return`, `if { } else { }`, `while`, `for` over vectors, `break`/`continue`, nested/unsafe blocks, expression statements |
| Expressions | Decimal i32 integers, Boolean/string literals, variables, calls, numeric casts, parentheses |
| Operators | Unary `+ - !`; `* / % + -`; `< <= > >= == !=`; `&& ||` |
| Objects | Non-generic structs, fields, labeled literals, static and instance methods, shared references |
| Collections | Typed `Vec<T>`, nested vectors, indexed access/assignment, constructors and core methods |
| Source | ASCII identifiers, whitespace, `//` and non-nested `/* */` comments; explicit statement semicolons |

Function arguments are immutable. Conditions require Bool. Arguments, assignments,
annotations, and return values are checked for type compatibility. Parameters and
locals cannot be duplicated in the same scope; nested blocks may shadow names.
Every non-`Void` function must have a provable return on every path; a loop alone
is not accepted as proof of a return. `Void` functions permit bare returns and
fallthrough.

Integer arithmetic follows the existing compiler's wrapping i32 behavior,
including `INT32_MIN / -1` and `% -1`. Out-of-range literals are rejected. Division
by zero terminates the generated program with an error. The C backend avoids
signed-overflow undefined behavior, emits explicit temporaries for left-to-right
evaluation, preserves Boolean short-circuiting, and mangles all source names.

## Structs and methods

```sh
build/rolang-stage0 selfhost/examples/structs.rl build/structs.c
cc -std=c11 -O3 build/structs.c -o build/structs
build/structs
# Exit status: 42.
```

Struct values are heap references. Assignment, parameters, and returns preserve
aliasing; nested fields can hold other structs. Forward type declarations, static
factories, instance `self`, and recursive methods are supported. As in the current
compiler, `let` prevents rebinding local references; writes through object fields
remain allowed. Struct equality is rejected, matching the existing compiler for
structs without comparison support.

Every stored field must be explicitly initialized exactly once. Field and method
names, initializer types, receivers, call arity, and argument/return types are
checked. Generic structs, default field values, and lifecycle hooks are rejected.
Dictionaries, imports, and type aliases are not lowered yet. String fields,
parameters, and return values use the built-in string representation.

The standalone generated C tracks allocations in a program-wide arena and frees
them at normal process exit (including runtime division-by-zero exits). Objects
are retained until exit, so allocation-heavy, long-running programs can consume
more memory than the existing ARC compiler. This is an intermediate ownership
model for the bootstrap backend. Destructors and other `__` hooks are rejected
rather than given incorrect destruction timing. ARC remains a future milestone.

Evaluation preserves source argument/initializer order. Field assignment follows
the existing compiler: evaluate the right-hand side, then resolve the target.

## Strings and signed widths

The backend supports built-in `String` without importing a standard-library file:

- String literals and `+`/`concat` concatenation.
- `len` (returns `i64`), `is_empty`, `equals`, and `compare_to`.
- `contains`, `starts_with`, `ends_with`, and `find_char`.
- `byte_at`/`char_at` and `substring`.
- `i32.to_string()` and `i64.to_string()`.

Strings preserve UTF-8 bytes and embedded NULs. Lengths and indices count bytes;
`char_at` has the same byte behavior as the existing library. Out-of-range byte
access returns `-1`. Substring clamps negative starts to zero, caps the requested
length at the remaining bytes, and returns an empty string for nonpositive
lengths or starts past the end. Comparisons use unsigned byte order and return
`-1`, `0`, or `1`. Use `equals` for equality; string comparison operators are not
supported by this subset. Literal escapes follow the existing frontend, including
its treatment of unknown escapes (discard the backslash).

Signed `i64` values support arithmetic, comparisons, parameters, fields, returns,
and widening from `i32`. Explicit `as i32` narrowing retains the low 32 bits;
`as i64` widens with sign extension. Arithmetic wraps at the result width and
handles minimum-signed-value division by `-1` without C undefined behavior.
Decimal literals still have the bootstrap's i32 range: larger i64 values must
currently be obtained through calculations, casts, or string lengths. Other
numeric widths and cast categories are rejected.

Strings and their byte buffers follow the same process-lifetime allocation model
as structs. The backend emits their helpers directly into standalone C. General
stdlib imports, output functions, StringBuilder, string splitting/replacement,
and additional collection operations remain future work.

## Vectors and iteration

`Vec<T>` is now a built-in typed collection in the C backend. Its element type can
be `i32`, `i64`, `Bool`, `String`, a declared non-generic struct, or another vector.
Vector types are invariant: `Vec<i32>` and `Vec<i64>` are distinct types. Individual
`i32` values can still widen when inserted into `Vec<i64>`.

Supported operations:

- `Vec<T>.new()` and `Vec<T>.with_capacity(capacity)`.
- `push`, `get`, `set`, `pop`, `len`, and `resize`.
- Indexed reads and writes (`items[index]`, `items[index] = value`).
- `for item in items`, including growth during iteration, nested loops, `break`,
  and `continue`. The iterable is evaluated once; length is checked each iteration.

`resize` grows capacity without changing length. Invalid indices exit with a
runtime bounds diagnostic. Empty `pop` follows the current library's zero-slot
behavior: zero/false for scalars and a null reference for heap elements. Callers
must ensure a nonempty vector before using a popped heap object. Explicit `free`,
raw handles, iterator objects, collection literals, and general user-defined
generic specialization remain unsupported.

Vector references preserve aliasing across calls, fields, and returned values.
Indexed assignment evaluates the receiver, index, and value in that order, as in
the existing compiler; ordinary field assignment continues to evaluate its value
first. Loop variables are immutable and scoped to the loop body.

Elements occupy typed numeric/reference slots, avoiding pointer/integer aliasing.
Resized buffers, vectors, and referenced objects use the bootstrap's process-wide
allocation lifetime; ARC reclamation is still pending. The differential suite
includes a small token scanner with `Vec<Token>`, nested vectors, mixed scalar and
heap elements, growth/aliasing, and mutation during iteration.

## Self-parsing frontend

```sh
build/rolang-stage0 --parse selfhost/parser.rl > build/parser.ast.json
```

`--parse` reads one file and prints a deterministic JSON syntax tree. It does not
resolve imports or check types, names, assignment mutability, or return paths.
A library file does not need `main`. Successful parsing does **not** mean that a
program can be compiled by this backend.

The extended syntax represented by the frontend includes:

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
- `string_codegen.rl`: emitted byte-string and signed 64-bit C helpers.
- `vector_codegen.rl`: emitted vector storage, growth, and bounds checks.
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
  flow, recursion, scopes, Booleans, struct aliasing, fields, and methods;
- exercise nested assignment order, forward struct types, empty structs, `Void`,
  and a 10,000-object allocation workload;
- run generated struct programs under AddressSanitizer and UndefinedBehaviorSanitizer;
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
  JSON escaping, and explicit C-backend rejection;
- compare string operations, UTF-8/NUL handling, substring bounds, numeric
  conversion, and signed 64-bit boundaries against the existing compiler;
- compare typed vector storage, heap aliasing, indexed assignment order, loops,
  capacity growth, and token scanning; check invalid indices under sanitizers.

## Deliberate limits and next milestones

The frontend still accepts a subset. Enums, protocols, extensions, constraints,
closures, labeled/default call parameters, collection literals, tuple types,
optional chaining, additional numeric literal forms, bitwise operators, async,
FFI declarations, and implicit value returns are not implemented here. Struct fields
and statements require semicolons. `else if` must be written as `else { if ... }`.
Generic expression receivers currently use unqualified names (`Vec<T>.new()`).
The existing compiler continues to provide the complete language.

Expression/block/type nesting is limited to 128; source length is limited to the
scanner's signed 32-bit indexing range. Identifiers are ASCII; strings may contain
UTF-8. The backend remains C-only, with no ARC, MIR or LLVM generation.

Next steps toward actual self-compilation:

1. **Done:** expand the frontend to parse its own source, verified against the
   existing frontend.
2. **In progress:** non-generic structs/methods, core strings, signed widths,
   and typed vectors now have C lowering. Add dictionaries, optional values,
   imports, enums, and general generic specialization.
3. Implement managed-object layouts, ownership lowering, and runtime linkage.
4. Make this compiler compile its own source; rebuild again with that executable.
5. Compare bootstrap generations and run the broader language regression suite.

A C backend and the existing C runtime can remain dependencies during those
bootstrap generations. Porting the package manager and language server is separate
from achieving compiler self-hosting.

## Validation

On Apple Silicon macOS, this milestone passed:

- **366 bootstrap tests** in 441.41 seconds, including O0/O3 self-parsing,
  differential execution of vectors/loops, strings, structs and numeric
  operations, diagnostics, and generated-C sanitizer checks.
- **90 sanitizer checks** in 161.91 seconds with the bootstrap runtime
  instrumented using AddressSanitizer and payload checks. These cover native
  self-parsing, typed/nested vectors, bounds failures, aliasing, iteration,
  token scanning, and malformed vector operations. `detect_leaks=0` means
  these do not validate leaks.

See the test command above to reproduce the standard suite.

This milestone changes only source-level bootstrap code and tests. Earlier
string differential tests found and fixed a Python-backend bug: UTF-8 string literals used character counts
instead of byte lengths. A separate regression covers Unicode and embedded NULs
across every optimization level.
