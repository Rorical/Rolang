# Chapter 21: Testing

Rolang test targets are ordinary programs. A test binary that exits with code 0 passes; any non-zero exit code is a failure. This simple contract means you can use any logic to drive tests — the standard library provides assertion helpers to make this convenient.

## Declaring a Test Target

Add a `[[test]]` section to `rolang.toml`:

```toml
[[test]]
name = "unit"
path = "tests/unit.rl"
```

Multiple test targets are allowed:

```toml
[[test]]
name = "unit"
path = "tests/unit.rl"

[[test]]
name = "integration"
path = "tests/integration.rl"
```

## Running Tests

```bash
rolang test              # run all test targets
rolang test unit         # run only targets whose name contains "unit"
rolang test -v           # show build output and binary stdout
```

## Writing a Test File

A test file is a normal Rolang program whose `main` returns an exit code. Return 0 to pass, any other value to fail:

```rolang
import "test.rl"

def add(a: i32, b: i32) -> i32 { a + b }

def main() -> i32 {
    var failures: i32 = 0;

    failures = failures + assert_eq_i32(5, add(2, 3));
    failures = failures + assert_eq_i32(0, add(0, 0));
    failures = failures + assert_eq_i32(-1, add(1, -2));
    failures = failures + assert_true(add(1, 1) == 2);

    return failures;
}
```

Each assertion function returns 0 on success and 1 on failure. Sum them up and return the total — a non-zero total fails the test.

## The `test.rl` Assertion Functions

```rolang
import "test.rl"
```

| Function | Passes when |
|----------|-------------|
| `assert_eq_i32(expected, actual) -> i32` | `expected == actual` |
| `assert_eq_i64(expected, actual) -> i64` | `expected == actual` |
| `assert_true(condition) -> i32` | `condition == true` |
| `assert_false(condition) -> i32` | `condition == false` |

All functions return 0 for pass, 1 for fail.

## Testing Structs and Methods

```rolang
import "test.rl"

struct Stack<T> {
    var items: Vec<T>;

    static def new() -> Stack<T> {
        Stack<T> { items: Vec<T>.new() }
    }

    def push(item: T) -> Void { self.items.push(item); }
    def pop()  -> T?  { if self.items.len() == 0 { return nil; } return self.items.pop(); }
    def len()  -> i32 { self.items.len() }
    def is_empty() -> Bool { self.items.len() == 0 }
}

def main() -> i32 {
    var f: i32 = 0;

    let s = Stack<i32>.new();
    f = f + assert_true(s.is_empty());
    f = f + assert_eq_i32(0, s.len());

    s.push(1);
    s.push(2);
    s.push(3);
    f = f + assert_eq_i32(3, s.len());
    f = f + assert_false(s.is_empty());

    if let top = s.pop() {
        f = f + assert_eq_i32(3, top);
    } else {
        f = f + 1;   // unexpected nil
    }

    f = f + assert_eq_i32(2, s.len());

    return f;
}
```

## Testing Error Handling

```rolang
import "test.rl"
import "result.rl"

def divide(a: i32, b: i32) -> Result<i32, String> {
    if b == 0 { return Result.err(error: "division by zero"); }
    return Result.ok(value: a / b);
}

def main() -> i32 {
    var f: i32 = 0;

    // Success case
    switch divide(10, 2) {
    case .ok(let v):  f = f + assert_eq_i32(5, v);
    case .err(let e): f = f + 1;
    }

    // Error case
    f = f + assert_true(is_err(divide(10, 0)));

    return f;
}
```

## Integration Tests

Integration tests live in a separate file and can import the whole project:

**tests/integration.rl**:
```rolang
import "src/main.rl"
import "test.rl"

def main() -> i32 {
    var f: i32 = 0;
    // ... test high-level behaviour ...
    return f;
}
```

Add it to the manifest:

```toml
[[test]]
name = "integration"
path = "tests/integration.rl"
```

## Testing Async Functions

Async test functions work the same way — declare `main` as async:

```rolang
import "test.rl"

def fetch(n: i32) async -> i32 { return n * 2; }

def main() async -> i32 {
    var f: i32 = 0;
    let result = await fetch(5);
    f = f + assert_eq_i32(10, result);
    return f;
}
```

## Organising Tests

For larger projects, split tests by concern:

```toml
[[test]]
name = "parser-tests"
path = "tests/parser_tests.rl"

[[test]]
name = "model-tests"
path = "tests/model_tests.rl"

[[test]]
name = "integration"
path = "tests/integration.rl"
```

Run a single group:

```bash
rolang test parser
```

## A Complete Test Example

```rolang
import "test.rl"

// --- Subject under test ---

def fizzbuzz(n: i32) -> String {
    if n % 15 == 0 { return "FizzBuzz"; }
    if n % 3  == 0 { return "Fizz"; }
    if n % 5  == 0 { return "Buzz"; }
    return n.to_string();
}

// --- Tests ---

def main() -> i32 {
    var f: i32 = 0;

    f = f + assert_true(fizzbuzz(1).equals("1"));
    f = f + assert_true(fizzbuzz(3).equals("Fizz"));
    f = f + assert_true(fizzbuzz(5).equals("Buzz"));
    f = f + assert_true(fizzbuzz(15).equals("FizzBuzz"));
    f = f + assert_true(fizzbuzz(9).equals("Fizz"));
    f = f + assert_true(fizzbuzz(10).equals("Buzz"));
    f = f + assert_true(fizzbuzz(30).equals("FizzBuzz"));
    f = f + assert_true(fizzbuzz(7).equals("7"));

    return f;
}
```

Run it:

```bash
rolang test
```

Expected output:

```
test fizzbuzz ... ok

test result: 1/1 passed
```

## Summary

- Test targets are declared in `rolang.toml` under `[[test]]`
- A test passes when its `main` returns 0; any other exit code is a failure
- Import `test.rl` for assertion helpers: `assert_eq_i32`, `assert_true`, `assert_false`
- Run tests with `rolang test [filter]`; use `-v` for verbose output
- Async test functions just need `async` on `main`
