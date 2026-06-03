# Chapter 9: Collections

Rolang provides three built-in collection types: arrays (fixed-size literals), `Vec<T>` (growable sequences), and `Dict<K, V>` (hash maps). All three are available in every file without an explicit `import`. `Set<T>` is available via `import "set.rl"`.

## Array Literals

An array literal creates an immutable, fixed-size sequence:

```rolang
let nums = [1, 2, 3, 4, 5];
let first = nums[0];   // 1
```

Array literals are primarily useful for small, known-at-compile-time sequences and for `for` loops:

```rolang
for n in [10, 20, 30] {
    println_i32(n);
}
```

Accessing an out-of-range index panics at runtime.

## Vec\<T\> — Growable Sequences

`Vec<T>` is a dynamically-sized array. It grows automatically as you add elements.

### Creating a Vec

```rolang
let v = Vec<i32>.new();               // empty, default capacity
let w = Vec<i32>.with_capacity(64);   // empty, initial capacity 64
```

### Adding and Accessing Elements

```rolang
let v = Vec<String>.new();
v.push("alpha");
v.push("beta");
v.push("gamma");

let n = v.len();          // 3
let s = v.get(0);         // "alpha"
v.set(1, "BETA");
let last = v.pop();       // "gamma"; len is now 2
```

### Iterating

```rolang
for item in v {
    println(item);
}
```

`Vec<T>` implements the iterator protocol, so it works directly in `for` loops.

### All Vec Methods

| Method | Description |
|--------|-------------|
| `Vec<T>.new()` | Create an empty vec |
| `Vec<T>.with_capacity(n)` | Create with initial capacity |
| `.push(value)` | Append an element |
| `.pop() -> T` | Remove and return the last element |
| `.get(i) -> T` | Return element at index `i` |
| `.set(i, value)` | Replace element at index `i` |
| `.len() -> i32` | Number of elements |
| `.resize(n)` | Resize backing buffer |
| `.free()` | Explicitly release memory |

### Typed Convenience Constructors

The standard library also provides free functions for common element types:

```rolang
let ints   = vec_i32_new();    // Vec<i32>
let longs  = vec_i64_new();    // Vec<i64>
let floats = vec_f64_new();    // Vec<f64>
let bytes  = vec_u8_new();     // Vec<u8>
let flags  = vec_bool_new();   // Vec<Bool>
```

## Dict\<K, V\> — Hash Maps

`Dict<K, V>` is an ordered hash map. Iteration yields keys in insertion order.

### Creating a Dict

The `with_capacity` constructor takes a key-kind flag: `1` for `String` keys (content comparison), `0` for all other types (byte comparison):

```rolang
let d = Dict<String, i32>.with_capacity(16, 1);   // String keys
let e = Dict<i32, i64>.with_capacity(8, 0);       // i32 keys
```

Dict literals use the `[key: value]` syntax and are converted automatically:

```rolang
let scores = ["alice": 95, "bob": 87, "carol": 92];
```

### Inserting and Looking Up

```rolang
let d = Dict<String, i32>.with_capacity(16, 1);
d.set("apples", 5);
d.set("oranges", 3);

let count: i32? = d.get("apples");   // 5
let missing = d.get("bananas");      // nil

d.contains("oranges");   // true
d.len();                 // 2
```

`.get` returns an optional — use `??` or `if let` to handle the missing case:

```rolang
let n = d.get("apples") ?? 0;
```

### Iterating

`for` over a `Dict<K, V>` yields keys:

```rolang
for key in d {
    let val = d.get(key) ?? 0;
    println(key + " -> " + val.to_string());
}
```

### All Dict Methods

| Method | Description |
|--------|-------------|
| `Dict<K,V>.with_capacity(n, kind)` | Create with capacity; kind=1 for String keys |
| `.set(key, value)` | Insert or update |
| `.get(key) -> V?` | Look up; returns nil if missing |
| `.contains(key) -> Bool` | Test membership |
| `.len() -> i64` | Number of entries |
| `.free()` | Explicitly release memory |

### Typed Convenience Constructors

```rolang
let string_int  = dict_string_i32_new();   // Dict<String, i32>
let string_long = dict_string_i64_new();   // Dict<String, i64>
let int_int     = dict_i32_i32_new();      // Dict<i32, i32>
```

## Set\<T\>

`Set<T>` stores unique values with fast membership tests:

```rolang
import "set.rl"

let s = Set<String>.new();
s.insert("alpha");
s.insert("beta");
s.insert("alpha");   // duplicate — ignored

s.contains("alpha");   // true
s.contains("gamma");   // false
s.len();               // 2
```

## Choosing a Collection

| Collection | Use when |
|------------|----------|
| `[T]` (array literal) | Small, fixed, known-at-compile-time sequences |
| `Vec<T>` | You need to grow, shrink, or randomly access a sequence |
| `Dict<K, V>` | You need key-based lookup |
| `Set<T>` | You need membership tests with no duplicates |

## A Complete Example: Word Count

```rolang
import "io.rl"

def word_count(text: String) -> Dict<String, i32> {
    let words = text.split(" ");
    let counts = Dict<String, i32>.with_capacity(64, 1);
    for word in words {
        let trimmed = word.trim();
        if trimmed.is_empty() { continue; }
        let current = counts.get(trimmed) ?? 0;
        counts.set(trimmed, current + 1);
    }
    return counts;
}

def main() -> i32 {
    let text = "the cat sat on the mat the cat";
    let counts = word_count(text);

    for word in counts {
        let n = counts.get(word) ?? 0;
        println(word + ": " + n.to_string());
    }
    return 0;
}
```

## Summary

- Array literals `[1, 2, 3]` are fixed-size and used primarily for iteration
- `Vec<T>` is a growable sequence: `.push`, `.pop`, `.get`, `.set`, `.len`
- `Dict<K, V>` is an insertion-ordered hash map: `.set`, `.get` (returns `T?`), `.contains`, `.len`
- `Set<T>` stores unique values; requires `import "set.rl"`
- Out-of-range array or vec access panics at runtime
