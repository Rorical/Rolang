# Chapter 18: Modules and Imports

Rolang programs are composed of source files. Each file is a module. The `import` statement brings the declarations from one module into another.

## Importing a File

```rolang
import "math.rl"
import "io.rl"
```

The string path is resolved relative to the importing file. A path beginning with a standard library name (e.g. `"vec.rl"`, `"math.rl"`) resolves to the bundled standard library.

When a module is imported, all its `pub`-marked declarations become available in the importing file.

## Implicit Imports

Three standard library modules are imported automatically into every non-stdlib source file:

- `vec.rl` — `Vec<T>`, `VecIter<T>`, and related free functions
- `dict.rl` — `Dict<K, V>` and related free functions
- `string.rl` — `String` and numeric `.to_string()` extensions

You do not need to write these imports.

## Visibility

By default, declarations are **internal** — visible only within the same compilation unit (file):

```rolang
struct InternalHelper { var n: i32; }   // not visible to importers

def internal_fn(x: i32) -> i32 { x * 2 }   // not visible to importers
```

Mark a declaration `pub` to export it:

```rolang
pub struct Point {
    pub var x: f64;
    pub var y: f64;
}

pub def distance(a: Point, b: Point) -> f64 { ... }
```

Fields follow the same rule: a `pub struct` with internal fields exposes the type but not the fields:

```rolang
pub struct Config {
    pub var timeout: i32;
    var secret_key: String;   // not accessible outside this file
}
```

## Including Paths

Pass `-I path` to `rolangc` to add directories to the import search path. The `rolang` toolchain does this automatically for installed dependencies:

```bash
rolangc -I ./libs my_program.rl
```

With an include path set, `import "utils/helpers.rl"` resolves to `./libs/utils/helpers.rl`.

## The Module Graph

When a file is compiled with imports, the compiler builds a *module graph* — a directed acyclic graph of all files reachable from the entry point, in dependency order. Each module is resolved, type-checked, and compiled once, then linked together.

Circular imports are detected and reported as errors.

## Dotted Module Paths

Standard library modules can also be imported with dotted syntax:

```rolang
import std.io
import std.math
```

Dotted paths resolve to the same bundled standard library files.

## Structuring a Multi-File Project

A typical project layout:

```
src/
├── main.rl           # entry point
├── models/
│   ├── user.rl
│   └── post.rl
└── utils/
    ├── string_ext.rl
    └── math_helpers.rl
```

`main.rl`:
```rolang
import "models/user.rl"
import "models/post.rl"
import "utils/string_ext.rl"
import "io.rl"

def main() -> i32 {
    let user = create_user("Alice");
    println(user.name);
    return 0;
}
```

`models/user.rl`:
```rolang
pub struct User {
    pub var name: String;
    pub var id: i32;
}

pub def create_user(name: String) -> User {
    return User { name: name, id: 1 };
}
```

## Error Cases

The import system reports errors for:

- File not found at the resolved path
- Circular imports (A imports B imports A)
- Non-`.rl` file extensions
- Absolute paths (use relative or `-I` paths instead)
- Case mismatches on case-sensitive filesystems
- Attempting to shadow a standard library module name

## Importing Dependencies

When a dependency is declared in `rolang.toml` and installed with `rolang install`, the toolchain makes it available under `.rolang/deps/` and adds that directory to the compiler's include path. Four import styles all work after installation:

```toml
[dependencies]
utils = { path = "../utils" }
```

```rolang
// 1. Module name — shortest form, works when the dep has a [lib] target
import utils

// 2. Quoted filename — same as above, explicit .rl extension
import "utils.rl"

// 3. Dotted sub-module — import a specific file inside the package
import utils.helpers          // resolves to utils/helpers.rl

// 4. Full explicit path — always works regardless of lib declaration
import "utils/src/lib.rl"
```

Forms 1 and 2 require the dependency to declare a library target (see [Chapter 20](ch20-toolchain.md)). The toolchain creates a `.rolang/deps/utils.rl` symlink pointing at the lib entry file, which the compiler finds through the include root.

Forms 3 and 4 always work — they resolve any `.rl` file inside the package tree.

See [Chapter 20](ch20-toolchain.md) for full package management and library creation details.

## Summary

- `import "path/to/file.rl"` brings a module's `pub` declarations into scope
- `Vec<T>`, `Dict<K,V>`, and `String` are available implicitly — no import required
- `pub` exports a declaration; without it, declarations are internal to the file
- Fields also require `pub` to be accessible outside their defining module
- The compiler discovers all transitive imports, checks for cycles, and compiles in dependency order
- Use `-I dir` for additional search paths; the toolchain handles this for declared dependencies
