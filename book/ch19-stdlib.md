# Chapter 19: The Standard Library

The standard library ships bundled with the compiler and is always available without any installation step. `Vec<T>`, `Dict<K,V>`, and `String` are injected implicitly. Everything else requires an explicit `import`.

---

## Implicit Modules (no import needed)

### `string.rl` — `String`

A heap-allocated, ARC-managed UTF-8 string. See [Chapter 10](ch10-strings.md) for the full API.

Key methods: `.len()`, `.concat()` / `+`, `.split()`, `.trim()`, `.contains()`, `.find()`, `.replace()`, `.substring()`, `.to_i32()`, `.to_f64()`

Numeric extensions (also from `string.rl`): `n.to_string()` on `i32`, `i64`, and `f64`.

### `vec.rl` — `Vec<T>`

A growable, heap-allocated sequence. See [Chapter 9](ch09-collections.md) for the full API.

```rolang
let v = Vec<i32>.new();
v.push(1); v.push(2); v.push(3);
let n = v.len();     // 3
let x = v.get(0);    // 1
v.set(0, 99);
let last = v.pop();  // 3
for item in v { println_i32(item); }
```

### `dict.rl` — `Dict<K, V>`

A hash map with insertion-ordered iteration. See [Chapter 9](ch09-collections.md) for the full API.

```rolang
let d = Dict<String, i32>.with_capacity(16, 1);
d.set("x", 10);
let v: i32? = d.get("x");   // 10
d.contains("x");             // true
d.len();                     // 1
for key in d { println(key); }
```

---

## I/O — `io.rl`

```rolang
import "io.rl"
```

| Function | Description |
|----------|-------------|
| `print(s: String)` | Print string without newline |
| `println(s: String)` | Print string with newline |
| `print_i32(n: i32)` | Print integer without newline |
| `println_i32(n: i32)` | Print integer with newline |
| `println_i64(n: i64)` | Print 64-bit integer with newline |

---

## Mathematics — `math.rl`

```rolang
import "math.rl"
```

### Free functions

| Function | Description |
|----------|-------------|
| `sqrt(x: f64) -> f64` | Square root |
| `sin(x: f64) -> f64` | Sine (radians) |
| `cos(x: f64) -> f64` | Cosine (radians) |
| `atan2(y, x: f64) -> f64` | Two-argument arctangent |
| `pi() -> f64` | 3.14159... |
| `abs_i32(x) -> i32` | Absolute value |
| `abs_i64(x) -> i64` | Absolute value |
| `min_i32(a, b) -> i32` | Minimum |
| `max_i32(a, b) -> i32` | Maximum |
| `min_i64(a, b) -> i64` | Minimum |
| `max_i64(a, b) -> i64` | Maximum |
| `pow_i32(base, exp) -> i32` | Integer power |

### Extension methods

```rolang
(-5).abs()          // 5
(2).pow(10)         // 1024
(7).min(3)          // 3
(7).max(3)          // 7
(5).clamp(0, 10)    // 5
(5).is_positive()   // true

(4.0).sqrt()        // 2.0
(0.0).sin()         // 0.0
(1.0).abs()         // 1.0
```

