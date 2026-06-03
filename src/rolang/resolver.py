"""Name resolution for RoLang - binds identifiers to unique SymbolIds."""

from __future__ import annotations
from typing import Optional, List, Dict, TYPE_CHECKING

from . import ast
from .symbols import (
    SymbolTable,
    Symbol,
    SymbolId,
    SymbolKind,
    Namespace,
    Scope,
    ScopeKind,
    ResolutionResult,
    ResolutionError,
    ResolutionErrorKind,
)

if TYPE_CHECKING:
    from .module import Module, ModuleGraph


class NameResolver:
    """Two-pass name resolver for RoLang.

    Pass 1: Collect all top-level declarations (enables forward references)
    Pass 2: Resolve all references within declaration bodies

    For multi-module support:
    - Each module gets its own module scope
    - Imports are resolved to symbols from other modules
    - Cross-module lookups use the module graph
    """

    def __init__(
        self,
        symbol_table: Optional[SymbolTable] = None,
        module_graph: Optional['ModuleGraph'] = None,
        current_module: Optional['Module'] = None,
        node_symbols: Optional[dict[int, SymbolId]] = None,
    ) -> None:
        self.symbol_table = symbol_table if symbol_table else SymbolTable()
        self.node_symbols: dict[int, SymbolId] = node_symbols if node_symbols is not None else {}
        self.errors: List[ResolutionError] = []
        self.current_scope: Optional[Scope] = None
        # Multi-module support
        self.module_graph = module_graph
        self.current_module = current_module
        self.imported_symbols: Dict[str, SymbolId] = {}  # name -> symbol_id for imports
        # Track current type context for self binding
        self._current_type_symbol: Optional[SymbolId] = None
        # Extension method export: (type_name, method_name, method_symbol_id)
        self._extension_methods: List[tuple[str, str, SymbolId, str]] = []
        # `pub import` re-exports that should appear in this module's
        # public surface so a downstream importer can see them too.
        self._re_exports: List[tuple[str, SymbolId, str]] = []
        self._re_exported_extension_methods: List[tuple[str, str, SymbolId, str]] = []
        # Imported extension methods: type_name -> list of (method_name, method_symbol_id)
        self.imported_extension_methods: Dict[str, List[tuple[str, SymbolId]]] = {}

    def resolve(self, program: ast.Program) -> ResolutionResult:
        """Resolve names in the program."""
        # Create module scope with builtins
        self._push_scope(ScopeKind.MODULE)
        self._inject_builtins()

        # Process imports first (for multi-module support)
        self._process_imports(program)

        # Pass 1: Collect top-level declarations
        self._collect_declarations(program)

        # Pass 2: Resolve references
        self._resolve_program(program)

        self._pop_scope()

        return ResolutionResult(
            symbol_table=self.symbol_table,
            node_symbols=self.node_symbols,
            errors=self.errors,
            self_symbols=getattr(self, '_self_symbols', {}),
            imported_symbols=self.imported_symbols,
            extension_methods=self._extension_methods,
            imported_extension_methods=self.imported_extension_methods,
            re_exports=self._re_exports,
            re_exported_extension_methods=self._re_exported_extension_methods,
        )

    # Reserved words that the IDENT lexer happens to accept but which we
    # never want to see as an `import "..." as <alias>` identifier — they
    # would compile but shadow language constructs and confuse readers.
    _RESERVED_ALIAS_WORDS = frozenset({
        "def", "let", "var", "if", "else", "while", "for", "return",
        "import", "as", "in", "where", "struct", "enum", "protocol",
        "extension", "extern", "init", "deinit", "self", "Self",
        "pub", "private", "internal", "async", "await", "try", "throws",
        "true", "false", "nil", "is", "switch", "case", "default",
        "guard", "defer", "break", "continue",
    })

    def _process_imports(self, program: ast.Program) -> None:
        """Process import declarations and make imported symbols available."""
        for item in program.items:
            if isinstance(item, ast.ImportDecl):
                self._resolve_import(item)

    def _validate_import_alias(
        self,
        alias: str,
        import_decl: ast.ImportDecl,
    ) -> bool:
        """Reject aliases that would shadow built-in names or reserved words.

        Returns True if the alias is OK to use, False (and records a
        diagnostic) if it must be rejected. Called from `_resolve_import`
        before any per-symbol registration happens.
        """
        span = getattr(import_decl, 'span', None)
        if alias in self._RESERVED_ALIAS_WORDS:
            self._error(
                ResolutionErrorKind.DUPLICATE_VALUE,
                alias,
                f"import alias '{alias}' is a reserved word",
                span,
            )
            return False
        if alias in self.symbol_table.builtins:
            self._error(
                ResolutionErrorKind.DUPLICATE_TYPE,
                alias,
                f"import alias '{alias}' shadows a built-in type",
                span,
            )
            return False
        if self.current_scope and (
            self.current_scope.has_value_local(alias)
            or self.current_scope.has_type_local(alias)
        ):
            self._error(
                ResolutionErrorKind.DUPLICATE_VALUE,
                alias,
                f"import alias '{alias}' is already defined in this scope",
                span,
            )
            return False
        return True

    def _resolve_import(self, import_decl: ast.ImportDecl) -> None:
        """
        Resolve an import declaration and inject symbols into scope.

        - import "file.rl"         -> all public symbols available directly
        - import "file.rl" as Name -> all public symbols under Name. prefix
        """
        if not self.module_graph or not self.current_module:
            # Single-file mode, skip imports
            return

        file_path: str = import_decl.path if hasattr(import_decl, 'path') else ""
        alias: Optional[str] = import_decl.alias if hasattr(import_decl, 'alias') else None

        if not file_path:
            return

        # Validate the alias up front: it must not be a reserved word, a
        # builtin type name, or already-defined in the current scope.
        if alias is not None and not self._validate_import_alias(alias, import_decl):
            return

        # Find the target module by file path
        target_module = None
        current_path = getattr(self.current_module, 'path', None)
        for mod in self.module_graph.modules.values():
            mp = mod.path
            if mp.name == file_path or str(mp) == file_path:
                target_module = mod
                break
            # Check full relative path (e.g. "std/io.rl" matches .../std/io.rl)
            if str(mp).endswith("/" + file_path) or str(mp).endswith("\\" + file_path):
                target_module = mod
                break
            # Check relative to current module's directory
            if current_path:
                resolved = (current_path.parent / file_path).resolve()
                try:
                    if mp.resolve() == resolved:
                        target_module = mod
                        break
                except OSError:
                    pass

        if not target_module:
            self._error(
                ResolutionErrorKind.UNDEFINED_TYPE,
                file_path,
                f"Module not found: '{file_path}'",
                import_decl.span if hasattr(import_decl, 'span') else None,
            )
            return

        # Filter exports by visibility: only import 'pub' symbols from other modules
        visible_exports = {}
        if target_module.exports:
            # Target module already compiled: use its exports
            for name, export in target_module.exports.items():
                if export.visibility == "pub":
                    visible_exports[name] = export
        else:
            # Target module not yet compiled: scan AST for public declarations
            if target_module.ast:
                for item in target_module.ast.items:
                    vis = getattr(item, 'visibility', 'internal')
                    if vis == "pub":
                        name = getattr(item, 'name', '')
                        if name:
                            kind = type(item).__name__.lower().replace('decl', '')
                            # Create a temporary export-like object
                            from dataclasses import dataclass
                            @dataclass
                            class _TempExport:
                                name: str
                                symbol_id: Any
                                kind: str
                                visibility: str
                            visible_exports[name] = _TempExport(name=name, symbol_id=None, kind=kind, visibility='pub')

        is_pub_import = getattr(import_decl, 'visibility', 'internal') == 'pub'
        if alias:
            for name, export in visible_exports.items():
                qualified_name = f"{alias}.{name}"
                self.imported_symbols[qualified_name] = export.symbol_id
                if is_pub_import:
                    self._re_exports.append((qualified_name, export.symbol_id, export.kind))
        else:
            # Both `import "file.rl"` and `import std.io` import the
            # target's symbols flatly into the importer's lookup table.
            # Use `import ... as M` to get an explicit namespace.
            #
            # We deliberately do NOT pre-register the imported symbols
            # in the lexical scope: that would cause a hard
            # "already defined" error if the importer locally declares
            # a symbol with the same name, preventing legitimate
            # shadowing. `_lookup_value` / `_lookup_type` fall back to
            # `imported_symbols` when a name isn't in scope.
            for name, export in visible_exports.items():
                self.imported_symbols[name] = export.symbol_id
                if is_pub_import:
                    self._re_exports.append((name, export.symbol_id, export.kind))

        # Import extension methods from the target module. Only "pub"
        # extensions cross the module boundary — `private` and `internal`
        # extensions are visible only inside their declaring module.
        for ext_export in getattr(target_module, 'extension_exports', []):
            if getattr(ext_export, 'visibility', 'internal') != "pub":
                continue
            type_name = ext_export.extended_type_name
            if type_name not in self.imported_extension_methods:
                self.imported_extension_methods[type_name] = []
            self.imported_extension_methods[type_name].append((ext_export.method_name, ext_export.method_symbol_id))
            if is_pub_import:
                self._re_exported_extension_methods.append(
                    (type_name, ext_export.method_name, ext_export.method_symbol_id, "pub")
                )

    def _try_resolve_import_member(self, expr: ast.MemberAccess) -> bool:
        """Handle member access on module namespace (e.g., lib.get_value, std.io.println)."""
        # Build the full qualified name by recursing through nested MemberAccess
        parts = self._collect_member_access_parts(expr)
        if parts is None:
            return False
        qualified = ".".join(parts)
        if qualified in self.imported_symbols:
            symbol_id = self.imported_symbols[qualified]
            self.node_symbols[id(expr)] = symbol_id
            return True
        return False

    def _collect_member_access_parts(self, expr: ast.MemberAccess) -> Optional[List[str]]:
        """Recursively collect parts of a dotted member access (e.g., std.io.println → ['std','io','println'])."""
        if isinstance(expr.object, ast.Identifier):
            return [expr.object.name, expr.member]
        if isinstance(expr.object, ast.MemberAccess):
            inner = self._collect_member_access_parts(expr.object)
            if inner is not None:
                return inner + [expr.member]
        return None

    # ========================= Scope Management =========================

    def _push_scope(self, kind: ScopeKind) -> Scope:
        """Push a new scope onto the stack."""
        scope = Scope(kind=kind, parent=self.current_scope)
        self.current_scope = scope
        return scope

    def _pop_scope(self) -> None:
        """Pop the current scope."""
        if self.current_scope:
            self.current_scope = self.current_scope.parent

    def _inject_builtins(self) -> None:
        """Inject builtin types into the current scope."""
        if not self.current_scope:
            return
        for name, symbol_id in self.symbol_table.builtins.items():
            self.current_scope.define_type(name, symbol_id)

    # ========================= Symbol Definition =========================

    def _define_type(
        self,
        name: str,
        kind: SymbolKind,
        node: Optional[ast.Node] = None,
        span: Optional[ast.Span] = None,
        visibility: str = "internal",
    ) -> Optional[Symbol]:
        """Define a type symbol in the current scope."""
        if not self.current_scope:
            return None

        if span is None and node is not None:
            span = node.span

        if self.current_scope.has_type_local(name):
            self._error(
                ResolutionErrorKind.DUPLICATE_TYPE,
                name,
                f"Type '{name}' is already defined in this scope",
                span,
            )
            return None

        symbol = self.symbol_table.create_symbol(
            name=name,
            kind=kind,
            namespace=Namespace.TYPE,
            span=span,
            decl_node=node,
            visibility=visibility,
        )
        self.current_scope.define_type(name, symbol.id)
        if node:
            self.node_symbols[id(node)] = symbol.id
        return symbol

    def _define_value(
        self,
        name: str,
        kind: SymbolKind,
        is_mutable: bool = False,
        node: Optional[ast.Node] = None,
        span: Optional[ast.Span] = None,
        visibility: str = "internal",
    ) -> Optional[Symbol]:
        """Define a value symbol in the current scope."""
        if not self.current_scope:
            return None

        if span is None and node is not None:
            span = node.span

        if self.current_scope.has_value_local(name):
            self._error(
                ResolutionErrorKind.DUPLICATE_VALUE,
                name,
                f"'{name}' is already defined in this scope",
                span,
            )
            return None

        symbol = self.symbol_table.create_symbol(
            name=name,
            kind=kind,
            namespace=Namespace.VALUE,
            span=span,
            decl_node=node,
            is_mutable=is_mutable,
            visibility=visibility,
        )
        self.current_scope.define_value(name, symbol.id)
        if node:
            self.node_symbols[id(node)] = symbol.id
        return symbol

    # ========================= Symbol Lookup =========================

    def _lookup_type(self, name: str, node: ast.Node, span: Optional[ast.Span] = None) -> Optional[SymbolId]:
        """Look up a type and record the binding."""
        if not self.current_scope:
            return None

        if span is None:
            span = node.span

        symbol_id = self.current_scope.lookup_type(name)
        if symbol_id is None:
            # Check imported symbols (types can be imported too)
            if name in self.imported_symbols:
                symbol_id = self.imported_symbols[name]
                self.node_symbols[id(node)] = symbol_id
                return symbol_id

            self._error(
                ResolutionErrorKind.UNDEFINED_TYPE,
                name,
                f"Undefined type '{name}'",
                span,
            )
            return None

        self.node_symbols[id(node)] = symbol_id
        return symbol_id

    def _lookup_value(self, name: str, node: ast.Node, span: Optional[ast.Span] = None) -> Optional[SymbolId]:
        """Look up a value and record the binding."""
        if not self.current_scope:
            return None

        if span is None:
            span = node.span

        symbol_id = self.current_scope.lookup_value(name)
        if symbol_id is None:
            # Check imported symbols
            if name in self.imported_symbols:
                symbol_id = self.imported_symbols[name]
                self.node_symbols[id(node)] = symbol_id
                return symbol_id

            # Type names can appear as the receiver of static methods,
            # e.g. `Box.new(...)`.
            type_symbol_id = self.current_scope.lookup_type(name)
            if type_symbol_id is not None:
                self.node_symbols[id(node)] = type_symbol_id
                return type_symbol_id
            self._error(
                ResolutionErrorKind.UNDEFINED_VALUE,
                name,
                f"Undefined variable or function '{name}'",
                span,
            )
            return None

        self.node_symbols[id(node)] = symbol_id
        return symbol_id

    # ========================= Error Handling =========================

    def _error(
        self,
        kind: ResolutionErrorKind,
        name: str,
        message: str,
        span: Optional[ast.Span] = None,
    ) -> None:
        """Record an error and continue (for reporting multiple errors)."""
        self.errors.append(ResolutionError(kind=kind, name=name, message=message, span=span))

    # ========================= Pass 1: Collect Declarations =========================

    def _collect_declarations(self, program: ast.Program) -> None:
        """Pass 1: Collect all top-level declarations."""
        for item in program.items:
            self._collect_item(item)

    def _collect_item(self, item: ast.TopLevelItem) -> None:
        """Collect a top-level item declaration."""
        if isinstance(item, ast.StructDecl):
            self._define_type(item.name, SymbolKind.STRUCT, node=item, visibility=item.visibility)
        elif isinstance(item, ast.EnumDecl):
            self._define_type(item.name, SymbolKind.ENUM, node=item, visibility=item.visibility)
            # NOTE: enum cases are intentionally NOT registered in the module
            # value namespace. They are only ever referenced as `Type.case`
            # (member access) or `.case` (pattern), both resolved via the
            # enum's type — never as bare identifiers. Registering them here
            # served no functional purpose (bare references already fail to
            # resolve) and caused spurious "already defined" collisions when
            # two enums — or an enum and a function — shared a case name.
        elif isinstance(item, ast.ProtocolDecl):
            self._define_type(item.name, SymbolKind.PROTOCOL, node=item, visibility=item.visibility)
        elif isinstance(item, ast.TypeAliasDecl):
            self._define_type(item.name, SymbolKind.TYPE_ALIAS, node=item, visibility=item.visibility)
        elif isinstance(item, ast.FuncDecl):
            self._define_value(item.name, SymbolKind.FUNCTION, node=item, visibility=item.visibility)
        elif isinstance(item, ast.ExternFuncDecl):
            self._define_value(item.name, SymbolKind.EXTERN_FUNC, node=item, visibility=item.visibility)
        # ImportDecl and ExtensionDecl don't create new names at module level

    # ========================= Pass 2: Resolve References =========================

    def _resolve_program(self, program: ast.Program) -> None:
        """Pass 2: Resolve all references in the program."""
        for item in program.items:
            self._resolve_item(item)

    def _resolve_item(self, item: ast.TopLevelItem) -> None:
        """Resolve references in a top-level item."""
        if isinstance(item, ast.StructDecl):
            self._resolve_struct(item)
        elif isinstance(item, ast.EnumDecl):
            self._resolve_enum(item)
        elif isinstance(item, ast.ProtocolDecl):
            self._resolve_protocol(item)
        elif isinstance(item, ast.TypeAliasDecl):
            self._resolve_type_alias(item)
        elif isinstance(item, ast.FuncDecl):
            self._resolve_func_decl(item)
        elif isinstance(item, ast.ExternFuncDecl):
            self._resolve_extern_func(item)
        elif isinstance(item, ast.ExtensionDecl):
            self._resolve_extension(item)

    # ========================= Type Declarations =========================

    def _resolve_struct(self, struct: ast.StructDecl) -> None:
        """Resolve a struct declaration."""
        self._push_scope(ScopeKind.TYPE)

        # Get the struct's symbol for self binding (look up in parent scope)
        struct_symbol = None
        if self.current_scope and self.current_scope.parent:
            struct_symbol = self.current_scope.parent.lookup_type(struct.name)

        # Register generic parameters in type namespace
        for param in struct.generic_params:
            self._define_type(param.name, SymbolKind.GENERIC_PARAM, node=param)
            # Resolve bounds
            for bound in (param.bounds or []):
                self._resolve_type(bound)

        # Resolve constraints
        for constraint in struct.constraints:
            self._resolve_constraint(constraint)
        self._merge_constraints_into_params(struct.constraints, struct.generic_params)

        # First pass: register all properties in value namespace for implicit self access
        for member in struct.members:
            if isinstance(member, ast.PropertyDecl):
                self._define_value(
                    member.name,
                    SymbolKind.FIELD,
                    is_mutable=member.is_mutable,
                    node=member,
                )

        # Second pass: resolve all members with self context
        old_type_symbol = self._current_type_symbol
        self._current_type_symbol = struct_symbol
        for member in struct.members:
            self._resolve_struct_member(member)
        self._current_type_symbol = old_type_symbol

        self._pop_scope()

    def _resolve_struct_member(self, member: ast.StructMember) -> None:
        """Resolve a struct member."""
        if isinstance(member, ast.PropertyDecl):
            if member.type_annotation:
                self._resolve_type(member.type_annotation)
            if member.initializer:
                self._resolve_expr(member.initializer)
        elif isinstance(member, ast.FuncDecl):
            # Define the method symbol before resolving it
            self._define_value(member.name, SymbolKind.FUNCTION, node=member)
            self._resolve_func_decl(member)

    def _resolve_enum(self, enum: ast.EnumDecl) -> None:
        """Resolve an enum declaration."""
        self._push_scope(ScopeKind.TYPE)

        # Get the enum's symbol for self binding (look up in parent scope)
        enum_symbol = None
        if self.current_scope and self.current_scope.parent:
            enum_symbol = self.current_scope.parent.lookup_type(enum.name)

        # Register generic parameters
        for param in enum.generic_params:
            self._define_type(param.name, SymbolKind.GENERIC_PARAM, node=param)
            for bound in (param.bounds or []):
                self._resolve_type(bound)

        # Resolve constraints
        for constraint in enum.constraints:
            self._resolve_constraint(constraint)
        self._merge_constraints_into_params(enum.constraints, enum.generic_params)

        # Resolve members with self context
        old_type_symbol = self._current_type_symbol
        self._current_type_symbol = enum_symbol
        for member in enum.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case in member.cases:
                    # Resolve payload types
                    for _, payload_type in case.payload:
                        self._resolve_type(payload_type)
            elif isinstance(member, ast.FuncDecl):
                # Define the method symbol so member resolution can find it.
                self._define_value(member.name, SymbolKind.FUNCTION, node=member)
                self._resolve_func_decl(member)
        self._current_type_symbol = old_type_symbol

        self._pop_scope()

    def _resolve_protocol(self, protocol: ast.ProtocolDecl) -> None:
        """Resolve a protocol declaration."""
        self._push_scope(ScopeKind.TYPE)

        # Register generic parameters
        for param in protocol.generic_params:
            self._define_type(param.name, SymbolKind.GENERIC_PARAM, node=param)
            for bound in (param.bounds or []):
                self._resolve_type(bound)

        # Resolve constraints
        for constraint in protocol.constraints:
            self._resolve_constraint(constraint)
        self._merge_constraints_into_params(protocol.constraints, protocol.generic_params)

        # Resolve members
        for member in protocol.members:
            self._resolve_protocol_member(member)

        self._pop_scope()

    def _resolve_protocol_member(self, member: ast.ProtocolMember) -> None:
        """Resolve a protocol member."""
        if isinstance(member, ast.ProtocolFuncReq):
            # Push function scope for generics
            self._push_scope(ScopeKind.FUNCTION)
            for param in member.generic_params:
                self._define_type(param.name, SymbolKind.GENERIC_PARAM, node=param)
                for bound in (param.bounds or []):
                    self._resolve_type(bound)
            for p in member.params:
                if p.type_annotation:
                    self._resolve_type(p.type_annotation)
            if member.return_type:
                self._resolve_type(member.return_type)
            self._pop_scope()
        elif isinstance(member, ast.ProtocolPropReq):
            if member.type_annotation:
                self._resolve_type(member.type_annotation)
        elif isinstance(member, ast.AssociatedTypeDecl):
            self._define_type(member.name, SymbolKind.ASSOCIATED_TYPE, node=member)
            for constraint in member.constraints:
                self._resolve_constraint(constraint)

    def _resolve_type_alias(self, alias: ast.TypeAliasDecl) -> None:
        """Resolve a type alias declaration."""
        if alias.aliased_type:
            self._resolve_type(alias.aliased_type)

    def _resolve_extension(self, ext: ast.ExtensionDecl) -> None:
        """Resolve an extension declaration."""
        # Mint a synthetic symbol for the extension so later passes (e.g. the
        # conformance checker) can recover the ExtensionDecl from a SymbolId.
        ext_name = "<extension>"
        if isinstance(ext.extended_type, ast.NamedType):
            ext_name = f"<extension:{ext.extended_type.name}>"
        ext_symbol = self.symbol_table.create_symbol(
            name=ext_name,
            kind=SymbolKind.EXTENSION,
            namespace=Namespace.TYPE,
            decl_node=ext,
        )
        self.node_symbols[id(ext)] = ext_symbol.id

        self._push_scope(ScopeKind.TYPE)

        # Resolve the extended type and get its symbol for self binding
        extended_type_symbol = None
        if ext.extended_type:
            self._resolve_type(ext.extended_type)
            # Get the symbol for the extended type (for self binding)
            extended_type_symbol = self.node_symbols.get(id(ext.extended_type))
            if extended_type_symbol is None and isinstance(ext.extended_type, ast.NamedType) and self.current_scope:
                extended_type_symbol = self.current_scope.lookup_type(ext.extended_type.name)

        # Resolve any declared protocol conformances.
        for conformance in ext.conformances:
            self._resolve_type(conformance)

        # Resolve constraints
        for constraint in ext.constraints:
            self._resolve_constraint(constraint)

        # Resolve members with self context
        old_type_symbol = self._current_type_symbol
        self._current_type_symbol = extended_type_symbol
        method_symbols: list[SymbolId] = []
        for member in ext.members:
            self._resolve_struct_member(member)
            if isinstance(member, ast.FuncDecl):
                sym = self.current_scope.lookup_value(member.name) if self.current_scope else None
                if sym is not None:
                    method_symbols.append(sym)
        self._current_type_symbol = old_type_symbol

        self._pop_scope()

        # Record extension method info for cross-module export. The
        # extension block's visibility ("pub" / "internal" / "private")
        # controls whether its methods cross module boundaries — see
        # _resolve_import for the filter.
        extended_name = ext.extended_type.name if isinstance(ext.extended_type, ast.NamedType) else ""
        ext_visibility = getattr(ext, 'visibility', 'internal')
        for msym_id in method_symbols:
            msym = self.symbol_table.get_symbol(msym_id)
            if msym:
                msym.is_extension_method = True
                # Propagate the extension block's visibility down to the
                # method's Symbol so downstream passes that look it up
                # see a consistent visibility.
                if ext_visibility != 'internal':
                    msym.visibility = ext_visibility
                self._extension_methods.append((extended_name, msym.name, msym_id, ext_visibility))

    # ========================= Function Declarations =========================

    def _resolve_func_decl(self, func: ast.FuncDecl) -> None:
        """Resolve a function declaration."""
        self._push_scope(ScopeKind.FUNCTION)

        # Register generic parameters in type namespace
        for param in func.generic_params:
            self._define_type(param.name, SymbolKind.GENERIC_PARAM, node=param)
            if param.bounds:
                for bound in (param.bounds or []):
                    self._resolve_type(bound)

        # Resolve constraints
        for constraint in func.constraints:
            self._resolve_constraint(constraint)
        self._merge_constraints_into_params(func.constraints, func.generic_params)

        # If inside a type (struct/enum), inject 'self' as an implicit parameter.
        # `self` is immutable as a binding (you can't write `self = something_else`)
        # but its fields are mutable through the heap reference.
        if self._current_type_symbol is not None and not func.is_static:
            self_symbol = self._define_value(
                "self",
                SymbolKind.PARAMETER,
                is_mutable=False,
                node=None,  # Don't use a node to avoid overwriting function symbol
            )
            # Store self symbol for the type checker to find
            if self_symbol and not hasattr(self, '_self_symbols'):
                self._self_symbols: Dict[SymbolId, SymbolId] = {}
            func_symbol = self.node_symbols.get(id(func))
            if self_symbol and func_symbol:
                self._self_symbols[func_symbol] = self_symbol.id

        # Register parameters in value namespace and resolve their types
        for param in func.params:
            if param.type_annotation:
                self._resolve_type(param.type_annotation)
            self._define_value(
                param.internal_name,
                SymbolKind.PARAMETER,
                is_mutable=False,
                node=param,
            )
            if param.default_value:
                self._resolve_expr(param.default_value)

        # Resolve return type
        if func.return_type:
            self._resolve_type(func.return_type)

        # Resolve body
        if func.body:
            self._resolve_block(func.body)

        self._pop_scope()

    def _resolve_extern_func(self, func: ast.ExternFuncDecl) -> None:
        """Resolve an extern function declaration."""
        self._push_scope(ScopeKind.FUNCTION)

        # Register generic parameters
        for param in func.generic_params:
            self._define_type(param.name, SymbolKind.GENERIC_PARAM, node=param)
            for bound in (param.bounds or []):
                self._resolve_type(bound)

        # Resolve constraints
        for constraint in func.constraints:
            self._resolve_constraint(constraint)
        self._merge_constraints_into_params(func.constraints, func.generic_params)

        # Resolve parameter types
        for param in func.params:
            if param.type_annotation:
                self._resolve_type(param.type_annotation)

        # Resolve return type
        if func.return_type:
            self._resolve_type(func.return_type)

        self._pop_scope()

    # ========================= Type Resolution =========================

    def _resolve_type(self, type_node: ast.Type) -> None:
        """Resolve a type reference."""
        if isinstance(type_node, ast.BuiltinType):
            # Builtins are pre-registered
            self._lookup_type(type_node.name, type_node)
        elif isinstance(type_node, ast.NamedType):
            # Build qualified name including module path for imported types
            lookup_name = type_node.name
            if type_node.module_path:
                lookup_name = ".".join(type_node.module_path) + "." + type_node.name
                # First try the qualified name, fall back to base name
                self._lookup_type(lookup_name, type_node)
            else:
                self._lookup_type(type_node.name, type_node)
            # Resolve generic arguments
            for arg in type_node.generic_args:
                self._resolve_type(arg)
        elif isinstance(type_node, ast.OptionalType):
            if type_node.inner:
                self._resolve_type(type_node.inner)
        elif isinstance(type_node, ast.ArrayType):
            if type_node.element:
                self._resolve_type(type_node.element)
        elif isinstance(type_node, ast.DictType):
            if type_node.key:
                self._resolve_type(type_node.key)
            if type_node.value:
                self._resolve_type(type_node.value)
        elif isinstance(type_node, ast.TupleType):
            for _, elem_type in type_node.elements:
                self._resolve_type(elem_type)
        elif isinstance(type_node, ast.FunctionType):
            for param_type in type_node.params:
                self._resolve_type(param_type)
            if type_node.return_type:
                self._resolve_type(type_node.return_type)
        elif isinstance(type_node, ast.AnyType):
            if type_node.protocol:
                self._resolve_type(type_node.protocol)
        elif isinstance(type_node, ast.PointerType):
            # RawPtr doesn't have an inner type to resolve.
            pass

    def _resolve_constraint(self, constraint: ast.Constraint) -> None:
        """Resolve a type constraint."""
        if isinstance(constraint.subject, ast.NamedType):
            self._resolve_type(constraint.subject)
        # Note: "Self" as subject is a special keyword, no resolution needed

        if constraint.kind == "conforms":
            for bound in constraint.bounds:
                self._resolve_type(bound)
        elif constraint.kind == "equals" and constraint.equal_type:
            self._resolve_type(constraint.equal_type)

    def _merge_constraints_into_params(
        self, constraints: list, generic_params: list
    ) -> None:
        """Merge `where` clause constraints into GenericParam.bounds by name."""
        if not constraints or not generic_params:
            return
        for constraint in constraints:
            if constraint.kind != "conforms":
                continue
            # Find the matching generic param by name
            subject_name = ""
            if isinstance(constraint.subject, ast.NamedType):
                subject_name = constraint.subject.name
            elif isinstance(constraint.subject, str):
                subject_name = constraint.subject
            if not subject_name:
                continue
            for param in generic_params:
                if param.name == subject_name:
                    if param.bounds is None:
                        param.bounds = []
                    for bound in constraint.bounds:
                        if bound not in param.bounds:
                            param.bounds.append(bound)
                    break

    # ========================= Statement Resolution =========================

    def _resolve_block(self, block: ast.Block) -> None:
        """Resolve a block of statements."""
        self._push_scope(ScopeKind.BLOCK)
        for stmt in block.statements:
            self._resolve_stmt(stmt)
        self._pop_scope()

    def _resolve_stmt(self, stmt: ast.Stmt) -> None:
        """Resolve a statement."""
        if isinstance(stmt, ast.VarDecl):
            self._resolve_var_decl(stmt)
        elif isinstance(stmt, ast.Assignment):
            self._resolve_expr(stmt.target)
            self._resolve_expr(stmt.value)
        elif isinstance(stmt, ast.ExprStmt):
            if stmt.expr:
                self._resolve_expr(stmt.expr)
        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value:
                self._resolve_expr(stmt.value)
        elif isinstance(stmt, ast.BreakStmt):
            pass
        elif isinstance(stmt, ast.ContinueStmt):
            pass
        elif isinstance(stmt, ast.Block):
            self._resolve_block(stmt)
        elif isinstance(stmt, ast.IfStmt):
            self._resolve_if_stmt(stmt)
        elif isinstance(stmt, ast.GuardStmt):
            if stmt.condition:
                self._resolve_expr(stmt.condition)
            if stmt.else_block:
                self._resolve_block(stmt.else_block)
        elif isinstance(stmt, ast.WhileStmt):
            if stmt.condition:
                self._resolve_expr(stmt.condition)
            if stmt.body:
                self._resolve_block(stmt.body)
        elif isinstance(stmt, ast.ForStmt):
            self._resolve_for_stmt(stmt)
        elif isinstance(stmt, ast.SwitchStmt):
            self._resolve_switch_stmt(stmt)
        elif isinstance(stmt, ast.DeferStmt):
            if stmt.body:
                self._resolve_block(stmt.body)

    def _resolve_var_decl(self, var_decl: ast.VarDecl) -> None:
        """Resolve a variable declaration."""
        # Resolve type annotation first
        if var_decl.type_annotation:
            self._resolve_type(var_decl.type_annotation)

        # Resolve initializer (before binding the name, so it can't reference itself)
        if var_decl.initializer:
            self._resolve_expr(var_decl.initializer)

        # Extract bindings from pattern
        if var_decl.pattern:
            self._bind_pattern(var_decl.pattern, var_decl.is_mutable)

    def _resolve_if_stmt(self, if_stmt: ast.IfStmt) -> None:
        """Resolve an if statement."""
        if isinstance(if_stmt.condition, tuple):
            # if let pattern = expr
            pattern, expr = if_stmt.condition
            self._resolve_expr(expr)
            # Create a scope for the then block that includes pattern bindings
            self._push_scope(ScopeKind.BLOCK)
            self._bind_pattern(pattern, is_mutable=False)
            if if_stmt.then_block:
                for stmt in if_stmt.then_block.statements:
                    self._resolve_stmt(stmt)
            self._pop_scope()
        else:
            # Regular if condition
            if if_stmt.condition:
                self._resolve_expr(if_stmt.condition)
            if if_stmt.then_block:
                self._resolve_block(if_stmt.then_block)

        # Resolve else block
        if if_stmt.else_block:
            if isinstance(if_stmt.else_block, ast.IfStmt):
                self._resolve_if_stmt(if_stmt.else_block)
            else:
                self._resolve_block(if_stmt.else_block)

    def _resolve_for_stmt(self, for_stmt: ast.ForStmt) -> None:
        """Resolve a for-in loop."""
        # Resolve iterable first
        if for_stmt.iterable:
            self._resolve_expr(for_stmt.iterable)

        # Create scope for loop variable and body
        self._push_scope(ScopeKind.FOR_LOOP)

        # Bind loop pattern
        if for_stmt.pattern:
            self._bind_pattern(for_stmt.pattern, is_mutable=False)

        # Resolve body
        if for_stmt.body:
            for stmt in for_stmt.body.statements:
                self._resolve_stmt(stmt)

        self._pop_scope()

    def _resolve_switch_stmt(self, switch_stmt: ast.SwitchStmt) -> None:
        """Resolve a switch statement."""
        # Resolve the value being matched
        if switch_stmt.value:
            self._resolve_expr(switch_stmt.value)

        # Resolve each case
        for case in switch_stmt.cases:
            self._resolve_switch_case(case)

    def _resolve_switch_case(self, case: ast.SwitchCase) -> None:
        """Resolve a switch case."""
        # Create scope for case bindings
        self._push_scope(ScopeKind.SWITCH_CASE)

        # Bind patterns and resolve guards
        for pattern, guard in case.patterns:
            self._bind_pattern(pattern, is_mutable=False)
            if guard:
                self._resolve_expr(guard)

        # Resolve case body
        for stmt in case.body:
            self._resolve_stmt(stmt)

        self._pop_scope()

    # ========================= Pattern Binding =========================

    def _bind_pattern(self, pattern: ast.Pattern, is_mutable: bool) -> None:
        """Extract and define bindings from a pattern."""
        if isinstance(pattern, ast.IdentifierPattern):
            # Determine mutability from binding keyword or parameter
            mutable = is_mutable or pattern.binding == "var"
            self._define_value(
                pattern.name,
                SymbolKind.VARIABLE,
                is_mutable=mutable,
                node=pattern,
            )
        elif isinstance(pattern, ast.TuplePattern):
            for _, elem_pattern in pattern.elements:
                self._bind_pattern(elem_pattern, is_mutable)
        elif isinstance(pattern, ast.EnumCasePattern):
            for payload_pattern in pattern.payload:
                self._bind_pattern(payload_pattern, is_mutable)
        elif isinstance(pattern, ast.TypedPattern):
            if pattern.type_annotation:
                self._resolve_type(pattern.type_annotation)
            if pattern.pattern:
                self._bind_pattern(pattern.pattern, is_mutable)
        elif isinstance(pattern, ast.OrPattern):
            # In or patterns, all branches must bind the same names
            # For simplicity, we just bind from the first pattern
            if pattern.patterns:
                self._bind_pattern(pattern.patterns[0], is_mutable)
        elif isinstance(pattern, ast.WildcardPattern):
            # Wildcard binds nothing
            pass
        elif isinstance(pattern, ast.LiteralPattern):
            # Literal patterns bind nothing
            pass

    # ========================= Expression Resolution =========================

    def _resolve_expr(self, expr: ast.Expr) -> None:
        """Resolve an expression."""
        if isinstance(expr, ast.Literal):
            # Literals need no resolution
            pass
        elif isinstance(expr, ast.Identifier):
            self._lookup_value(expr.name, expr)
        elif isinstance(expr, ast.TypeReference):
            if expr.type_name:
                self._resolve_type(expr.type_name)
        elif isinstance(expr, ast.BinaryOp):
            if expr.left:
                self._resolve_expr(expr.left)
            if expr.right:
                self._resolve_expr(expr.right)
        elif isinstance(expr, ast.UnaryOp):
            if expr.operand:
                self._resolve_expr(expr.operand)
        elif isinstance(expr, ast.TernaryOp):
            if expr.condition:
                self._resolve_expr(expr.condition)
            if expr.then_expr:
                self._resolve_expr(expr.then_expr)
            if expr.else_expr:
                self._resolve_expr(expr.else_expr)
        elif isinstance(expr, ast.Call):
            if expr.callee:
                self._resolve_expr(expr.callee)
            for arg in (expr.arguments or []):
                if arg.value:
                    self._resolve_expr(arg.value)
        elif isinstance(expr, ast.MemberAccess):
            # Check if this is module-namespace access (e.g., `lib.get_value`)
            if self._try_resolve_import_member(expr):
                pass  # Handled by import namespace resolution
            elif expr.object:
                self._resolve_expr(expr.object)
        elif isinstance(expr, ast.OptionalChain):
            if expr.object:
                self._resolve_expr(expr.object)
            # Suffix handling: call args or subscript
            if expr.suffix:
                if isinstance(expr.suffix, list):
                    # Call arguments
                    for arg in expr.suffix:
                        if isinstance(arg, ast.Argument) and arg.value:
                            self._resolve_expr(arg.value)
                elif isinstance(expr.suffix, ast.Expr):
                    self._resolve_expr(expr.suffix)
        elif isinstance(expr, ast.Subscript):
            if expr.object:
                self._resolve_expr(expr.object)
            for index in expr.indices:
                self._resolve_expr(index)
        elif isinstance(expr, ast.TupleExpr):
            for _, elem_expr in expr.elements:
                self._resolve_expr(elem_expr)
        elif isinstance(expr, ast.ArrayLiteral):
            for elem in expr.elements:
                self._resolve_expr(elem)
        elif isinstance(expr, ast.DictLiteral):
            for key, value in expr.entries:
                self._resolve_expr(key)
                self._resolve_expr(value)
        elif isinstance(expr, ast.Lambda):
            self._resolve_lambda(expr)
        elif isinstance(expr, ast.TryExpr):
            if expr.value:
                self._resolve_expr(expr.value)
        elif isinstance(expr, ast.StructLiteral):
            if expr.type_name:
                self._resolve_type(expr.type_name)
            for arg in (expr.arguments or []):
                if arg.value:
                    self._resolve_expr(arg.value)
        elif isinstance(expr, ast.Cast):
            if expr.expr:
                self._resolve_expr(expr.expr)
            if expr.target_type:
                self._resolve_type(expr.target_type)
        elif isinstance(expr, ast.TypeCheck):
            if expr.expr:
                self._resolve_expr(expr.expr)
            if expr.checked_type:
                self._resolve_type(expr.checked_type)
        elif isinstance(expr, (ast.SizeOfExpr, ast.AlignOfExpr, ast.DropOfExpr, ast.CloneOfExpr)):
            if expr.type_arg:
                self._resolve_type(expr.type_arg)
        elif isinstance(expr, ast.TypeIdExpr):
            if expr.type_arg:
                self._resolve_type(expr.type_arg)

    def _resolve_lambda(self, lambda_expr: ast.Lambda) -> None:
        """Resolve a lambda expression."""
        self._push_scope(ScopeKind.LAMBDA)

        # Bind parameters
        for pattern, type_ann in lambda_expr.params:
            if type_ann:
                self._resolve_type(type_ann)
            self._bind_pattern(pattern, is_mutable=False)

        # Resolve body
        for stmt in lambda_expr.body:
            self._resolve_stmt(stmt)

        self._pop_scope()


def resolve(program: ast.Program) -> ResolutionResult:
    """Resolve names in a RoLang program.

    Args:
        program: The parsed AST.

    Returns:
        ResolutionResult containing symbol table, node mappings, and errors.
    """
    resolver = NameResolver()
    return resolver.resolve(program)


def resolve_with_modules(
    program: ast.Program,
    module_graph: 'ModuleGraph',
    current_module: 'Module',
    symbol_table: Optional[SymbolTable] = None,
    node_symbols: Optional[dict[int, SymbolId]] = None,
) -> ResolutionResult:
    """
    Resolve names in a RoLang program with multi-module support.

    Args:
        program: The parsed AST
        module_graph: The module dependency graph
        current_module: The module being resolved
        symbol_table: Shared symbol table (created if None)

    Returns:
        ResolutionResult containing symbol table, node mappings, and errors
    """
    resolver = NameResolver(
        symbol_table=symbol_table,
        module_graph=module_graph,
        current_module=current_module,
        node_symbols=node_symbols,
    )
    return resolver.resolve(program)
