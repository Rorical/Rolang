# Chapter 7: Optionals

## What Is an Optional?

An optional represents a value that might not exist. `T?` is shorthand for "either a value of type `T`, or nothing at all". The absence of a value is written `nil`.

```rolang
var name: String? = "Alice";
var age: i32? = nil;
```

Optionals are used wherever a value might legitimately be absent — search results, parsed data, unset configuration, and so on.

## Declaring and Assigning Optionals

Any type can be made optional by appending `?`:

```rolang
let a: i32?    = 42;
let b: f64?    = nil;
let c: String? = "hello";
let d: String? = nil;
```

A non-optional value widens to an optional automatically:

```rolang
let x: i32 = 10;
let y: i32? = x;   // ok — i32 widens to i32?
```

## Checking for Nil

Use a comparison to `nil` to test whether an optional has a value:

```rolang
let value: i32? = 42;

if value != nil {
    println("has a value");
}
```

## If-Let Binding

`if let` unwraps an optional into a new binding that is valid only inside the `if` block. When the optional is `nil`, the `else` branch runs instead:

```rolang
let maybe: String? = "Rolang";

if let s = maybe {
    println(s);   // s is String here, not String?
} else {
    println("nothing");
}
```

Chaining multiple unwraps is done with nested `if let`:

```rolang
let a: i32? = 5;
let b: i32? = 10;

if let x = a {
    if let y = b {
        println_i32(x + y);
    }
}
```

## Guard-Let

When the unwrapped value should be available for the rest of the function rather than just inside an `if` block, use `guard let`:

```rolang
def process(input: String?) -> i32 {
    guard let s = input else { return -1; }
    // s is a plain String here, in scope for the rest of the function
    return s.len() as i32;
}
```

## Optional Chaining

The `?.` operator calls a method or accesses a field on an optional, returning `nil` if the optional is `nil`:

```rolang
let name: String? = "hello";
let upper: String? = name?.uppercased();   // "HELLO"

let empty: String? = nil;
let nothing: String? = empty?.uppercased();   // nil
```

Chains can be arbitrarily long. The whole chain short-circuits to `nil` at the first `nil` it encounters:

```rolang
struct User {
    var profile: Profile?;
}

struct Profile {
    var avatar: Image?;
}

struct Image {
    var url: String;
}

let user: User? = get_user();
let url: String? = user?.profile?.avatar?.url;
```

## Nil-Coalescing (`??`)

The `??` operator provides a default value when an optional is `nil`:

```rolang
let greeting: String? = nil;
let message = greeting ?? "Hello, stranger!";   // "Hello, stranger!"

let score: i32? = 95;
let display = score ?? 0;   // 95
```

`??` chains:

```rolang
let a: i32? = nil;
let b: i32? = nil;
let c: i32? = 42;
let result = a ?? b ?? c ?? 0;   // 42
```

## Built-in Optional Methods

The optional type provides three convenience methods:

```rolang
let v: i32? = 7;
let empty: i32? = nil;

v.is_some();         // true
empty.is_some();     // false

v.is_none();         // false
empty.is_none();     // true

v.unwrap_or(0);      // 7
empty.unwrap_or(0);  // 0
```

## Optional Switch

`switch` on an optional dispatches on both the nil and the Some cases. Use the `.Some(let x)` and `.None` (or `nil`) patterns:

```rolang
let n: i32? = 42;

switch n {
case .Some(let x):
    println_i32(x);
case .None:
    println("nothing");
}
```

The compiler enforces exhaustiveness: both cases (or a `default`) must be present.

## Propagating Nil with `?`

The postfix `?` operator propagates `nil` out of the current function. If the expression is `nil`, the function returns `nil` immediately. The function's own return type must be an optional:

```rolang
def double_length(s: String?) -> i32? {
    let len = s?.len();   // len is i64?
    return len as i32? * 2;
}
```

## Combining Optionals and Error Handling

Optionals are best for "value or nothing". For "value or a reason why not", prefer `Result<T, E>` (covered in [Chapter 8](ch08-error-handling.md)):

```rolang
// Optional: the item is just absent, no explanation needed
def find_user(id: i32) -> User? { ... }

// Result: the operation failed with a specific reason
def load_config(path: String) -> Result<Config, String> { ... }
```

## Summary

- `T?` is an optional that holds either a value of type `T` or `nil`
- `if let name = optional { }` unwraps safely; the binding is only valid inside the block
- `guard let name = optional else { return }` unwraps for the rest of the scope
- `a?.method()` chains optional access; returns `nil` if `a` is nil
- `a ?? default` returns `a`'s value when present, otherwise `default`
- `.is_some()`, `.is_none()`, and `.unwrap_or(default)` are built-in methods
- `switch` on an optional dispatches on `.Some(let x)` and `.None`
