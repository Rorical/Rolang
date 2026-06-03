"""
Module representation for the Rolang compiler.

Defines the `Module` (a single compilation unit) and `ModuleGraph` (the
dependency graph the driver walks to schedule compilation).

The driver keys the module graph by canonical resolved file path so two
files sharing a basename in different directories cannot collide. The
helpers below — `module_name_from_path`, `Export`, `ExtensionExport`,
`ModuleExports` — provide the stable semantic surface that resolver/
checker/codegen layers consume.

Visibility:
- pub: visible to all importing modules
- internal: visible only within the declaring module (default)
- private: visible only within the declaring file
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import ast
from .symbols import SymbolTable, SymbolId


class ModuleState(Enum):
    """State of a module in the compilation pipeline."""
    DISCOVERED = auto()   # Path found, not yet parsed
    PARSED = auto()       # AST available
    RESOLVED = auto()     # Names resolved
    TYPECHECKED = auto()  # Types checked
    COMPILED = auto()     # Code generated
    ERROR = auto()        # Compilation failed


@dataclass
class Export:
    """
    Represents an exported symbol from a module.

    Visibility controls cross-module access:
    - pub: visible to all importers
    - internal: visible only within the same module (not exported)
    - private: visible only within the declaring file (not exported)
    """
    name: str
    symbol_id: SymbolId
    kind: str  # "function", "struct", "enum", "protocol", "type_alias"
    visibility: str = "internal"  # "pub", "private", "internal"


@dataclass
class ExtensionExport:
    """Metadata for an extension method exported from a module."""
    method_name: str
    method_symbol_id: SymbolId
    extended_type_name: str  # "Vec", "Vec64", etc.
    visibility: str = "internal"  # "pub" exports cross module boundaries


@dataclass
class ModuleExports:
    """Stable semantic representation of a module's public exports.

    Built during resolution and stored on the Module, eliminating
    the need for post-hoc O(n) symbol table scanning.
    """
    exports: Dict[str, Export] = field(default_factory=dict)
    extension_exports: List[ExtensionExport] = field(default_factory=list)
    node_symbols: Dict[int, SymbolId] = field(default_factory=dict)
    imported_symbols: Dict[str, SymbolId] = field(default_factory=dict)


@dataclass
class Module:
    """
    Represents a single compilation unit (source file).

    A module contains:
    - Source code and AST
    - Symbol table for this module's scope
    - Import/export information
    - Dependency tracking
    """
    # Identity
    name: str                   # Qualified name: "math.vector"
    path: Path                  # File path: src/math/vector.rl

    # Source and AST
    source: Optional[str] = None
    ast: Optional[ast.Program] = None

    # Symbol information
    symbol_table: Optional[SymbolTable] = None
    exports: Dict[str, Export] = field(default_factory=dict)
    extension_exports: List[ExtensionExport] = field(default_factory=list)

    # Dependencies — names of other modules in the graph
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)

    # State tracking
    state: ModuleState = ModuleState.DISCOVERED
    errors: List[str] = field(default_factory=list)

    def qualified_name(self, local_name: str) -> str:
        """Get the fully qualified name for a local symbol."""
        return f"{self.name}.{local_name}"

    def has_export(self, name: str) -> bool:
        """Check if this module exports a symbol with the given name."""
        return name in self.exports

    def get_export(self, name: str) -> Optional[Export]:
        """Get an exported symbol by name."""
        return self.exports.get(name)

    def add_export(self, name: str, symbol_id: SymbolId, kind: str, visibility: str = "internal") -> None:
        """Add a symbol to the module's exports."""
        self.exports[name] = Export(name=name, symbol_id=symbol_id, kind=kind, visibility=visibility)

    def add_extension_export(self, method_name: str, method_symbol_id: SymbolId, extended_type_name: str) -> None:
        """Record an extension method for cross-module visibility."""
        self.extension_exports.append(
            ExtensionExport(method_name=method_name, method_symbol_id=method_symbol_id, extended_type_name=extended_type_name)
        )

    def get_extension_methods(self, type_name: str) -> List[tuple[str, SymbolId]]:
        """Get extension methods for a type name from this module."""
        return [(ee.method_name, ee.method_symbol_id) for ee in self.extension_exports if ee.extended_type_name == type_name]

    def add_dependency(self, module_name: str) -> None:
        """Record a dependency on another module."""
        self.dependencies.add(module_name)

    def add_dependent(self, module_name: str) -> None:
        """Record that another module depends on us."""
        self.dependents.add(module_name)


@dataclass
class ModuleGraph:
    """
    Tracks all modules and their dependencies.

    The graph is used for:
    - Detecting circular dependencies
    - Determining compilation order
    - Managing incremental compilation
    """
    modules: Dict[str, Module] = field(default_factory=dict)
    source_roots: List[Path] = field(default_factory=list)

    def add_module(self, module: Module) -> None:
        """Add a module to the graph."""
        self.modules[module.name] = module

    def get_module(self, name: str) -> Optional[Module]:
        """Get a module by name."""
        return self.modules.get(name)

    def has_module(self, name: str) -> bool:
        """Check if a module exists in the graph."""
        return name in self.modules

    def get_all_modules(self) -> List[Module]:
        """Get all modules in the graph."""
        return list(self.modules.values())

    def add_dependency(self, from_module: str, to_module: str) -> None:
        """Record a dependency from one module to another."""
        if from_module in self.modules:
            self.modules[from_module].add_dependency(to_module)
        if to_module in self.modules:
            self.modules[to_module].add_dependent(from_module)

    def topological_sort(self) -> List[str]:
        """
        Sort modules in dependency order (dependencies first).

        Uses Kahn's algorithm. An edge A -> B means "A depends on B",
        so B must be compiled before A.

        Returns:
            List of module names in compilation order (dependencies first)
        """
        in_degree: Dict[str, int] = {name: 0 for name in self.modules}

        # Edge A -> B means A depends on B
        # in_degree[B] stays 0, in_degree[A] increases
        for from_module in self.modules:
            module = self.modules[from_module]
            in_degree[from_module] = len(module.dependencies)

        # Start with modules that have no dependencies
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result: List[str] = []

        while queue:
            name = queue.pop(0)
            result.append(name)

            # When we compile 'name', modules that depend on 'name' have
            # one fewer unmet dependency
            for other_name, other_module in self.modules.items():
                if name in other_module.dependencies:
                    in_degree[other_name] -= 1
                    if in_degree[other_name] == 0:
                        queue.append(other_name)

        if len(result) != len(self.modules):
            remaining = set(self.modules.keys()) - set(result)
            raise ValueError(f"Circular dependency detected involving: {remaining}")

        return result

    def get_compilation_order(self) -> List[Module]:
        """
        Get modules in the order they should be compiled.

        Dependencies are compiled before dependents.
        """
        order = self.topological_sort()
        return [self.modules[name] for name in order]


def module_name_from_path(path: Path, source_root: Path) -> str:
    """
    Derive a module name from a file path.

    Args:
        path: The source file path
        source_root: The root directory for source files

    Returns:
        Module name (e.g., "math.vector" for "src/math/vector.rl")

    Examples:
        module_name_from_path(Path("src/math/vector.rl"), Path("src"))
        -> "math.vector"

        module_name_from_path(Path("main.rl"), Path("."))
        -> "main"
    """
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        # Path is not under source root
        relative = path

    # Remove .rl extension and convert path separators to dots
    parts = relative.with_suffix("").parts
    return ".".join(parts)



