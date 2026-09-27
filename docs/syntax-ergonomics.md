# Syntax ergonomics for the Python/LLVM compiler

Implementation sequence: contextual callbacks, generic aliases, value-producing
switches, iterator method chains, guard bindings, destructuring/ranges/slices,
raw/multiline strings, and named/default parameters. Each stage is executable
and tested before its commit. Self-hosted syntax coverage remains separate.

## Contextual callbacks

`map_vec(nodes, { node in node.name })` infers callback inputs from the collection
and function signature. An expression body returns its final value. Typed locals
also supply context: `let f: (i32) -> i32 = { x in x + 1 };`. Explicit annotations
remain available. A callback without enough context receives a type diagnostic.
Concrete return contexts support optional lifting and `?` propagation.

## Generic aliases

```
typealias ParseResult<T> = Result<T, String>;
typealias Option<T> = T?;
typealias Callback<T, U> = (T) -> U;
```

Aliases remain transparent. Type arguments are checked for arity, recursive
aliases are rejected, and exported aliases cannot expose private concrete types.
Alias targets resolve in their declaration scope. Method signatures can also
return ordinary aliases (fixing a previously recorded static-method issue).

Initial validation: 12 callback/collection/iterator checks and 18 alias checks
passed, including O0/O3 execution.

## Switch values and guard bindings

```
guard let token = next_token() else { return nil; }
let text = switch token {
    case .number(let value): f"{value}";
    case .name(let name): name;
};
```

Switch expressions require exhaustive coverage and compatible value types.
Guards and nested patterns work as in switch statements; the scrutinee runs
once. Each arm ends with a semicolon. A surrounding type annotation provides
context for the arm values. `guard let` bindings remain available after the
statement; the `else` block must leave the current path and cannot see the new
binding. Existing `defer` cleanup also runs on those exits.

## Iterator methods and generic methods

```
import std.iterator
let names = nodes.iter()
    .filter({ node in node.is_public })
    .map({ node in node.name })
    .collect();
```

`Iter<T>` also provides `take`, `zip`, `enumerate`, and `fold`. The adapters are
lazy and single-pass. Importing `std.iterator` adds `Vec<T>.iter()` via an
extension. Generic methods and generic extensions are instantiated when called:

```
struct Box<T> {
    var value: T;
    def map<U>(f: (T) -> U) -> U { return f(self.value); }
}
pub extension<T> Vec<T> {
    pub def first_or(fallback: T) -> T {
        if self.len() == 0 { return fallback; }
        return self.get(0);
    }
}
```

## Destructuring, ranges, and slices

```
let (node, span) = parse_node();
var ((line, column), _) = location();
for (key, value) in entries { consume(key, value); }
for i in 0..<tokens.len() { consume(tokens.get(i)); }
let selected = tokens[start..<end];
let prefix = source[0...3];
```

Nested tuple bindings evaluate their initializer once. `var` bindings can be
reassigned; `_` discards a component. Range bounds are `i32`: `..<` excludes the
upper bound and `...` includes it. Descending ranges are empty, and each loop
creates an independent cursor. Inclusive maximum-integer bounds do not overflow.

Slices clamp bounds to the container length and return copies. Vector copies
retain the selected elements, so referenced objects remain shared. String slice
indices count bytes, like `byte_at`; they do not count Unicode characters.
Slice assignment and omitted bounds are not supported.

## Raw and multiline strings

```
let path = r"C:\compiler\cache";
let template = """int main(void) {
    return 0;
}
""";
let generated = f"""int main(void) {{
    return {value};
}}
""";
```

`r"..."` and `r"""..."""` preserve backslashes literally. Ordinary triple-quoted
strings process escapes; `f"""..."""` also processes interpolation. Newlines and
indentation are preserved exactly. In interpolated strings, `{{` and `}}` emit
literal braces.

## Named arguments and defaults

```
def emit(node: String, indent: i32 = 2) -> String { return f"{node}:{indent}"; }
let a = emit("node", indent: 4);
let b = emit(node: "node");
```

Ordinary parameters accept either positional arguments or their internal name
as a label. Explicit external labels (`to value: i32`) remain required. Arguments
follow declaration order; only trailing defaults may be omitted. Defaults run at
each call that omits them and resolve in declaration scope. They cannot reference
`self` or the callee's parameters. Function values do not carry labels/defaults.

## Validation

The added programs execute at both O0 and O3. Tests cover success paths, invalid
program diagnostics, evaluation count, captures, optional propagation, and cleanup.
Validation for this implementation includes:

- 229 parser, HIR/MIR, monomorphization, contextual-literal, interpolation,
  compiler-standard-library, collection, and iterator regression checks.

- 21 switch-expression, guard-binding, raw/multiline-string, and named/default
  parameter checks.
- 14 range/destructuring, iterator-chain, and generic-alias/module checks with
  AddressSanitizer and runtime payload validation. The module test removes the
  source before importing the generated `.rlm`.
- 15 contextual-callback, alias, chain, and collection-inference checks.
- Two native CLI bootstrap checks, including three compiler generations and
  identical generated C.

The tiny compiler example also compiles its emitted C with `-Wall -Werror` and
runs that executable, checking its exit value of 42.
