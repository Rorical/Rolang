# Rorical (Rolang)

Rorical (Rolang) is a statically typed, compiled systems programming language with automatic memory management. Programs compile to native executables via LLVM. Memory is managed through reference counting (ARC) with a cycle-detecting GC backstop — no manual `free`, no garbage collection pauses. The language has first-class async/await built on a cooperative task scheduler, generics, protocols, pattern matching, and a standard library covering collections, I/O, math, and more.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A C compiler available as `cc` (for linking executables)

## Installation

```bash
uv tool install git+https://github.com/Rorical/Rolang
```

Two commands become available globally:

```
rolang   — project and package management
rolangc  — single-file compiler with all emit modes
```

**Specific version:**

```bash
uv tool install "git+https://github.com/Rorical/Rolang@v0.2.0"   # tag
uv tool install "git+https://github.com/Rorical/Rolang@main"     # branch
uv tool install "git+https://github.com/Rorical/Rolang@a1b2c3d"  # commit
```

**Update:**

```bash
uv tool upgrade rolang
```

**From a local clone:**

```bash
git clone https://github.com/Rorical/Rolang
cd Rolang
uv tool install .
uv tool install . --reinstall   # after local changes
```

## Quick Start

```bash
rolang new hello-world
cd hello-world
rolang run
```

## Toolchain

| Command | Description |
|---|---|
| `rolang new <name>` | Create a new binary project |
| `rolang new <name> --lib` | Create a new library project |
| `rolang init [--lib]` | Initialise a project in the current directory |
| `rolang build` | Compile all targets (output in `build/`) |
| `rolang build --release` | Compile with optimizations |
| `rolang run` | Build and execute |
| `rolang run -- <args>` | Pass extra arguments to the binary |
| `rolang test [filter]` | Compile and run `[[test]]` targets |
| `rolang check` | Type-check without producing output |
| `rolang clean` | Remove the `build/` directory |
| `rolang install` | Fetch and install all dependencies |
| `rolang add <name> --path <path>` | Add a local path dependency |
| `rolang add <name> --git <url> --tag <tag>` | Add a git dependency |
| `rolang remove <name>` | Remove a dependency |
| `rolang info` | Show project metadata |

## Project Manifest (`rolang.toml`)

```toml
[package]
name    = "my-app"
version = "0.1.0"
type    = "binary"   # or "library"

[dependencies]
utils = { path = "../utils" }
mylib = { git = "https://github.com/org/mylib", tag = "v1.0.0" }

[build]
opt-level  = 0       # 0–3
output-dir = "build"

[[bin]]
name = "my-app"
path = "src/main.rl"

[[test]]
name = "integration"
path = "tests/integration.rl"
```

Dependencies are installed under `.rolang/deps/<name>/` and added to the include path automatically:

```rolang
import "mylib/src/lib.rl"
```

## Language

### Hello, World

```rolang
import "io.rl"

def main() -> Void {
    print("Hello, World!")
}
```

### Bindings

`let` freezes the binding; `var` allows rebinding. All struct and enum values live on the heap and are reference-counted — `let` only prevents the variable from being rebound to a different object, it does not make the object's fields immutable.

```rolang
let x: i32 = 10;
var y: i32 = 20;
y = 30;       // ok
// x = 5;    // error: cannot rebind a let binding

struct Point { var x: f64; var y: f64; }
let p = Point(x: 1.0, y: 2.0);
p.x = 9.0;   // ok — same object, only the binding is frozen
```

### Primitives

`i8` `i16` `i32` `i64` · `u8` `u16` `u32` `u64` · `f32` `f64` · `Bool` · `Void`

Smaller integers widen implicitly in assignments, arguments, and binary operations (`i8 → i64`, `u32 → i64`, etc.). All other conversions require an explicit `as` cast.

### Structs and Methods

```rolang
struct Vec2 {
    var x: f64
    var y: f64

    def length() -> f64 {
        sqrt(self.x * self.x + self.y * self.y)
    }

    def __add__(other: Vec2) -> Vec2 {
        self.x = self.x + other.x;
        self.y = self.y + other.y;
        return self;
    }

    def __release__() -> Void {
        // runs when the last reference is released
    }
}

`__release__` runs exactly once at end of life, before the runtime walks the object's pointer fields. Operator overloading uses `__add__`, `__sub__`, `__mul__`, etc.

### Enums and Pattern Matching

```rolang
enum Shape {
    Circle(radius: f64)
    Rect(w: f64, h: f64)
}

def area(s: Shape) -> f64 {
    switch s {
        case .Circle(let r):      return 3.14159 * r * r;
        case .Rect(let w, let h): return w * h;
    }
}
```

`switch` is exhaustive — the compiler rejects unhandled cases.

### Optionals

```rolang
def find(items: [String], target: String) -> String? {
    for s in items {
        if s == target { return s; }
    }
    return nil;
}

let result = find(["a", "b"], "b");
let val    = result ?? "not found";  // nil-coalescing
let upper  = result?.uppercased();   // optional chaining
if let v = result { print(v); }      // if-let binding
```

### Error Handling

`Result<T, E>` with `try` / `?` for propagation:

```rolang
import "result.rl"

def parse_int(s: String) -> Result<i32, String> { /* ... */ }

def compute() -> Result<i32, String> {
    let n = try parse_int("42");   // returns Err upstream, unwraps Ok
    return Result.ok(value: n * 2);
}
```

### Generics and Protocols

```rolang
protocol Printable {
    def display() -> String
}

def print_all<T: Printable>(items: [T]) -> Void {
    for item in items { print(item.display()); }
}

