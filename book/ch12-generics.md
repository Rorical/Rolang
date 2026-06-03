# Chapter 12: Generics

Generics let you write code that works with many different types while still being fully type-checked. A single generic function or struct works for `i32`, `String`, a custom struct — any type you choose.

## Generic Functions

Declare type parameters in angle brackets after the function name. Use those parameters in the signature and body:

```rolang
def identity<T>(x: T) -> T {
    return x;
}

let n = identity(42);           // T = i32
let s = identity("hello");      // T = String
```

The type parameter `T` is inferred from the argument — you rarely need to write it explicitly.

Multiple type parameters:

```rolang
def swap<A, B>(a: A, b: B) -> (B, A) {
    return (b, a);
}

let pair = swap(1, "one");   // (B, A) = (String, i32)
```

## Protocol-Constrained Type Parameters

Constrain a type parameter to require it to conform to a protocol (see [Chapter 13](ch13-protocols.md)). The function can then call the protocol's methods on values of that type:

```rolang
protocol Printable {
    def display() -> String;
}

def print_twice<T: Printable>(item: T) -> Void {
    println(item.display());
    println(item.display());
}
```

Multiple constraints use `&`:

```rolang
protocol Named   { def name() -> String; }
protocol Scored  { def score() -> i32; }

def leaderboard_entry<T: Named & Scored>(player: T) -> String {
    return player.name() + ": " + player.score().to_string();
}
```

## Generic Structs

Structs can be parameterised over types:

```rolang
struct Pair<A, B> {
    var first: A;
    var second: B;

    def swap() -> Pair<B, A> {
        return Pair<B, A> { first: self.second, second: self.first };
    }
}

let p = Pair<i32, String> { first: 1, second: "one" };
let q = p.swap();   // Pair<String, i32>
```

## Generic Enums

Enums take type parameters too. The standard `Result<T, E>` is a generic enum:

```rolang
enum Either<L, R> {
    case left(L)
    case right(R)
}

def parse(s: String) -> Either<i32, String> {
    let n = s.to_i32();
    if n == 0 && s != "0" {
        return Either.right(s + " is not a number");
    }
    return Either.left(n);
}

switch parse("42") {
case .left(let n):  println_i32(n);
case .right(let e): println(e);
}
```

## Generic Methods

Methods on generic structs have access to the struct's type parameters and may introduce their own:

```rolang
struct Box<T> {
    var value: T;

    def get() -> T {
        return self.value;
    }

    def map<U>(f: (T) -> U) -> Box<U> {
        return Box<U> { value: f(self.value) };
    }
}

let b = Box<i32> { value: 5 };
let s = b.map((n: i32) -> String { n.to_string() });
```

## Building Generic Collections

Generic types compose naturally:

```rolang
def zip<A, B>(a: Vec<A>, b: Vec<B>) -> Vec<Pair<A, B>> {
    let result = Vec<Pair<A, B>>.new();
    let len = a.len().min(b.len());
    var i: i32 = 0;
    while i < len {
        result.push(Pair<A, B> { first: a.get(i), second: b.get(i) });
        i = i + 1;
    }
    return result;
}
```

## Type Inference

The compiler infers type arguments from usage. Explicit annotations are only necessary when inference is ambiguous:

```rolang
let v = Vec<i32>.new();   // explicit — needed because .new() has no argument
v.push(1);                // T is already known as i32

let b = Box<String> { value: "hi" };   // explicit
let c = b.map((s: String) -> i32 { s.len() as i32 });   // inferred U = i32
```

## Monomorphisation

Rolang compiles generic code by instantiating a separate specialised version for each concrete type combination used. This means:

- No runtime overhead for generics
- Calling `identity(42)` and `identity("hello")` produces two distinct compiled functions
- Type errors are caught at the call site, not inside the generic definition

## A Complete Example: Generic Stack

```rolang
import "vec.rl"

struct Stack<T> {
    var items: Vec<T>;

    static def new() -> Stack<T> {
        return Stack<T> { items: Vec<T>.new() };
    }

    def push(item: T) -> Void {
        self.items.push(item);
    }

    def pop() -> T? {
        if self.is_empty() { return nil; }
        return self.items.pop();
    }

    def peek() -> T? {
        if self.is_empty() { return nil; }
        return self.items.get(self.items.len() - 1);
    }

    def is_empty() -> Bool {
        return self.items.len() == 0;
    }

    def len() -> i32 {
        return self.items.len();
    }
}

def main() -> i32 {
    let s = Stack<i32>.new();
    s.push(1);
    s.push(2);
    s.push(3);

    while !s.is_empty() {
        if let top = s.pop() {
            println_i32(top);   // 3, 2, 1
        }
    }
    return 0;
}
```

## Summary

- Generic type parameters are declared in angle brackets: `def f<T>(x: T) -> T`
- Constrain parameters with protocol requirements: `<T: Protocol>`
- Structs and enums can be generic: `struct Box<T>`, `enum Either<L, R>`
- The compiler generates a specialised version for each distinct type combination
- Type arguments are usually inferred; write them explicitly only when needed
