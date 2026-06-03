# Chapter 10: Strings

## The String Type

`String` is a heap-allocated, reference-counted UTF-8 string. It is part of the implicit standard library — no import is required to use it.

String literals are written with double quotes:

```rolang
let hello = "Hello, World!";
let empty = "";
```

## String Concatenation

Use the `+` operator (via `__add__`) to join strings:

```rolang
let first = "Hello";
let second = "World";
let greeting = first + ", " + second + "!";   // "Hello, World!"
```

Or call `.concat` directly:

```rolang
let full = "foo".concat("bar");   // "foobar"
```

## String Length

```rolang
let s = "Rolang";
let n = s.len();   // 6 (returns i64)
```

## Inspecting Content

```rolang
let s = "  hello  ";

s.is_empty();                  // false
s.contains("ello");            // true
s.starts_with("  h");          // true
s.ends_with("  ");             // true
s.equals("  hello  ");         // true
s.compare_to("abc");           // < 0, = 0, or > 0
```

## Substrings and Slicing

```rolang
let s = "Hello, World!";

s.substring(7, 5);    // "World" — start index, length
s.char_at(0);         // 72 (ASCII code for 'H')
```

Note: `char_at` returns an `i32` ASCII/Unicode code point, not a character type.

## Searching

```rolang
let s = "abcabc";

s.find("bc");          // 1 — first occurrence, or -1 if not found
s.count("a");          // 2 — number of non-overlapping occurrences
s.find_char(97, 0);    // 0 — first 'a' (ASCII 97) starting from index 0
```

## Trimming and Transforming

```rolang
let s = "  hello world  ";

s.trim();         // "hello world"
s.trim_start();   // "hello world  "
s.trim_end();     // "  hello world"
s.replace("world", "Rolang");   // "  hello Rolang  "
s.repeat(2);                    // "  hello world    hello world  "
```

## Splitting

`.split` returns a `Vec<String>`:

```rolang
let csv = "a,b,c,d";
let parts = csv.split(",");    // Vec<String> with ["a", "b", "c", "d"]

for part in parts {
    println(part);
}
```

`.lines` splits on newlines:

```rolang
let text = "line one\nline two\nline three";
let lines = text.lines();   // Vec<String>
```

## Parsing Numbers

```rolang
let s = "42";
let n = s.to_i32();    // 42
let m = s.to_i64();    // 42
let f = "3.14".to_f64();   // 3.14
```

## Converting Numbers to Strings

The standard library adds `.to_string()` to numeric types via extensions:

```rolang
let n: i32 = 42;
let s = n.to_string();     // "42"

let x: i64 = 9999999;
let t = x.to_string();     // "9999999"

let f: f64 = 3.14;
let u = f.to_string();     // "3.14"
```

## Building Strings Dynamically

Combine `.to_string()` and `+` to format output:

```rolang
def describe(name: String, age: i32, score: f64) -> String {
    return name + " (age " + age.to_string() + ") scored " + score.to_string();
}
```

## String Comparison

Strings compare by content using `==`, `!=`, and `.compare_to`:

```rolang
let a = "apple";
let b = "banana";

a.equals("apple");    // true
a.equals(b);          // false
a.compare_to(b);      // negative — "apple" < "banana"
```

## Full String API

| Method | Return | Description |
|--------|--------|-------------|
| `.len()` | `i64` | Length in bytes |
| `.is_empty()` | `Bool` | True when length is 0 |
| `.equals(s)` | `Bool` | Content equality |
| `.compare_to(s)` | `i32` | Lexicographic: <0, 0, >0 |
| `.contains(s)` | `Bool` | Substring test |
| `.starts_with(s)` | `Bool` | Prefix test |
| `.ends_with(s)` | `Bool` | Suffix test |
| `.concat(s)` | `String` | Append and return new string |
| `+` | `String` | Same as `.concat` |
| `.repeat(n)` | `String` | Repeat n times |
| `.char_at(i)` | `i32` | Code point at index |
| `.byte_at(i)` | `i32` | Byte at index (alias) |
| `.find_char(ch, start)` | `i32` | First occurrence of code point |
| `.substring(start, len)` | `String` | Extract substring |
| `.find(s)` | `i32` | First occurrence of substring, or -1 |
| `.count(s)` | `i32` | Number of non-overlapping occurrences |
| `.trim()` | `String` | Remove leading and trailing whitespace |
| `.trim_start()` | `String` | Remove leading whitespace |
| `.trim_end()` | `String` | Remove trailing whitespace |
| `.replace(old, new)` | `String` | Replace all occurrences |
| `.split(sep)` | `Vec<String>` | Split on separator |
| `.lines()` | `Vec<String>` | Split on newlines |
| `.to_i32()` | `i32` | Parse as integer |
| `.to_i64()` | `i64` | Parse as integer |
| `.to_f64()` | `f64` | Parse as float |

## Summary

- `String` is a heap-allocated, ARC-managed UTF-8 string; no import required
- Concatenate with `+` or `.concat`
- `.len()` returns the byte length as `i64`
- `.split`, `.lines`, `.trim`, `.replace`, `.find`, `.contains` cover most text processing
- Convert numbers to strings with `.to_string()`; parse strings with `.to_i32()`, `.to_f64()`
