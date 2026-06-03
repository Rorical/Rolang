# Chapter 11: Closures and First-Class Functions

## Functions as Values

In Rolang, functions are first-class values. You can store a function in a variable, pass it to another function, and return it from a function.

The type of a function value is written as `(ParamTypes) -> ReturnType`:

```rolang
let double: (i32) -> i32 = (n: i32) -> i32 { n * 2 };
let result = double(5);   // 10
```

## Closure Syntax

Closures are anonymous function expressions. There are two equivalent forms.

### Arrow form

The explicit form matches the ordinary function signature style:

```rolang
let add = (a: i32, b: i32) -> i32 { a + b };
```

### Block form

The block form uses `{ parameters in body }`:

```rolang
let add = { a: i32, b: i32 in
    return a + b;
};
```

Both forms are equivalent. Choose whichever is clearer for the context.

## Captures

Closures capture variables from the enclosing scope by reference. The captured variable must be accessible at the point of capture:

```rolang
let multiplier: i64 = 3;

let triple = { n: i64 in
    return n * multiplier;
};

let result = triple(7);   // 21
```

Multiple captures work the same way:

```rolang
let base: i32 = 100;
let step: i32 = 5;

let next = { offset: i32 in
    return base + offset * step;
};

println_i32(next(2));   // 110
```

## Passing Closures to Functions

A function that accepts a closure takes a function-value parameter:

```rolang
def apply(f: (i32) -> i32, x: i32) -> i32 {
    return f(x);
}

let doubled = apply((n: i32) -> i32 { n * 2 }, 5);   // 10
let squared = apply((n: i32) -> i32 { n * n }, 4);   // 16
```

## Returning Closures

A function can return a closure. The return type is the function-value type:

```rolang
def make_adder(n: i32) -> (i32) -> i32 {
    return (x: i32) -> i32 { x + n };
}

let add5  = make_adder(5);
let add10 = make_adder(10);

println_i32(add5(3));    // 8
println_i32(add10(3));   // 13
```

## Higher-Order Functions

Higher-order functions take functions as parameters and apply them to collections:

```rolang
import "vec.rl"

def map_vec(v: Vec<i32>, f: (i32) -> i32) -> Vec<i32> {
    let result = Vec<i32>.new();
    for item in v {
        result.push(f(item));
    }
    return result;
}

def filter_vec(v: Vec<i32>, pred: (i32) -> Bool) -> Vec<i32> {
    let result = Vec<i32>.new();
    for item in v {
        if pred(item) { result.push(item); }
    }
    return result;
}

def fold_vec(v: Vec<i32>, initial: i32, f: (i32, i32) -> i32) -> i32 {
    var acc = initial;
    for item in v {
        acc = f(acc, item);
    }
    return acc;
}

def main() -> i32 {
    let nums = Vec<i32>.new();
    nums.push(1);
    nums.push(2);
    nums.push(3);
    nums.push(4);
    nums.push(5);

    let doubled  = map_vec(nums, (n: i32) -> i32 { n * 2 });
    let evens    = filter_vec(nums, (n: i32) -> Bool { n % 2 == 0 });
    let total    = fold_vec(nums, 0, (acc: i32, n: i32) -> i32 { acc + n });
    return 0;
}
```

## Bare Function References

You can pass a named function wherever a function-value is expected, as long as the signature matches:

```rolang
def square(n: i32) -> i32 { n * n }

let result = apply(square, 6);   // 36
```

Named functions and closures share the same calling convention — they are both represented as typed heap objects with a function pointer.

## Closures Inside Methods

Closures can be defined inside methods and capture `self`'s fields:

```rolang
struct Adder {
    var base: i32;

    def make_closure() -> (i32) -> i32 {
        let b = self.base;
        return (x: i32) -> i32 { x + b };
    }
}

let adder = Adder { base: 100 };
let f = adder.make_closure();
println_i32(f(7));   // 107
```

Note: lambda bodies support declarations, expressions, and `return`. Complex control flow (`if`, `while`, `for`, `switch`) inside a lambda should be extracted into a named function.

## Function Types in Structs

Store callbacks and strategies as struct fields:

```rolang
struct Transformer {
    var transform: (i32) -> i32;

    def apply(n: i32) -> i32 {
        return self.transform(n);
    }
}

let t = Transformer { transform: (n: i32) -> i32 { n * n } };
println_i32(t.apply(5));   // 25
```

## Summary

- Function types are written as `(T1, T2) -> R`
- Closures are anonymous functions: `(params) -> R { body }` or `{ params in body }`
- Closures capture variables from the enclosing scope
- Pass closures to functions, return them from functions, store them in variables and struct fields
- Named functions can be used anywhere a closure of the same type is expected
