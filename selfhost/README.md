# Rolang-written bootstrap compiler — native module self-rebuild milestone

This directory starts the compiler rewrite in Rolang. The frontend is native
Rolang code: it reads source, lexes, parses, resolves names, checks types, and
emits standalone C11 for the supported subset. It does not invoke Python, Lark,
or llvmlite.

**The compiler executable rebuilds directly from `selfhost/main.rl`.** Its native
module loader reads local imports, resolves paths relative to each importing file,
deduplicates canonical paths, and merges parsed syntax trees with index remapping.
No external source concatenation is needed. The original CLI reads files, parses,
checks types, emits C, and writes its output in every native generation.

The Python-built stage 0 emits stage 1, which emits stage 2, which emits stage 3.
All three generated C files must be byte-for-byte identical. Rebuild subprocesses
run with Python and other tools absent from PATH; a C compiler is invoked only
between generations by the test harness. One generation is instrumented with
AddressSanitizer and UndefinedBehaviorSanitizer. Rebuilt executables compile sample
programs, emit matching AST JSON, and retain diagnostics and output protections.

**The rewrite remains incomplete:** the backend is a language subset with
process-lifetime allocation. Broader language coverage, complete module semantics,
and ownership lowering remain work ahead. The Python compiler is still the
primary compiler. Native compiler-core and frontend tests remain in place.

## Native local imports

Quoted imports are resolved relative to their source file. Shared
dependencies, symlink aliases, and cycles are loaded once. Diagnostics carry the
originating file and position. Compilation refuses to overwrite any loaded source;
`--parse` remains a single-file syntax operation and does not read imports.

Declarations have module-scoped identities: separate files can define the same
private functions or structs, and public functions can use private implementation
types. Imports expose public declarations and explicit public re-exports;
ordinary transitive imports stay private. Local declarations take precedence over
unaliased imports; conflicting public names are diagnosed when used. Standard
library free-function calls require a direct import or public re-export. Fields and methods without `pub` are accessible only within their source file.
Type resolution preserves identity through nested vectors, dictionaries, optionals,
parameters, return values, field types, constructors, and static receivers.

`import "library.rl" as L` exposes names as `L.name`, including function calls,
nested type annotations, struct literals, and static methods. Bare names do not
leak from aliased imports. `pub import` forwards public names, retaining an optional
alias; downstream aliases compose (`API.Inner.Box`). Re-export lookup preserves
original declaration identities through diamonds and terminates on cycles.

Duplicate aliases for different modules, aliases colliding with local declarations
or built-in types, and access to private exports are rejected. Local variables can
shadow an alias in expressions. Aliases and re-exports also work for the supported
standard-library built-ins. Escaped import paths and imported entrypoints remain
unsupported. Import and re-export traversal depth are limited to 128. Output path
protection uses canonical paths, not hard-link identity.

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

Rebuild directly from the project sources:

```sh
build/rolang-stage0 selfhost/main.rl build/stage1.c
cc -std=c11 -O3 build/stage1.c -o build/stage1
build/stage1 selfhost/main.rl build/stage2.c
cmp build/stage1.c build/stage2.c
```

Run the executable rebuild verification with:

```sh
.venv/bin/pytest -q tests/test_selfhost.py -k native_cli_rebuilds_itself
```

For C emission the native frontend takes two paths: input `.rl` and output C. C emission
needs no Python or C compiler on PATH. Compiling the resulting C requires a C11
compiler; the resulting program needs no Rolang runtime.

Exit codes: 0 for successful emission or parsing, 1 for a frontend diagnostic, and 2 for
usage/root-file errors (missing imports are frontend diagnostics). Diagnostics currently go to stdout and use `path:line:column`,
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
Local imports support the module subset above; type aliases are not lowered yet. String fields,
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
stdlib imports beyond the supported built-ins, string splitting/replacement,
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
   now have C lowering, including the OS bridge and native module loader used by
   the real CLI. Enums and general generic specialization
   remain part of broader language coverage.
3. Implement managed-object layouts, ownership lowering, and runtime linkage.
4. **Verified from project sources:** rebuild the complete CLI through three C
   generations directly from `selfhost/main.rl`.
5. Generation C parity is checked for the module graph; expand broader language coverage
   and its regression suite.

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

`import std.string_builder` is accepted as a built-in module, including aliases
and public re-exports through the module loader.
Private builder fields, explicit release, and StringBuilder equality are rejected.

