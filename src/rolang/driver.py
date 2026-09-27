"""
Compilation driver for the Rolang compiler.

Orchestrates the compilation pipeline from source to executable.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import Enum, auto
from importlib import resources
from pathlib import Path
from typing import List, Optional

from . import ast as ast_module
from .parser import parse
from .resolver import resolve
from .checker import typecheck
from .hir_builder import build_hir
from .monomorphize import monomorphize
from .mir_builder import build_mir
from .mir_outparam_init import elide_outparam_default_init
from .async_lowering import lower_async
from .arc_insertion import insert_arc
from .mir_optimize import optimize_mir
from .codegen import compile_to_llvm, compile_to_object
from .codegen.object_file import compile_module_to_assembly, optimize_module_to_ir
from .mir import format_program as format_mir
from .diagnostics import DiagnosticCollector
from .module import ModuleState
from .context import CompilerContext, PassRunner
from .types import TypeTable
from .symbols import SymbolTable


class EmitKind(Enum):
    """Output format for compilation."""
    MODULE = auto()      # Native module archive with generic metadata (.rlm)
    OBJECT = auto()      # Object file (.o)
    LLVM_IR = auto()     # LLVM IR (.ll)
    MIR = auto()         # Mid-level IR (debug)
    MIR_OPTIMIZED = auto()  # Lowered, optimized MIR with ARC
    LLVM_OPTIMIZED = auto() # LLVM IR after backend optimization
    ASSEMBLY = auto()       # Native assembly (.s)
    EXECUTABLE = auto()  # Linked executable


class OptLevel(Enum):
    """Optimization level."""
    O0 = 0  # No optimization
    O1 = 1  # Basic optimizations
    O2 = 2  # Standard optimizations
    O3 = 3  # Aggressive optimizations


@dataclass
class CompileOptions:
    """Compilation options."""
    emit: EmitKind = EmitKind.EXECUTABLE
    opt_level: OptLevel = OptLevel.O0
    output_path: Optional[Path] = None
    target_triple: Optional[str] = None
    runtime_path: Optional[Path] = None
    include_paths: List[Path] = field(default_factory=list)
    verbose: bool = False
    use_color: Optional[bool] = None
    cache_dir: Optional[Path] = None
    cache_context: str = ""


@dataclass
class CompileResult:
    """Result of compilation."""
    success: bool
    output_path: Optional[Path] = None
    output_content: Optional[str] = None  # For LLVM IR / MIR output
    diagnostics: Optional[DiagnosticCollector] = None


class CompilationDriver:
    """Orchestrates the compilation pipeline."""

    def __init__(self, options: CompileOptions) -> None:
        self.options = options
        self._cache_base_key = None
        self._cache_watches = {}
        self.source_files: dict[Path, str] = {}
        self.context: Optional[CompilerContext] = None
        self.runner: Optional[PassRunner] = None
        # Bundled stdlib location: src/rolang/std/ ships alongside this driver
        # module. We use it to resolve unqualified imports like
        # `import "char.rl"` or module-path imports like `import std.io`.
        self.stdlib_path: Path = Path(__file__).parent / "std"

    def compile_file(self, source_path: Path) -> CompileResult:
        self._build_cache = None
        self._cache_base_key = None
        self._cache_watches = {}
        self.cache_hit = False
        result = self._compile_file(source_path)
        if (self._build_cache is not None and result.success
                and result.output_path and not (result.diagnostics and result.diagnostics.diagnostics)):
            from .build_cache import snapshot, save_index
            try:
                # Do not associate an artifact with inputs edited during compilation.
                if all(snapshot(Path(p)) == state for p, state in self._cache_watches.items()):
                    if not self.cache_hit:
                        self._build_cache.store(result.output_path)
                    save_index(self.options.cache_dir, self._cache_base_key,
                               self._build_cache, self._cache_watches)
            except (OSError, RuntimeError):
                pass  # Cache failures never make successful compilation fail.
        return result

    def _try_build_cache(self) -> Optional[CompileResult]:
        if (self.options.cache_dir is None or self.options.output_path is None
                or self.options.emit not in (EmitKind.EXECUTABLE, EmitKind.OBJECT, EmitKind.MODULE)
                or self.context.diagnostics.diagnostics):
            return None
        if self._cache_base_key is None:
            return None
        from .build_cache import BuildCache, cache_key
        try:
            self._build_cache = BuildCache(self.options.cache_dir, cache_key(self))
            if self._build_cache.restore(self.options.output_path):
                self.cache_hit = True
                if self.options.verbose:
                    print(f"Cached -> {self.options.output_path}")
                return CompileResult(True, self.options.output_path, diagnostics=self.context.diagnostics)
        except (OSError, subprocess.SubprocessError):
            self._build_cache = None
        return None

    def _watch_path(self, path: Path) -> Path:
        if self._cache_base_key is not None:
            from .build_cache import snapshot
            name = str(path.absolute())
            try:
                if name not in self._cache_watches:
                    self._cache_watches[name] = snapshot(path)
            except (OSError, RuntimeError):
                self._cache_base_key = None
        return path

    def _try_early_cache(self) -> Optional[CompileResult]:
        if (self.options.cache_dir is None or self.options.output_path is None
                or self.options.emit not in (EmitKind.EXECUTABLE, EmitKind.OBJECT, EmitKind.MODULE)):
            return None
        from .build_cache import cache_key, restore_index
        try:
            self._cache_base_key = cache_key(self, inputs=False)
            if restore_index(self.options.cache_dir, self._cache_base_key, self.options.output_path):
                self.cache_hit = True
                if self.options.verbose:
                    print(f"Cached -> {self.options.output_path}")
                return CompileResult(True, self.options.output_path, diagnostics=self.context.diagnostics)
        except (OSError, subprocess.SubprocessError):
            self._cache_base_key = None
        return None

    def _compile_file(self, source_path: Path) -> CompileResult:
        """
        Compile a source file and any transitively imported files.

        If the file has no import declarations, uses the fast single-file path.
        Otherwise discovers and compiles all imports in dependency order.
        """
        self.source_files = {}
        self.module_sources = {}
        self.module_objects = {}
        self.compile_order = []
        self._module_link_objects = []
        self.separate_modules = self.options.emit == EmitKind.MODULE
        self.entry_path = source_path.resolve()
        diagnostics = DiagnosticCollector(self.source_files)
        self.context = CompilerContext(
            symbol_table=SymbolTable(),
            type_table=TypeTable(),
            diagnostics=diagnostics,
        )
        self.runner = PassRunner(self.context, verbose=self.options.verbose)

        cached = self._try_early_cache()
        if cached is not None:
            return cached
        self._watch_path(source_path)
        # Read source file
        try:
            source_content = source_path.read_text(encoding="utf-8")
        except OSError as e:
            diagnostics.add_io_error(str(e), source_path)
            return CompileResult(success=False, diagnostics=diagnostics)

        self.source_files[source_path.resolve()] = source_content

        # Parse
        if self.options.verbose:
            print(f"Parsing {source_path}...")

        try:
            ast = parse(source_content)
        except Exception as e:
            error_msg = str(e)
            line, col = 1, 1
            if hasattr(e, "line"):
                line = e.line
            if hasattr(e, "column"):
                col = e.column
            diagnostics.add_parse_error(error_msg, source_path, line, col)
            return CompileResult(success=False, diagnostics=diagnostics)

        self._inject_implicit_core_imports(source_path, ast)

        # Check for imports
        has_imports = any(isinstance(item, ast_module.ImportDecl) for item in ast.items)
        if has_imports or self.separate_modules:
            return self._compile_with_imports(source_path, ast)

        return self._compile_single(source_path, ast, source_content)

    def _inject_implicit_core_imports(self, source_path: Path, ast_node) -> None:
        """Make core std heap types available without user imports."""
        try:
            source_path.resolve().relative_to(self.stdlib_path.resolve())
            return
        except ValueError:
            pass

        existing = {
            item.path for item in ast_node.items
            if isinstance(item, ast_module.ImportDecl) and item.path
        }
        implicit = []
        for path in ("vec.rl", "dict.rl", "string.rl", "range.rl"):
            if path not in existing:
                implicit.append(ast_module.ImportDecl(path=path))
        if implicit:
            ast_node.items = implicit + list(ast_node.items)

    def _compile_single(self, source_path: Path, ast_node, source_content: str) -> CompileResult:
        """Compile a single source file with no imports."""
        cached = self._try_build_cache()
        if cached is not None:
            return cached
        # Resolve names
        if self.options.verbose:
            print("Resolving names...")

        resolution_result = resolve(ast_node)
        for error in resolution_result.errors:
            self.context.diagnostics.add_resolution_error(error, source_path)

        if self.context.diagnostics.has_errors():
            return CompileResult(success=False, diagnostics=self.context.diagnostics)

        return self._run_pipeline(ast_node, resolution_result, source_path)

    def _compile_with_imports(self, entry_path: Path, entry_ast) -> CompileResult:
        """Compile an entry file and all its transitive imports."""
        from . import module as mod

        # Discover all imported files. The module graph is keyed by the
        # canonical resolved path of each file so that two files sharing
        # a basename (e.g. lib/util.rl and vendor/util.rl) do not collide.
        graph = mod.ModuleGraph()
        self._discover_imports(entry_path, entry_ast, graph, set())

        if self.context.has_errors():
            return CompileResult(success=False, diagnostics=self.context.diagnostics)

        try:
            compile_order = graph.get_compilation_order()
        except ValueError as e:
            self.context.diagnostics.add_error(f"Circular import: {e}")
            return CompileResult(success=False, diagnostics=self.context.diagnostics)

        if self.options.verbose:
            print(f"Compiling {len(compile_order)} module(s): {[m.name for m in compile_order]}")

        self.compile_order = compile_order
        cached = self._try_build_cache()
        if cached is not None:
            return cached
        if self.separate_modules:
            from .module_abi import mark_declarations, digest
            self.context.symbol_table.separate_modules = True
            self.context.symbol_table.module_type_keys = {}
            for module in compile_order:
                key = self._module_key(module.path)
                mark_declarations(module.ast, key, digest(self.source_files[module.path]))
                if (module.path != self.entry_path and not key.startswith('std:')
                        and key not in self.module_objects):
                    self.context.diagnostics.add_error(
                        f"Compile dependency '{module.path}' with --emit module and import its .rlm artifact")
            if self.context.has_errors():
                return self._fail()

        # All output formats share the same program: imported generic bodies
        # must be available when monomorphizing, including for object output.
        for module in compile_order:
            if self.options.verbose:
                print(f"  Resolving {module.name} ({module.path})...")
            if not self._resolve_module_exports(module, graph):
                return self._fail()

        return self._compile_unified(entry_path, compile_order)

    def _compile_unified(self, entry_path, compile_order) -> CompileResult:
        """Unified compilation: merge all module ASTs and compile as one program.

        Used for every output format so imported generics are specialized together.

        Each module has already been resolved individually (in
        _resolve_module_exports), so we reuse those per-module
        ResolutionResults. Running the resolver a second time over the
        merged AST would:
          * double-register every user symbol (two SymbolIds per name)
          * force every cross-module decl to share one flat scope, which
            forbids legitimate same-name declarations across modules
            (e.g. an `internal` helper in lib.rl colliding with the
            importer's own `helper`).
        """
        merged_items: list = []
        for module in compile_order:
            if module.ast:
                for item in module.ast.items:
                    # Drop ImportDecls from the merged program — they
                    # were already processed at per-module resolve time
                    # and downstream stages (HIR / MIR / codegen) have
                    # no use for them.
                    if isinstance(item, ast_module.ImportDecl):
                        continue
                    # Tag every kept top-level item with the canonical
                    # path of its source module so the checker can enforce
                    # cross-module visibility for extension methods (a
                    # non-`pub` extension is only callable from within its
                    # declaring module).
                    try:
                        item._source_module = module.name
                    except (AttributeError, TypeError):
                        pass
                    merged_items.append(item)

        merged_ast = ast_module.Program(items=merged_items)

        resolution_result = self._aggregate_resolution_results(compile_order)

        if self.context.has_errors():
            return self._fail()

        return self._run_pipeline(merged_ast, resolution_result, entry_path)

    def _aggregate_resolution_results(self, compile_order):
        """Merge ResolutionResults from per-module resolves into one.

        The shared SymbolTable and node_symbols already aggregate across
        per-module runs; here we just stitch the per-module
        imported_symbols / extension_methods / self_symbols lookups into
        a single ResolutionResult for downstream passes.
        """
        from .symbols import ResolutionResult

        merged = ResolutionResult(
            symbol_table=self.context.symbol_table,
            node_symbols=self.context.node_symbols,
            errors=[],
        )
        for module in compile_order:
            r = getattr(module, '_resolution_result', None)
            if r is None:
                continue
            if r.imported_symbols:
                merged.imported_symbols.update(r.imported_symbols)
            if r.self_symbols:
                merged.self_symbols.update(r.self_symbols)
            if r.extension_methods:
                merged.extension_methods.extend(r.extension_methods)
            if r.imported_extension_methods:
                for type_name, methods in r.imported_extension_methods.items():
                    merged.imported_extension_methods.setdefault(type_name, []).extend(methods)
        return merged

    def _resolve_dotted_module(self, module_name: str) -> Optional[Path]:
        """Resolve a dotted module path (e.g. `std.io` -> `std/io.rl`).

        Searches in order: configured include paths, then the bundled
        standard library. Returns None when no candidate exists.
        """
        relative = Path(*module_name.split(".")).with_suffix(".rl")
        for root in self.options.include_paths:
            full = self._watch_path(root / relative).resolve()
            if full.exists():
                return full
        if self.stdlib_path.exists():
            full = self._watch_path(self.stdlib_path / relative).resolve()
            if full.exists():
                return full
        return None

    def _discover_imports(self, source_path: Path, ast_node, graph, visited: set) -> None:
        """Recursively discover all transitively imported files."""
        from . import module as mod

        resolved = source_path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)

        # Create module for this file. We key the module graph by the
        # canonical (resolved) path string so two files sharing a basename
        # in different directories cannot collide. `module.path` retains
        # the actual Path object for diagnostics; `module.name` is what
        # the graph indexes by.
        name = str(resolved)
        module = mod.Module(name=name, path=resolved)
        module.ast = ast_node
        module.source = self.source_files.get(resolved, "")
        module.import_paths = {}
        module.state = mod.ModuleState.PARSED
        graph.add_module(module)

        # Extract imports
        seen_imports: set[str] = set()
        for item in ast_node.items:
            if not isinstance(item, ast_module.ImportDecl):
                continue

            # Reject empty-string imports up front. The grammar accepts
            # `import ""` because STRING matches the empty string; we
            # don't want to silently treat it as a no-op.
            raw_path = getattr(item, 'path', '')
            module_parts = getattr(item, 'module', []) or []
            if not raw_path and not module_parts:
                self.context.diagnostics.add_error(
                    "Empty import path",
                    file_path=resolved,
                    code="E0007",
                )
                continue

            # Surface a non-fatal warning when the same import target appears
            # more than once in the same file. The resolver is idempotent on
            # duplicates, but it is almost always a typo / leftover edit.
            import_key = raw_path if raw_path else ".".join(module_parts)
            if import_key in seen_imports:
                self.context.diagnostics.add_warning(
                    f"Duplicate import of '{import_key}'",
                    file_path=resolved,
                    code="W0001",
                )
            seen_imports.add(import_key)

            # Warn on file-path imports that don't end in `.rl`. We
            # deliberately don't make this a hard error so embedded DSLs
            # or test fixtures can still opt in, but a typo like
            # `import "io"` would otherwise silently fail to find a file.
            if raw_path and not raw_path.endswith((".rl", ".rlm")):
                self.context.diagnostics.add_warning(
                    f"Imported file '{raw_path}' does not end in '.rl' or '.rlm'",
                    file_path=resolved,
                    code="W0002",
                )

            # Warn on absolute paths in imports. They are almost never
            # what the user actually wants and they are a common copy-paste artefact.
            if raw_path and Path(raw_path).is_absolute():
                self.context.diagnostics.add_warning(
                    f"Imported absolute path '{raw_path}'; consider a "
                    f"relative path or an -I include root",
                    file_path=resolved,
                    code="W0005",
                )

            dep_path: Optional[Path] = None

            record = self.module_sources.get(self._module_key(resolved))
            saved_import = record['imports'].get(import_key) if record else None
            if saved_import:
                dep_path = self._module_path(saved_import)

            # Handle file-path imports: import "./foo.rl"
            if raw_path and dep_path is None:
                import_path = raw_path
                # 1. Relative to the importing module
                candidate = self._watch_path(resolved.parent / import_path).resolve()
                if candidate.exists():
                    dep_path = candidate
                # 2. Each configured include path
                if dep_path is None:
                    for inc_path in self.options.include_paths:
                        candidate = self._watch_path(inc_path / import_path).resolve()
                        if candidate.exists():
                            dep_path = candidate
                            break
                # 3. Bundled standard library
                if dep_path is None and self.stdlib_path.exists():
                    stdlib_relative = Path(import_path)
                    if len(module_parts) > 1 and module_parts[0] == "std":
                        stdlib_relative = Path(*module_parts[1:]).with_suffix(".rl")
                    candidate = self._watch_path(self.stdlib_path / stdlib_relative).resolve()
                    if candidate.is_file():
                        dep_path = candidate
                if dep_path is None:
                    self.context.diagnostics.add_error(
                        f"Imported file not found: '{import_path}'",
                        file_path=resolved,
                        code="E0005",
                    )
                    continue

                # Warn when a user file shadows a bundled stdlib file:
                # `import "io.rl"` from a project that ships its own
                # `io.rl` next to main silently turns off the stdlib
                # `println` etc. for the whole project.
                if (
                    self.stdlib_path.exists()
                    and dep_path.parent != self.stdlib_path
                ):
                    stdlib_candidate = self._watch_path(self.stdlib_path / import_path).resolve()
                    if stdlib_candidate.exists() and stdlib_candidate != dep_path:
                        self.context.diagnostics.add_warning(
                            f"Imported file '{import_path}' shadows the bundled "
                            f"standard library file at '{stdlib_candidate}'",
                            file_path=resolved,
                            code="W0003",
                        )

                # Warn on case-mismatch between the imported path and the
                # actual filename on disk. Case-insensitive filesystems
                # (macOS HFS+/APFS, Windows NTFS-default) silently accept
                # `import "IO.rl"` when the real file is `io.rl`; on
                # Linux ext4 the same code is a hard "file not found".
                # Catch the portability footgun at compile time.
                requested_name = Path(import_path).name
                actual_name = dep_path.name
                if (
                    requested_name != actual_name
                    and requested_name.lower() == actual_name.lower()
                ):
                    self.context.diagnostics.add_warning(
                        f"Import path '{requested_name}' differs in case from "
                        f"the actual filename '{actual_name}'; this will fail "
                        f"on case-sensitive filesystems",
                        file_path=resolved,
                        code="W0004",
                    )

            # Handle module-path imports: import std.io
            elif dep_path is None and getattr(item, 'module', []):
                module_path = ".".join(item.module)
                dep_path = self._resolve_dotted_module(module_path)
                if not dep_path:
                    self.context.diagnostics.add_error(
                        f"Module not found: '{module_path}'",
                        file_path=resolved,
                        code="E0005",
                    )
                    continue

            if dep_path is None:
                continue

            dep_path = dep_path.resolve()
            if dep_path.suffix == '.rlm':
                try:
                    dep_path = self._load_module_artifact(dep_path)
                except ValueError as error:
                    self.context.diagnostics.add_error(str(error))
                    continue
            module.import_paths[import_key] = self._module_key(dep_path)
            # Resolve against the artifact's preserved module identity, even
            # when its source files no longer exist on this machine.
            # Preserve the exact discovery result through name resolution.
            # Installed packages can have differently named symlink entries.
            item.path = str(dep_path)

            # Reject self-imports up front with a clearer diagnostic than
            # "Circular dependency detected involving: {...}".
            if dep_path == resolved:
                self.context.diagnostics.add_error(
                    f"File '{resolved.name}' imports itself",
                    file_path=resolved,
                    code="E0006",
                )
                continue

            # Record dependency. The module graph is keyed by canonical
            # resolved path string to avoid file-stem collisions.
            dep_name = str(dep_path)
            module.add_dependency(dep_name)
            graph.add_dependency(name, dep_name)

            # Parse the dependency if not already parsed
            if dep_name not in graph.modules:
                try:
                    saved_source = self.module_sources.get(self._module_key(dep_path))
                    dep_content = saved_source['text'] if saved_source else dep_path.read_text(encoding="utf-8")
                except OSError as e:
                    self.context.diagnostics.add_io_error(str(e), dep_path)
                    continue
                self.source_files[dep_path] = dep_content
                try:
                    dep_ast = parse(dep_content)
                except Exception as e:
                    error_msg = str(e)
                    line, col = 1, 1
                    if hasattr(e, "line"): line = e.line
                    if hasattr(e, "column"): col = e.column
                    self.context.diagnostics.add_parse_error(error_msg, dep_path, line, col)
                    continue
                self._inject_implicit_core_imports(dep_path, dep_ast)

                # Recursively discover imports in the dependency
                self._discover_imports(dep_path, dep_ast, graph, visited)

    def _module_key(self, path):
        path = path.resolve()
        try:
            return 'std:' + path.relative_to(self.stdlib_path.resolve()).as_posix()
        except ValueError:
            return 'user:' + str(path)

    def _module_path(self, key):
        if key.startswith('std:'):
            relative = Path(key[4:])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Invalid standard module path')
            return self._watch_path(self.stdlib_path / relative).resolve()
        if not key.startswith('user:') or not Path(key[5:]).is_absolute():
            raise ValueError('Invalid user module path')
        return Path(key[5:]).resolve()

    def _module_target(self):
        from .codegen.object_file import get_host_triple
        return self.options.target_triple or get_host_triple()

    def _load_module_artifact(self, path):
        from .module_artifact import read_artifact
        entry, sources, objects = read_artifact(path, self._module_target())
        for key, record in sources.items():
            source_path = self._module_path(key)
            loaded = self.source_files.get(source_path)
            if loaded is not None and loaded != record['text']:
                raise ValueError(f'Conflicting module source for {key}')
            previous = self.module_sources.get(key)
            if previous is not None and previous != record:
                raise ValueError(f'Conflicting module versions for {key}')
        for key, data in objects.items():
            previous = self.module_objects.get(key)
            if previous is not None and previous != data:
                raise ValueError(f'Conflicting native module objects for {key}')
        self.module_sources.update(sources)
        self.module_objects.update(objects)
        self.separate_modules = True
        return self._module_path(entry)

    def _resolve_module_exports(
        self,
        module,
        graph,
    ) -> bool:
        """Resolve a single module and build its ModuleExports."""
        from .resolver import resolve_with_modules
        from .symbols import SymbolKind, SymbolId
        from .module import ModuleExports, Export, ExtensionExport

        # Snapshot the symbol-ID counter so we only export symbols
        # created by *this* module.
        start_id = self.context.symbol_table._next_id

        resolution_result = resolve_with_modules(
            module.ast,
            graph,
            module,
            symbol_table=self.context.symbol_table,
            node_symbols=self.context.node_symbols,
        )
        # Stash the per-module result so _compile_unified can aggregate
        # without re-running the resolver over the merged AST (which
        # would double-register every symbol and forbid same-name decls
        # across modules).
        module._resolution_result = resolution_result
        for error in resolution_result.errors:
            self.context.diagnostics.add_resolution_error(error, module.path)

        if self.context.has_errors():
            return False

        # Build stable ModuleExports from resolution result (no scanning)
        exports = ModuleExports()
        if resolution_result.node_symbols:
            exports.node_symbols.update(resolution_result.node_symbols)
        if getattr(resolution_result, 'imported_symbols', None):
            exports.imported_symbols.update(resolution_result.imported_symbols)

        for sym in self.context.symbol_table.symbols.values():
            if hasattr(sym, 'id') and sym.id.id < start_id:
                continue
            if not hasattr(sym, 'kind') or not hasattr(sym, 'name'):
                continue
            if sym.kind in (
                SymbolKind.FUNCTION, SymbolKind.STRUCT, SymbolKind.ENUM,
                SymbolKind.PROTOCOL, SymbolKind.TYPE_ALIAS,
                SymbolKind.VARIABLE, SymbolKind.EXTERN_FUNC,
            ):
                if getattr(sym, 'is_extension_method', False):
                    continue  # Extension methods go through extension_exports
                visibility = getattr(sym, 'visibility', 'internal')
                exports.exports[sym.name] = Export(
                    name=sym.name, symbol_id=sym.id,
                    kind=sym.kind.name.lower(), visibility=visibility,
                )

        for ext_tuple in getattr(resolution_result, 'extension_methods', []):
            if len(ext_tuple) == 4:
                type_name, method_name, method_sym_id, visibility = ext_tuple
            else:
                type_name, method_name, method_sym_id = ext_tuple
                visibility = "internal"
            exports.extension_exports.append(
                ExtensionExport(method_name=method_name,
                                method_symbol_id=method_sym_id,
                                extended_type_name=type_name,
                                visibility=visibility,
                )
            )

        # `pub import` re-exports: forward the imported symbols through
        # this module's public surface so a downstream importer can see
        # them without importing the upstream module directly.
        for name, sym_id, kind in getattr(resolution_result, 're_exports', []):
            if name not in exports.exports:
                exports.exports[name] = Export(
                    name=name, symbol_id=sym_id,
                    kind=kind, visibility="pub",
                )
        for type_name, method_name, method_sym_id, visibility in getattr(
            resolution_result, 're_exported_extension_methods', []
        ):
            exports.extension_exports.append(
                ExtensionExport(
                    method_name=method_name,
                    method_symbol_id=method_sym_id,
                    extended_type_name=type_name,
                    visibility=visibility,
                )
            )

        module.exports = exports.exports
        module.extension_exports = [
            ExtensionExport(e.method_name, e.method_symbol_id,
                            e.extended_type_name, e.visibility)
            for e in exports.extension_exports
        ]
        module._semantic_exports = exports

        # Merge node symbols into context
        if resolution_result.node_symbols:
            self.context.node_symbols.update(resolution_result.node_symbols)

        module.state = ModuleState.RESOLVED
        return True

    def _run_pipeline(self, ast_node, resolution_result, source_path: Path) -> CompileResult:
        """Run the compiler pipeline from type-checking through linking."""
        r = self.runner  # shorthand

        # Type check
        typecheck_result = r.run("Type checking", typecheck, ast_node, resolution_result)
        for error in typecheck_result.errors:
            self.context.diagnostics.add_type_error(error, source_path)

        if self.context.has_errors():
            return self._fail()

        # Build HIR
        hir_result = r.run("Building HIR", build_hir, ast_node, resolution_result, typecheck_result)

        if hir_result.has_errors():
            for error in hir_result.errors:
                self.context.diagnostics.add_error(error)
            return self._fail()

        # Monomorphize
        mono_result = r.run("Monomorphizing", monomorphize, hir_result)

        if mono_result.has_errors():
            for error in mono_result.errors:
                self.context.diagnostics.add_error(error)
            return self._fail()

        # Build MIR
        mir_result = r.run("Building MIR", build_mir, mono_result)

        if mir_result.has_errors():
            for error in mir_result.errors:
                self.context.diagnostics.add_error(error)
            return self._fail()

        # Elide the phantom heap allocation that `var out: T;` emits for the
        # out-param idiom (every Vec/Dict accessor). The runtime overwrites the
        # slot without releasing it, so the phantom would otherwise leak one
        # object per call. Runs at every opt level — it is a correctness fix
        # (the leak and the spurious phantom __release__ are present at -O0 too),
        # not just an optimization. Mutates the program in place.
        elide_outparam_default_init(mir_result.program, mir_result.type_table)

        # Emit MIR if requested
        if self.options.emit == EmitKind.MIR:
            return self._emit_text(
                source_path, ".mir", format_mir(mir_result.program, mir_result.type_table)
            )

        # Lower async functions to state machines
        if self._has_async_functions(mir_result):
            async_result = r.run("Lowering async functions", lower_async, mir_result)

            if async_result.has_errors():
                for error in async_result.errors:
                    self.context.diagnostics.add_error(error)
                return self._fail()

            # Use the transformed MIR for further processing
            from .mir import MirBuildResult
            mir_result = MirBuildResult(
                program=async_result.program,
                type_table=async_result.type_table,
                symbol_table=async_result.symbol_table,
                frame_structs=async_result.frame_structs,
                errors=[],
            )

        # MIR-level optimizations: inline small straight-line functions, then
        # scalar-replace non-escaping all-scalar structs (SROA). Must run
        # BEFORE ARC insertion — scalarized struct locals then simply never
        # receive retain/release ops or a heap allocation.
        if self.options.opt_level.value >= 2:
            r.run("Optimizing MIR", optimize_mir,
                  mir_result.program, mir_result.type_table)

        # Insert ARC operations
        # Enable optimization for -O1 and above
        optimize_arc = self.options.opt_level.value >= 1
        arc_result = r.run("Inserting ARC operations", insert_arc, mir_result, optimize=optimize_arc)

        if arc_result.has_errors():
            for error in arc_result.errors:
                self.context.diagnostics.add_error(error)
            return self._fail()

        if self.options.emit == EmitKind.MIR_OPTIMIZED:
            return self._emit_text(
                source_path, ".opt.mir", format_mir(arc_result.program, arc_result.type_table)
            )

        if self.separate_modules:
            return self._emit_separate(arc_result, source_path)

        # All backend text outputs start with the same ARC-lowered program.
        if self.options.emit in (EmitKind.LLVM_IR, EmitKind.LLVM_OPTIMIZED, EmitKind.ASSEMBLY):
            llvm_result = r.run("Generating LLVM IR", compile_to_llvm,
                arc_result,
                module_name=source_path.stem,
                target_triple=self.options.target_triple,
                frame_structs=arc_result.frame_structs,
            )

            if llvm_result.has_errors():
                for error in llvm_result.errors:
                    self.context.diagnostics.add_codegen_error(error)
                return self._fail()

            if self.options.emit == EmitKind.LLVM_IR:
                return self._emit_text(source_path, ".ll", str(llvm_result.module))
            emitter = (compile_module_to_assembly if self.options.emit == EmitKind.ASSEMBLY
                       else optimize_module_to_ir)
            content, errors = r.run(
                "Preparing backend output", emitter, llvm_result.module,
                opt_level=self.options.opt_level.value,
                target_triple=self.options.target_triple,
            )
            if errors:
                for error in errors:
                    self.context.diagnostics.add_codegen_error(error)
                return self._fail()
            suffix = ".s" if self.options.emit == EmitKind.ASSEMBLY else ".opt.ll"
            return self._emit_text(source_path, suffix, content)

        # Compile to object file
        if self.options.emit == EmitKind.OBJECT:
            output_path = self._get_output_path(source_path, ".o")
        else:
            # For executable, use temp file for object
            output_path = Path(tempfile.mktemp(suffix=".o"))

        errors = r.run("Generating object file", compile_to_object,
            arc_result,
            str(output_path),
            module_name=source_path.stem,
            target_triple=self.options.target_triple,
            frame_structs=arc_result.frame_structs,
            opt_level=self.options.opt_level.value,
        )

        if errors:
            for error in errors:
                self.context.diagnostics.add_codegen_error(error)
            return self._fail()

        if self.options.emit == EmitKind.OBJECT:
            return CompileResult(
                success=True,
                output_path=output_path,
                diagnostics=self.context.diagnostics,
            )

        # Link to executable
        exe_path = self._get_output_path(source_path, "")
        result = r.run("Linking executable", self._link_executable, output_path, exe_path)

        # Clean up temp object file
        if output_path.exists():
            output_path.unlink()

        return result

    def _emit_separate(self, arc_result, source_path):
        from .module_abi import finish_module
        from .module_artifact import write_artifact
        from .codegen.object_file import compile_module_to_object
        owner = self._module_key(self.entry_path)
        if self.options.emit == EmitKind.MODULE and any(f.name == 'main' for f in arc_result.program.functions):
            self.context.diagnostics.add_error('A library module cannot define main')
            return self._fail()
        try:
            result = compile_to_llvm(arc_result, module_name=source_path.stem,
                target_triple=self.options.target_triple, frame_structs=arc_result.frame_structs)
            if result.has_errors():
                raise ValueError('; '.join(result.errors))
            native = finish_module(result.module, arc_result.program,
                                   arc_result.symbol_table, arc_result.type_table, owner)
            with native:
                if self.options.emit == EmitKind.LLVM_IR:
                    return self._emit_text(source_path, '.ll', str(native))
                if self.options.emit in (EmitKind.LLVM_OPTIMIZED, EmitKind.ASSEMBLY):
                    emitter = compile_module_to_assembly if self.options.emit == EmitKind.ASSEMBLY else optimize_module_to_ir
                    content, errors = emitter(native, opt_level=self.options.opt_level.value,
                                              target_triple=self.options.target_triple)
                    if errors:
                        raise ValueError('; '.join(errors))
                    return self._emit_text(source_path, '.s' if self.options.emit == EmitKind.ASSEMBLY else '.opt.ll', content)
                with tempfile.TemporaryDirectory(prefix='rolang-module-') as directory:
                    obj = Path(directory) / 'entry.o'
                    errors = compile_module_to_object(native, str(obj), self.options.opt_level.value, self.options.target_triple)
                    if errors:
                        raise ValueError('; '.join(errors))
                    if self.options.emit == EmitKind.MODULE:
                        path = self._get_output_path(source_path, '.rlm')
                        sources = {self._module_key(m.path): {
                            'text': self.source_files[m.path], 'imports': m.import_paths,
                        } for m in self.compile_order}
                        objects = {**self.module_objects, owner: obj.read_bytes()}
                        write_artifact(path, owner, sources, objects, self._module_target())
                        return CompileResult(True, path, diagnostics=self.context.diagnostics)
                    if self.options.emit == EmitKind.OBJECT:
                        path = self._get_output_path(source_path, '.o')
                        path.write_bytes(obj.read_bytes())
                        return CompileResult(True, path, diagnostics=self.context.diagnostics)
                    dependencies = []
                    for index, data in enumerate(self.module_objects.values()):
                        dep = Path(directory) / f'dependency_{index}.o'
                        dep.write_bytes(data)
                        dependencies.append(dep)
                    self._module_link_objects = dependencies
                    return self._link_executable(obj, self._get_output_path(source_path, ''))
        except (OSError, ValueError, RuntimeError) as error:
            self.context.diagnostics.add_error(f'Module compilation failed: {error}')
            return self._fail()

    def _emit_text(self, source_path: Path, suffix: str, content: str) -> CompileResult:
        """Write textual compiler output with consistent I/O diagnostics."""
        output_path = self._get_output_path(source_path, suffix)
        try:
            output_path.write_text(content, encoding="utf-8")
        except OSError as error:
            self.context.diagnostics.add_io_error(str(error), output_path)
            return self._fail()
        return CompileResult(
            success=True, output_path=output_path, output_content=content,
            diagnostics=self.context.diagnostics,
        )

    def _fail(self) -> CompileResult:
        """Return a failed CompileResult with current diagnostics."""
        return CompileResult(success=False, diagnostics=self.context.diagnostics)

    def _get_output_path(self, source_path: Path, extension: str) -> Path:
        """Determine output path based on options."""
        if self.options.output_path:
            return self.options.output_path
        return source_path.with_suffix(extension)

    def _has_async_functions(self, mir_result) -> bool:
        """Check if the program has any async functions."""
        for func in mir_result.program.functions:
            if func.is_async:
                return True
        return False

    def _link_executable(
        self,
        object_path: Path,
        output_path: Path,
    ) -> CompileResult:
        """Link object file with runtime to create executable."""
        runtime_context = self._runtime_file_context()

        with runtime_context as runtime_path:
            if runtime_path is None or not runtime_path.exists():
                self.context.diagnostics.add_io_error(
                    f"Runtime library not found at {runtime_path}"
                )
                return CompileResult(success=False, diagnostics=self.context.diagnostics)

            runtime_obj = Path(tempfile.mktemp(suffix=".o"))

            try:
                compile_result = self._compile_runtime(runtime_path, runtime_obj)
                if not compile_result.success:
                    return compile_result

                link_result = self._link_object_files(
                    object_path,
                    runtime_obj,
                    output_path,
                )
                if not link_result.success:
                    return link_result
            finally:
                if runtime_obj.exists():
                    runtime_obj.unlink()

        return CompileResult(
            success=True,
            output_path=output_path,
            diagnostics=self.context.diagnostics,
        )

    def _runtime_file_context(self):
        """Return a context manager yielding the runtime C source path."""
        if self.options.runtime_path is not None:
            return nullcontext(self.options.runtime_path)

        packaged_runtime = resources.files("rolang.runtime").joinpath("rolang_rt.c")
        try:
            return resources.as_file(packaged_runtime)
        except ModuleNotFoundError:
            return nullcontext(Path("runtime") / "rolang_rt.c")

    def _compile_runtime(
        self,
        runtime_path: Path,
        runtime_obj: Path,
    ) -> CompileResult:
        """Compile the C runtime into a temporary object file."""
        if self.options.verbose:
            print(f"Compiling runtime {runtime_path}...")

        cc = os.environ.get("CC", "cc")
        # The runtime is small, hot, and shared by every program — there is no
        # debugging reason to ship it unoptimized once the program is past O0.
        rt_opt = 3 if self.options.opt_level.value >= 1 else 0
        compile_cmd = [
            cc,
            "-c",
            str(runtime_path),
            "-o", str(runtime_obj),
            f"-O{rt_opt}",
            # Single-threaded cooperative runtime: refcount RMWs need no atomics.
            "-DROLANG_SINGLE_THREADED",
        ]
        if rt_opt >= 1:
            # Portable, distribution-safe (no -march=native).
            compile_cmd += ["-fno-semantic-interposition", "-fvisibility=hidden"]
        if self.options.opt_level.value >= 2:
            compile_cmd.append("-flto")
        # Escape hatch for extra runtime cflags (profiling, sanitizers, e.g.
        # ROLANG_RT_CFLAGS="-DROLANG_POOL_PROFILE"). Empty/unset in normal builds.
        extra_rt_cflags = os.environ.get("ROLANG_RT_CFLAGS", "").split()
        if extra_rt_cflags:
            compile_cmd += extra_rt_cflags

        try:
            result = subprocess.run(
                compile_cmd,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.context.diagnostics.add_error(
                    f"Failed to compile runtime: {result.stderr}"
                )
                return CompileResult(success=False, diagnostics=self.context.diagnostics)
        except FileNotFoundError:
            self.context.diagnostics.add_error(
                f"C compiler not found: {cc}. Please install a C compiler."
            )
            return CompileResult(success=False, diagnostics=self.context.diagnostics)

        return CompileResult(success=True, diagnostics=self.context.diagnostics)

    def _link_object_files(
        self,
        object_path: Path,
        runtime_obj: Path,
        output_path: Path,
    ) -> CompileResult:
        """Link generated code and runtime object files into an executable."""
        cc = os.environ.get("CC", "cc")
        link_cmd = [
            cc,
            str(object_path),
            *(str(path) for path in getattr(self, '_module_link_objects', [])),
            str(runtime_obj),
            "-o", str(output_path),
            f"-O{self.options.opt_level.value}",
        ]
        if self.options.opt_level.value >= 2:
            link_cmd.append("-flto")

        try:
            result = subprocess.run(
                link_cmd,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.context.diagnostics.add_error(
                    f"Linking failed: {result.stderr}"
                )
                return CompileResult(success=False, diagnostics=self.context.diagnostics)
        except FileNotFoundError:
            self.context.diagnostics.add_error(
                f"C compiler not found: {cc}. Please install a C compiler."
            )
            return CompileResult(success=False, diagnostics=self.context.diagnostics)

        return CompileResult(success=True, diagnostics=self.context.diagnostics)


def compile_source(
    source_path: Path,
    options: Optional[CompileOptions] = None,
) -> CompileResult:
    """
    Compile a Rolang source file.

    Args:
        source_path: Path to the .rl source file
        options: Compilation options (uses defaults if None)

    Returns:
        CompileResult indicating success/failure
    """
    if options is None:
        options = CompileOptions()

    driver = CompilationDriver(options)
    return driver.compile_file(source_path)