All implementations are pure Rolang (Newton's method, Taylor series) — no `libm` dependency.

---

## Error Handling — `result.rl`

```rolang
import "result.rl"
```

```rolang
pub enum Result<T, E> {
    case ok(value: T)
    case err(error: E)
}
```

| Function | Description |
|----------|-------------|
| `is_ok(r) -> Bool` | True if `ok` variant |
| `is_err(r) -> Bool` | True if `err` variant |
| `unwrap_or(r, default) -> T` | Value or default |
| `map(r, f) -> Result<U, E>` | Transform the ok value |

---

## Collections — `set.rl`

```rolang
import "set.rl"
```

`Set<T>` stores unique values:

```rolang
let s = Set<String>.new();
s.insert("alpha");
s.insert("beta");
s.insert("alpha");   // ignored
s.contains("beta");  // true
s.len();             // 2
s.remove("alpha");
```

---

## Linked List — `linked_list.rl`

```rolang
import "linked_list.rl"
```

A singly-linked list with O(1) prepend:

```rolang
let list = LinkedList<i32>.new();
list.prepend(3);
list.prepend(2);
list.prepend(1);
// list is now 1 → 2 → 3
let head = list.head();   // 1?
```

---

## Iterators — `iter.rl`

```rolang
import "iter.rl"
```

The iterator protocol. Any struct implementing `__iter__()` and `__next__() -> T?` works in a `for` loop. `iter.rl` provides helper types and free functions for building iterators:

```rolang
// Range-style iteration is idiomatic with while loops:
var i: i32 = 0;
while i < 10 { i = i + 1; }
```

Custom iterator structs need only `__iter__` and `__next__`. See [Chapter 4](ch04-control-flow.md) for an example.

---

## File System — `fs.rl`

```rolang
import "fs.rl"
```

Basic file I/O helpers for reading and writing files as strings or byte sequences.

---

## Paths — `path.rl`

```rolang
import "path.rl"
```

String-based path manipulation: joining segments, extracting extensions, resolving relative paths.

---

## Characters — `char.rl`

```rolang
import "char.rl"
```

Character classification functions operating on `i32` code points:

| Function | Description |
|----------|-------------|
| `char_is_digit(c) -> Bool` | ASCII digit (0–9) |
| `char_is_alpha(c) -> Bool` | ASCII letter |
| `char_is_space(c) -> Bool` | Whitespace |
| `char_is_upper(c) -> Bool` | Uppercase letter |
| `char_is_lower(c) -> Bool` | Lowercase letter |
| `char_to_upper(c) -> i32` | Convert to uppercase |
| `char_to_lower(c) -> i32` | Convert to lowercase |

---

## Formatting — `fmt.rl`

```rolang
import "fmt.rl"
```

String formatting helpers for composing output with mixed types.

---

## Process — `process.rl`

```rolang
import "process.rl"
```

Process utilities including exit helpers:

```rolang
exit(0);      // exit with code 0
exit_fail();  // exit with code 1
```

---

## Panic — `panic.rl`

```rolang
import "panic.rl"
```

Explicit panic — aborts with a message:

```rolang
panic("unreachable state reached");
```

---

## Testing — `test.rl`

```rolang
import "test.rl"
```

Simple assertion functions for test binaries. Return 0 for pass, non-zero for fail. See [Chapter 21](ch21-testing.md).

| Function | Description |
|----------|-------------|
| `assert_eq_i32(expected, actual) -> i32` | Equality check |
| `assert_eq_i64(expected, actual) -> i32` | Equality check |
| `assert_true(condition) -> i32` | Truth check |
| `assert_false(condition) -> i32` | Falsity check |

---

## ByteString — `bytestring.rl`

```rolang
import "bytestring.rl"
```

A `Vec<u8>`-backed byte buffer for low-level string manipulation. Useful when you need raw byte access to string content.

---

## Module Index

| Module | Key Exports |
|--------|-------------|
| *(implicit)* | `String`, `Vec<T>`, `Dict<K,V>` |
| `io.rl` | `print`, `println`, `print_i32`, `println_i32`, `println_i64` |
| `math.rl` | `sqrt`, `sin`, `cos`, `atan2`, `pi`, extension methods |
| `result.rl` | `Result<T,E>`, `is_ok`, `is_err`, `unwrap_or`, `map` |
| `set.rl` | `Set<T>` |
| `iter.rl` | Iterator protocol helpers |
| `linked_list.rl` | `LinkedList<T>` |
| `array.rl` | Fixed-array utilities |
| `char.rl` | `char_is_digit`, `char_is_alpha`, `char_to_upper`, … |
| `fmt.rl` | Formatting helpers |
| `fs.rl` | File I/O |
| `path.rl` | Path manipulation |
| `process.rl` | `exit`, `exit_fail` |
| `panic.rl` | `panic` |
| `test.rl` | `assert_eq_i32`, `assert_true`, … |
| `bytestring.rl` | `ByteString` (Vec\<u8\> wrapper) |
