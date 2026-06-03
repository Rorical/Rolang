# Chapter 5: Structs

## Defining a Struct

A struct groups related fields under a single named type:

```rolang
struct Point {
    var x: f64;
    var y: f64;
}
```

All fields must be declared with `var`. Field declarations may optionally end with a semicolon.

## Creating Instances

Use named-field initialisation syntax — all fields must be supplied:

```rolang
let p = Point { x: 3.0, y: 4.0 };
```

## Accessing and Mutating Fields

Fields are accessed with the dot operator:

```rolang
let px = p.x;   // 3.0
```

Fields can be mutated regardless of whether the variable binding is `let` or `var`, because `let` only freezes the binding, not the object:

```rolang
let p = Point { x: 0.0, y: 0.0 };
p.x = 10.0;   // ok
```

## Methods

Define methods inside the struct body with `def`. The method receives the current instance as `self`:

```rolang
struct Circle {
    var x: f64;
    var y: f64;
    var radius: f64;

    def area() -> f64 {
        3.14159265 * self.radius * self.radius
    }

    def contains(px: f64, py: f64) -> Bool {
        let dx = px - self.x;
        let dy = py - self.y;
        let dist_sq = dx * dx + dy * dy;
        dist_sq <= self.radius * self.radius
    }

    def scale(factor: f64) -> Void {
        self.radius = self.radius * factor;
    }
}

let c = Circle { x: 0.0, y: 0.0, radius: 5.0 };
let a = c.area();
c.scale(2.0);
```

Methods can mutate `self` without any special annotation. All struct values live on the heap under ARC, so mutations affect the shared object.

## Static Methods

`static def` methods belong to the type, not to any instance. They are called on the type name and are commonly used as named constructors:

```rolang
struct Colour {
    var r: u8;
    var g: u8;
    var b: u8;

    static def from_rgb(r: u8, g: u8, b: u8) -> Colour {
        return Colour { r: r, g: g, b: b };
    }

    static def red()   -> Colour { Colour { r: 255, g: 0, b: 0 } }
    static def green() -> Colour { Colour { r: 0, g: 255, b: 0 } }
    static def blue()  -> Colour { Colour { r: 0, g: 0, b: 255 } }

    def luminance() -> f64 {
        0.299 * (self.r as f64) + 0.587 * (self.g as f64) + 0.114 * (self.b as f64)
    }
}

let red   = Colour.red();
let lum   = red.luminance();
let teal  = Colour.from_rgb(0, 128, 128);
```

## Field Visibility

By default, fields are internal — visible only within the same compilation unit. Mark a field `pub` to make it accessible from other modules:

```rolang
pub struct Person {
    pub var name: String;
    pub var age: i32;
    var internal_id: i64;   // not visible outside this file
}
```

Methods follow the same rule. `pub def` is accessible from other modules; plain `def` is not.

## The `__release__` Method

`__release__` is a special method called by the runtime exactly once, when the last reference to the object is released. It runs before the runtime frees the object's fields, so `self` is fully readable inside the body.

```rolang
struct FileHandle {
    var fd: i32;

    static def open(path: String) -> FileHandle {
        // ... open the file, return fd ...
        return FileHandle { fd: 3 };
    }

    def __release__() -> Void {
        // fd is still valid here
        close_fd(self.fd);
    }
}
```

`__release__` is optional. When present, it acts as an automatic destructor; you do not call it manually.

## Nested Structs

Structs can contain other structs as fields:

```rolang
struct Address {
    var street: String;
    var city: String;
}

struct Person {
    var name: String;
    var age: i32;
    var address: Address;
}

let person = Person {
    name: "Alice",
    age: 30,
    address: Address { street: "1 Main St", city: "Springfield" },
};

let city = person.address.city;
```

## Recursive Structs via Optionals

A struct can refer to itself through an optional field:

```rolang
struct Node {
    var value: i32;
    var next: Node?;
}

let tail = Node { value: 3, next: nil };
let mid  = Node { value: 2, next: tail };
let head = Node { value: 1, next: mid };
```

## Struct Cloning

Structs are reference-counted heap objects — multiple variables can hold a reference to the same object. To get an independent copy, write an explicit `clone` method:

```rolang
struct Vec2 {
    var x: f64;
    var y: f64;

    def clone() -> Vec2 {
        return Vec2 { x: self.x, y: self.y };
    }
}

let a = Vec2 { x: 1.0, y: 2.0 };
let b = a;          // b and a point to the same object
let c = a.clone();  // c is a separate object
b.x = 99.0;         // also changes a.x
```

## A Complete Example

```rolang
import "math.rl"
import "io.rl"

struct Vec3 {
    var x: f64;
    var y: f64;
    var z: f64;

    def length() -> f64 {
        sqrt(self.x * self.x + self.y * self.y + self.z * self.z)
    }

    def dot(other: Vec3) -> f64 {
        self.x * other.x + self.y * other.y + self.z * other.z
    }

    def __add__(other: Vec3) -> Vec3 {
        return Vec3 { x: self.x + other.x, y: self.y + other.y, z: self.z + other.z };
    }

    def __mul__(scalar: f64) -> Vec3 {
        return Vec3 { x: self.x * scalar, y: self.y * scalar, z: self.z * scalar };
    }

    def normalize() -> Vec3 {
        let len = self.length();
        return self * (1.0 / len);
    }
}

def main() -> i32 {
    let a = Vec3 { x: 1.0, y: 0.0, z: 0.0 };
    let b = Vec3 { x: 0.0, y: 1.0, z: 0.0 };
    let c = a + b;
    let d = c.dot(Vec3 { x: 1.0, y: 1.0, z: 0.0 });
    return 0;
}
```

## Summary

- Structs group fields with `struct Name { var field: Type; }`
- Instances are created with named-field syntax: `Name { field: value }`
- `let` bindings allow field mutation — only the binding is frozen
- `def method()` methods receive `self`; `static def` methods belong to the type
- `pub` makes a field or method visible outside the current file
- `__release__()` is the automatic destructor, called on the final reference release
