# Native enums and switch

The Rolang-written compiler now compiles enums with associated values,
recursive payloads, methods, and module-scoped identities into standalone C11.
`Pattern` in `selfhost/ast.rl` is itself a recursive enum. Its parser, module-index
remapping, AST serializer, and backend use constructors and switch patterns.
Expressions and statements still use their existing flat indexed representation;
converting those nodes is a later refactoring step.

```rolang
enum Expr {
    case number(i32);
    case add(left: Expr, right: Expr);

    def evaluate() -> i32 {
        return switch self {
            case .number(let n): n;
            case .add(let a, let b): a.evaluate() + b.evaluate();
        };
    }
}
```

## Semantics

- Constructors accept positional and labeled payloads, evaluate arguments once in
  source order, and check payload types, arity, unknown labels and duplicates.
  Bare `Type.case` constructs a case without payloads. Enum types work inside
  structs, Vec, Dict, optionals, signatures, aliases and imported namespaces.
- Switch statements and value expressions evaluate their subject once. Arms run
  in source order with no fallthrough; `where` guards run after the pattern binds.
  A rejected guard proceeds to the next arm. Nested switches retain independent
  subjects, result variables, scopes and branch targets.
- Patterns support recursive `.case(...)`, `let`/`var` bindings, `_`, and numeric,
  Boolean and String literals. Strings compare bytes by content, including NUL.
  Pattern bindings stay inside their arm; duplicate names are errors.
- A containing enum tag is tested before any associated value is loaded, including
  nested patterns. Generated payload fields have their actual C types. Enums are
  immutable references using the existing process-lifetime allocation arena.
- Enum and Bool switches must be exhaustive. A guarded case or a case with a
  restrictive payload pattern does not cover an entire variant. An unguarded
  wildcard/binding or `default` covers all remaining values. Exhaustiveness is
  conservative: use a fallback when nested literal patterns partition a payload.
- Switch expressions require a concrete compatible result type and complete
  coverage, also for numeric and String subjects. An expected type from a return,
  local annotation or call flows into each branch, including numeric literals
  and optional lifting. Statement switches over open scalar domains may omit a
  fallback, but then do not prove a function returns on every path.
- `break`/`continue` retain their surrounding loop meaning; a switch introduces
  no loop. All arms are checked, including unreachable arms.

## Current boundaries

Generic enums are covered by [native specialization](selfhost-generics.md).
OR/multiple patterns per arm, tuple patterns, and optional
`.Some`/`.None` patterns remain unsupported. Use `if let`/`guard let` for optionals.
General function named/default arguments remain unsupported; labels are accepted
for enum constructors. Switch value branches require explicit semicolons.

The native parser additionally accepts contextual `.case` construction in annotated
locals, returns and arguments. The Python frontend currently requires `Type.case`
construction, so compiler sources and differential examples use qualified names.
This shorthand is not used to establish bootstrap parity.

## Primary compiler fixes discovered during adoption

Pattern keywords now require identifier boundaries: `let variants`, `let letter`
and `var variable` retain their full names. A statement switch at the end of a
Void function is no longer parsed as a value-returning switch. Value-returning
functions and expression closures retain their implicit trailing-switch behavior.

## Validation

The native suite covers C O0/O3 execution, AddressSanitizer/UndefinedBehaviorSanitizer,
Python/LLVM differential output, full AST comparisons, diagnostics that preserve an
existing output file, module aliases/re-exports, and three-generation self-rebuilds.
The rebuilt compiler compiles enum programs as well as the existing regression
samples. See `tests/test_selfhost.py` and `tests/test_switch_expressions.py`.

Validated on Apple Silicon macOS:

- **792 native checks passed**: 700 existing regression checks (1147.11s),
  88 focused enum/frontend/bootstrap checks (245.90s), and four additional
  O0/O3 bootstrap checks for contextual constructors and inactive nested payloads.
  The complete suite now collects 792 cases.
- **45 primary compiler checks passed**: 12 parser/switch/guard checks (46.87s)
  and 33 existing enum/switch/pattern checks (82.18s).
- Both Python bootstrap optimization levels rebuilt the real CLI through three
  native generations with byte-identical generated C. Stage 2 used ASAN/UBSAN;
  rebuilt compilers compiled and ran the recursive enum and guarded-switch samples.
- Enum execution checks compiled generated C at O0 and O3 with ASAN/UBSAN and
  compared results with Python/LLVM. These checks do not establish leak freedom
  or change the native backend's process-lifetime allocation model.
