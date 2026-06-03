# Chapter 20: The Rolang Toolchain

The `rolang` command is the project manager for Rolang. It handles creating projects, building, running, testing, and managing dependencies. The lower-level `rolangc` command compiles individual files directly.

## Creating a Project

```bash
rolang new my-app          # binary project
rolang new my-lib --lib    # library project
```

Both commands create a directory with a `rolang.toml` manifest and a starter source file.

To initialise a project in an existing directory:

```bash
cd existing-dir
rolang init          # binary
rolang init --lib    # library
```

## Creating a Library

A library is a package meant to be imported by other projects rather than run directly.

```bash
rolang new my-utils --lib
```

This creates:

```
my-utils/
├── rolang.toml
└── src/
    └── lib.rl
```

`rolang.toml` for a library:

```toml
[package]
name    = "my-utils"
version = "0.1.0"
type    = "library"
```

Expose your public API from `src/lib.rl` using `pub`:

```rolang
// src/lib.rl

pub def add(a: i32, b: i32) -> i32 {
    a + b
}

pub struct Point {
    pub var x: f64;
    pub var y: f64;

    pub def distance_to(other: Point) -> f64 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        sqrt(dx * dx + dy * dy)
    }
}
```

Only declarations marked `pub` are visible to importers.

## Importing a Library

After adding and installing a dependency, four import styles work:

```rolang
// 1. Module name — cleanest, recommended
import my-utils

// 2. Quoted filename
import "my-utils.rl"

// 3. Dotted sub-module — import a specific file inside the package
import my-utils.helpers       // resolves to my-utils/helpers.rl

// 4. Explicit path
import "my-utils/src/lib.rl"
```

Forms 1 and 2 resolve through a `.rl` entry-point shim that `rolang install` creates automatically at `.rolang/deps/my-utils.rl`, pointing at the lib target declared in the dependency's `rolang.toml`. Forms 3 and 4 always work regardless.

### Adding the Library as a Dependency

In your app's `rolang.toml`:

```toml
[dependencies]
my-utils = { path = "../my-utils" }
```

Then install and use it:

```bash
rolang install
```

```rolang
import my-utils

def main() -> i32 {
    let result = add(3, 4);
    return result;
}
```

## Project Structure

A typical binary project:

```
my-app/
├── rolang.toml
├── src/
│   └── main.rl
└── .gitignore
```

A library project uses `src/lib.rl` instead.

## The Manifest: `rolang.toml`

`rolang.toml` describes the project. All fields in `[package]` are required; other sections are optional.

```toml
[package]
name    = "my-app"
version = "0.1.0"
description = "A description"
authors = ["Alice <alice@example.com>"]
edition = "2024"
type    = "binary"   # "binary" or "library"

[dependencies]
utils = { path = "../utils" }
mylib = { git = "https://github.com/org/mylib", tag = "v1.2.0" }

[dev-dependencies]
test-helpers = { path = "../test-helpers" }

[build]
opt-level  = 0       # 0, 1, 2, or 3
output-dir = "build" # where compiled binaries go

[[bin]]
name = "my-app"
path = "src/main.rl"

[[bin]]
name = "worker"
path = "src/worker.rl"

[[test]]
name = "integration"
path = "tests/integration.rl"
```

When no `[[bin]]` section is present, the toolchain infers a single binary named after the package with `src/main.rl` as its source. A library project infers `src/lib.rl`.

## Building

```bash
rolang build             # debug build (opt-level from manifest, default 0)
rolang build --release   # optimized build (opt-level 2)
```

Output goes to `build/<name>` (or `build/lib<name>.o` for libraries).

## Running

```bash
rolang run               # build and execute
rolang run --release     # build with optimizations and execute
rolang run --bin worker  # run a specific binary target
rolang run -- arg1 arg2  # pass arguments to the binary
```

## Checking

Type-check without producing any output:

```bash
rolang check
```

Useful for fast feedback during development.

## Cleaning

Remove the build directory:

