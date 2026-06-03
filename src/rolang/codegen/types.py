"""
TypeLayoutCache - LLVM type mapping and layout computation.

Maps Rolang types to LLVM IR types and computes memory layouts.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from llvmlite import ir

from ..types import (
    TypeId,
    TypeKind,
    TypeTable,
    PrimitiveType,
    PrimitiveTypeData,
    StructTypeData,
    EnumTypeData,

    OptionalTypeData,
    ClosureTypeData,
    ProtocolTypeData,
    ExistentialTypeData,
)
from ..symbols import SymbolTable, SymbolId
from ..mir import MirStruct, MirEnum, MirEnumCase, MirField
from ..layout import LayoutService


# Size in bytes of the ARC object header (ObjHeader in rolang_rt.c) that
# prefixes every heap object's payload: {i64 rc, u64 type_id, ObjHeader* prev,
# ObjHeader* next}. This is the single source of truth for the payload offset
# baked into codegen GEPs (`gep obj, OBJ_HEADER_SIZE` -> payload). It MUST equal
# sizeof(ObjHeader) on the runtime side, which a _Static_assert in rolang_rt.c
# enforces against its own _OBJ_HEADER_SIZE. Change this in lockstep with the
# struct to resize the header.
OBJ_HEADER_SIZE = 32


class TypeLayoutCache:
    """
    Cache for LLVM type representations and layouts.

    Handles mapping from Rolang types to LLVM IR types, including:
    - Primitives (integers, floats, bool)
    - Aggregates (structs, tuples, enums)
    - Optionals
    """

    # Exposed on the instance (via self.type_cache) so codegen mixins can write
    # `self.type_cache.OBJ_HEADER_SIZE` for the heap payload offset.
    OBJ_HEADER_SIZE = OBJ_HEADER_SIZE

    def __init__(
        self,
        module: ir.Module,
        type_table: TypeTable,
        symbol_table: SymbolTable,
    ) -> None:
        self.module = module
        self.type_table = type_table
        self.symbol_table = symbol_table
        self._layout = LayoutService(type_table, symbol_table)

        # Cache: TypeId -> LLVM type
        self._type_cache: Dict[TypeId, ir.Type] = {}

        # Cache for struct types by symbol
        self._struct_types: Dict[SymbolId, ir.IdentifiedStructType] = {}
        self._struct_field_indices: Dict[SymbolId, Dict[str, int]] = {}
        # Cache for anonymous (tuple-backed) struct types by TypeId
        self._anon_struct_types: Dict[TypeId, ir.LiteralStructType] = {}
        # MIR-only structs, such as async frames, may not have AST symbols.
        # Keep their fields here so payload sizing and GC descriptors still
        # match the LLVM struct body emitted from MIR.
        self._mir_struct_fields_by_type: Dict[TypeId, list[MirField]] = {}

        # Cache for enum types by symbol
        self._enum_types: Dict[SymbolId, ir.IdentifiedStructType] = {}
        self._enum_tag_types: Dict[SymbolId, ir.IntType] = {}
        self._enum_max_payload_sizes: Dict[SymbolId, int] = {}
        self._enum_case_tags: Dict[SymbolId, Dict[str, int]] = {}
        # (symbol_id, case_name) -> list of (byte_offset, payload_type_id)
        self._enum_case_payload_layout: Dict[
            tuple[SymbolId, str], list[tuple[int, TypeId]]
        ] = {}

        # Type descriptor ID assignment for GC tracing
        # Maps TypeId → sequential descriptor index (0-based)
        self._descriptor_ids: Dict[TypeId, int] = {}
        self._next_descriptor_id = 0

        # Common LLVM types
        self.i1 = ir.IntType(1)
        self.i8 = ir.IntType(8)
        self.i16 = ir.IntType(16)
        self.i32 = ir.IntType(32)
        self.i64 = ir.IntType(64)
        self.f32 = ir.FloatType()
        self.f64 = ir.DoubleType()
        self.void = ir.VoidType()
        self.ptr = ir.PointerType(self.i8)  # Opaque pointer

    def get_llvm_type(self, type_id: TypeId) -> ir.Type:
        """Get the LLVM type for a Rolang type."""
        if type_id in self._type_cache:
            return self._type_cache[type_id]

        info = self.type_table.get_type(type_id)
        if info is None:
            # Default to void for unknown types
            return self.void

        llvm_type = self._compute_llvm_type(info.kind, info.data, type_id)
        self._type_cache[type_id] = llvm_type
        return llvm_type

    def _try_set_struct_body(
        self,
        symbol_id: SymbolId,
        struct_type: ir.IdentifiedStructType,
        name: str,
    ) -> None:
        """Set the body of an opaque struct using pre-computed layout metadata.

        If the layout contains unresolved type variables (e.g. a generic struct
        that has not yet been monomorphized) we leave the body opaque so that
        ``get_struct_type`` can set it later from the MIR struct fields.
        """
        if struct_type.elements:
            return
        layout = self._layout.get_struct_layout(symbol_id)
        if layout is None:
            return
        field_types: list[ir.Type] = []
        field_indices: Dict[str, int] = {}
        for i, field in enumerate(layout.fields):
            # Unresolved type variable — wait for monomorphized MIR struct
            info = self.type_table.get_type(field.type_id)
            if info and info.kind == TypeKind.TYPE_VARIABLE:
                return
            field_types.append(self.get_llvm_type(field.type_id))
            field_indices[field.name] = i
        if field_types:
            struct_type.set_body(*field_types)
            self._struct_field_indices[symbol_id] = field_indices

    def _compute_llvm_type(
        self,
        kind: TypeKind,
        data,
        type_id: TypeId,
    ) -> ir.Type:
        """Compute LLVM type for a Rolang type."""
        if kind == TypeKind.PRIMITIVE:
            return self._primitive_type(data)

        elif kind == TypeKind.STRUCT:
            struct_data: StructTypeData = data
            if struct_data.symbol_id is None:
                # Anonymous struct (tuple): use LiteralStructType, cached by type_id
                if type_id in self._anon_struct_types:
                    return ir.PointerType(self._anon_struct_types[type_id])
                fields = struct_data.anon_fields or ()
                element_types = [self.get_llvm_type(t) for _, t in fields]
                inner = ir.LiteralStructType(element_types)
                self._anon_struct_types[type_id] = inner
                return ir.PointerType(inner)
            if struct_data.symbol_id in self._struct_types:
                return ir.PointerType(self._struct_types[struct_data.symbol_id])
            # Forward reference - create opaque struct
            name = self._get_struct_name(struct_data.symbol_id)
            struct_type = self.module.context.get_identified_type(name)
            self._struct_types[struct_data.symbol_id] = struct_type
            # Try to set the body if the struct symbol has field info
            self._try_set_struct_body(struct_data.symbol_id, struct_type, name)
            return ir.PointerType(struct_type)

        elif kind == TypeKind.ENUM:
            enum_data: EnumTypeData = data
            if enum_data.symbol_id in self._enum_types:
                return ir.PointerType(self._enum_types[enum_data.symbol_id])
            # Forward reference - create opaque struct
            name = self._get_enum_name(enum_data.symbol_id)
            enum_type = self.module.context.get_identified_type(name)
            self._enum_types[enum_data.symbol_id] = enum_type
            return ir.PointerType(enum_type)

        elif kind == TypeKind.OPTIONAL:
            optional_data: OptionalTypeData = data
            inner_type = self.get_llvm_type(optional_data.inner)

            # If inner is already a pointer type, use null for None
            # Otherwise, use { i1, T } representation
            if isinstance(inner_type, ir.PointerType) or inner_type == self.ptr:
                return inner_type  # null = None
            else:
                # { i1 is_some, T value }
                return ir.LiteralStructType([self.i1, inner_type])

        elif kind == TypeKind.FUNCTION:
            # First-class callable values are references to compiler-generated
            # closure heap objects whose payload starts with fn_ptr.
            return self.ptr

        elif kind == TypeKind.CLOSURE:
            # Closure values are typed-object references. The object payload is
            # { fn_ptr, captures... } and is described in the GC descriptor table.
            return self.ptr

        elif kind == TypeKind.PROTOCOL:
            # Protocol type is just for type checking, represented as pointer at runtime
            return self.ptr

        elif kind == TypeKind.EXISTENTIAL:
            # Existential values are typed-object references. The object
            # payload is { witness_table_ptr, value_obj_ptr }.
            return self.ptr

        elif kind == TypeKind.NEVER:
            return self.void

        elif kind == TypeKind.ERROR:
            return self.void

        else:
            return self.void

    def _primitive_type(self, data: PrimitiveTypeData) -> ir.Type:
        """Get LLVM type for a primitive."""
        prim = data.primitive
        match prim:
            case PrimitiveType.I8:
                return self.i8
            case PrimitiveType.I16:
                return self.i16
            case PrimitiveType.I32:
                return self.i32
            case PrimitiveType.I64:
                return self.i64
            case PrimitiveType.U8:
                return self.i8
            case PrimitiveType.U16:
                return self.i16
            case PrimitiveType.U32:
                return self.i32
            case PrimitiveType.U64:
                return self.i64
            case PrimitiveType.F32:
                return self.f32
            case PrimitiveType.F64:
                return self.f64
            case PrimitiveType.BOOL:
                return self.i1
            case PrimitiveType.VOID:
                return self.void
            case PrimitiveType.RAW_PTR:
                return self.ptr
            case _:
                return self.void

    def get_struct_type(self, mir_struct: MirStruct) -> ir.IdentifiedStructType:
        """Get or create LLVM struct type from MIR struct."""
        symbol_id = mir_struct.symbol_id
        self._mir_struct_fields_by_type[mir_struct.type_id] = list(mir_struct.fields)
        field_indices = {
            field.name: i for i, field in enumerate(mir_struct.fields)
        }
        if symbol_id and symbol_id in self._struct_types:
            struct_type = self._struct_types[symbol_id]
            self._struct_field_indices[symbol_id] = field_indices
            # Check if already set
            if struct_type.elements:
                return struct_type
        else:
            name = mir_struct.name
            struct_type = self.module.context.get_identified_type(name)
            if symbol_id:
                self._struct_types[symbol_id] = struct_type

        # Compute field types and indices
        field_types: list[ir.Type] = []

        for i, field in enumerate(mir_struct.fields):
            field_types.append(self.get_llvm_type(field.type_id))

        # Set the struct body. Skip if already set (e.g. two symbol_ids share a
        # mangled name across modules, since the LLVM context can be shared).
        if not struct_type.elements:
            struct_type.set_body(*field_types)

        if symbol_id:
            self._struct_field_indices[symbol_id] = field_indices

        return struct_type

    def get_struct_field_index(self, symbol_id: SymbolId, field_name: str) -> int:
        """Get the index of a field in a struct."""
        if symbol_id in self._struct_field_indices:
            return self._struct_field_indices[symbol_id].get(field_name, 0)
        return 0

    def get_struct_field_index_any(self, type_id: TypeId, field_name: str) -> int:
        """Get the field index for any struct type, by TypeId."""
        info = self.type_table.get_type(type_id)
        if info and info.kind == TypeKind.STRUCT:
            from ..types import StructTypeData
            if isinstance(info.data, StructTypeData):
                if info.data.symbol_id is not None:
                    return self.get_struct_field_index(info.data.symbol_id, field_name)
                # Anonymous struct: search anon_fields by name
                fields = info.data.anon_fields or ()
                for i, (fname, _) in enumerate(fields):
                    if fname == field_name:
                        return i
                # Fallback: treat field_name as a numeric index string
                try:
                    return int(field_name)
                except ValueError:
                    pass
        return 0

    def get_or_assign_descriptor_id(self, type_id: TypeId) -> int:
        """Get or assign a sequential type descriptor ID for GC tracing."""
        if type_id not in self._descriptor_ids:
            self._descriptor_ids[type_id] = self._next_descriptor_id
            self._next_descriptor_id += 1
        return self._descriptor_ids[type_id]

    def get_descriptor_count(self) -> int:
        """Get the total number of assigned type descriptors."""
        return self._next_descriptor_id

    def compute_field_descriptors(self) -> dict[int, list[tuple[int, int, int]]]:
        """Compute field descriptors for all types with assigned descriptor IDs.

        Returns a dict mapping descriptor_id -> list of
        (byte_offset, field_type_descriptor_id, case_tag)
        for heap-typed fields that need ARC tracking.

        case_tag is -1 for struct/tuple fields, and the enum case tag value
        for enum payload fields.  The runtime uses case_tag to filter fields
        belonging to the currently-active enum case.
        """

        def _is_heap_field(tid: TypeId) -> bool:
            """Check if a field type is a heap type that needs a field descriptor."""
            info = self.type_table.get_type(tid)
            if info is None:
                return False
            if info.kind in (TypeKind.STRUCT, TypeKind.ENUM, TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL):
                return True
            if info.kind == TypeKind.OPTIONAL:
                d = info.data
                if not hasattr(d, 'inner'):
                    return False
                inner_info = self.type_table.get_type(d.inner)
                return inner_info is not None and inner_info.kind in (
                    TypeKind.STRUCT, TypeKind.ENUM,
                    TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL,
                )
            return False

        def _get_field_desc_type(tid: TypeId) -> Optional[TypeId]:
            """Get the type to describe for a heap-typed field."""
            info = self.type_table.get_type(tid)
            if info is None:
                return None
            if info.kind in (TypeKind.STRUCT, TypeKind.ENUM, TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL):
                return tid
            if info.kind == TypeKind.OPTIONAL:
                d = info.data
                inner_info = self.type_table.get_type(d.inner) if hasattr(d, 'inner') else None
                if inner_info is not None and inner_info.kind in (
                    TypeKind.STRUCT, TypeKind.ENUM,
                    TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL,
                ):
                    return d.inner
            return None

        # Worklist approach: field descriptor computation may assign new
        # descriptor IDs for nested heap types. Process until all assigned
        # types have been handled.
        processed: set[int] = set()
        result: dict[int, list[tuple[int, int, int]]] = {}

        while True:
            pending = [
                (tid, self._descriptor_ids[tid])
                for tid in self._descriptor_ids
                if self._descriptor_ids[tid] not in processed
            ]
            if not pending:
                break

            for type_id, desc_id in pending:
                processed.add(desc_id)
                field_descs: list[tuple[int, int, int]] = []
                info = self.type_table.get_type(type_id)
                if info is None:
                    result[desc_id] = field_descs
                    continue

                if info.kind == TypeKind.STRUCT:
                    struct_data = info.data
                    if not hasattr(struct_data, 'symbol_id'):
                        result[desc_id] = field_descs
                        continue
                    symbol_id = struct_data.symbol_id
                    for offset, elem_type_id in self._struct_payload_layout(type_id):
                        if _is_heap_field(elem_type_id):
                            field_type = _get_field_desc_type(elem_type_id)
                            if field_type is not None:
                                field_desc_id = self.get_or_assign_descriptor_id(field_type)
                                field_descs.append((offset, field_desc_id, -1))

                elif info.kind == TypeKind.ENUM:
                    enum_data = info.data
                    if not hasattr(enum_data, 'symbol_id'):
                        result[desc_id] = field_descs
                        continue
                    symbol_id = enum_data.symbol_id
                    # Use codegen's *own* layout (the same source used to emit the
                    # LLVM body in get_enum_type) so descriptor offsets match the
                    # generated code exactly. The body is { tag, [payload x i8] }
                    # where the tag is the codegen tag type (i32, read as
                    # int32_t by the runtime GC paths) and each payload field is
                    # naturally aligned within the payload array.
                    #
                    # NOTE: the LayoutService's get_enum_layout() must NOT be used
                    # here: it models the tag as an adaptive 1/2/4-byte field and
                    # lays out payload fields without alignment padding, neither of
                    # which matches the emitted body — using it records heap-pointer
                    # offsets several bytes too low, so the runtime reads garbage
                    # pointers and crashes on release.
                    tag_size = self.get_enum_tag_type(symbol_id).width // 8
                    case_tags = self._enum_case_tags.get(symbol_id, {})
                    for case_name, case_tag in case_tags.items():
                        for payload_offset, payload_type_id in \
                                self.get_enum_case_payload_layout(symbol_id, case_name):
                            actual_offset = tag_size + payload_offset
                            if _is_heap_field(payload_type_id):
                                field_type = _get_field_desc_type(payload_type_id)
                                if field_type is not None:
                                    field_desc_id = self.get_or_assign_descriptor_id(field_type)
                                    field_descs.append((actual_offset, field_desc_id, case_tag))

                elif info.kind == TypeKind.CLOSURE:
                    closure_data = info.data
                    if not isinstance(closure_data, ClosureTypeData):
                        result[desc_id] = field_descs
                        continue

                    for offset, capture_type_id in self.get_closure_capture_layout(type_id):
                        if _is_heap_field(capture_type_id):
                            field_type = _get_field_desc_type(capture_type_id)
                            if field_type is not None:
                                field_desc_id = self.get_or_assign_descriptor_id(field_type)
                                field_descs.append((offset, field_desc_id, -1))

                elif info.kind == TypeKind.EXISTENTIAL:
                    # Existential payload is { witness_table_ptr, value_obj_ptr }.
                    # Only the value_obj_ptr at offset 8 is a managed reference.
                    # The concrete type is unknown statically; the GC must follow
                    # the pointer and use the object's own type-descriptor header
                    # rather than a parent-side descriptor.  We use the sentinel
                    # value 0xFFFF_FFFF_FFFF_FFFF (-1 as uint64) so the runtime
                    # GC can recognise "opaque managed pointer — trace via header".
                    OPAQUE_MANAGED_PTR_DESC = (1 << 64) - 1  # sentinel: trace via header
                    field_descs.append((8, OPAQUE_MANAGED_PTR_DESC, -1))

                result[desc_id] = field_descs

        return result

    def acyclic_descriptor_ids(self, type_table, type_to_trace) -> set:
        """Return descriptor ids whose type can never be part of a cycle.

        `field_desc_map` (from compute_field_descriptors) gives heap-field edges
        desc_id -> field_type_descriptor_id. Existentials, closures, and any
        type with a registered trace_fn (containers) are conservative: their
        element/capture set isn't statically walkable here, so they may point
        anywhere. Field edges whose target descriptor id is the opaque
        existential sentinel (or otherwise out of range) are likewise modeled
        as pointing to "unknown", so the source becomes conservative.
        """
        from ..acyclic import cyclic_capable_ids
        from ..types import TypeKind

        field_desc_map = self.compute_field_descriptors()
        num = self.get_descriptor_count()
        desc_to_type = {did: tid for tid, did in self._descriptor_ids.items()}

        edges: Dict[int, list] = {}
        conservative = set()
        for did in range(num):
            # field_desc_map[did] entries are
            # (byte_offset, field_type_descriptor_id, case_tag); index [1] is
            # the target descriptor id.
            targets = []
            for fd in field_desc_map.get(did, []):
                target = fd[1]
                if 0 <= target < num:
                    targets.append(target)
                else:
                    # Opaque managed pointer (existential sentinel) or any other
                    # out-of-range target: points to an unknown type. Treat the
                    # source as conservative so it can reach anything.
                    conservative.add(did)
            edges[did] = targets

            tid = desc_to_type.get(did)
            if tid is None:
                conservative.add(did)
                continue
            info = type_table.get_type(tid)
            if info is not None and info.kind in (TypeKind.EXISTENTIAL, TypeKind.CLOSURE):
                conservative.add(did)
            if tid in type_to_trace:  # containers (Vec/Dict/user __gc_trace__)
                conservative.add(did)

        cyclic = cyclic_capable_ids(num_ids=num, edges=edges, conservative=conservative)
        return set(range(num)) - cyclic

    def get_enum_type(self, mir_enum: MirEnum) -> ir.IdentifiedStructType:
        """
        Get or create LLVM enum type from MIR enum.

        Enum layout: { tag_type, [max_payload_size x i8] }
        - tag: i8 for ≤255 cases, i16 or i32 otherwise
        - payload: union of all case payloads as byte array
        """
        symbol_id = mir_enum.symbol_id
        if symbol_id and symbol_id in self._enum_types:
            enum_type = self._enum_types[symbol_id]
            if enum_type.elements:
                return enum_type
        else:
            name = mir_enum.name
            enum_type = self.module.context.get_identified_type(name)
            if symbol_id:
                self._enum_types[symbol_id] = enum_type

        # Determine tag type — always use i32 to match runtime (rolang_rt.c
        # reads *(int32_t*)payload for the enum discriminant in every GC path).
        tag_type = self.i32

        if symbol_id:
            self._enum_tag_types[symbol_id] = tag_type

        # Compute max payload size and build case tag map
        max_payload_size = 0
        case_tags: Dict[str, int] = {}

        for case in mir_enum.cases:
            case_tags[case.name] = case.tag

            # Compute payload size and per-element offsets for this case,
            # applying C/LLVM-style natural alignment padding between fields.
            case_size = 0
            layout: list[tuple[int, TypeId]] = []
            for _, payload_type_id in case.payload_types:
                size, align = self._field_size_and_align_for_payload(payload_type_id)
                case_size = self._align_to(case_size, align)
                layout.append((case_size, payload_type_id))
                case_size += size
            if symbol_id is not None:
                self._enum_case_payload_layout[(symbol_id, case.name)] = layout

            max_payload_size = max(max_payload_size, case_size)

        if symbol_id:
            self._enum_max_payload_sizes[symbol_id] = max_payload_size
            self._enum_case_tags[symbol_id] = case_tags

        # Set enum body: { tag, [payload_size x i8] }
        # Skip if the LLVM type body is already set (e.g. two symbol_ids map to
        # the same mangled name after monomorphization).
        if not enum_type.elements:
            if max_payload_size > 0:
                payload_type = ir.ArrayType(self.i8, max_payload_size)
                enum_type.set_body(tag_type, payload_type)
            else:
                # No payload - just the tag
                enum_type.set_body(tag_type)

        return enum_type

    def get_enum_tag_type(self, symbol_id: SymbolId) -> ir.IntType:
        """Get the tag type for an enum."""
        return self._enum_tag_types.get(symbol_id, self.i8)

    def get_enum_case_tag(self, symbol_id: SymbolId, case_name: str) -> int:
        """Get the tag value for an enum case."""
        if symbol_id in self._enum_case_tags:
            return self._enum_case_tags[symbol_id].get(case_name, 0)
        return 0

    def get_enum_payload_size(self, symbol_id: SymbolId) -> int:
        """Get the maximum payload size for an enum."""
        return self._enum_max_payload_sizes.get(symbol_id, 0)

    def get_enum_case_payload_layout(
        self, symbol_id: SymbolId, case_name: str
    ) -> list[tuple[int, TypeId]]:
        """Return [(byte_offset, payload_type_id), ...] for a case's payload."""
        return self._enum_case_payload_layout.get((symbol_id, case_name), [])

    def _get_type_size(self, type_id: TypeId) -> int:
        """Get the size in bytes of a type (approximate)."""
        info = self.type_table.get_type(type_id)
        if info is not None and info.kind == TypeKind.STRUCT:
            return self._struct_payload_size(type_id)
        if info is not None and info.kind == TypeKind.ENUM:
            # The emitted body is { tag, [max_payload x i8] }: the allocator
            # must reserve the tag plus the largest case payload. Falling back
            # to size_of() here would return the pointer size (8) and badly
            # under-allocate any enum that carries a payload, corrupting the heap.
            sym = getattr(info.data, "symbol_id", None)
            if sym is not None:
                tag_bytes = self.get_enum_tag_type(sym).width // 8
                return tag_bytes + self.get_enum_payload_size(sym)
            return self._layout.size_of(type_id)
        if info is not None and info.kind == TypeKind.CLOSURE:
            return self.get_closure_payload_size(type_id)
        if info is not None and info.kind == TypeKind.EXISTENTIAL:
            return self.get_existential_payload_size()
        return self._layout.size_of(type_id)

    def _field_size_and_align_for_payload(self, type_id: TypeId) -> tuple[int, int]:
        """Return storage size/alignment for values embedded in object payloads."""
        info = self.type_table.get_type(type_id)
        if info and info.kind in (TypeKind.STRUCT, TypeKind.ENUM, TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL):
            return (8, 8)
        if info and info.kind == TypeKind.OPTIONAL:
            data = info.data
            inner = getattr(data, "inner", None)
            inner_info = self.type_table.get_type(inner) if inner is not None else None
            if inner_info is not None and inner_info.kind in (
                TypeKind.STRUCT, TypeKind.ENUM,
                TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL,
            ):
                return (8, 8)
        return (self._layout.size_of(type_id), self._layout.align_of(type_id))

    def _struct_payload_fields(self, type_id: TypeId) -> list[tuple[str, TypeId]]:
        """Return source/MIR fields for a struct type in payload order."""
        mir_fields = self._mir_struct_fields_by_type.get(type_id)
        if mir_fields is not None:
            return [(field.name, field.type_id) for field in mir_fields]

        info = self.type_table.get_type(type_id)
        if info is None or info.kind != TypeKind.STRUCT or not isinstance(info.data, StructTypeData):
            return []

        if info.data.symbol_id is None:
            return list(info.data.anon_fields or ())

        layout = self._layout.get_struct_layout(info.data.symbol_id)
        if layout is None:
            return []
        return [(field.name, field.type_id) for field in layout.fields]

    def _struct_payload_layout(self, type_id: TypeId) -> list[tuple[int, TypeId]]:
        """Return [(byte_offset, field_type), ...] with C/LLVM-style padding."""
        offset = 0
        layout: list[tuple[int, TypeId]] = []
        for _, field_type in self._struct_payload_fields(type_id):
            size, align = self._field_size_and_align_for_payload(field_type)
            offset = self._align_to(offset, align)
            layout.append((offset, field_type))
            offset += size
        return layout

    def _struct_payload_size(self, type_id: TypeId) -> int:
        """Return the padded payload size for a struct object."""
        offset = 0
        max_align = 1
        for _, field_type in self._struct_payload_fields(type_id):
            size, align = self._field_size_and_align_for_payload(field_type)
            max_align = max(max_align, align)
            offset = self._align_to(offset, align)
            offset += size
        return max(1, self._align_to(offset, max_align))

    @staticmethod
    def _align_to(offset: int, align: int) -> int:
        return (offset + align - 1) & ~(align - 1)

    def get_closure_payload_type(self, type_id: TypeId) -> ir.LiteralStructType:
        """Return the LLVM payload type for a closure object: {fn_ptr, captures...}."""
        info = self.type_table.get_type(type_id)
        capture_types: tuple[TypeId, ...] = ()
        if info and info.kind == TypeKind.CLOSURE and isinstance(info.data, ClosureTypeData):
            capture_types = info.data.captures
        fields = [self.ptr]
        fields.extend(self.get_llvm_type(capture_type) for capture_type in capture_types)
        return ir.LiteralStructType(fields)

    def get_closure_capture_layout(self, type_id: TypeId) -> list[tuple[int, TypeId]]:
        """Return [(payload_offset, capture_type), ...] for closure captures."""
        info = self.type_table.get_type(type_id)
        if info is None or info.kind != TypeKind.CLOSURE or not isinstance(info.data, ClosureTypeData):
            return []

        offset = 8  # fn_ptr lives at payload offset 0
        layout: list[tuple[int, TypeId]] = []
        for capture_type in info.data.captures:
            size, align = self._field_size_and_align_for_payload(capture_type)
            offset = self._align_to(offset, align)
            layout.append((offset, capture_type))
            offset += size
        return layout

    def get_closure_payload_size(self, type_id: TypeId) -> int:
        """Return payload bytes for a closure object, including fn_ptr and captures."""
        info = self.type_table.get_type(type_id)
        capture_types: tuple[TypeId, ...] = ()
        if info and info.kind == TypeKind.CLOSURE and isinstance(info.data, ClosureTypeData):
            capture_types = info.data.captures

        offset = 8
        max_align = 8
        for capture_type in capture_types:
            size, align = self._field_size_and_align_for_payload(capture_type)
            max_align = max(max_align, align)
            offset = self._align_to(offset, align)
            offset += size
        return max(8, self._align_to(offset, max_align))

    def get_existential_payload_type(self) -> ir.LiteralStructType:
        """Return the LLVM payload type for any Protocol objects."""
        return ir.LiteralStructType([self.ptr, self.ptr])

    def get_existential_payload_size(self) -> int:
        """Return payload bytes for { witness_table_ptr, value_obj_ptr }."""
        return 16

    def get_type_alignment(self, type_id: TypeId) -> int:
        """Get the alignment in bytes of a type."""
        info = self.type_table.get_type(type_id)
        if info is not None and info.kind == TypeKind.STRUCT:
            max_align = 1
            for _, field_type in self._struct_payload_fields(type_id):
                _, align = self._field_size_and_align_for_payload(field_type)
                max_align = max(max_align, align)
            return max_align
        return self._layout.align_of(type_id)

    def _get_struct_name(self, symbol_id: SymbolId) -> str:
        """Get the LLVM struct name for a symbol."""
        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol:
            return symbol.name
        return f"struct.{symbol_id.id}"

    def _get_enum_name(self, symbol_id: SymbolId) -> str:
        """Get the LLVM enum name for a symbol."""
        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol:
            return symbol.name
        return f"enum.{symbol_id.id}"

    def is_signed_integer(self, type_id: TypeId) -> bool:
        """Check if a type is a signed integer."""
        return self.type_table.is_signed_integer(type_id)

    def is_float(self, type_id: TypeId) -> bool:
        """Check if a type is a float."""
        return self.type_table.is_float(type_id)

    def is_integer(self, type_id: TypeId) -> bool:
        """Check if a type is an integer."""
        return self.type_table.is_integer(type_id)

    def is_numeric(self, type_id: TypeId) -> bool:
        """Check if a type is numeric."""
        return self.type_table.is_numeric(type_id)

    def is_bool(self, type_id: TypeId) -> bool:
        """Check if a type is Bool."""
        return self.type_table.is_bool(type_id)

    # ================================================================
    # v2: Inner type accessors (get the pointed-to type without pointer)
    # ================================================================

    def get_inner_struct_type(self, type_id: TypeId) -> Optional[ir.Type]:
        """Get the LLVM struct type that a pointer-to-struct points to."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return None
        if info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                if data.symbol_id is None:
                    # Anonymous struct: build/retrieve LiteralStructType
                    if type_id in self._anon_struct_types:
                        return self._anon_struct_types[type_id]
                    fields = data.anon_fields or ()
                    element_types = [self.get_llvm_type(t) for _, t in fields]
                    inner = ir.LiteralStructType(element_types)
                    self._anon_struct_types[type_id] = inner
                    return inner
                if data.symbol_id in self._struct_types:
                    return self._struct_types[data.symbol_id]
        return None

    def get_inner_enum_type(self, type_id: TypeId) -> Optional[ir.Type]:
        """Get the LLVM enum type that a pointer-to-enum points to."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return None
        if info.kind == TypeKind.ENUM:
            data = info.data
            if isinstance(data, EnumTypeData) and data.symbol_id in self._enum_types:
                return self._enum_types[data.symbol_id]
        return None