// Dynamic dispatch via existential type
def show(item: any Printable) -> Void {
    print(item.display());
}
```

Protocols can be used with static dispatch through generics (`<T: P>`) or dynamic dispatch through existentials (`any P`).

### Async / Await

Async functions compile to state machines driven by a single-threaded cooperative task scheduler in the runtime. Every `await` is a yield point; the scheduler runs all tasks to completion without OS threads or preemption.

```rolang
def load() async -> i32 {
    return 42;
}

def process() async -> i32 {
    let a = await load();
    let b = await load();
    return a + b;
}

def main() async -> i32 {
    await process()
}
```

`async` propagates upward — calling an async function from a non-async context is a compile error.

### Closures and Function Values

```rolang
let add = (a: i32, b: i32) -> i32 { a + b };
let result = add(3, 4);   // 7

def apply(f: (i32) -> i32, x: i32) -> i32 { f(x) }
let doubled = apply((n: i32) -> i32 { n * 2 }, 5);  // 10
```

Closures capture variables from their enclosing scope. Bare function references and closures share the same type and calling convention.

### Unsafe Interop

```rolang
extern "C" def malloc(size: i64) -> RawPtr
extern "C" def free(ptr: RawPtr) -> Void

def alloc_bytes(n: i64) -> RawPtr {
    unsafe { return malloc(n); }
}
```

`unsafe { }` is required for `RawPtr` operations, `extern` calls, and user-declared `unsafe def` functions. The unsafe context does not propagate into closures.

## Standard Library

`Vec<T>`, `Dict<K,V>`, and `String` are available in every file without an explicit import. Everything else requires `import "module.rl"`.

| Module | Contents |
|---|---|
| `vec.rl` | `Vec<T>` — growable array |
| `dict.rl` | `Dict<K, V>` — hash map with insertion-ordered iteration |
| `string.rl` | `String` — UTF-8 string |
| `result.rl` | `Result<T, E>` |
| `array.rl` | Fixed-array utilities |
| `iter.rl` | Iterator protocol |
| `io.rl` | `print`, `println` |
| `fs.rl` | File system helpers |
| `math.rl` | `sqrt`, `sin`, `cos`, `atan2`, … |
| `char.rl` | Character utilities |
| `path.rl` | Path manipulation |
| `process.rl` | Process and exit helpers |
| `set.rl` | `Set<T>` |
| `linked_list.rl` | Linked list |
| `fmt.rl` | String formatting |
| `test.rl` | Test assertions |

## Low-Level Compiler (`rolangc`)

```bash
rolangc hello.rl                 # compile to ./hello
rolangc hello.rl -o greet        # custom output name
rolangc -c hello.rl -o hello.o   # object file only
rolangc --emit llvm hello.rl     # print LLVM IR
rolangc --emit mir  hello.rl     # print MIR (debug)
rolangc -O2 hello.rl             # optimized build
rolangc -I ./deps hello.rl       # add include path
```

## Compiler Pipeline

```
Source (.rl)
    → Parser         → AST
    → Resolver       → Symbols
    → Type Checker   → Typed AST
    → HIR Builder    → High-level IR
    → Monomorphizer  → Specialized HIR
    → MIR Builder    → Mid-level IR (explicit CFG)
    → Async Lowering → State-machine MIR
    → ARC Insertion  → MIR with retain/release
    → Codegen        → LLVM IR → Object file
    → Linker         → Native executable
```

## Benchmarks

A cross-language suite lives in `benchmarks/`. Each program is byte-identical in
output across all seven languages; timings are the **minimum** wall-clock over
repeated runs (the most stable estimator), measured on the same machine with
Rolang built at `-O3`.

```bash
python benchmarks/runner.py                  # run all, all languages
python benchmarks/runner.py --langs C,Rolang # subset
python benchmarks/runner.py --out results.md # append a markdown report
```

Min wall-clock in milliseconds (lower is better); **×C** is Rolang relative to C.

| Benchmark    |     C | Rolang |   ×C |  Rust |    Go |   Java | Node.js |   Python |
|--------------|------:|-------:|-----:|------:|------:|-------:|--------:|---------:|
| fib          | 22.7  |  34.0  | 1.50 |  35.6 |  55.6 |   77.3 |   306.6 |   1518.0 |
| mandelbrot   | 178.9 | 189.7  | 1.06 | 185.4 | 193.0 |  219.6 |   368.4 |   5484.3 |
| json_parse   | 31.7  |  44.8  | 1.41 |  31.2 |  61.8 |  166.9 |   361.5 |    609.6 |
| nbody        | 809.1 | 930.0  | 1.15 | 865.9 | 901.7 |  977.1 |  1748.6 |  14182.1 |
| word_freq    | 42.8  |  78.3  | 1.83 |  66.4 |  55.0 |  159.4 |   463.7 |    814.4 |
| binary_trees | 66.3  | 193.9  | 2.93 |  84.6 | 125.7 |  103.9 |   275.0 |   1345.2 |

Execution models: C/Rust = native (no GC), Go/Java/Node = managed (GC/JIT),
Python = interpreted, Rolang = native with automatic reference counting + a
generational cycle collector. On compute-bound workloads Rolang runs within
~1.1–1.5× of C and ahead of Rust on `fib`; the allocation-heavy `binary_trees`
is the current outlier and an active optimization target.

## Development

```bash
uv sync                                        # install dev dependencies
uv run pytest                                  # full test suite
uv run pytest tests/test_parser.py             # parser only
uv run pytest tests/test_checker.py            # type checker only
uv run pytest tests/test_runtime_execution.py  # end-to-end execution tests
uv run pytest tests/test_toolchain.py          # toolchain tests
```