## Native OS bridge and unsafe operations

Imports of `std.process`, `std.fs`, `std.io`, and `std.path` select these
built-in calls:

| Module | Supported calls |
|---|---|
| process | `argc`, `argv` |
| io | `print`, `println`, `print_i32`, `println_i32`, `println_i64` |
| fs | `fs_open`, `fs_close`, `fs_read_all`, `fs_read_line`, `fs_write_str`, `fs_seek`, `fs_tell`, `fs_flush`, `fs_eof` |
| path | `path_join`, `path_dirname`, `path_basename`, `path_extension`, `path_exists`, `path_is_dir`, `path_is_file`, `path_resolve` |

File modes are 0 for reading, 1 for writing/truncation, and 2 for appending; other
modes fall back to reading, matching the current library. Text reads and writes
preserve byte lengths, UTF-8, and embedded NULs. Line reads retain the newline;
whole-file reads start at the current position. Open failures return a null
RawPtr; flush/seek/tell return errors for null handles. Files must be closed by
the program. `fs_open` rejects filenames containing NUL; writes larger than the
i32 byte-count API can represent return zero. Printed strings preserve NUL bytes.

Path manipulation follows the existing POSIX library. `path_resolve` uses realpath
on macOS/Linux and returns the original input when resolution fails. Generated
programs need the platform C headers and library, including filesystem support.
The validation here runs on Apple Silicon macOS.

`RawPtr` can be stored and passed to these wrappers. Explicit `unsafe { }` blocks
are required for casts from integer literals to RawPtr and from RawPtr to supported
integer types. `unsafe def` is parsed and calls to it require an unsafe block;
its body also needs explicit unsafe blocks for pointer casts, matching the primary
compiler. Casting a stored value to RawPtr means address-of-storage in the existing
compiler; the native backend rejects that operation until address-of is implemented.
General memory access, FFI declarations, File wrapper objects, process spawning,
environment APIs, stdin, and directory listing are not lowered yet.

## Validation

On Apple Silicon macOS, this milestone passed:

- **116 regression checks passed** in 201.60 seconds across O0/O3 bootstrap
  builds, covering struct execution and diagnostics, generated-C sanitizers,
  parsing the compiler's source files, and standard-library I/O.
- **136 focused checks passed** in 109.56 seconds with the bootstrap runtime
  instrumented using AddressSanitizer and payload checks. These cover module
  identities, visibility, aliases, re-exports, cycles, ambiguity, shadowing,
  standard-library calls, source protections, and native self-rebuilds.
- **4 final checks passed** in 70.26 seconds after fixing computed member receivers:
  the new member/type-name collision regression and direct project self-rebuilds
  at both bootstrap optimization levels.
- Rebuild tests compile the actual CLI through three C generations with identical
  C output and no Python/tools on the rebuilding executable's PATH. Stage 3 also
  compiles nested aliases and standard-library re-exports. Stage 2 is compiled with AddressSanitizer and UndefinedBehaviorSanitizer and emits stage 3.
  Generated file-byte tests also use both sanitizers. `detect_leaks=0` means these
  checks do not validate leaks.
- The preceding I/O milestone also passed **5 primary-runtime regressions**
  in 20.10 seconds for NUL-preserving
  file reads and stdout, filename rejection, and reads after a partial read.

See the test command above to reproduce the standard suite.

These differential checks also found and fixed an LLVM backend bug: mixed-width
integer operations sign-extended unsigned operands and could use the wrong
comparison/division signedness. Regression tests exercise every optimization level.
Earlier string differential tests found and fixed another Python-backend bug: UTF-8 string literals used character counts
instead of byte lengths. A separate regression covers Unicode and embedded NULs
across every optimization level.

The OS bridge tests also found and fixed primary-runtime truncation of file reads
and printed strings at embedded NULs. Both now preserve explicit byte lengths;
NUL-containing filenames are rejected before opening or truncating a file.

The namespace tests also expose an unresolved limitation in the Python/LLVM
compiler: two imported modules with private structs named `Cell` can produce
`Cannot assign Cell to Cell`. The native compiler checks that collision case
against an explicit expected result at C O0/O3; other module/type cases retain
differential checks against LLVM. The reference also conflates some identically
named functions reached through different import aliases. Native collision tests
retain explicit expected results; renamed equivalents provide LLVM comparisons.
