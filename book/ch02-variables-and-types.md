# Chapter 2: Variables, Bindings, and Types

## Declaring Variables

Rolang has two kinds of variable declarations: `let` and `var`.

```rolang
let x: i32 = 42;   // immutable binding
var y: i32 = 10;   // mutable binding
```

**`let`** creates an *immutable binding* — you cannot assign a new value to the variable after declaration. Attempting to do so is a compile error:

```rolang
let x: i32 = 1;
x = 2;   // error: cannot rebind a let binding
```

**`var`** creates a *mutable binding* — you can assign to it as many times as you like:

```rolang
var count: i32 = 0;
count = count + 1;
count = 99;
```

### Bindings vs. Object Mutability

`let` freezes the *binding*, not the object behind it. All structs and enums are heap-allocated, so a `let` binding to a struct just means you cannot point the variable at a different object — the object's fields remain freely writable:

```rolang
struct Counter {
    var n: i32;
    def increment() -> Void { self.n = self.n + 1; }
}

let c = Counter { n: 0 };
c.n = 5;         // ok — mutating the object, not the binding
c.increment();   // ok — method can mutate self
// c = Counter { n: 99 };  // error: rebinding is forbidden
```

### Type Inference

When the type can be inferred from the right-hand side, the annotation may be omitted:

```rolang
let x = 42;       // inferred as i32
let y = 3.14;     // inferred as f64
let s = "hello";  // inferred as String
```

### Default Initialisation

Declaring a variable without an initial value zero-initialises it:

```rolang
var n: i32;        // 0
var flag: Bool;    // false
var ratio: f64;    // 0.0
var name: String;  // empty string
var opt: i32?;     // nil
```

## Primitive Types

### Integer Types

| Type | Size | Range |
|------|------|-------|
| `i8`  | 8-bit  | −128 to 127 |
| `i16` | 16-bit | −32 768 to 32 767 |
| `i32` | 32-bit | −2 147 483 648 to 2 147 483 647 |
| `i64` | 64-bit | −9.2×10¹⁸ to 9.2×10¹⁸ |
| `u8`  | 8-bit  | 0 to 255 |
| `u16` | 16-bit | 0 to 65 535 |
| `u32` | 32-bit | 0 to 4 294 967 295 |
| `u64` | 64-bit | 0 to 1.8×10¹⁹ |

Integer literals are written as plain digits. Negative literals use a unary minus:

```rolang
let a: i32 = 100;
let b: i64 = -9999999999;
let c: u8  = 255;
```

### Floating-Point Types

| Type | Size | Precision |
|------|------|-----------|
| `f32` | 32-bit | ~7 decimal digits |
| `f64` | 64-bit | ~15 decimal digits |

Floating-point literals must contain a decimal point:

```rolang
let pi: f64 = 3.14159265;
let small: f32 = 0.001;
```

### Bool

`Bool` holds `true` or `false`:

```rolang
let flag: Bool = true;
let other = false;
```

### Void

`Void` is the unit type. Functions that produce no result declare `-> Void`. It cannot be stored in a variable.

## Type Conversions

### Implicit Widening

Rolang widens integers automatically when going from a smaller type to a larger compatible type:

| From | To |
|------|----|
| `i8`  | `i16`, `i32`, `i64` |
| `i16` | `i32`, `i64` |
| `i32` | `i64` |
| `u8`  | `u16`, `u32`, `u64`, `i16`, `i32`, `i64` |
| `u16` | `u32`, `u64`, `i32`, `i64` |
| `u32` | `u64`, `i64` |

This widening is applied automatically in function calls, return values, struct field assignment, and binary operations. No cast is needed:

```rolang
def takes_i64(n: i64) -> Void { }

let x: i32 = 5;
takes_i64(x);   // ok — i32 widens to i64 automatically
```

### Explicit Cast (`as`)

All other numeric conversions require an explicit `as` cast:

```rolang
let big: i64 = 1000;
let small: i32 = big as i32;   // narrowing — may truncate
let f: f64 = small as f64;     // int to float
let i: i32 = 3.99 as i32;      // float to int — truncates toward zero
let b: Bool = 1 as Bool;       // non-zero → true
let n: i32 = true as i32;      // true → 1, false → 0
```

**Float-to-integer conversion** saturates: NaN and values outside the integer range produce the minimum or maximum representable value rather than undefined behaviour.

**Integer shift** with a shift amount ≥ bit-width masks the amount to `bitwidth − 1`, giving defined wraparound.

Struct-to-integer, struct-to-struct, and existential-to-concrete casts are rejected at compile time. Use pattern matching for those.

### Type Detection (`is`)

The `is` operator tests whether a value's static type matches a given type:

```rolang
let x: i32 = 42;
let a = x is i32;   // true
let b = x is i64;   // false
```

For optional values, `expr is T` is true when the optional is non-nil.

## Tuples

Tuples group multiple values of different types:

```rolang
let pair: (i32, i32) = (3, 4);
let first = pair.0;
let second = pair.1;
```

Named tuples give fields labels:

```rolang
let point = (x: 10, y: 20);
let px = point.x;
let py = point.y;
```

Tuples are useful for returning multiple values from a function:

```rolang
def min_max(a: i32, b: i32) -> (i32, i32) {
    if a < b { return (a, b); }
    return (b, a);
}

let result = min_max(7, 3);
let lo = result.0;   // 3
let hi = result.1;   // 7
```

## The Ternary Expression

Rolang supports a ternary conditional expression:

```rolang
let sign = x >= 0 ? 1 : -1;
```

## Summary

- `let` binds a name immutably; `var` allows rebinding
- `let` does not make struct fields read-only — it only prevents the variable from pointing at a different object
- Integer types range from `i8` to `u64`; floating-point types are `f32` and `f64`
- Small integers widen implicitly to larger compatible types; all other conversions use `as`
- Tuples hold fixed collections of values; fields are accessed by index (`.0`, `.1`) or label
