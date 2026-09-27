# Rolang-written bootstrap compiler — compiler-core code generation milestone

This directory starts the compiler rewrite in Rolang. The frontend is native
Rolang code: it reads source, lexes, parses, resolves names, checks types, and
emits standalone C11 for the supported subset. It does not invoke Python, Lark,
or llvmlite.

**This is not full self-hosting yet.** The Python compiler still builds the
OS-facing compiler executable. The native C backend now compiles the actual
lexer, AST, parser, backend, runtime emitters, and JSON emitter when assembled
into one file with imports removed and a test entry point supplied.

That native-compiled core emits byte-for-byte identical C to the Python-built
bootstrap for recursive, dictionary-heavy, and StringBuilder programs. It also
reports invalid programs and produces AST JSON. The separately compiled frontend
parses its own source and matches all four flat AST node counts. Full executable
self-rebuilding still needs module loading and filesystem/process/I/O support.
Here, “stage 0” names the initial compiler, not a successfully self-rebuilt CLI.

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
| Functions | Supported value types and `Void` returns, forward calls, recursion, mutual recursion |
| Entry point | Exactly one `def main() -> i32` |
| Variables | Initialized `let`/`var`, optional explicit `i32`/`i64`/`Bool`/`String`/struct annotation, lexical block scopes, assignment to `var` |
| Statements | `if let`, `return`, `if { } else { }`, `while`, `for` over vectors, `break`/`continue`, nested/unsafe blocks, expression statements |
| Expressions | Decimal i32/i64 integers, checked byte literals, Boolean/string literals, variables, calls, numeric casts, parentheses |
| Operators | Unary `+ - !`; `* / % + -`; `< <= > >= == !=`; `&& ||` |
| Objects | Non-generic structs, fields, labeled literals, static and instance methods, shared references |
| Collections | Typed `Vec<T>` and `Dict<K,V>`, nested collections, indexed access/assignment, constructors and core methods |
| Source | ASCII identifiers, whitespace, `//` and non-nested `/* */` comments; explicit statement semicolons |

Function arguments are immutable. Conditions require Bool. Arguments, assignments,
annotations, and return values are checked for type compatibility. Parameters and
locals cannot be duplicated in the same scope; nested blocks may shadow names.
Every non-`Void` function must have a provable return on every path; a loop alone
is not accepted as proof of a return. `Void` functions permit bare returns and
fallthrough.

Integer arithmetic follows the existing compiler's wrapping i32 behavior,
including `INT32_MIN / -1` and `% -1`. Literals outside the requested numeric range are rejected. Division
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
Imports and type aliases are not lowered yet. String fields,
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
Decimal literals infer i32 or i64 by range, or use an explicit numeric context.
Both signed minima are supported; digits are range-checked without overflowing
the compiler's own arithmetic. Leading zeros remain decimal in emitted C.
`u8` supports byte casts, wrapping arithmetic, comparisons, storage, and widening
with zero extension to i32/i64. Casts to u8 retain the low eight bits. Direct byte
literals are checked against 0–255; other numeric widths remain unsupported.
Use explicit byte casts for field/index writes to stay compatible with the
Python compiler's current literal-inference behavior.

Strings and their byte buffers follow the same process-lifetime allocation model
as structs. The backend emits their helpers directly into standalone C. General
stdlib imports, output functions, string splitting/replacement,
and additional collection operations remain future work.

## Vectors and iteration

`Vec<T>` is now a built-in typed collection in the C backend. Its element type can
be any supported value type, including optional values and nested collections.
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
The existing compiler remains the primary compiler for the broader language.

Expression/block/type nesting is limited to 128; source length is limited to the
scanner's signed 32-bit indexing range. Identifiers are ASCII; strings may contain
UTF-8. The backend remains C-only, with no ARC, MIR or LLVM generation.

Next steps toward actual self-compilation:

1. **Done:** expand the frontend to parse its own source, verified against the
   existing frontend.
2. **In progress:** non-generic structs/methods, core strings, signed widths,
   typed vectors, dictionaries, optional values, StringBuilder, and byte casts
   now have C lowering. Add module loading and filesystem/process/I/O support
   needed by the compiler itself. Enums and general generic specialization
   remain part of broader language coverage.
3. Implement managed-object layouts, ownership lowering, and runtime linkage.
4. Make this compiler compile its own source; rebuild again with that executable.
5. Compare bootstrap generations and run the broader language regression suite.

A C backend and the existing C runtime can remain dependencies during those
bootstrap generations. Porting the package manager and language server is separate
from achieving compiler self-hosting.

## Optional values