```bash
rolang clean
```

## Project Information

```bash
rolang info
```

Prints the project name, version, type, targets, and dependencies.

## Dependencies

### Adding Dependencies

```bash
# Local path dependency
rolang add utils --path ../utils

# Git dependency (latest default branch)
rolang add mylib --git https://github.com/org/mylib

# Git dependency at a specific tag or branch
rolang add mylib --git https://github.com/org/mylib --tag v1.2.0
rolang add mylib --git https://github.com/org/mylib --branch main

# Dev-only dependency
rolang add test-utils --path ../test-utils --dev
```

Each command updates `rolang.toml` with the new entry.

### Removing Dependencies

```bash
rolang remove utils
```

Removes the entry from `rolang.toml` and from `rolang.lock`.

### Installing Dependencies

```bash
rolang install          # install runtime dependencies
rolang install --dev    # also install dev dependencies
```

Dependencies are installed as symlinks in `.rolang/deps/<name>/`. The `build/` and `.rolang/deps/` directories are automatically added to the include paths when compiling.

Import a dependency:

```rolang
import "utils/src/lib.rl"
```

(Where `utils` is the dependency name and `src/lib.rl` is the path within it.)

### The Lock File

`rolang.lock` is generated automatically and records the exact resolved version and source of every dependency. Commit this file to version control.

```toml
# This file is automatically generated by the Rolang toolchain.
# Do not edit manually. Commit this file to version control.

version = 1

[[package]]
name = "utils"
version = "0.3.0"
source = "path:../utils"
```

## Testing

Define test targets in `rolang.toml`:

```toml
[[test]]
name = "unit"
path = "tests/unit.rl"

[[test]]
name = "integration"
path = "tests/integration.rl"
```

Run them:

```bash
rolang test              # run all tests
rolang test unit         # run only tests whose name contains "unit"
rolang test -v           # verbose output
```

Each test target is compiled to a binary and executed. A zero exit code means the test passed; non-zero means it failed.

## Workspaces

A workspace groups multiple packages under a single root `rolang.toml`:

```toml
[workspace]
members = ["core", "cli", "web"]
```

Each member directory must contain its own `rolang.toml`. The workspace root can also declare a `[package]` section if the root is itself a package.

## The Low-Level Compiler: `rolangc`

`rolangc` compiles a single `.rl` file. Use it for quick experiments, when you don't need a full project, or when you need fine-grained control over compiler options.

```bash
# Compilation modes
rolangc hello.rl                    # compile to executable ./hello
rolangc hello.rl -o out             # custom output name
rolangc -c hello.rl -o hello.o      # object file only (no link)

# Emit intermediate representations
rolangc --emit mir  hello.rl        # print MIR to stdout
rolangc --emit llvm hello.rl        # print LLVM IR to stdout
rolangc --emit obj  hello.rl -o a.o # object file

# Optimization
rolangc -O0 hello.rl    # no optimisation (default)
rolangc -O1 hello.rl    # basic
rolangc -O2 hello.rl    # standard
rolangc -O3 hello.rl    # aggressive

# Include paths
rolangc -I ./libs hello.rl
rolangc -I ./libs -I ./vendor hello.rl

# Target triple
rolangc --target x86_64-unknown-linux-gnu hello.rl

# Verbose output
rolangc -v hello.rl
```

## Summary of Commands

| Command | Description |
|---------|-------------|
| `rolang new <name>` | Create a new project directory |
| `rolang new <name> --lib` | Create a library project |
| `rolang init [--lib]` | Init in the current directory |
| `rolang build [--release]` | Compile the project |
| `rolang run [-- args]` | Build and run |
| `rolang check` | Type-check only |
| `rolang clean` | Remove build output |
| `rolang test [filter]` | Run test targets |
| `rolang install [--dev]` | Fetch and install dependencies |
| `rolang add <name> ...` | Add a dependency |
| `rolang remove <name>` | Remove a dependency |
| `rolang info` | Show project metadata |
