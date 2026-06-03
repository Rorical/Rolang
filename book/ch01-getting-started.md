# Chapter 1: Getting Started

## Installing Rorical (Rolang)

Rolang requires Python 3.11 or later and the `uv` package manager. It also needs a C compiler reachable as `cc` for linking executables.

Install the toolchain globally with a single command:

```bash
uv tool install git+https://github.com/Rorical/Rolang
```

This installs two executables:

- `rolang` — the project manager (create, build, run, test, manage dependencies)
- `rolangc` — the low-level single-file compiler

Verify the installation:

```bash
rolang --version
rolangc --version
```

To update to the latest version:

```bash
uv tool upgrade rolang
```

## Your First Project

Create a new project, build it, and run it:

```bash
rolang new hello-world
cd hello-world
rolang run
```

You should see:

```
Hello, World!
```

The `rolang new` command creates this directory layout:

```
hello-world/
├── rolang.toml    # project manifest
├── src/
│   └── main.rl   # main source file
└── .gitignore
```

## Reading the Source

Open `src/main.rl`:

```rolang
import "io.rl"

def main() -> Void {
    print("Hello, World!")
}
```

There are three things to notice:

1. **`import "io.rl"`** — brings in the standard I/O module. The standard library is bundled with the compiler; no path is needed.
2. **`def main() -> Void`** — declares the entry point. `main` may return `Void` (nothing) or an integer type for an exit code.
3. **`print(...)`** — a function provided by `io.rl` that writes to standard output.

## The Project Manifest

`rolang.toml` describes your project:

```toml
[package]
name    = "hello-world"
version = "0.1.0"
type    = "binary"
```

The full manifest format is covered in [Chapter 20](ch20-toolchain.md).

## Compiling Without a Project

For quick experiments, `rolangc` compiles a single file directly:

```bash
rolangc hello.rl          # produces ./hello
./hello
```

To see intermediate representations:

```bash
rolangc --emit mir  hello.rl   # Mid-level IR (debug)
rolangc --emit llvm hello.rl   # LLVM IR
```

## What the Compiler Does

Every `.rl` file passes through this pipeline before becoming a native executable:

```
Source (.rl)
    → Parser        → AST
    → Resolver      → Symbols
    → Type Checker  → Typed AST
    → HIR Builder   → High-level IR
    → Monomorphizer → Specialized HIR
    → MIR Builder   → Mid-level IR
    → Async Lowering
    → ARC Insertion → retain/release calls
    → Codegen       → LLVM IR → Object file
    → Linker        → Native executable
```

The C runtime (`rolang_rt.c`) is compiled and linked automatically. It provides memory allocation, ARC reference counting, a cycle-detecting garbage collector, panic handlers, and the async task scheduler.

## Summary

- Install with `uv tool install git+https://github.com/Rorical/Rolang`
- `rolang new <name>` scaffolds a project; `rolang run` builds and executes it
- Source files use the `.rl` extension
- `main` is the entry point; return `Void` or an integer exit code
- `rolangc` compiles individual files when you don't need a full project