The C backend supports `T?` for supported value types, typed `nil`, implicit
wrapping at assignments, calls, returns, field initializers and vector writes,
and `if let` with a scoped immutable binding. Optional locals without an
initializer start as `nil`. Presence is independent of the payload: zero,
`false`, and empty strings remain present values. A scrutinee is evaluated once.

`selfhost/examples/optionals.rl` demonstrates compiler-style binding lookup
with a `Binding?` return type and exits with status 42.

Use `if let` to unwrap values. Optional comparisons, chaining, and coalescing
are not implemented. A bare inferred `let x = nil` requires a type annotation.
Optional boxes use the same process-lifetime arena as other generated objects.

The Python reference compiler currently fails LLVM code generation for
`Vec<i32?>` push/set calls (optional aggregate argument type mismatches).
The native C implementation is tested directly at O0/O3 and with sanitizers
for this case; other optional programs are compared against the reference.

## Dictionaries

`Dict<K,V>` uses an insertion-ordered entry array with a linear-probe hash index.
Keys can be supported numeric, Boolean, or reference types; optional keys are
rejected. Values can be any supported type, including nested dictionaries,
vectors, structs, and optionals. Dictionary types are invariant.

Supported operations:

- `with_capacity(capacity, key_kind)` and `new(capacity, key_kind, key_type_id, value_type_id)`.
  As in the standard library, `new` evaluates its type-id arguments but derives
  storage from its generic types.
- `set`, `get`, indexed reads/writes, `contains`, and `len`.
- `remove` and `clear`; removal preserves insertion order.
- `keys` and `values`, returning independent ordered vector snapshots with shared
  object references.
- `entry_index`, `value_at`, and `set_value_at` for hash-free access by entry index.
  Invalid indices read a zero slot or ignore a write, matching the current library.

Key kind `1` compares String bytes by content, including UTF-8 and embedded NULs.
Other kinds use scalar equality or reference identity. Passing kind `1` with a
non-String key or a negative capacity exits with a runtime diagnostic.
The native backend limits entry counts to `INT32_MAX` and checks allocation sizes.

`get`, indexed reads, and `remove` return `V?`. A stored optional `nil` is still
present: unwrapping the outer optional succeeds, then the inner optional is nil.
`selfhost/examples/symbols.rl` demonstrates nested `Vec<Dict<String, Binding>>`
scopes, shadowing, shared bindings, and missing-name lookup; it exits with 42.

Entries and resized buffers follow the process-lifetime arena described above.
`entries()` (which needs generic `DictEntry` objects), explicit `free`, raw handles,
and dictionary iteration remain unsupported. The reference LLVM compiler's
optional-collection limitation also affects optional-valued dictionaries; native
execution tests cover those directly.

## StringBuilder

`StringBuilder.new()` creates a shared mutable byte buffer. The C backend supports
`append`, `append_line`, `append_byte`, `len`, `clear`, and `to_string`.
Appending preserves UTF-8 bytes and embedded NULs. Byte appends accept explicit
u8 casts or checked byte literals. `len` counts bytes. `to_string` copies the
buffer so later appends/clears do not mutate an existing snapshot; `clear` keeps
capacity for reuse. Buffer growth checks length and allocation-size overflow.
Allocations follow the same process-lifetime arena as other native objects.

The unaliased `import std.string_builder` is accepted as a built-in module.
General imports and import aliases still require the future module loader.
Private builder fields, explicit release, and StringBuilder equality are rejected.

## Validation

On Apple Silicon macOS, validation covered all 502 bootstrap cases:

- The broad run completed **501 passing checks** in 679.94 seconds. One randomized
  arithmetic case exceeded the 10-second limit while running the Python-built
  reference executable. Its emitted C was identical for O0/O3 bootstrap builds.
  The same executable and differential test passed on rerun; an exact pytest
  rerun with a fresh O3 bootstrap also passed (**1 passed**, 36.59 seconds).
- **50 checks** in 124.92 seconds with the bootstrap runtime instrumented using
  AddressSanitizer and payload checks. These cover the native compiler-core and
  frontend rebuilds, StringBuilder operations, byte/i64 boundaries, and diagnostics.
- Generated builder C additionally runs with AddressSanitizer and
  UndefinedBehaviorSanitizer. `detect_leaks=0` means these checks do not validate leaks.
- **40 LLVM code-generation checks** in 15.38 seconds, including mixed integer
  arithmetic/comparisons across every optimization level, passed for the
  separately committed operand-promotion fix.

See the test command above to reproduce the standard suite.

These differential checks also found and fixed an LLVM backend bug: mixed-width
integer operations sign-extended unsigned operands and could use the wrong
comparison/division signedness. Regression tests exercise every optimization level.
Earlier string differential tests found and fixed another Python-backend bug: UTF-8 string literals used character counts
instead of byte lengths. A separate regression covers Unicode and embedded NULs
across every optimization level.
