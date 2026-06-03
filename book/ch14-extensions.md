# Chapter 14: Extensions

An `extension` adds new methods to an existing type without modifying its original definition. This works for your own types, standard library types, and even built-in types.

## Basic Extension Syntax

```rolang
struct Point {
    var x: f64;
    var y: f64;
}

extension Point {
    def magnitude() -> f64 {
        sqrt(self.x * self.x + self.y * self.y)
    }

    def distance_to(other: Point) -> f64 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        sqrt(dx * dx + dy * dy)
    }
}
```

The extended methods are available on all `Point` values, exactly as if they were defined in the original struct:

```rolang
let p = Point { x: 3.0, y: 4.0 };
let m = p.magnitude();   // 5.0
```

## Extending Built-in Types

Extensions can be applied to the numeric primitives and other built-in types. The standard library uses this extensively:

```rolang
extension i32 {
    def is_even() -> Bool { self % 2 == 0 }
    def is_odd()  -> Bool { self % 2 != 0 }
    def squared() -> i32  { self * self }
}

let n: i32 = 7;
n.is_odd();    // true
n.squared();   // 49
```

Extension methods on `i32`, `i64`, and `f64` are already provided by the `math.rl` standard library module:

```rolang
import "math.rl"

let x: i32 = -5;
x.abs();         // 5
x.clamp(-10, 10);// -5
(2).pow(8);      // 256

let y: f64 = 4.0;
y.sqrt();        // 2.0
y.sin();
y.cos();
```

## Protocol Conformance via Extension

Extensions are the standard way to add protocol conformance to a type that was defined elsewhere — your own type in a different file, or a standard library type:

```rolang
protocol Describable {
    def describe() -> String;
}

// Add conformance to an existing struct without modifying it
extension Point: Describable {
    def describe() -> String {
        "(" + self.x.to_string() + ", " + self.y.to_string() + ")"
    }
}

def print_item<T: Describable>(item: T) -> Void {
    println(item.describe());
}

let p = Point { x: 1.0, y: 2.0 };
print_item(p);   // (1.0, 2.0)
```

## Extensions in Separate Files

A common pattern is to define a type's core fields in one file and add protocol conformance or domain-specific helpers in another:

**geometry.rl** — core type:
```rolang
pub struct Rect {
    pub var x: f64;
    pub var y: f64;
    pub var w: f64;
    pub var h: f64;
}
```

**rendering.rl** — rendering-specific helpers:
```rolang
import "geometry.rl"

extension Rect {
    def centre_x() -> f64 { self.x + self.w * 0.5 }
    def centre_y() -> f64 { self.y + self.h * 0.5 }

    def intersects(other: Rect) -> Bool {
        self.x < other.x + other.w &&
        self.x + self.w > other.x &&
        self.y < other.y + other.h &&
        self.y + self.h > other.y
    }
}
```

## Static Methods in Extensions

Extensions can also add static methods:

```rolang
extension Point {
    static def origin() -> Point {
        Point { x: 0.0, y: 0.0 }
    }

    static def unit_x() -> Point {
        Point { x: 1.0, y: 0.0 }
    }
}

let o = Point.origin();
```

## Limitations

- Extensions cannot add stored fields (fields with storage); only methods
- Extensions cannot override existing methods — they only add new ones
- An extension cannot weaken the visibility of an existing member

## A Complete Example

```rolang
import "math.rl"

struct Vec2 {
    var x: f64;
    var y: f64;
}

// Core arithmetic — in the struct definition
extension Vec2 {
    def __add__(other: Vec2) -> Vec2 {
        Vec2 { x: self.x + other.x, y: self.y + other.y }
    }

    def __sub__(other: Vec2) -> Vec2 {
        Vec2 { x: self.x - other.x, y: self.y - other.y }
    }

    def __mul__(scalar: f64) -> Vec2 {
        Vec2 { x: self.x * scalar, y: self.y * scalar }
    }
}

// Geometry helpers — added via extension
extension Vec2 {
    def length() -> f64 {
        sqrt(self.x * self.x + self.y * self.y)
    }

    def dot(other: Vec2) -> f64 {
        self.x * other.x + self.y * other.y
    }

    def normalize() -> Vec2 {
        let len = self.length();
        self * (1.0 / len)
    }

    static def zero() -> Vec2 { Vec2 { x: 0.0, y: 0.0 } }
    static def one()  -> Vec2 { Vec2 { x: 1.0, y: 1.0 } }
}

def main() -> i32 {
    let a = Vec2 { x: 3.0, y: 0.0 };
    let b = Vec2 { x: 0.0, y: 4.0 };
    let c = a + b;
    let len = c.length();   // 5.0
    let n = c.normalize();
    return 0;
}
```

## Summary

- `extension TypeName { }` adds methods to any existing type
- Extensions work on your own types, standard library types, and built-in types
- Protocol conformance is commonly added through extensions: `extension Type: Protocol { }`
- Extensions can add both instance methods and static methods
- Extensions cannot add stored fields or override existing methods
