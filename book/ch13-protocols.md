# Chapter 13: Protocols

A protocol is a named set of requirements — methods and properties — that a type promises to satisfy. Protocols enable polymorphism: you can write functions that work with any type conforming to a protocol, without knowing the concrete type at the call site.

## Defining a Protocol

```rolang
protocol Greetable {
    def greet() -> String;
}
```

A protocol body lists method signatures, terminated with semicolons:

```rolang
protocol Shape {
    def area() -> f64;
    def perimeter() -> f64;
    def name() -> String;
}
```

## Conforming to a Protocol

A struct or enum conforms to a protocol by implementing all its required methods. Conformance is declared in the type definition with a colon:

```rolang
struct Circle {
    var radius: f64;
}

extension Circle: Shape {
    def area() -> f64 {
        3.14159265 * self.radius * self.radius
    }

    def perimeter() -> f64 {
        2.0 * 3.14159265 * self.radius
    }

    def name() -> String {
        "circle"
    }
}
```

Conformance can also be declared directly in the struct body:

```rolang
struct Rectangle: Shape {
    var width: f64;
    var height: f64;

    def area() -> f64 { self.width * self.height }
    def perimeter() -> f64 { 2.0 * (self.width + self.height) }
    def name() -> String { "rectangle" }
}
```

## Static Dispatch with Generics

The most efficient use of protocols is as constraints on generic type parameters. The compiler generates a specialised version for each concrete type — no runtime overhead:

```rolang
def print_shape<T: Shape>(s: T) -> Void {
    println(s.name() + " area=" + s.area().to_string());
}

let c = Circle { radius: 5.0 };
let r = Rectangle { width: 3.0, height: 4.0 };

print_shape(c);   // circle area=78.539...
print_shape(r);   // rectangle area=12.0
```

## Dynamic Dispatch with `any`

When you need to store different conforming types in the same collection or pass them through a single variable, use `any Protocol`. This uses dynamic dispatch via a vtable — a small runtime cost:

```rolang
def describe(s: any Shape) -> Void {
    println(s.name() + " has area " + s.area().to_string());
}

let shapes: Vec<any Shape> = Vec<any Shape>.new();
shapes.push(Circle { radius: 3.0 });
shapes.push(Rectangle { width: 2.0, height: 5.0 });

for shape in shapes {
    describe(shape);
}
```

`any Shape` is an *existential type* — it boxes the concrete value along with a pointer to its vtable. Use it when you need heterogeneous collections or late binding; prefer generic constraints when all types are known at compile time.

## Protocol Properties

Protocols can require properties with `{ get }` (read-only) or `{ get set }` (read-write):

```rolang
protocol Named {
    var name: String { get };
}

protocol Configurable {
    var enabled: Bool { get set };
}
```

Implementing types provide these as regular fields or computed getters.

## Protocol Inheritance

Protocols can inherit from other protocols. A type conforming to the child must implement all requirements of both:

```rolang
protocol Printable {
    def to_string() -> String;
}

protocol Debuggable: Printable {
    def debug_info() -> String;
}

// A type conforming to Debuggable must implement both to_string and debug_info
```

## Multiple Protocol Constraints

Constrain a type parameter to multiple protocols with `&`:

```rolang
protocol Serializable {
    def serialize() -> String;
}

protocol Comparable {
    def less_than(other: Self) -> Bool;
}

def sorted_and_serialized<T: Comparable & Serializable>(items: Vec<T>) -> Vec<String> {
    // sort items, then serialize each
    let result = Vec<String>.new();
    for item in items {
        result.push(item.serialize());
    }
    return result;
}
```

## Protocol-Oriented Design

Protocols encourage small, composable interfaces. Rather than large inheritance hierarchies, build behaviour from combinations of focused protocols:

```rolang
protocol Drawable {
    def draw() -> Void;
}

protocol Resizable {
    def resize(factor: f64) -> Void;
}

protocol Widget: Drawable & Resizable {
    def label() -> String;
}

struct Button: Widget {
    var text: String;
    var w: f64;
    var h: f64;

    def draw()  -> Void  { println("drawing " + self.text); }
    def resize(factor: f64) -> Void {
        self.w = self.w * factor;
        self.h = self.h * factor;
    }
    def label() -> String { self.text }
}
```

## A Complete Example

```rolang
import "vec.rl"
import "io.rl"

protocol Animal {
    def name() -> String;
    def sound() -> String;
    def speak() -> Void;
}

struct Dog: Animal {
    var dog_name: String;
    def name()  -> String { self.dog_name }
    def sound() -> String { "woof" }
    def speak() -> Void { println(self.dog_name + " says " + self.sound()); }
}

struct Cat: Animal {
    var cat_name: String;
    def name()  -> String { self.cat_name }
    def sound() -> String { "meow" }
    def speak() -> Void { println(self.cat_name + " says " + self.sound()); }
}

def chorus(animals: Vec<any Animal>) -> Void {
    for a in animals {
        a.speak();
    }
}

def main() -> i32 {
    let animals = Vec<any Animal>.new();
    animals.push(Dog { dog_name: "Rex" });
    animals.push(Cat { cat_name: "Whiskers" });
    animals.push(Dog { dog_name: "Buddy" });
    chorus(animals);
    return 0;
}
```

## Summary

- Protocols define a set of method and property requirements
- Types conform by implementing all required members (in the type body or via `extension`)
- Generic constraints (`<T: Protocol>`) give static dispatch — no runtime overhead
- `any Protocol` gives dynamic dispatch — allows heterogeneous collections
- Protocols can inherit from other protocols; type parameters can require multiple protocols with `&`
