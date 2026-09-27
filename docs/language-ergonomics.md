# Compiler-writing ergonomics (Python/LLVM compiler)

This work targets the primary compiler first. The self-hosted C backend has
separate feature coverage; new syntax here is not automatically supported there.

## Sequence

1. Validate existing payload enums, pattern matching and generic ASTs.
2. Harden existing `Result<T, E>` / `?` error propagation; keep `T?` as the
   language's optional representation, with `if let`, `?.` and `??`.
3. Add collection and text helpers for compiler passes.
4. Add explicit interpolated strings and a code writer with C byte escaping.
5. Add iterator adapters and a complete compiler-writing example.

Each completed milestone is tested and committed independently.

## Error propagation

Both `read()?` and `try read()` unwrap `ok` or return `err` from the current
function. The input and output success types may differ. Error types must match
exactly; convert errors explicitly when changing diagnostic representations.
User enums may participate if they have exactly two cases, `ok` and `err`, each
with one payload. Case order and generic parameter order do not matter.
Propagation evaluates its input once and executes pending `defer` blocks.

Optional propagation is also supported: in a function returning `U?`, `value?`
unwraps a `T?` or returns `nil`, evaluating once and running defers.

Completed core validation: 36 targeted checks covering propagation, generic
ASTs, enum matching, existing switch regressions, and error diagnostics passed.

## Collections and composition

`import std.collections` provides `map_vec`, `filter_vec`, `fold_vec`,
`find_vec`, `any_vec`, `all_vec`, `slice_vec` and `join_strings`.
Transforms return new vectors; elements retain normal reference semantics.
Slices use clamped half-open bounds. `find_vec` returns `T?`; `any_vec` and
`all_vec` short-circuit (empty inputs return false and true respectively).
Callbacks must not structurally mutate the traversed vector.

`import std.option` provides `option_map`, `option_and_then` and
`option_filter` on the existing `T?` type. `std.result` adds `map_err` and
`and_then` alongside its existing `map` and `unwrap_or` functions.

Callback parameter and return types now participate in generic inference.
Specialized call signatures preserve optional argument wrapping. Closure names
include their enclosing function identity, avoiding collisions between functions
and generic specializations. Collection execution and monomorphization checks:
39 passed with AddressSanitizer and runtime payload checking enabled.

## Interpolated strings and code output

Use explicit `f"..."` templates. Normal strings never interpolate:

```rolang
let message = f"{path}:{line}:{column}: expected {name}";
let braces = f"{{{42}}}"; // {42}
```

Fields accept expressions, including calls, struct literals and nested templates.
`{{` and `}}` emit literal braces. Existing backslash escapes work in text.
Whitespace, UTF-8 and embedded NULs are preserved. Fields evaluate once in
left-to-right order and call ordinary `to_string()` methods. All integer widths,
Bool, f32/f64 and String have conversions; user types can define their own
`to_string() -> String`. Missing conversions and non-String results are errors.
There is no implicit conversion in ordinary strings or arithmetic. Precision and
alignment format specifiers are not part of this syntax.

`import std.code_writer` provides `CodeWriter.new()` (four spaces),
`CodeWriter.with_indent(unit)`, `write`, `line`, `indent`, `dedent`, `clear` and
`to_string`. Embedded newlines are indented, blank lines stay blank, successive
writes continue the same line, and `to_string()` returns an independent snapshot.
`dedent()` returns false at depth zero without changing state.

```rolang
let writer = CodeWriter.new();
writer.line("int main(void) {");
writer.indent();
writer.line(f"return {value};");
writer.dedent();
writer.line("}");
```

`c_quote(text)` returns a complete quoted C byte-string literal. It escapes
quotes, backslashes, control bytes, non-ASCII bytes and question marks; fixed
three-digit octal escapes avoid consuming following digits and avoid trigraphs.
Interpolation performs formatting only: use `c_quote` when embedding text into C.

Validation: 39 interpolation, writer, string-literal and parser checks passed.
The writer tests compile generated C and compare the executed program's exact
output bytes, including NULs and UTF-8.
