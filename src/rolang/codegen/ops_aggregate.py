"""Aggregate type LLVM codegen operations."""

from __future__ import annotations

from llvmlite import ir

from ..mir import (
    Operand, CopyOperand, operand_type,
    MakeStruct, MakeEnum, MakeSome, MakeNone,
    ExtractField, ExtractClosureCapture, ExtractEnumPayload, GetTag,
    AllocObj, Clone, GCCheck,
)
from ..types import TypeId, TypeKind


class OpsAggregateMixin:
    """Mixin for aggregate operations: struct, enum, optional."""

    # Aggregate operations

    def _emit_make_struct(self, op: MakeStruct) -> ir.Value:
        """Emit struct construction — heap-allocate and return pointer."""
        llvm_type = self.type_cache.get_llvm_type(op.struct_type)
        # llvm_type is PointerType(struct_type) in v2

        inner_type = self.type_cache.get_inner_struct_type(op.struct_type)
        if inner_type is None:
            inner_type = llvm_type

        # Get payload size from type descriptor
        desc = self.type_table.get_descriptor(op.struct_type)
        payload_size = desc.payload_size if desc else self.type_cache._get_type_size(op.struct_type)

        # Evaluate every field operand BEFORE allocating. Operand emission can
        # itself allocate (string/array constants), and an allocation between
        # the noinit alloc below and the field stores is a GC-observable point
        # where this object's payload would still be garbage.
        field_vals = []
        for field_name, field_op in op.fields:
            field_val = self.emit_operand(field_op)
            field_index = self.type_cache.get_struct_field_index_any(op.struct_type, field_name)
            if hasattr(inner_type, 'elements') and field_index < len(inner_type.elements):
                field_type = inner_type.elements[field_index]
                field_val = self._coerce_int(field_val, field_type,
                                             signed=self._operand_is_signed(field_op))
            field_vals.append((field_name, field_index, field_val))

        # Allocate heap object WITHOUT the payload zero-fill when the literal
        # provably stores every LLVM field immediately below with no
        # allocation/release in between (the language requires every field in
        # a struct literal, so this is the common case). Fall back to the
        # zero-filling alloc if the store list does not cover the layout.
        covers_all_fields = (
            hasattr(inner_type, 'elements')
            and len({fi for _, fi, _ in field_vals}) == len(inner_type.elements)
        )
        type_id = self.type_cache.get_or_assign_descriptor_id(op.struct_type)
        ptr = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, payload_size),
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, type_id),
            zero_init=not covers_all_fields,
        )

        # v2: payload starts after the 32-byte header
        # GEP past the header, then bitcast to inner struct type
        payload_byte_ptr = self.builder.gep(
            ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],  # OBJ_HEADER_SIZE
            name="payload.ptr"
        )
        struct_ptr = self.builder.bitcast(payload_byte_ptr, ir.PointerType(inner_type), name="struct.typed")

        # Store each field
        for field_name, field_index, field_val in field_vals:
            field_ptr = self.builder.gep(
                struct_ptr,
                [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, field_index)],
                name=f"struct.{field_name}.ptr"
            )
            self.builder.store(field_val, field_ptr)

        self._store_local(op.result, ptr)
        return ptr

    def _emit_make_enum(self, op: MakeEnum) -> ir.Value:
        """Emit enum construction — heap-allocate and return pointer (v2)."""
        llvm_type = self.type_cache.get_llvm_type(op.enum_type)

        # Get the tag type
        info = self.type_table.get_type(op.enum_type)
        if info and info.kind == TypeKind.ENUM:
            from ..types import EnumTypeData
            enum_data: EnumTypeData = info.data
            tag_type = self.type_cache.get_enum_tag_type(enum_data.symbol_id)
        else:
            tag_type = self.type_cache.i8

        inner_type = self.type_cache.get_inner_enum_type(op.enum_type)
        if inner_type is None:
            inner_type = llvm_type

        # Evaluate payload operands BEFORE allocating (operand emission can
        # allocate — see _emit_make_struct) and decide whether every declared
        # payload slot of the active case gets stored.
        layout = self.type_cache.get_enum_case_payload_layout(
            enum_data.symbol_id, op.case_name
        ) if info and info.kind == TypeKind.ENUM else []
        payload_vals = []
        for i, payload_op in enumerate(op.payload):
            payload_val = self.emit_operand(payload_op)
            offset = layout[i][0] if i < len(layout) else 0
            # Coerce integer payloads to the *declared* field width so the
            # store covers exactly the slot the layout reserved and matches
            # the width used when the payload is later extracted. Storing at
            # the value's own (possibly narrower) width leaves the high bytes
            # of the slot uninitialised and reads back as garbage.
            if i < len(layout) and isinstance(payload_val.type, ir.IntType):
                declared_llvm = self.type_cache.get_llvm_type(layout[i][1])
                if isinstance(declared_llvm, ir.IntType) and \
                        declared_llvm.width != payload_val.type.width:
                    payload_val = self._coerce_int(
                        payload_val, declared_llvm,
                        signed=self._operand_is_signed(payload_op))
            payload_vals.append((offset, payload_val))

        # Skip the payload zero-fill when the tag and the active case's whole
        # payload are stored immediately below: every descriptor walk
        # (release_fields, GC trace, clone) is tag-filtered, so the inactive
        # union bytes are never read. Fall back to zeroing if the operand list
        # does not cover the case's declared layout.
        covers_case = len(op.payload) == len(layout)

        # Allocate heap object
        payload_size = self.type_cache._get_type_size(op.enum_type)
        ptr = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, payload_size),
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, self.type_cache.get_or_assign_descriptor_id(op.enum_type)),
            zero_init=not covers_case,
        )

        # GEP past 32-byte header to payload
        payload_byte_ptr = self.builder.gep(
            ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="payload.ptr"
        )
        enum_ptr = self.builder.bitcast(payload_byte_ptr, ir.PointerType(inner_type), name="enum.typed")

        # Store tag at index 0
        tag_ptr = self.builder.gep(
            enum_ptr,
            [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 0)],
            name="enum.tag.ptr",
        )
        self.builder.store(ir.Constant(tag_type, op.tag), tag_ptr)

        if payload_vals:
            # Pack payload values
            payload_byte_ptr2 = self.builder.gep(
                enum_ptr,
                [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 1)],
                name="enum.payload.bytes",
            )
            for offset, payload_val in payload_vals:
                byte_ptr = self.builder.gep(
                    payload_byte_ptr2,
                    [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, offset)],
                    name="enum.payload.byte",
                )
                typed_ptr = self.builder.bitcast(
                    byte_ptr,
                    ir.PointerType(payload_val.type),
                    name="enum.payload.typed",
                )
                self.builder.store(payload_val, typed_ptr)

        self._store_local(op.result, ptr)
        return ptr

    def _emit_make_some(self, op: MakeSome) -> ir.Value:
        """Emit Some(value) construction."""
        inner_val = self.emit_operand(op.value)
        llvm_type = self.type_cache.get_llvm_type(op.result_type)

        # Check if optional is represented as pointer (null = None)
        if llvm_type == self.type_cache.ptr or isinstance(llvm_type, ir.PointerType):
            # Pointer type - just use the value
            result = inner_val
        else:
            # { i1, T } representation
            result = ir.Constant(llvm_type, ir.Undefined)
            result = self.builder.insert_value(result, ir.Constant(self.type_cache.i1, 1), 0, name="some.flag")
            result = self.builder.insert_value(result, inner_val, 1, name="some.val")

        self._store_local(op.result, result)
        return result

    def _emit_make_none(self, op: MakeNone) -> ir.Value:
        """Emit None value construction."""
        llvm_type = self.type_cache.get_llvm_type(op.result_type)

        if llvm_type == self.type_cache.ptr or isinstance(llvm_type, ir.PointerType):
            # Pointer type - null is None
            result = ir.Constant(llvm_type, None)
        else:
            # { i1, T } representation - is_some = false
            result = ir.Constant(llvm_type, ir.Undefined)
            result = self.builder.insert_value(result, ir.Constant(self.type_cache.i1, 0), 0, name="none.flag")

        self._store_local(op.result, result)
        return result

    def _emit_extract_field(self, op: ExtractField) -> ir.Value:
        """Emit field extraction from a heap object (v2)."""
        ptr = self.emit_operand(op.aggregate)
        agg_type = operand_type(op.aggregate)

        # v2: agg is a pointer to a heap object (including 32-byte header).
        # ptr might be a typed pointer (Point*), not i8*. Bitcast to i8*,
        # then GEP past the 32-byte header to the payload.
        inner_type = self.type_cache.get_inner_struct_type(agg_type)
        if inner_type is not None and isinstance(ptr.type, ir.PointerType):
            # Bitcast to i8* for byte-based GEP past the header
            raw_ptr = self.builder.bitcast(ptr, self.type_cache.ptr, name="raw.ptr")
            payload_byte_ptr = self.builder.gep(
                raw_ptr,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="payload.ptr"
            )
            struct_ptr = self.builder.bitcast(payload_byte_ptr, ir.PointerType(inner_type), name="field.struct.ptr")
            field_ptr = self.builder.gep(
                struct_ptr,
                [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, op.field_index)],
                name=f"field.{op.field_name}.ptr"
            )
            result = self.builder.load(field_ptr, name=f"field.{op.field_name}")
            self._store_local(op.result, result)
            return result

        # Fallback (v1 compat): extract_value on a loaded struct
        result = self.builder.extract_value(ptr, op.field_index, name=f"field.{op.field_name}")
        self._store_local(op.result, result)
        return result

    def _emit_extract_closure_capture(self, op: ExtractClosureCapture) -> ir.Value:
        """Emit capture extraction from a closure heap object."""
        closure_ptr = self.emit_operand(op.closure)
        raw_ptr = self.builder.bitcast(closure_ptr, self.type_cache.ptr, name="closure.raw")
        payload_byte_ptr = self.builder.gep(
            raw_ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="closure.payload",
        )

        closure_type = operand_type(op.closure)
        payload_type = self.type_cache.get_closure_payload_type(closure_type)
        payload_ptr = self.builder.bitcast(
            payload_byte_ptr,
            ir.PointerType(payload_type),
            name="closure.payload.typed",
        )
        capture_ptr = self.builder.gep(
            payload_ptr,
            [
                ir.Constant(self.type_cache.i32, 0),
                ir.Constant(self.type_cache.i32, op.capture_index + 1),
            ],
            name=f"closure.capture.{op.capture_index}.ptr",
        )
        result = self.builder.load(capture_ptr, name=f"closure.capture.{op.capture_index}")
        self._store_local(op.result, result)
        return result

    def _emit_extract_enum_payload(self, op: ExtractEnumPayload) -> ir.Value:
        """Emit enum payload extraction from heap pointer (v2)."""
        enum_ptr = self.emit_operand(op.enum_val)
        enum_type_id = operand_type(op.enum_val)
        info = self.type_table.get_type(enum_type_id)

        # Optionals use a {i1, T} representation: payload is just element 1.
        if info and info.kind == TypeKind.OPTIONAL:
            # Check if the optional uses pointer representation (null = None)
            if isinstance(enum_ptr.type, ir.PointerType):
                # For pointer-optionals, the payload IS the pointer
                result = enum_ptr
                self._store_local(op.result, result)
                return result
            result = self.builder.extract_value(enum_ptr, 1, name="opt.payload")
            self._store_local(op.result, result)
            return result

        if not (info and info.kind == TypeKind.ENUM):
            llvm_type = self.type_cache.get_llvm_type(op.result_type)
            result = ir.Constant(llvm_type, ir.Undefined)
            self._store_local(op.result, result)
            return result

        from ..types import EnumTypeData
        enum_data: EnumTypeData = info.data
        layout = self.type_cache.get_enum_case_payload_layout(
            enum_data.symbol_id, op.case_name
        )
        offset = layout[op.payload_index][0] if op.payload_index < len(layout) else 0

        # enum is now a pointer — bitcast to i8*, GEP past header, then extract
        inner_type = self.type_cache.get_inner_enum_type(enum_type_id)
        if inner_type is not None and isinstance(enum_ptr.type, ir.PointerType):
            raw_ptr = self.builder.bitcast(enum_ptr, self.type_cache.ptr, name="raw.ptr")
            payload_byte_ptr = self.builder.gep(
                raw_ptr,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="payload.ptr"
            )
            enum_typed = self.builder.bitcast(payload_byte_ptr, ir.PointerType(inner_type), name="enum.extract.typed")
            payload_byte_ptr2 = self.builder.gep(
                enum_typed,
                [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 1)],
                name="enum.extract.bytes",
            )
            byte_ptr = self.builder.gep(
                payload_byte_ptr2,
                [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, offset)],
                name="enum.extract.byte",
            )
            result_llvm_type = self.type_cache.get_llvm_type(op.result_type)
            typed_ptr = self.builder.bitcast(
                byte_ptr, ir.PointerType(result_llvm_type), name="enum.extract.typed"
            )
            result = self.builder.load(typed_ptr, name="enum.payload")
            self._store_local(op.result, result)
            return result

        # Fallback: enum_ptr is a heap pointer (PointerType).  Load the enum
        # struct directly through the ARC header using the raw byte approach.
        # enum_ptr : %EnumName* (single-level heap pointer in v2)
        # Layout: [32-byte ARC header][tag][payload bytes...]
        if isinstance(enum_ptr.type, ir.PointerType):
            raw_ptr = self.builder.bitcast(enum_ptr, self.type_cache.ptr, name="fb.raw.ptr")
            payload_byte_ptr = self.builder.gep(
                raw_ptr,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="fb.payload.ptr",
            )
            # Advance past the tag field (i8/i16/i32 depending on case count)
            # by casting to i8* and using a byte-level offset into the payload.
            # The tag is at offset 0 of the struct body; payload bytes start
            # right after it (struct field index 1 in the LLVM type).
            # Re-use inner_type if available, otherwise obtain via get_enum_type.
            if inner_type is None:
                inner_type = self.type_cache.get_inner_enum_type(enum_type_id)
            if inner_type is not None:
                enum_typed = self.builder.bitcast(
                    payload_byte_ptr, ir.PointerType(inner_type), name="fb.enum.typed"
                )
                payload_bytes_ptr = self.builder.gep(
                    enum_typed,
                    [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 1)],
                    name="fb.payload.bytes",
                )
                byte_ptr = self.builder.gep(
                    payload_bytes_ptr,
                    [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, offset)],
                    name="fb.payload.byte",
                )
            else:
                # Absolute last resort: treat payload bytes as a raw byte array
                # starting right after the 32-byte header.
                byte_ptr = self.builder.gep(
                    raw_ptr,
                    [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE + offset)],
                    name="fb.abs.byte",
                )
            result_llvm_type = self.type_cache.get_llvm_type(op.result_type)
            typed_ptr = self.builder.bitcast(
                byte_ptr, ir.PointerType(result_llvm_type), name="fb.typed.ptr"
            )
            result = self.builder.load(typed_ptr, name="fb.enum.payload")
            self._store_local(op.result, result)
            return result

        # Should be unreachable in v2 (all enum values are heap pointers).
        result_llvm_type = self.type_cache.get_llvm_type(op.result_type)
        result = ir.Constant(result_llvm_type, ir.Undefined)
        self._store_local(op.result, result)
        return result

    def _emit_get_tag(self, op: GetTag) -> ir.Value:
        """Emit enum/optional tag extraction."""
        enum_val = self.emit_operand(op.enum_val)
        enum_type_id = operand_type(op.enum_val)
        info = self.type_table.get_type(enum_type_id)

        # Optional<T> where T is a pointer: null = None, non-null = Some
        if info and info.kind == TypeKind.OPTIONAL and isinstance(enum_val.type, ir.PointerType):
            # Tag: 0 = None (null), 1 = Some (non-null)
            null_val = ir.Constant(enum_val.type, None)
            is_some = self.builder.icmp_unsigned("!=", enum_val, null_val, name="opt.tag")
            result = self.builder.zext(is_some, self.type_cache.i32, name="tag.i32")
            self._store_local(op.result, result)
            return result

        inner_type = self.type_cache.get_inner_enum_type(enum_type_id)
        if inner_type is not None and isinstance(enum_val.type, ir.PointerType):
            raw_ptr = self.builder.bitcast(enum_val, self.type_cache.ptr, name="raw.ptr")
            payload_byte_ptr = self.builder.gep(
                raw_ptr,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="payload.ptr"
            )
            enum_typed = self.builder.bitcast(payload_byte_ptr, ir.PointerType(inner_type), name="gettag.typed")
            tag_ptr = self.builder.gep(
                enum_typed,
                [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 0)],
                name="tag.ptr"
            )
            result = self.builder.load(tag_ptr, name="tag")
            self._store_local(op.result, result)
            return result

        # Fallback: extract_value on value type
        result = self.builder.extract_value(enum_val, 0, name="tag")
        self._store_local(op.result, result)
        return result



    def _value_to_runtime_ptr(
        self,
        value: ir.Value,
        type_id: TypeId,
        name: str,
    ) -> ir.Value:
        """Store a value in a temporary slot and return it as i8*."""
        llvm_type = self.type_cache.get_llvm_type(type_id)
        slot = self.builder.alloca(llvm_type, name=name)
        self.builder.store(value, slot)
        return self.builder.bitcast(slot, self.type_cache.ptr, name=f"{name}.ptr")

    def _runtime_out_slot(self, type_id: TypeId, name: str) -> tuple[ir.Value, ir.AllocaInstr]:
        """Create a runtime output slot and return (i8* slot, typed slot)."""
        llvm_type = self.type_cache.get_llvm_type(type_id)
        slot = self.builder.alloca(llvm_type, name=name)
        self.builder.store(ir.Constant(llvm_type, ir.Undefined), slot)
        runtime_ptr = self.builder.bitcast(slot, self.type_cache.ptr, name=f"{name}.ptr")
        return runtime_ptr, slot

    def _to_i64(self, value: ir.Value) -> ir.Value:
        """Coerce an integer LLVM value to i64 for runtime calls."""
        if value.type == self.type_cache.i64:
            return value
        if isinstance(value.type, ir.IntType):
            if value.type.width < 64:
                return self.builder.sext(value, self.type_cache.i64, name="idx64")
            if value.type.width > 64:
                return self.builder.trunc(value, self.type_cache.i64, name="idx64")
        return value

    def _make_optional_result(
        self,
        optional_type: TypeId,
        value: ir.Value,
        found: ir.Value,
    ) -> ir.Value:
        """Build an Optional<T> result from a runtime found flag and loaded value."""
        llvm_type = self.type_cache.get_llvm_type(optional_type)
        if llvm_type == self.type_cache.ptr or isinstance(llvm_type, ir.PointerType):
            null_value = ir.Constant(llvm_type, None)
            is_found = self.builder.icmp_unsigned("!=", found, ir.Constant(found.type, 0), name="dict.found")
            return self.builder.select(is_found, value, null_value, name="dict.optional")

        flag = self.builder.icmp_unsigned("!=", found, ir.Constant(found.type, 0), name="dict.found")
        result = ir.Constant(llvm_type, ir.Undefined)
        result = self.builder.insert_value(result, flag, 0, name="dict.some")
        result = self.builder.insert_value(result, value, 1, name="dict.value")
        return result

    def _dict_key_kind(self, key_type: TypeId) -> int:
        """Return the runtime comparison strategy for a dictionary key."""
        if self.type_table.is_string(key_type):
            return 1
        return 0

    def _get_elem_type_id(self, type_id: TypeId) -> int:
        """Return the type descriptor id for a collection element type.

        Returns 0 for byte-only values and a non-zero ARC marker for heap
        references. Runtime vectors/dicts only need this to decide whether a
        copied slot contains an object pointer that must be retained/released.
        """
        return self.type_table.runtime_type_id(type_id)

    # ========================================================================
    # v2 Object Operations
    # ========================================================================

    def _emit_alloc_obj(self, op: AllocObj) -> ir.Value:
        """Emit heap object allocation for a heap type."""
        payload_size = self.type_cache._get_type_size(op.result_type)
        ptr = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, payload_size),
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, self.type_cache.get_or_assign_descriptor_id(op.result_type)),
        )
        self._store_local(op.result, ptr)
        return ptr

    def _emit_clone(self, op: Clone) -> ir.Value:
        """Emit deep clone via rt_obj_clone."""
        value = self.emit_operand(op.value)
        result = self.runtime.emit_obj_clone(self.builder, value)
        self._store_local(op.result, result)
        return result

    def _emit_gc_check(self, op: GCCheck) -> ir.Value:
        """Emit conditional GC trigger for cycle detection.

        Calls rt_gc_collect() which internally checks allocation count
        and early-returns if not enough allocations since last collection.
        """
        self.runtime.emit_gc_collect(self.builder)
        return None
