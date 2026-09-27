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
