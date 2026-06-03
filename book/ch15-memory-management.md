# Chapter 15: Memory Management

Rolang manages memory automatically through two complementary mechanisms: *Automatic Reference Counting* (ARC) for the common case, and a *cycle-detecting garbage collector* as a backstop for cyclic structures. You do not call `malloc` or `free` — the runtime handles allocation and deallocation.

## Heap Allocation

Every struct and enum value lives on the heap. When you write:

```rolang
let p = Point { x: 1.0, y: 2.0 };
```

the runtime allocates a heap object containing a reference-count header followed by the fields. `p` holds a reference to that object.

Primitive values (`i32`, `f64`, `Bool`, etc.) are stored directly by value on the stack or in registers.

## Reference Counting

Every heap object has a reference count — the number of live references pointing to it. The count starts at 1 when the object is created. It increases by 1 when a reference is copied, and decreases by 1 when a reference goes out of scope.

When the reference count reaches zero, the runtime calls `__release__` (if defined) and then recursively decrements the reference counts of all pointer fields before freeing the memory.

```rolang
def example() -> Void {
    let a = Point { x: 1.0, y: 2.0 };   // ref-count = 1
    let b = a;                             // ref-count = 2 (a and b point to same object)
    // b goes out of scope → ref-count = 1
    // a goes out of scope → ref-count = 0 → object freed
}
```

You never see or manipulate reference counts directly. The compiler inserts all retain and release calls.

## Sharing and Mutation

Because all structs are reference-counted heap objects, multiple variables can point to the same object:

```rolang
struct Counter {
    var n: i32;
}

let c1 = Counter { n: 0 };
let c2 = c1;         // c1 and c2 point to the same Counter
c1.n = 99;
println_i32(c2.n);   // 99 — c2 sees the change
```

If you need an independent copy, write an explicit `clone` method:

```rolang
struct Counter {
    var n: i32;

    def clone() -> Counter {
        Counter { n: self.n }
    }
}

let c1 = Counter { n: 0 };
let c2 = c1.clone();   // separate object
c1.n = 99;
println_i32(c2.n);     // 0 — c2 is unaffected
```

## The `__release__` Destructor

Define `__release__() -> Void` on a struct or enum to run code when the last reference is dropped. The runtime calls it exactly once before freeing the object:

```rolang
struct FileHandle {
    var fd: i32;

    def __release__() -> Void {
        // self.fd is still readable here
        if self.fd >= 0 { close_fd(self.fd); }
    }
}
```

`__release__` is called both on normal ARC decrement and during GC sweeps. Do not call it manually.

## The Cycle-Detecting GC

ARC alone cannot free cyclic structures — two objects pointing to each other keep each other's reference counts above zero forever:

```rolang
struct Node {
    var next: Node?;
}

let a = Node { next: nil };
let b = Node { next: a };
a.next = b;   // cycle: a → b → a
// When a and b go out of scope, neither can be freed by ARC alone
```

Rolang's runtime runs a cycle-detecting collector periodically to find and free such cycles. The collector:

1. Builds a graph of all heap objects
2. Identifies strongly-connected components (cycles)
3. Calls `__release__` on each object in the cycle before freeing it

The GC is not a replacement for ARC — it is a backstop. Well-structured code that avoids deliberate cycles rarely triggers it.

## ARC Optimisations

The compiler performs several optimisations to reduce retain/release traffic:

- **Release sinking**: defers a release to the latest safe point in a block to reduce retain/release round-trips
- **Elision**: when an object is clearly not shared, redundant retain/release pairs are removed

These optimisations are transparent to the programmer.

## Zero-Initialised Variables

Declaring a variable without an initial value always produces a well-defined zero state:

```rolang
var n: i32;        // 0
var f: f64;        // 0.0
var flag: Bool;    // false
var opt: i32?;     // nil
var s: String;     // "" (empty string)
```

No code path can observe uninitialised memory.

## Panics

Certain runtime conditions abort the program with a panic message to stderr:

| Condition | Panic message |
|-----------|---------------|
| Integer division by zero | `rolang panic: integer division by zero` |
| Integer modulo by zero | `rolang panic: integer remainder by zero` |
| Array/Vec index out of bounds | `rolang panic: index out of bounds` |

Panics are not catchable — they immediately call `abort()`. Avoid them by checking preconditions before the operation.

## Practical Guidance

- **Structs are shared by default.** Assign to a new variable to share the same object; call `.clone()` (if provided) for an independent copy.
- **Avoid cycles when possible.** Use optionals and break back-references to nil before objects go out of scope.
- **Use `defer` for cleanup.** It is more reliable than manually releasing resources mid-function.
- **`__release__` is for external resources.** For pure Rolang fields, ARC handles cleanup automatically.

## Summary

- All structs and enums live on the heap, managed by reference counting
- Reference counts are maintained automatically; you never call retain or release
- When the count reaches zero, `__release__` is called (if defined), then fields are released, then the object is freed
- A cycle-detecting GC collects cyclic structures that ARC cannot free
- Variables are zero-initialised when declared without a value
- Division by zero and out-of-bounds access panic with `abort()`
