# Native generic specialization

The Rolang-written compiler specializes generic functions, structs and enums into
standalone C11. It uses this capability itself: `copy_items<T>` in
`selfhost/generics.rl` copies typed AST lists during specialization.

```rolang
enum Tree<T> {
    case leaf(T);
    case branch(Tree<T>, Tree<T>);
    def first() -> T {
        return switch self {
            case .leaf(let value): value;
            case .branch(let left, _): left.first();
        };
    }
}
def identity<T>(value: T) -> T { return value; }
def main() -> i32 {
    let tree: Tree<i32> = Tree.branch(Tree.leaf(42), Tree.leaf(0));
    return identity(tree.first());
}
```

## Supported behavior

- Functions infer type arguments from arguments and expected return types.
  Return context also permits calls such as `let items: Vec<i32> = empty();`.
  Recursive and mutually recursive calls reuse the same concrete instance.
- Enum constructors accept explicit type arguments or infer them from payloads
  and context. Nullary cases such as `let x: Choice<i32> = Choice.none;` use
  context. Recursive payloads, nested containers and patterns retain their
  concrete types.
- Generic structs use explicit arguments, for example `Box<i32> { value: 42 }`.
  Both nominal types support instance/static methods and methods with their own
  generic parameters. Type and method parameters must have distinct names.
- Concrete types can be nested inside Vec, Dict, optionals, fields, signatures,
  and ordinary transparent aliases. Qualified constructors such as
  `API.Types.Box<i32>.value(42)` work through imports and public re-exports.
  Type parameters shadow module types inside their declaration; aliases retain
  the lexical context of their own declaration. Visibility checks still apply.
- Arguments are evaluated once in source order, including labeled enum payloads.
  Each specialization gets a separate AST copy, including local annotations,
  casts, nested switches and patterns. Instances are cached by declaration and
  concrete type arguments. C emission is deterministic.
- Headers are checked even for unused templates; generic bodies are checked when
  instantiated. Consequently native templates can use concrete-type operations
  that the Python checker requires protocol constraints to express generically.
  This difference is tested separately from common-subset differential programs.

## Implementation and limits

Nominal layouts are registered before their payloads are visited, allowing
recursive types. Function emission processes a growing queue of concrete bodies;
layouts and prototypes are written after this queue has discovered all instances.

Expansion is bounded: 256 new type/function instances, 32 nested nominal layout
expansions, 128 nested type checks, and 8192 bytes per concrete type spelling.
Growing recursion such as `Loop<T> -> Loop<Vec<T>>` produces a diagnostic and
preserves any existing output file.

Generic aliases, constraints/protocols, higher-order functions, explicit function
type arguments, inferred generic struct literals, and arbitrary stdlib source
compilation remain outside the native subset. Numeric conversions and container
invariance follow the existing native rules. The native C backend still uses
process-lifetime allocation; specialization does not add ownership lowering.

## Primary compiler fixes

The differential programs exposed and now cover these Python/LLVM bugs:

- A call could infer an optional return type but be specialized again using a
  different argument-derived type, creating an incompatible LLVM call ABI.
- An expected member type could leak into a generic receiver call's inference.
- A generic enum case without payloads failed to use its expected type.
- Wildcard payloads were unnecessarily loaded, and nested generic enum patterns
  resolved original payload annotations without concrete substitutions.

## Validation

`tests/test_selfhost.py` exercises both Python O0/O3 bootstrap compilers, generated
C O0/O3 under AddressSanitizer and UndefinedBehaviorSanitizer, LLVM differential
execution, AST comparisons, instance reuse, module visibility and diagnostics.
The real CLI rebuild test also runs generic programs through rebuilt compilers
and compares C output through three native generations.

`tests/test_generic_specialization_regressions.py` exercises the primary compiler
fixes at O0 and O3. See `selfhost/examples/generics.rl` for an executable example.

Validated on Apple Silicon macOS:

- **858 native cases covered:** the full sweep passed 843 cases in 1225.57s.
  Its 15 diagnostic assertion failures were resolved by updating obsolete
  non-generic-only wording and restoring the specific unknown-struct diagnostic.
  A **142-case rerun passed** in 207.23s, covering all affected cases, generics and
  three-generation rebuilds under both bootstrap optimization levels.
- **114 distinct primary compiler checks passed**, including the eight new
  O0/O3 regressions after fixes, existing specialization/alias/switch checks,
  and 46 generic/enum/switch runtime checks.
- Both bootstrap levels rebuilt the CLI through three generations with identical
  C output. Stage 2 used ASAN/UBSAN; generated generic programs were also executed
  at C O0/O3 under both sanitizers. These checks do not establish leak freedom.
