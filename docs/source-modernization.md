# Rolang source modernization

The migration applies the new syntax to compiler, library, example, and benchmark
code where it simplifies the existing algorithm. Cursor-driven scanning, growing
worklists, reverse searches, and i64 benchmark counters keep explicit while loops.

## Compiler

- Diagnostic and generated-code fragments use interpolation instead of chained
  concatenation and numeric `to_string` calls.
- Runtime C templates use raw multiline literals; their emitted bytes remain the
  same. Quotes, braces, and C escapes are directly readable.
- Fixed index traversals use ranges, byte-offset substrings use slices, and alias
  expansion uses `guard let` to keep its successful path outside an optional branch.
- The native compiler implements these constructs itself; the rewritten sources
  still participate in the three-generation bootstrap, with no Python in child
  processes. Its remaining syntax subset is documented in `selfhost/README.md`.

The subsequent [native enum milestone](selfhost-enums.md) represents the
pattern AST with a recursive associated-value enum. Its
parser, module remapping, serializer and matcher use the new constructors and
switch syntax directly.

## Standard library and applications

- Result helpers return switch expressions.
- Optional and iterator helpers use guard bindings and expression-body callbacks.
- `slice_vec` delegates to the common range-subscript implementation.
- Array, ByteString, directory listing, and fixed math loops use ranges.
- Closure examples demonstrate contextual parameter inference; the compiler
  frontend example uses slices and interpolated reports.
- JSON fixtures use raw literals. JSON, Mandelbrot, and n-body benchmark loops use
  ranges without changing workload sizes, numeric widths, or expected checksums.

## Bugs exposed by the migration

ARC use analysis now includes references stored into aggregates, captures, and
other operations. Optimization refreshes operation indices after removing pairs
and keeps call arguments alive when owner mutation could invalidate a borrow.
Regression tests cover stored vector aliases and a callee replacing the field
that owns another argument, at O0/O3 with AddressSanitizer and payload validation.

Implicit core imports now resolve directly to the bundled standard library. A
program named `range.rl`, `vec.rl`, `dict.rl`, or `string.rl` does not import itself.
Explicit user imports retain their normal lookup rules.

## Validation

- 32 ARC checks, including O0/O3 lifetime programs with ASAN and payload checks,
  plus five existing runtime leak/lifetime regression checks.
- 82 standard-library, iterator, collection, file-byte, switch, and guard checks.
- 40 range and import-system checks, including core-named source files.
- 728 native compiler checks: 698 existing regression cases plus 30 syntax/
  bootstrap cases. These include full-source AST comparisons at O0/O3, three
  identical generated C generations, and the sanitized native generation.
- Nine changed programs compiled and executed before/after at O3; exit codes,
  stdout, and stderr matched. Benchmark checksums: JSON `85400000`, Mandelbrot
  `42970365`, n-body `2446731634`.
- Native/Python AST comparisons normalize interpolation's equivalent concatenation
  grouping while checking field order, expressions, bindings, and control flow.
