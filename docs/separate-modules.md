# Separate native modules

Build a library artifact with `rolangc`:

```sh
rolangc --emit module -O2 math.rl -o math.rlm
rolangc -O2 main.rl -o main
```

```rolang
// math.rl
pub def answer() -> i32 { return 42; }
pub def identity<T>(value: T) -> T { return value; }
```

```rolang
// main.rl
import "math.rlm"
def main() -> i32 { return identity(answer()); }
```

The `.rlm` file can be moved or distributed without the original `.rl` files.
File imports and `-I` search roots accept it. A module cannot define `main`.
Build user dependencies as modules first and import their `.rlm` files; bundled
standard library imports continue to work normally.

## Contents and linking

An artifact is a ZIP archive containing JSON metadata, source snapshots, a native
object, and the native objects of its transitive dependencies. Generic bodies
remain available for specialization with types introduced by a consumer.
Visibility rules still apply to declarations imported from an artifact.

The consumer emits declarations for imported non-generic functions and links the
saved objects. Generic specializations and bundled standard library definitions
use coalescible linkage. Shared dependency objects are deduplicated; conflicting
versions are diagnosed. Private functions with the same spelling in different
modules have different native identities.

Heap type identities remain stable across compilations. Each object registers
its own descriptor tables before program startup. The runtime checks full type
identities and layouts when multiple modules register the same descriptor ID.
This supports ARC, destructors, generic collections, and async task frames across
module boundaries.

## Current constraints

- Artifacts contain source, including private implementation and original source
  paths. They are not an opaque binary distribution format.
- Consumers still parse, resolve, type-check, and lower the recorded sources.
  Native non-generic bodies are removed before object emission. This is native
  object reuse, not an incremental front-end cache.
- Artifacts are pinned to the compiler implementation, bundled standard library,
  llvmlite version, and target triple. Rebuild them after compiler changes.
  Object checksums detect damaged artifacts.
- Source path and content contribute to declaration identities. Changing a
  library requires rebuilding its dependent artifacts; there is no stable ABI
  across library revisions.
- `--emit obj` on an artifact consumer emits only that translation unit. Use
  executable output to link dependency objects automatically, or distribute a
  library with `--emit module`. Plain `.rl` imports retain unified compilation.
- `rolang build` and the path/Git package manager retain their existing source
  workflow. Automated artifact caching and registry distribution are future work.
- Validation currently targets the native Apple Silicon macOS host. Cross-target
  runtime linking is not established by these tests.

## Regression coverage

`tests/test_module_artifacts.py` covers O0–O3 execution with relocated artifacts
and deleted source files, consumer-only generic instantiations, heap objects,
async tasks, generic methods, callbacks, collection values, destructors, diamond
dependencies, damaged or incompatible metadata, and compiler output products.

Validation on 26 September 2026 (Apple Silicon macOS): the full suite passed
**819 tests**. After the final sparse-ID cycle-analysis and compatibility checks,
**69 focused tests** passed, including all 16 module regressions, code generation,
layout, and cycle analysis. Seven module scenarios also passed with the C runtime
built under AddressSanitizer and payload-size checks; cycle collection was rerun
after the final analysis change. This does not instrument generated LLVM code or
establish leak-sanitizer coverage.
