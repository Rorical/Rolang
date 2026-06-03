"""Memory and place LLVM codegen operations."""

from __future__ import annotations

from typing import Optional

from llvmlite import ir

from ..mir import (
    LocalId, Operand, CopyOperand, ConstantOperand, ConstantKind, operand_type,
    Place, PlaceProjection, FieldProjection, IndexProjection, DerefProjection,
    Assign, Store, Load,
)
from ..types import TypeId, TypeKind


class OpsMemoryMixin:
    """Mixin for memory operations: place helpers, store, load, assign, constants, coercion."""

    def _load_place(self, place: Place) -> ir.Value:
        """Load value from a place."""
        # Start with the base local's alloca
        if place.base not in self.local_storage:
            raise ValueError(
                f"Codegen error: local _{place.base.id} not found in local_storage "
                f"(type: {self.type_table.format_type(place.type_id)})"
            )

        ptr = self.local_storage[place.base]

        # Get base local's type for field lookups
        current_type_id = self.local_types.get(place.base)

        # v2: If base is a heap type AND there are projections, load the pointer
        # and advance past the 32-byte header to the payload
        has_projections = len(place.projections) > 0
        if has_projections and current_type_id and self.type_table.is_heap_type(current_type_id):
            heap_ptr = self.builder.load(ptr, name="heap_ptr")
            raw_ptr = self.builder.bitcast(heap_ptr, self.type_cache.ptr, name="raw.ptr")
            ptr = self.builder.gep(
                raw_ptr,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="payload.ptr"
            )
            inner_type = self.type_cache.get_inner_struct_type(current_type_id)
            if inner_type is None:
                inner_type = self.type_cache.get_inner_enum_type(current_type_id)
            if inner_type is None:
                llvm_t = self.type_cache.get_llvm_type(current_type_id)
                if isinstance(llvm_t, ir.PointerType):
                    inner_type = llvm_t.pointee
            if inner_type is not None:
                ptr = self.builder.bitcast(ptr, ir.PointerType(inner_type), name="typed.ptr")

        # Apply projections
        for idx, proj in enumerate(place.projections):
            if isinstance(proj, FieldProjection):
                # GEP to field
                ptr = self._gep_to_field(ptr, proj.field_name, proj.result_type, current_type_id)
                current_type_id = proj.result_type
            elif isinstance(proj, IndexProjection):
                # Array/dict indexing - needs runtime call
                index_val = self.emit_operand(proj.index)
                ptr = self._gep_dynamic(ptr, index_val)
            elif isinstance(proj, DerefProjection):
                # Load pointer, then we can continue with that
                loaded = self.builder.load(ptr, name="deref_ptr")
                ptr = loaded

            # If the projected field is itself a heap type AND there are more
            # projections ahead, load the pointer and advance past the 32-byte
            # header so the next projection works on the inner object's payload.
            is_last = idx == len(place.projections) - 1
            if not is_last and isinstance(proj, FieldProjection):
                if current_type_id and self.type_table.is_heap_type(current_type_id):
                    heap_ptr = self.builder.load(ptr, name="chained.heap_ptr")
                    raw_ptr = self.builder.bitcast(heap_ptr, self.type_cache.ptr, name="chained.raw")
                    ptr = self.builder.gep(
                        raw_ptr,
                        [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                        name="chained.payload"
                    )
                    inner_type = self.type_cache.get_inner_struct_type(current_type_id)
                    if inner_type is None:
                        inner_type = self.type_cache.get_inner_enum_type(current_type_id)
                    if inner_type is None:
                        llvm_t = self.type_cache.get_llvm_type(current_type_id)
                        if isinstance(llvm_t, ir.PointerType):
                            inner_type = llvm_t.pointee
                    if inner_type is not None:
                        ptr = self.builder.bitcast(ptr, ir.PointerType(inner_type), name="chained.typed")

        # Load final value
        if isinstance(ptr.type, ir.PointerType):
            pointed_type = self.type_cache.get_llvm_type(place.type_id)
            if isinstance(pointed_type, ir.VoidType):
                return ir.Constant(self.type_cache.i64, 0)
            # Bitcast if pointer type doesn't match (e.g. i8* -> {i8*, i64}*)
            if ptr.type != ir.PointerType(pointed_type):
                ptr = self.builder.bitcast(ptr, ir.PointerType(pointed_type), name="cast")
            return self.builder.load(ptr, name="load")
        return ptr

    def _store_place(self, place: Place, value: ir.Value) -> None:
        """Store value to a place."""
        if place.base not in self.local_storage:
            raise ValueError(
                f"Codegen error: local _{place.base.id} not found in local_storage "
                f"(type: {self.type_table.format_type(place.type_id)})"
            )

        ptr = self.local_storage[place.base]

        # Get base local's type for detection
        current_type_id = self.local_types.get(place.base)

        # v2: If base is a heap type AND there are projections, load and deref
        has_projections = len(place.projections) > 0
        if has_projections and current_type_id and self.type_table.is_heap_type(current_type_id):
            heap_ptr = self.builder.load(ptr, name="heap_ptr")
            raw_ptr = self.builder.bitcast(heap_ptr, self.type_cache.ptr, name="raw.ptr")
            ptr = self.builder.gep(
                raw_ptr,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="payload.ptr"
            )
            inner_type = self.type_cache.get_inner_struct_type(current_type_id)
            if inner_type is None:
                inner_type = self.type_cache.get_inner_enum_type(current_type_id)
            if inner_type is None:
                llvm_t = self.type_cache.get_llvm_type(current_type_id)
                if isinstance(llvm_t, ir.PointerType):
                    inner_type = llvm_t.pointee
            if inner_type is not None:
                ptr = self.builder.bitcast(ptr, ir.PointerType(inner_type), name="typed.ptr")

        # Apply projections to get the store target
        for idx, proj in enumerate(place.projections):
            if isinstance(proj, FieldProjection):
                ptr = self._gep_to_field(ptr, proj.field_name, proj.result_type, current_type_id)
                current_type_id = proj.result_type  # Update type after projection
            elif isinstance(proj, IndexProjection):
                index_val = self.emit_operand(proj.index)
                ptr = self._gep_dynamic(ptr, index_val)
                current_type_id = proj.result_type
            elif isinstance(proj, DerefProjection):
                loaded = self.builder.load(ptr, name="deref_ptr")
                ptr = loaded
                current_type_id = proj.result_type

            # If the projected field is itself a heap type AND there are more
            # projections ahead, load the pointer and advance past the 32-byte
            # header so the next projection works on the inner object's payload.
            is_last = idx == len(place.projections) - 1
            if not is_last and isinstance(proj, FieldProjection):
                if current_type_id and self.type_table.is_heap_type(current_type_id):
                    heap_ptr = self.builder.load(ptr, name="chained.heap_ptr")
                    raw_ptr = self.builder.bitcast(heap_ptr, self.type_cache.ptr, name="chained.raw")
                    ptr = self.builder.gep(
                        raw_ptr,
                        [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                        name="chained.payload"
                    )
                    inner_type = self.type_cache.get_inner_struct_type(current_type_id)
                    if inner_type is None:
                        inner_type = self.type_cache.get_inner_enum_type(current_type_id)
                    if inner_type is None:
                        llvm_t = self.type_cache.get_llvm_type(current_type_id)
                        if isinstance(llvm_t, ir.PointerType):
                            inner_type = llvm_t.pointee
                    if inner_type is not None:
                        ptr = self.builder.bitcast(ptr, ir.PointerType(inner_type), name="chained.typed")

        # Coerce value to target type if needed
        target_type = self.type_cache.get_llvm_type(place.type_id)
        value = self._coerce_int(value, target_type,
                                 signed=self._type_is_signed(place.type_id))

        # Store the value
        self.builder.store(value, ptr)

    def _gep_to_field(
        self,
        ptr: ir.Value,
        field_name: str,
        result_type: TypeId,
        base_type_id: Optional[TypeId] = None,
    ) -> ir.Value:
        """Get element pointer to a struct field."""
        from ..types import StructTypeData

        actual_struct_type = base_type_id

        # Get field index from struct type
        field_index = 0
        if actual_struct_type:
            field_index = self.type_cache.get_struct_field_index_any(
                actual_struct_type, field_name
            )

        i32 = ir.IntType(32)
        zero = ir.Constant(i32, 0)
        idx = ir.Constant(i32, field_index)
        return self.builder.gep(ptr, [zero, idx], name=f"field_{field_name}")

    def _gep_to_index(self, ptr: ir.Value, index: int) -> ir.Value:
        """Get element pointer by index (for tuples)."""
        i32 = ir.IntType(32)
        zero = ir.Constant(i32, 0)
        idx = ir.Constant(i32, index)
        return self.builder.gep(ptr, [zero, idx], name=f"elem_{index}")

    def _gep_dynamic(self, ptr: ir.Value, index: ir.Value) -> ir.Value:
        """Get element pointer with dynamic index."""
        i64 = ir.IntType(64)
        zero = ir.Constant(i64, 0)
        # Normalise to i64: widen narrow types, keep i64 as-is.
        # Never truncate — that would silently wrap indices > 2^31.
        if index.type != i64:
            if isinstance(index.type, ir.IntType) and index.type.width < 64:
                index = self.builder.zext(index, i64, name="idx.zext")
            else:
                index = self.builder.bitcast(index, i64, name="idx.cast")
        return self.builder.gep(ptr, [zero, index], name="dyn_elem")

    def _emit_constant(self, const: ConstantOperand) -> ir.Value:
        """Emit LLVM value for a constant."""
        llvm_type = self.type_cache.get_llvm_type(const.type_id)

        if const.kind == ConstantKind.INT:
            return ir.Constant(llvm_type, const.value)

        elif const.kind == ConstantKind.FLOAT:
            return ir.Constant(llvm_type, const.value)

        elif const.kind == ConstantKind.BOOL:
            return ir.Constant(self.type_cache.i1, 1 if const.value else 0)

        elif const.kind == ConstantKind.STRING:
            old_constant_type = getattr(self, "_current_constant_type", None)
            self._current_constant_type = const.type_id
            try:
                return self._emit_string_constant(const.value)
            finally:
                self._current_constant_type = old_constant_type

        elif const.kind == ConstantKind.NIL:
            # null pointer, typed to the constant's own type so it can be stored
            # into a typed heap-pointer slot (e.g. String*, Node*), not only the
            # generic i8* RawPtr slot. RawPtr's llvm type is itself i8*, so this
            # is unchanged for the pre-existing RawPtr/`nil`-RawPtr cases.
            if isinstance(llvm_type, ir.PointerType):
                return ir.Constant(llvm_type, None)
            return ir.Constant(self.type_cache.ptr, None)

        elif const.kind == ConstantKind.UNIT:
            # Unit/void - return dummy value
            return ir.Constant(self.type_cache.i64, 0)

        else:
            return ir.Constant(llvm_type, ir.Undefined)

    def _emit_string_constant(self, value: str) -> ir.Value:
        """Emit a string constant."""
        # Create global string constant
        string_bytes = (value + "\0").encode("utf-8")
        string_type = ir.ArrayType(ir.IntType(8), len(string_bytes))

        # Counter lives on the Module so names stay unique across functions
        # (InstructionEmitter is per-function and would otherwise reuse `.str.0`).
        next_id = getattr(self.module, "_rolang_str_counter", 0)
        self.module._rolang_str_counter = next_id + 1
        name = f".str.{next_id}"

        global_str = ir.GlobalVariable(self.module, string_type, name=name)
        global_str.global_constant = True
        global_str.linkage = 'private'
        global_str.initializer = ir.Constant(
            string_type,
            bytearray(string_bytes)
        )

        # Get pointer to the string data
        i32 = ir.IntType(32)
        zero = ir.Constant(i32, 0)
        str_ptr = self.builder.gep(
            global_str,
            [zero, zero],
            name="str_ptr"
        )

        length = ir.Constant(self.type_cache.i64, len(value))

        # Allocate a std String heap object whose first payload field is
        # the runtime-owned StringVal handle.
        type_id = getattr(self, "_current_constant_type", None)
        assert type_id is not None, (
            "String constant emitted without a type context — "
            "_current_constant_type must be set before calling _emit_string_constant"
        )
        info = self.type_table.get_type(type_id)
        assert (info is not None and info.kind == TypeKind.STRUCT
                and self.type_table.format_type(type_id).startswith("String")), (
            f"_emit_string_constant: unexpected type context {self.type_table.format_type(type_id)}"
        )
        handle = self.runtime.emit_string_from_rodata(self.builder, str_ptr, length)
        payload_size = ir.Constant(self.type_cache.i64, 16)  # StringPayload {data*, len}

        obj = self.runtime.emit_obj_alloc(
            self.builder,
            payload_size,
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, self.type_cache.get_or_assign_descriptor_id(type_id)),
        )
        payload = self.builder.gep(
            obj,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="string.payload",
        )
        data_slot = self.builder.bitcast(
            payload,
            ir.PointerType(self.type_cache.ptr),
            name="string.data.slot",
        )
        len_slot = self.builder.bitcast(
            self.builder.gep(payload, [ir.Constant(self.type_cache.i64, 8)]),
            ir.PointerType(self.type_cache.i64),
            name="string.len.slot",
        )
        # Extract {data, len} from the StringVal handle
        sv_type = ir.LiteralStructType([self.type_cache.ptr, self.type_cache.i64])
        sv_ptr = self.builder.bitcast(handle, ir.PointerType(sv_type), name="string.val.ptr")
        sv_data = self.builder.load(
            self.builder.gep(sv_ptr, [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 0)]),
            name="string.val.data",
        )
        sv_len = self.builder.load(
            self.builder.gep(sv_ptr, [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 1)]),
            name="string.val.len",
        )
        self.builder.store(sv_data, data_slot)
        self.builder.store(sv_len, len_slot)
        self.runtime.emit_free(self.builder, handle)
        target_type = self.type_cache.get_llvm_type(type_id)
        if isinstance(target_type, ir.PointerType) and obj.type != target_type:
            return self.builder.bitcast(obj, target_type, name="string.obj")
        return obj


    # Memory operations

    def _emit_assign(self, op: Assign) -> ir.Value:
        """Emit assignment."""
        value = self.emit_operand(op.value)
        self._store_place(op.place, value)
        return value

    def _emit_store(self, op: Store) -> ir.Value:
        """Emit store to place."""
        value = self.emit_operand(op.value)
        self._store_place(op.place, value)
        return value

    def _emit_load(self, op: Load) -> ir.Value:
        """Emit load from place."""
        value = self._load_place(op.place)
        self._store_local(op.result, value)
        return value


    # Helper methods

    def _coerce_int(self, value: ir.Value, target_type: ir.Type,
                    signed: bool = True) -> ir.Value:
        """Coerce an integer value to a target integer type.

        `signed` selects sign- vs zero-extension when widening. Callers that
        know the source value is an unsigned integer should pass signed=False
        so the high bits are zero-filled (sign-extending an unsigned value with
        the top bit set corrupts it, e.g. u8 200 -> 0xFFFFFFC8).
        """
        if value.type == target_type:
            return value
        # Don't coerce non-integers or void types
        if not isinstance(value.type, ir.IntType) or not isinstance(target_type, ir.IntType):
            return value
        if isinstance(target_type, ir.VoidType):
            return value

        src_bits = value.type.width
        tgt_bits = target_type.width

        if src_bits < tgt_bits:
            if src_bits == 1 or not signed:
                # Bool (i1) and unsigned integers zero-extend.
                return self.builder.zext(value, target_type, name="zext")
            return self.builder.sext(value, target_type, name="sext")
        elif src_bits > tgt_bits:
            return self.builder.trunc(value, target_type, name="trunc")
        return value

    def _operand_is_signed(self, operand) -> bool:
        """True if a MIR operand's type is a signed integer (defaults to True
        when the type is unknown, preserving prior sign-extending behaviour)."""
        try:
            from ..mir import operand_type as _op_type
            tid = _op_type(operand)
        except Exception:
            return True
        return self._type_is_signed(tid)

    def _type_is_signed(self, type_id) -> bool:
        """True if `type_id` is a signed integer; defaults to True for
        non-integer/unknown types (preserving prior sign-extending behaviour)."""
        if type_id is not None and self.type_table.is_integer(type_id):
            return self.type_table.is_signed_integer(type_id)
        return True

    def _coerce_binop_operands(self, left: ir.Value, right: ir.Value) -> tuple[ir.Value, ir.Value]:
        """Coerce binary operation operands to a common type."""
        if left.type == right.type:
            return left, right

        # Both must be integers for integer coercion
        if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
            left_bits = left.type.width
            right_bits = right.type.width

            if left_bits > right_bits:
                right = self._coerce_int(right, left.type)
            elif right_bits > left_bits:
                left = self._coerce_int(left, right.type)

        return left, right

    def _store_local(self, local_id: LocalId, value: ir.Value) -> None:
        """Store a value to a local variable."""
        if local_id in self.local_storage:
            alloca = self.local_storage[local_id]
            # Get the target type from the alloca (pointer to T -> T)
            if hasattr(alloca.type, 'pointee'):
                target_type = alloca.type.pointee
            else:
                # For opaque pointers, get from local_types
                target_type_id = self.local_types.get(local_id)
                if target_type_id:
                    target_type = self.type_cache.get_llvm_type(target_type_id)
                else:
                    target_type = value.type

            # v2: If both value and target are pointer types, bitcast to match
            if isinstance(value.type, ir.PointerType) and isinstance(target_type, ir.PointerType):
                if value.type != target_type:
                    value = self.builder.bitcast(value, target_type, name="ptr.cast")
            else:
                # Coerce integers if needed
                value = self._coerce_int(
                    value, target_type,
                    signed=self._type_is_signed(self.local_types.get(local_id)))
            self.builder.store(value, alloca)
