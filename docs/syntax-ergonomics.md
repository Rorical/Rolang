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
