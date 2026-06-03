"""Layout computation - unified size and alignment for all types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import ast
from .symbols import SymbolId, SymbolKind, SymbolTable
from .types import (
    TypeId,
    TypeKind,
    TypeTable,
    PrimitiveTypeData,
    PrimitiveType,
    OptionalTypeData,
    StructTypeData,
    EnumTypeData,
)


def _align_up(value: int, alignment: int) -> int:
    """Round ``value`` up to the next multiple of ``alignment`` (which must
    be a positive power of two for the natural-alignment cases we use)."""
    if alignment <= 1:
        return value
    return ((value + alignment - 1) // alignment) * alignment


@dataclass
class FieldLayout:
    """Resolved field information for a struct or enum."""

    name: str
    type_id: TypeId
    index: int


@dataclass
class StructLayout:
    """Resolved layout metadata for a struct type."""

    symbol_id: SymbolId
    fields: List[FieldLayout] = field(default_factory=list)
    size: int = 0
    alignment: int = 8


@dataclass
class EnumCaseLayout:
    """Resolved layout metadata for a single enum case."""

    name: str
    tag: int
    payload_size: int = 0
    # [(byte_offset, type_id), ...]
    payload_layout: List[tuple[int, TypeId]] = field(default_factory=list)


@dataclass
class EnumLayout:
    """Resolved layout metadata for an enum type."""

    symbol_id: SymbolId
    cases: List[EnumCaseLayout] = field(default_factory=list)
    tag_size: int = 1
    max_payload_size: int = 0
    size: int = 0
    alignment: int = 8


class LayoutService:
    """Compute byte sizes and alignments for Rolang types.

    Consolidates the previously scattered size logic from checker, MIR
    builder, and codegen into one place so that ``sizeof(T)``,
    runtime elem_size, and LLVM aggregate layout agree.
    """

    def __init__(
        self,
        type_table: TypeTable,
        symbol_table: SymbolTable,
        type_resolver: Optional[object] = None,
    ) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.type_resolver = type_resolver
        self._size_cache: dict[TypeId, int] = {}
        self._struct_layout_cache: dict[SymbolId, StructLayout] = {}
        self._enum_layout_cache: dict[SymbolId, EnumLayout] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def size_of(self, type_id: TypeId) -> int:
        """Return the storage size of a type in bytes.

        In Rolang v2 every struct/enum value lives on the heap and is
        represented by a pointer everywhere it appears as a value (variables,
        fields, function arguments, vec slots). Therefore the "size" of a
        heap type for the purposes of `sizeof(T)` — the macro that drives
        Vec / Dict element layout — is the pointer size (8), NOT the size
        of the underlying payload.

        Use `payload_size_of` when you need the size of the *payload*
        allocated by `rt_obj_alloc` (e.g. for the Type Descriptor table).
        """
        if type_id in self._size_cache:
            return self._size_cache[type_id]

        info = self.type_table.get_type(type_id)
        if info is None:
            result = 8
        elif info.kind == TypeKind.PRIMITIVE:
            result = self._primitive_size(info.data)
        elif info.kind in (
            TypeKind.STRUCT,
            TypeKind.ENUM,
            TypeKind.FUNCTION,
            TypeKind.CLOSURE,
            TypeKind.EXISTENTIAL,
        ):
            # Heap types are represented as pointers.
            result = 8
        elif info.kind == TypeKind.OPTIONAL:
            result = self._optional_size(info.data)
        else:
            result = 8

        self._size_cache[type_id] = result
        return result

    def payload_size_of(self, type_id: TypeId) -> int:
        """Return the *payload* size of a type — the number of bytes the
        runtime allocator (`rt_obj_alloc`) reserves for the object's
        fields, not counting the 32-byte object header.

        Distinct from `size_of`, which returns the storage size of a
        *value* of that type (pointer-sized for heap kinds).
        """
        info = self.type_table.get_type(type_id)
        if info is None:
            return 8
        if info.kind == TypeKind.STRUCT:
            return self._struct_size(info.data)
        if info.kind == TypeKind.ENUM:
            return self._enum_size(info.data)
        return self.size_of(type_id)

    def align_of(self, type_id: TypeId) -> int:
        """Return the alignment of a type in bytes."""
        size = self.size_of(type_id)
        if size <= 1:
            return 1
        if size <= 2:
            return 2
        if size <= 4:
            return 4
        return 8

    def get_struct_layout(self, symbol_id: SymbolId) -> Optional[StructLayout]:
        """Return cached or freshly-computed field layout for a struct symbol."""
        if symbol_id in self._struct_layout_cache:
            return self._struct_layout_cache[symbol_id]

        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.StructDecl):
            return None

        fields: list[FieldLayout] = []
        total = 0
        field_index = 0
        for member in symbol.decl_node.members:
            if isinstance(member, ast.PropertyDecl) and member.type_annotation:
                field_type = self._resolve_type(member.type_annotation)
                fields.append(
                    FieldLayout(
                        name=member.name,
                        type_id=field_type,
                        index=field_index,
                    )
                )
                total += self.size_of(field_type)
                field_index += 1

        layout = StructLayout(
            symbol_id=symbol_id,
            fields=fields,
            size=max(total, 1),
            alignment=self.align_of(self.type_table.make_struct(symbol_id, ())),
        )
        self._struct_layout_cache[symbol_id] = layout
        return layout

    def get_enum_layout(self, symbol_id: SymbolId) -> Optional[EnumLayout]:
        """Return cached or freshly-computed layout metadata for an enum."""
        if symbol_id in self._enum_layout_cache:
            return self._enum_layout_cache[symbol_id]

        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.EnumDecl):
            return None

        cases: list[EnumCaseLayout] = []
        tag = 0
        max_payload = 0

        for member in symbol.decl_node.members:
            if not isinstance(member, ast.EnumCaseDecl):
                continue
            for case in member.cases:
                payload_size = 0
                payload_layout: list[tuple[int, TypeId]] = []
                for _, type_node in case.payload:
                    payload_type = self._resolve_type(type_node)
                    payload_layout.append((payload_size, payload_type))
                    payload_size += self.size_of(payload_type)
                cases.append(
                    EnumCaseLayout(
                        name=case.name,
                        tag=tag,
                        payload_size=payload_size,
                        payload_layout=payload_layout,
                    )
                )
                max_payload = max(max_payload, payload_size)
                tag += 1

        num_cases = len(cases)
        tag_size = 1 if num_cases <= 256 else (2 if num_cases <= 65536 else 4)
        # Avoid recursion: compute alignment directly instead of calling
        # align_of() which would call size_of() -> _enum_size() -> get_enum_layout().
        alignment = tag_size if max_payload == 0 else 8
        layout = EnumLayout(
            symbol_id=symbol_id,
            cases=cases,
            tag_size=tag_size,
            max_payload_size=max_payload,
            size=tag_size + max_payload,
            alignment=alignment,
        )
        self._enum_layout_cache[symbol_id] = layout
        return layout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _primitive_size(self, data: object) -> int:
        if not isinstance(data, PrimitiveTypeData):
            return 8
        match data.primitive:
            case PrimitiveType.I8 | PrimitiveType.U8 | PrimitiveType.BOOL:
                return 1
            case PrimitiveType.I16 | PrimitiveType.U16:
                return 2
            case PrimitiveType.I32 | PrimitiveType.U32 | PrimitiveType.F32:
                return 4
            case PrimitiveType.I64 | PrimitiveType.U64 | PrimitiveType.F64:
                return 8
            case PrimitiveType.VOID:
                return 0
            case _:
                return 8

    def _optional_size(self, data: object) -> int:
        """Size of an ``Optional<T>``.

        Heap-shaped ``T`` (struct, enum, closure, existential) uses the
        null-pointer-is-None representation and therefore costs one pointer.

        For all other ``T`` we use a ``{i1 tag, T payload}`` layout that
        LLVM emits as ``LiteralStructType([i1, T])`` — its byte size includes
        padding so the payload sits at a multiple of ``align_of(T)`` and a
        trailing pad rounds the whole struct up to the same alignment. The
        size we report MUST match LLVM's struct size, otherwise vec/dict
        slot strides and async result allocations under-allocate and the
        runtime writes past the buffer (corrupted reads, heap overflow).
        """
        if not isinstance(data, OptionalTypeData):
            return 8
        inner_info = self.type_table.get_type(data.inner)
        if inner_info and inner_info.kind in (
            TypeKind.STRUCT,
            TypeKind.ENUM,
            TypeKind.CLOSURE,
            TypeKind.EXISTENTIAL,
            TypeKind.FUNCTION,
        ):
            return 8
        inner_size = self.size_of(data.inner)
        inner_align = max(1, self.align_of(data.inner))
        # Pad the 1-byte tag up to the payload's alignment, then add the
        # payload, then round the total up to the same alignment.
        payload_offset = _align_up(1, inner_align)
        total = payload_offset + inner_size
        return _align_up(total, inner_align)

    def _struct_size(self, data: object) -> int:
        if not isinstance(data, StructTypeData):
            return 8
        if data.symbol_id is None:
            # Anonymous struct (tuple): sum element sizes; empty tuple = 0
            fields = data.anon_fields or ()
            return sum(self.size_of(t) for _, t in fields)
        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.StructDecl):
            return 8
        total = 0
        for member in symbol.decl_node.members:
            if isinstance(member, ast.PropertyDecl) and member.type_annotation:
                field_type = self._resolve_type(member.type_annotation)
                total += self.size_of(field_type)
        return max(total, 1)

    def _enum_size(self, data: object) -> int:
        if not isinstance(data, EnumTypeData):
            return 8
        layout = self.get_enum_layout(data.symbol_id)
        if layout is None:
            return 8
        return layout.size

    def _resolve_type(self, type_node: ast.Type) -> TypeId:
        if self.type_resolver is not None:
            return self.type_resolver.resolve(type_node)
        # Fallback: build a temporary resolver with symbol-table lookup.
        from .type_resolver import TypeResolver

        resolver = TypeResolver(
            self.type_table, self.symbol_table, allow_symbol_table_lookup=True
        )
        return resolver.resolve(type_node)
