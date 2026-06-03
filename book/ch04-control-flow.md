# Chapter 4: Control Flow

## If / Else

The `if` statement executes a block when a condition is true:

```rolang
let x: i32 = 10;

if x > 0 {
    println("positive");
}
```

An optional `else` clause executes when the condition is false:

```rolang
if x > 0 {
    println("positive");
} else {
    println("non-positive");
}
```

Chain multiple conditions with `else if`:

```rolang
if x > 100 {
    println("large");
} else if x > 10 {
    println("medium");
} else {
    println("small");
}
```

### If as an Expression

`if` can produce a value when both branches yield a compatible type:

```rolang
let label = x > 0 ? "pos" : "neg";   // ternary shorthand
```

## While

`while` repeats a block as long as a condition is true:

```rolang
var i: i32 = 0;
while i < 5 {
    println_i32(i);
    i = i + 1;
}
```

Use `break` to exit the loop early and `continue` to skip to the next iteration:

```rolang
var n: i32 = 0;
while n < 100 {
    n = n + 1;
    if n % 2 == 0 { continue; }   // skip even numbers
    if n > 10 { break; }          // stop after 10
    println_i32(n);
}
```

## For … In

`for` iterates over any value that provides an iterator — arrays, `Vec<T>`, `Dict<K,V>` keys, and any struct implementing `__iter__` / `__next__`:

```rolang
// Array literal
for n in [1, 2, 3, 4, 5] {
    println_i32(n);
}

// Vec<T>
import "vec.rl"

let v = Vec<i32>.new();
v.push(10);
v.push(20);
v.push(30);

for item in v {
    println_i32(item);
}
```

Iterating a `Dict<K, V>` yields keys in insertion order:

```rolang
import "dict.rl"

let d = Dict<String, i32>.with_capacity(8, 1);
d.set("a", 1);
d.set("b", 2);

for key in d {
    println(key);   // prints "a", then "b"
}
```

### The Iterator Protocol

Any struct with `__iter__()` and `__next__() -> T?` works in a `for` loop. `__next__` returns `nil` to signal the end:

```rolang
struct Range {
    var current: i32;
    var end: i32;

    def __iter__() -> Range { return self; }

    def __next__() -> i32? {
        if self.current >= self.end { return nil; }
        let val = self.current;
        self.current = self.current + 1;
        return val;
    }
}

let r = Range { current: 0, end: 5 };
for n in r {
    println_i32(n);   // 0 1 2 3 4
}
```

## Switch

`switch` dispatches on a value and matches against a list of cases. It is exhaustive — the compiler rejects a `switch` that does not cover all possible values (or lacks a `default` case).

### Matching Integer Values

```rolang
let code: i32 = 2;

switch code {
case 0:
    println("ok");
case 1:
    println("warning");
case 2:
    println("error");
default:
    println("unknown");
}
```

### Matching Enum Variants

```rolang
enum Direction {
    case North
    case South
    case East
    case West
}

let dir = Direction.North;

switch dir {
case .North: println("going north");
case .South: println("going south");
case .East:  println("going east");
case .West:  println("going west");
}
```

### Destructuring Enum Payloads

When an enum variant carries associated values, bind them with `let`:

```rolang
enum Shape {
    case Circle(radius: f64)
    case Rect(w: f64, h: f64)
    case Point
}

def describe(s: Shape) -> Void {
    switch s {
    case .Circle(let r):
        println("circle with radius " + r.to_string());
    case .Rect(let w, let h):
        println("rect " + w.to_string() + "x" + h.to_string());
    case .Point:
        println("point");
    }
}
```

### Where Guards

Add a `where` clause to filter within a case:

```rolang
switch code {
case let n where n < 0:
    println("negative");
case let n where n == 0:
    println("zero");
default:
    println("positive");
}
```

### The Default Case

`default` matches anything not covered by explicit cases. When all cases are explicitly listed (for example, all variants of an enum), `default` is optional but allowed:

```rolang
switch dir {
case .North: println("north");
default:     println("other");
}
```

## Guard

`guard` is an inverted `if` that requires a condition to be true in order to continue. The `else` clause must exit the current scope:

```rolang
def parse_age(s: String) -> i32 {
    let n = s.to_i32();
    guard n >= 0 else { return -1; }
    guard n <= 150 else { return -1; }
    return n;
}
```

`guard let` unwraps an optional and makes the bound name available in the rest of the current scope:

```rolang
def process(value: String?) -> Void {
    guard let s = value else { return; }
    // s is a plain String here
    println(s);
}
```

## Summary

- `if / else if / else` handles conditional branching
- `while` repeats while a condition holds; `break` and `continue` control flow within loops
- `for item in collection` iterates any value implementing `__iter__` / `__next__`
- `switch` does exhaustive pattern matching on values, enum variants, and destructured payloads
- `guard condition else { exit }` validates preconditions and keeps the happy path unindented
