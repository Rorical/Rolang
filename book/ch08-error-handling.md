# Chapter 8: Error Handling

## The Result Type

Rolang's primary error-handling mechanism is the `Result<T, E>` enum from the standard library:

```rolang
enum Result<T, E> {
    case ok(value: T)
    case err(error: E)
}
```

A function that might fail returns `Result<T, E>` instead of just `T`. The caller must handle both the success and the failure case explicitly.

## Returning Results

Construct a success or failure value with the enum constructors:

```rolang
import "result.rl"

def parse_positive(n: i32) -> Result<i32, String> {
    if n < 0 {
        return Result.err(error: "expected a positive number");
    }
    return Result.ok(value: n);
}
```

## Handling Results

Use `switch` to handle both outcomes:

```rolang
let r = parse_positive(-5);

switch r {
case .ok(let value):
    println("got " + value.to_string());
case .err(let message):
    println("error: " + message);
}
```

## The `try` Operator

`try` is the early-return propagation operator. When the expression is `Result.err(...)`, `try` immediately returns that error from the enclosing function. When it is `Result.ok(value: v)`, it evaluates to `v`.

The enclosing function's return type must itself be a `Result`:

```rolang
def double_positive(n: i32) -> Result<i32, String> {
    let p = try parse_positive(n);   // returns Err if n < 0
    return Result.ok(value: p * 2);
}
```

The postfix `?` operator is equivalent to `try`:

```rolang
def double_positive(n: i32) -> Result<i32, String> {
    let p = parse_positive(n)?;
    return Result.ok(value: p * 2);
}
```

Both forms desugar to the same code. Choose whichever reads more clearly.

## Chaining Operations

`try` / `?` makes it easy to chain operations that each might fail, while keeping the error path out of the way:

```rolang
def load_and_validate(path: String) -> Result<Config, String> {
    let text   = try read_file(path);
    let config = try parse_config(text);
    let valid  = try validate(config);
    return Result.ok(value: valid);
}
```

If any step returns `Err`, the function returns that error immediately. The success path reads like straight-line code.

## Standard Library Helpers

The `result.rl` module provides several utility functions:

```rolang
import "result.rl"

let r: Result<i32, String> = Result.ok(value: 42);

is_ok(r);                // true
is_err(r);               // false
unwrap_or(r, 0);         // 42 — returns the value, or a default on Err

let doubled = map(r, (n: i32) -> i32 { n * 2 });  // Result.ok(value: 84)
```

For an `Err` result, `unwrap_or` returns the default:

```rolang
let err: Result<i32, String> = Result.err(error: "oops");
unwrap_or(err, -1);   // -1
```

## The `throws` Annotation

Functions can be annotated with `throws` to signal that they produce a `Result`. This is purely informational — `try` and `?` work identically with or without it:

```rolang
def risky() throws -> Result<i32, String> {
    return Result.ok(value: 99);
}
```

The compiler enforces that a `throws`-annotated function returns a `Result`-shaped type.

## Custom Error Types

The error type `E` can be any type — a `String`, a custom enum, or a struct:

```rolang
enum IoError {
    case NotFound(path: String)
    case PermissionDenied(path: String)
    case Unknown(code: i32)
}

def open_file(path: String) -> Result<FileHandle, IoError> {
    // ... open logic ...
    return Result.err(error: IoError.NotFound(path: path));
}

def run() -> Result<Void, IoError> {
    let f = try open_file("/etc/nonexistent");
    // ... use f ...
    return Result.ok(value: ());
}
```

Using an enum for errors lets you match on the specific failure:

```rolang
let result = open_file("/etc/passwd");

switch result {
case .ok(let handle):
    // use handle
case .err(let e):
    switch e {
    case .NotFound(let p):
        println("not found: " + p);
    case .PermissionDenied(let p):
        println("permission denied: " + p);
    case .Unknown(let code):
        println("error code: " + code.to_string());
    }
}
```

## Optionals vs. Results

| Use | When |
|-----|------|
| `T?` | The value is simply absent — no reason needed |
| `Result<T, E>` | The operation failed and the reason matters to the caller |

```rolang
// Optional: absence is normal
def find_user_by_email(email: String) -> User? { ... }

// Result: failure needs a diagnosis
def create_user(email: String) -> Result<User, CreateError> { ... }
```

## Summary

- `Result<T, E>` represents either success (`ok(value:)`) or failure (`err(error:)`)
- Handle both with `switch`, or propagate errors with `try expr` / `expr?`
- `try` / `?` immediately return the error from the enclosing function
- `throws` annotates a function to declare it returns a Result; it does not change semantics
- Use a custom error enum when callers need to distinguish between failure modes
