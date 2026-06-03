# Chapter 6: Enums and Pattern Matching

## Defining Enums

An `enum` defines a type that can be one of several named variants:

```rolang
enum Direction {
    case North
    case South
    case East
    case West
}
```

Create a value by prefixing the variant name with the type name, or by using the dot shorthand when the type is known:

```rolang
let heading = Direction.North;
let other: Direction = .South;
```

## Variants with Associated Values

Variants can carry data. The payload is declared in parentheses:

```rolang
enum Shape {
    case Circle(radius: f64)
    case Rectangle(width: f64, height: f64)
    case Triangle(base: f64, height: f64)
    case Point
}
```

Construct a variant with associated values by supplying the payload:

```rolang
let c = Shape.Circle(radius: 5.0);
let r = Shape.Rectangle(width: 3.0, height: 4.0);
let p = Shape.Point;
```

## Pattern Matching with Switch

`switch` is the primary way to work with enums. It must be exhaustive — every variant (or a `default` case) must be handled:

```rolang
def area(s: Shape) -> f64 {
    switch s {
    case .Circle(let r):
        return 3.14159265 * r * r;
    case .Rectangle(let w, let h):
        return w * h;
    case .Triangle(let b, let h):
        return 0.5 * b * h;
    case .Point:
        return 0.0;
    }
}
```

### Binding Payload Values

Use `let` inside a `case` pattern to bind the associated values to local names:

```rolang
switch shape {
case .Circle(let r):
    println("circle, radius = " + r.to_string());
case .Rectangle(let w, let h):
    println("rect " + w.to_string() + " x " + h.to_string());
default:
    println("other");
}
```

### Where Guards

A `where` clause filters a case further. It is checked after the variant matches:

```rolang
switch shape {
case .Circle(let r) where r > 10.0:
    println("large circle");
case .Circle(let r):
    println("small circle");
default:
    break;
}
```

## Generic Enums

Enums can be parameterised over types. The standard library's `Result` and optional-like types are generic enums:

```rolang
enum Maybe<T> {
    case some(T)
    case none
}

let x: Maybe<i32> = Maybe.some(42);
let y: Maybe<String> = Maybe.none;
```

Use a generic enum in a function:

```rolang
def first<T>(items: [T]) -> Maybe<T> {
    if items.len() == 0 { return Maybe.none; }  // conceptual
    return Maybe.some(items[0]);
}
```

## Enums with Methods

Enums can have methods, just like structs:

```rolang
enum Coin {
    case Penny
    case Nickel
    case Dime
    case Quarter

    def value_cents() -> i32 {
        switch self {
        case .Penny:   return 1;
        case .Nickel:  return 5;
        case .Dime:    return 10;
        case .Quarter: return 25;
        }
    }
}

let c = Coin.Quarter;
let v = c.value_cents();   // 25
```

## Enums as State Machines

Enums are a natural fit for state machines:

```rolang
enum TrafficLight {
    case Red
    case Yellow
    case Green

    def next() -> TrafficLight {
        switch self {
        case .Red:    return TrafficLight.Green;
        case .Green:  return TrafficLight.Yellow;
        case .Yellow: return TrafficLight.Red;
        }
    }

    def can_go() -> Bool {
        switch self {
        case .Green: return true;
        default:     return false;
        }
    }
}

var light = TrafficLight.Red;
light = light.next();   // Green
```

## Recursive Enums

Enums can be self-referential through an optional:

```rolang
enum Tree {
    case Leaf(value: i32)
    case Branch(left: Tree?, right: Tree?)
}

def sum(t: Tree?) -> i32 {
    if t == nil { return 0; }
    switch t! {
    case .Leaf(let v):
        return v;
    case .Branch(let l, let r):
        return sum(l) + sum(r);
    }
}
```

## A Realistic Example: JSON Values

```rolang
import "vec.rl"

enum JsonValue {
    case null_val
    case bool_val(val: Bool)
    case int_val(val: i64)
    case str_val(val: String)
    case arr_val(elements: Vec<JsonValue>)
}

def type_name(v: JsonValue) -> String {
    switch v {
    case .null_val:         return "null";
    case .bool_val(let b):  return "bool";
    case .int_val(let n):   return "int";
    case .str_val(let s):   return "string";
    case .arr_val(let a):   return "array";
    }
}
```

## Summary

- Enums define a type that is exactly one of its named variants
- Variants are constructed with `TypeName.VariantName` or `.VariantName`
- Variants can carry labeled associated values declared in parentheses
- `switch` on enums is exhaustive; bind payload values with `case .Variant(let x)`
- `where` adds an extra filter condition to a case
- Enums support methods, generic parameters, and recursive definitions
