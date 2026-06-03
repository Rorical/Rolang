"""Existential type LLVM codegen operations."""

from __future__ import annotations

from typing import Optional

from llvmlite import ir

from ..types import (
    TypeId, TypeKind, ExistentialTypeData, ProtocolTypeData,
)
from ..mir import BoxExistential, ExistentialCheckType, ExistentialUnbox


class OpsExistentialMixin:
    """Mixin for existential operations: box, unbox, close."""

    def _emit_existential_payload_ptr(self, existential: ir.Value) -> ir.Value:
        """Return a typed pointer to an existential object's payload."""
        if existential.type == self.type_cache.ptr:
            raw_ptr = existential
        else:
            raw_ptr = self.builder.bitcast(
                existential,
                self.type_cache.ptr,
                name="exist.raw",
            )
        payload_byte_ptr = self.builder.gep(
            raw_ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="exist.payload",
        )
        return self.builder.bitcast(
            payload_byte_ptr,
            ir.PointerType(self.type_cache.get_existential_payload_type()),
            name="exist.payload.typed",
        )

    def _emit_existential_parts(self, existential: ir.Value) -> tuple[ir.Value, ir.Value]:
        """Load { witness_table_ptr, value_obj_ptr } from an existential object."""
        payload_ptr = self._emit_existential_payload_ptr(existential)
        witness_slot = self.builder.gep(
            payload_ptr,
            [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 0)],
            name="exist.witness.ptr",
        )
        value_slot = self.builder.gep(
            payload_ptr,
            [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 1)],
            name="exist.value.ptr",
        )
        witness_ptr = self.builder.load(witness_slot, name="exist.witness")
        value_ptr = self.builder.load(value_slot, name="exist.value")
        return witness_ptr, value_ptr

    def _emit_boxed_value_object(self, value: ir.Value, concrete_type: TypeId) -> ir.Value:
        """Return the managed object pointer stored inside an existential."""
        if self.type_table.is_heap_type(concrete_type):
            if value.type == self.type_cache.ptr:
                return value
            return self.builder.bitcast(value, self.type_cache.ptr, name="exist.value.obj")

        payload_size = max(1, self.type_cache._get_type_size(concrete_type))
        value_obj = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, payload_size),
            ir.Constant(self.type_cache.i64, self.type_cache.get_type_alignment(concrete_type)),
            ir.Constant(self.type_cache.i64, self.type_cache.get_or_assign_descriptor_id(concrete_type)),
        )

        if not isinstance(value.type, ir.VoidType):
            value_payload = self.builder.gep(
                value_obj,
                [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
                name="exist.value.payload",
            )
            typed_value_ptr = self.builder.bitcast(
                value_payload,
                value.type.as_pointer(),
                name="exist.value.typed",
            )
            self.builder.store(value, typed_value_ptr)

        return value_obj

    def _existential_method_index(
        self,
        existential_type: TypeId,
        method_name: str,
    ) -> Optional[int]:
        """Return a method's witness-table index for an existential type."""
        info = self.type_table.get_type(existential_type)
        if info is None or info.kind != TypeKind.EXISTENTIAL:
            return None
        if not isinstance(info.data, ExistentialTypeData):
            return None

        protocol_info = self.type_table.get_type(info.data.protocol_id)
        if protocol_info is None or not isinstance(protocol_info.data, ProtocolTypeData):
            return None

        for index, requirement in enumerate(protocol_info.data.func_requirements):
            if requirement.name == method_name:
                return index
        return None

    def _emit_box_existential(self, op: BoxExistential) -> ir.Value:
        """Emit boxing a value as a typed existential heap object."""
        value = self.emit_operand(op.value)

        witness_table = self.witness_tables.get((op.concrete_type, op.protocol_type))
        if witness_table is None:
            witness_ptr = ir.Constant(self.type_cache.ptr, None)
        else:
            witness_ptr = witness_table.bitcast(self.type_cache.ptr)

        existential = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, self.type_cache.get_existential_payload_size()),
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, self.type_cache.get_or_assign_descriptor_id(op.result_type)),
        )

        payload_ptr = self._emit_existential_payload_ptr(existential)
        witness_slot = self.builder.gep(
            payload_ptr,
            [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 0)],
            name="exist.witness.ptr",
        )
        value_slot = self.builder.gep(
            payload_ptr,
            [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 1)],
            name="exist.value.ptr",
        )
        self.builder.store(witness_ptr, witness_slot)
        self.builder.store(
            self._emit_boxed_value_object(value, op.concrete_type),
            value_slot,
        )

        self._store_local(op.result, existential)
        return existential

    def _emit_existential_check_type(self, op: ExistentialCheckType) -> ir.Value:
        """Emit ``e as? T`` / ``e as! T`` discriminator.

        Compares the existential's witness pointer against the witness
        table for ``(concrete_type, protocol_type)``. Stores a Bool
        result. A missing witness table (e.g. the concrete type was
        never boxed against the protocol elsewhere in the program) is
        treated as a guaranteed mismatch — the boolean is always false.
        """
        existential = self.emit_operand(op.existential)
        witness_ptr, _value_ptr = self._emit_existential_parts(existential)

        expected_table = self.witness_tables.get((op.concrete_type, op.protocol_type))
        if expected_table is None:
            # No witness table ever emitted ⇒ this concrete type can't be
            # what `existential` carries. Yield `false` directly.
            result = ir.Constant(self.type_cache.i1, 0)
        else:
            expected_ptr = expected_table.bitcast(self.type_cache.ptr)
            result = self.builder.icmp_unsigned(
                "==", witness_ptr, expected_ptr, name="exist.match",
            )

        self._store_local(op.result, result)
        return result

    def _emit_existential_unbox(self, op: ExistentialUnbox) -> ir.Value:
        """Unbox an existential's payload as the concrete type.

        Mirrors :meth:`_emit_boxed_value_object` in reverse: for heap
        ``concrete_type`` the stored value-object pointer is the result
        directly; for primitive ``concrete_type`` the value sits in the
        payload slot at offset 32 of the boxed object and is loaded.
        """
        existential = self.emit_operand(op.existential)
        _witness_ptr, value_obj = self._emit_existential_parts(existential)

        if self.type_table.is_heap_type(op.concrete_type):
            # Heap types: the value_obj IS the managed pointer.
            self._store_local(op.result, value_obj)
            return value_obj

        # Primitive payload: load from the boxed payload slot. Mirrors
        # _emit_boxed_value_object's store of the primitive at +32.
        primitive_payload_ptr = self.builder.gep(
            value_obj,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="exist.unbox.payload",
        )
        primitive_type = self.type_cache.get_llvm_type(op.concrete_type)
        typed_payload_ptr = self.builder.bitcast(
            primitive_payload_ptr,
            primitive_type.as_pointer(),
            name="exist.unbox.payload.typed",
        )
        loaded = self.builder.load(typed_payload_ptr, name="exist.unbox.value")
        self._store_local(op.result, loaded)
        return loaded


