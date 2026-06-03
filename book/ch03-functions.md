# Chapter 3: Functions

## Defining Functions

Functions are defined with the `def` keyword, followed by a name, a parameter list, and a return type:

```rolang
def add(a: i32, b: i32) -> i32 {
    return a + b;
}
```

Call a function by name, passing arguments positionally:

```rolang
let result = add(3, 4);   // 7
```

## Parameters

Every parameter has a name and an explicit type annotation. There is no default for omitting the type:

```rolang
def greet(name: String, times: i32) -> Void {
    var i: i32 = 0;
    while i < times {
        println(name);
        i = i + 1;
    }
}
```

## Return Types

The return type follows the `->` arrow. Functions that produce no result use `-> Void`:

```rolang
def log(message: String) -> Void {
    println(message);
}
```

## Implicit Return

The last expression in a function body, when written without a semicolon, is the implicit return value:

```rolang
def square(n: i64) -> i64 {
    n * n   // no semicolon — returned automatically
}

def abs(n: i32) -> i32 {
    if n < 0 { return -n; }
    n                       // implicit return for the non-negative path
}
```

When a semicolon is present, the expression is a statement and its value is discarded.

## Early Return

Use `return` to exit a function before reaching the end:

```rolang
def find(items: [i32], target: i32) -> i32 {
    var i: i32 = 0;
    while i < 5 {
        if items[i] == target { return i; }
        i = i + 1;
    }
    return -1;
}
```

## Static Methods

A function defined inside a struct with the `static` qualifier belongs to the type rather than to any instance. It is called on the type name:

```rolang
struct Colour {
    var r: i32;
    var g: i32;
    var b: i32;

    static def black() -> Colour {
        return Colour { r: 0, g: 0, b: 0 };
    }

    static def white() -> Colour {
        return Colour { r: 255, g: 255, b: 255 };
    }
}

let bg = Colour.black();
let fg = Colour.white();
```

## Instance Methods

Methods defined without `static` receive the struct's current value as `self`:

```rolang
struct Rectangle {
    var width: f64;
    var height: f64;

    def area() -> f64 {
        self.width * self.height
    }

    def scale(factor: f64) -> Void {
        self.width  = self.width  * factor;
        self.height = self.height * factor;
    }
}

let r = Rectangle { width: 4.0, height: 3.0 };
let a = r.area();     // 12.0
r.scale(2.0);
let b = r.area();     // 48.0
```

Because all structs are heap-allocated and reference-counted, any method may freely mutate `self` — there is no `mut self` qualifier.

## Generic Functions

Functions can be parameterised over types using angle-bracket syntax:

```rolang
def identity<T>(x: T) -> T {
    return x;
}

def swap<T>(a: T, b: T) -> (T, T) {
    return (b, a);
}

let n = identity(42);
let pair = swap(1, 2);   // (2, 1)
```

Generic functions are covered in depth in [Chapter 12](ch12-generics.md).

## Defer

`defer` schedules a block to run when the current scope exits, regardless of how it exits. Deferred blocks run in reverse declaration order:

```rolang
def process() -> i32 {
    defer { println("second"); }
    defer { println("first"); }
    return 0;
    // prints: first
    //         second
}
```

`defer` is useful for cleanup operations that should always happen:

```rolang
def read_file(path: String) -> i32 {
    let fd = open(path);
    defer { close(fd); }   // guaranteed to run
    // ... work with fd ...
    return 0;
}
```

## Guard

`guard` asserts a condition and exits the current scope if it is false. It must always transfer control out — via `return`, `break`, or `continue`:

```rolang
def process(value: i32?) -> i32 {
    guard let v = value else { return -1; }
    // v is bound here and is a plain i32, not i32?
    return v * 2;
}
```

`guard` is like an early-exit `if` written inside-out: the success path stays at the same indentation level rather than being nested.

## Operator Overloading

Operators are overloaded by defining specially-named methods on a struct or enum:

| Operator | Method name |
|----------|-------------|
| `+` | `__add__` |
| `-` | `__sub__` |
| `*` | `__mul__` |
| `/` | `__div__` |
| `%` | `__mod__` |
| `==` | `__eq__` |
| `<` | `__lt__` |
| `>` | `__gt__` |

```rolang
struct Vector2 {
    var x: f64;
    var y: f64;

    def __add__(other: Vector2) -> Vector2 {
        return Vector2 { x: self.x + other.x, y: self.y + other.y };
    }

    def __eq__(other: Vector2) -> Bool {
        return self.x == other.x && self.y == other.y;
    }
}

let a = Vector2 { x: 1.0, y: 2.0 };
let b = Vector2 { x: 3.0, y: 4.0 };
let c = a + b;   // calls a.__add__(b)
```

## Summary

- Functions are declared with `def name(params) -> ReturnType { body }`
- The last expression without a semicolon is the implicit return value
- `static def` belongs to the type; plain `def` receives `self`
- `defer { }` runs cleanup code at scope exit
- `guard condition else { exit }` provides readable early-exit validation
- Operators are overloaded through dunder methods like `__add__`, `__eq__`
