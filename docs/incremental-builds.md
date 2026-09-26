# Incremental project builds

`rolang build`, `rolang run`, and `rolang test` now cache completed native targets
by default. Use verbose output to see hits:

```sh
rolang build -v
rolang build -v           # Cached -> build/<target>
rolang build --no-cache  # bypass cache reads and writes
rolang clean             # remove outputs and cache
```

`run` and `test` also accept `--no-cache`. Cached test binaries are still executed
on every `rolang test` invocation. `rolang check` continues to run its checks
without using native build artifacts.

## What is reused

The cache stores complete executable or library-object outputs, with their
checksums and file permissions, under `<output-dir>/.rolang-cache/`. Each target
has its own input fingerprint. Editing a source used by one target rebuilds that
target; targets with unchanged inputs reuse their outputs. Previously built
configurations remain reusable when switching optimization levels.

A valid hit skips parsing, resolution, type checking, lowering, code generation,
and linking. It leaves an intact output's timestamp unchanged, or restores a
missing or damaged output atomically. The driver also supports caching `.rlm`
output through `CompileOptions(cache_dir=..., output_path=...)`.

This is caching at target granularity. A changed target still passes its full
import graph through the compiler. Automatic conversion of source dependencies
to separately cached `.rlm` modules, per-function recompilation, and intermediate
representation caching remain future work. `rolangc` keeps its existing uncached
behavior unless its Python driver is explicitly configured with a cache directory.

## Invalidation

Fingerprints include:

- Entry and transitive source contents, resolved import edges, and native module
  object contents. They use content hashes rather than modification times.
- Import-search candidates, including missing higher-priority files, and their
  resolved symlink targets. Adding a shadowing import or retargeting a symlink
  invalidates the saved discovery result.
- Effective project manifest and lockfile, include paths, output path, output
  kind, optimization level, and requested target.
- Compiler implementation, grammar, bundled library/runtime, llvmlite version,
  LLVM version, and host target.
- For executables: C compiler path, executable contents, version report, and the
  macOS SDK path/version where applicable.
- Relevant build environment values, including `CC`, `ROLANG_RT_CFLAGS`,
  `SDKROOT`, `DEVELOPER_DIR`, deployment target, compiler/include/library search
  paths, and reproducibility timestamp controls.

Warm builds validate recorded search candidates directly instead of reparsing
imports. If validation fails, normal discovery runs again and refreshes the
record. Dependency installation still runs before cache lookup, so changes to
resolved package inputs are visible to the compiler.

Only successful builds without diagnostics are cached. Builds with warnings run
again so warnings remain visible. Failures are never cached. Inputs are checked
again before saving; edits observed during compilation prevent cache insertion.

## Operational limits

The cache is disposable. Corrupt entries are ignored and rebuilt; an unavailable
cache does not turn an otherwise successful compilation into a failure. Artifact
and index writes use temporary files and atomic replacement. Entries larger than
512 MiB are not cached, and cache files are removed by `rolang clean`; there is no
automatic eviction policy yet.

This is a trusted local build cache, not an authenticated remote artifact store.
Arbitrary inputs read by custom C compiler wrappers or native flags (such as
external headers/libraries) are not exhaustively tracked. Use `--no-cache` after
changing such inputs or when bypassing the cache for diagnosis. Concurrent edits
or builds targeting the same output are not a supported build workflow.

## Validation

Regression tests cover pipeline skipping, output restoration, unchanged output
timestamps, edits with preserved timestamps, transitive changes, added shadowing
imports, retargeted symlinks, configuration changes, compiler changes, corrupted
artifacts/indexes, failed builds, warnings, unavailable caches, per-target reuse,
`.rlm` changes, explicit bypass, clean, and edits during compilation.

A small local example on Apple Silicon macOS measured **2.608 seconds cold** and
**0.038 seconds warm**. This is one example, not a general performance guarantee.

Final validation on Apple Silicon macOS: **155 tests passed** across incremental
builds, the project toolchain, registries, compiler output modes, module artifacts,
and imports. The 15 incremental-build regressions include an assertion that a
warm build never invokes the parser or the compilation pipeline.
