"""For-loop lowering for MIR builder.

Extracted from MirFunctionBuilder to isolate for-loop lowering logic.
"""

from typing import Optional

from .types import (
    TypeId, TypeKind, TypeTable,
    StructTypeData,
)
from .hir import HirFor
from .mir import (
    BlockId, Operand, CopyOperand, ConstantOperand, ConstantKind,
    Place, BinOp, BinOpKind, CmpOp, CmpOpKind,
    Assign, Branch, CondBranch,
    CallStatic, CallWitness,
    GetTag, ExtractEnumPayload,
)


class MirForLoopLowerer:
    """Lower for loops to MIR control flow."""

    def __init__(self, builder: "MirFunctionBuilder") -> None:
        self._b = builder

    def lower_for(self, for_stmt: HirFor) -> None:
        """Lower a for loop to CFG.

        Dispatch by type:
            * ``Dict<K, V>`` -> index loop yielding keys (key pointer + load)
            * ``Vec<T>``  and all other types -> ``__iter__`` / ``__next__`` protocol
        """
        iterable = self._b.expr_lowerer.lower_expr(for_stmt.iterable)
        iterable_type = iterable.type_id if hasattr(iterable, 'type_id') else None
        if iterable_type is None:
            from .mir import operand_type
            iterable_type = operand_type(iterable)

        header_bb = self._b.create_block()
        body_bb = self._b.create_block()
        exit_bb = self._b.create_block()

        info = self._b.type_table.get_type(iterable_type)

        # Dict<K, V> still uses a specialised key-pointer lowering because
        # the key type is opaque and must be loaded from the runtime buffer.
        if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            from .mir_expr_lowerer import _is_std_collection
            struct_sym = self._b.symbol_table.get_symbol(info.data.symbol_id)
            struct_name = struct_sym.name if struct_sym is not None else ""
            if _is_std_collection(struct_name, "Dict"):
                self._lower_dict_for(for_stmt, iterable, iterable_type, header_bb, body_bb, exit_bb)
                self._b.switch_to_block(exit_bb)
                return

        # All other types (Vec<T>, Range, CharIter, user structs): __iter__ / __next__ protocol
        self._lower_protocol_for(
            for_stmt, iterable, iterable_type,
            header_bb, body_bb, exit_bb
        )

        self._b.switch_to_block(exit_bb)

    def _lower_dict_for(
        self,
        for_stmt: HirFor,
        iterable: Operand,
        iterable_type: TypeId,
        header_bb: BlockId,
        body_bb: BlockId,
        exit_bb: BlockId,
    ) -> None:
        """Lower `for k in dict_value { ... }` over a `Dict<K, V>` struct.

        The dict's data lives behind an opaque `RawPtr` field
        (`Dict.handle`), and `rt_dict_key_ptr` returns a pointer to the
        key slot at a given index. Iterate by integer index in [0, len),
        load the key by `Deref`, and bind it to the loop pattern. The
        body still has full access to the dict for value lookups.
        """
        info = self._b.type_table.get_type(iterable_type)
        if info is None or not isinstance(info.data, StructTypeData):
            return
        type_args = info.data.type_args
        if len(type_args) < 1:
            return

        key_type = type_args[0]
        i64_type = self._b.type_table.get_builtin("i64") or self._b._i64_type()
        ptr_type = self._b.type_table.get_builtin("RawPtr") or self._b.type_table.void_type

        # Build the mangled `Dict_<K>_<V>_len` name for the length call.
        prefix = self._b.expr_lowerer._struct_mangled_prefix(iterable_type)

        index_local = self._b.create_temp(i64_type, "__idx")
        index_place = Place(base=index_local, projections=[], type_id=i64_type)
        self._b.emit_op(Assign(place=index_place, value=ConstantOperand(ConstantKind.INT, 0, i64_type)))

        len_local = self._b.create_temp(i64_type, "__len")
        self._b.emit_op(CallStatic(
            result=len_local,
            func_name=f"{prefix}_len",
            func_symbol=None,
            args=[iterable],
            result_type=i64_type,
        ))

        self._b.emit_terminator(Branch(target=header_bb))

        self._b.switch_to_block(header_bb)
        cond_local = self._b.create_temp(self._b._bool_type())
        self._b.emit_op(CmpOp(result=cond_local, op=CmpOpKind.LT,
            left=CopyOperand(index_place),
            right=CopyOperand(Place(base=len_local, projections=[], type_id=i64_type)),
        ))
        self._b.emit_terminator(CondBranch(
            condition=CopyOperand(Place(base=cond_local, projections=[], type_id=self._b._bool_type())),
            true_target=body_bb, false_target=exit_bb,
        ))

        self._b.switch_to_block(body_bb)
        self._b.push_loop(header_bb, exit_bb)

        # Pull the raw `Dict.handle` field via ExtractField, then ask the
        # runtime for the key slot. The handle field is at index 0 of
        # the `Dict<K, V>` payload.
        from .mir import ExtractField
        handle_local = self._b.create_temp(ptr_type, "__handle")
        self._b.emit_op(ExtractField(
            result=handle_local,
            aggregate=iterable,
            field_name="handle",
            field_index=0,
            result_type=ptr_type,
        ))

        key_ptr_local = self._b.create_temp(ptr_type, "__keyptr")
        self._b.emit_op(CallStatic(
            result=key_ptr_local, func_name="rt_dict_key_ptr", func_symbol=None,
            args=[
                CopyOperand(Place(base=handle_local, projections=[], type_id=ptr_type)),
                CopyOperand(index_place),
            ],
            result_type=ptr_type,
        ))

        key_local = self._b.create_temp(key_type, "__key")
        self._b.emit_op(Assign(
            place=Place(base=key_local, projections=[], type_id=key_type),
            value=CopyOperand(Place(base=key_ptr_local, projections=[
                DerefProjection(result_type=key_type)
            ], type_id=key_type)),
        ))

        self._b.bind_pattern(for_stmt.pattern, CopyOperand(Place(base=key_local, projections=[], type_id=key_type)))
        self._b.lower_block(for_stmt.body)
        self._b.pop_loop()

        if not self._b.is_terminated():
            new_idx = self._b.create_temp(i64_type)
            self._b.emit_op(BinOp(result=new_idx, op=BinOpKind.ADD,
                left=CopyOperand(index_place),
                right=ConstantOperand(ConstantKind.INT, 1, i64_type),
                result_type=i64_type))
            self._b.emit_op(Assign(place=index_place, value=CopyOperand(Place(base=new_idx, projections=[], type_id=i64_type))))
            self._b.emit_terminator(Branch(target=header_bb))

    def _lower_protocol_for(
        self,
        for_stmt: HirFor,
        iterable: Operand,
        iterable_type: TypeId,
        header_bb: BlockId,
        body_bb: BlockId,
        exit_bb: BlockId,
    ) -> None:
        """Lower a for loop using protocol-based iteration.

        Handles any type conforming to an Iterable-style protocol:
            1. Call ``__iter__()`` to obtain an iterator value.
            2. In the loop header, call ``__next__() -> Element?`` on the iterator.
            3. If the result is ``Some(value)`` bind it and run the body.
            4. If the result is ``None``, exit the loop.

        The iterator's concrete type and the element type are resolved
        from the receiver's ``makeIterator`` / ``next`` method signatures
        on the member table — falling back to ``iterable_type`` /
        ``pattern.type_id`` only when the lookups fail.
        """
        # Resolve the iterator type from __iter__()'s return type if we
        # have access to a member resolver; otherwise assume the iterable
        # is its own iterator (the common case for Range, etc.).
        iterator_type = iterable_type
        member_resolver = getattr(self._b, "member_resolver", None)
        if member_resolver is not None:
            mk_iter = member_resolver.get_method(iterable_type, "__iter__")
            if mk_iter is not None:
                func_data = self._b.type_table.get_function_data(mk_iter.signature)
                if func_data is not None:
                    iterator_type = func_data.return_type

        iterator_local = self._b.create_temp(iterator_type, "__iter")
        iterator_place = Place(base=iterator_local, projections=[], type_id=iterator_type)

        # Call __iter__ via witness table
        self._b.emit_op(CallWitness(
            result=iterator_local,
            witness_type=iterable_type,
            method_name="__iter__",
            args=[iterable],
            result_type=iterator_type,
        ))

        self._b.emit_terminator(Branch(target=header_bb))

        # Header: call next() and check result
        self._b.switch_to_block(header_bb)

        # Resolve the element type from __next__()'s return type (which is T?)
        # rather than relying on the for-loop pattern's recorded type.
        elem_type = getattr(for_stmt.pattern, 'type_id', self._b.type_table.error_type)
        if member_resolver is not None:
            next_method = member_resolver.get_method(iterator_type, "__next__")
            if next_method is not None:
                next_data = self._b.type_table.get_function_data(next_method.signature)
                if next_data is not None:
                    inner = self._b.type_table.get_optional_inner(next_data.return_type)
                    if inner is not None:
                        elem_type = inner
        optional_elem_type = self._b.type_table.make_optional(elem_type)

        next_result = self._b.create_temp(optional_elem_type, "__next")
        self._b.emit_op(CallWitness(
            result=next_result,
            witness_type=iterator_type,
            method_name="__next__",
            args=[CopyOperand(iterator_place)],
            result_type=optional_elem_type,
        ))

        # Get discriminant (Some = 1, None = 0)
        tag_type = self._b._i64_type()
        tag_local = self._b.create_temp(tag_type)
        self._b.emit_op(GetTag(
            result=tag_local,
            enum_val=CopyOperand(Place(base=next_result, projections=[], type_id=optional_elem_type)),
        ))

        # Check if tag != 0 (i.e., is Some)
        is_some = self._b.create_temp(self._b._bool_type())
        self._b.emit_op(CmpOp(
            result=is_some,
            op=CmpOpKind.NE,
            left=CopyOperand(Place(base=tag_local, projections=[], type_id=tag_type)),
            right=ConstantOperand(ConstantKind.INT, 0, tag_type),
        ))

        self._b.emit_terminator(CondBranch(
            condition=CopyOperand(Place(base=is_some, projections=[], type_id=self._b._bool_type())),
            true_target=body_bb,
            false_target=exit_bb,
        ))

        # Body: extract element from Some and bind pattern
        self._b.switch_to_block(body_bb)
        self._b.push_loop(header_bb, exit_bb)

        # Extract payload from Optional
        elem_local = self._b.create_temp(elem_type, "__elem")
        self._b.emit_op(ExtractEnumPayload(
            result=elem_local,
            enum_val=CopyOperand(Place(base=next_result, projections=[], type_id=optional_elem_type)),
            case_name="Some",
            payload_index=0,
            result_type=elem_type,
        ))

        # Bind the pattern to the element
        elem_operand = CopyOperand(Place(base=elem_local, projections=[], type_id=elem_type))
        self._b.bind_pattern(for_stmt.pattern, elem_operand)

        # Execute body
        self._b.lower_block(for_stmt.body)
        self._b.pop_loop()

        if not self._b.is_terminated():
            # Continue to next iteration
            self._b.emit_terminator(Branch(target=header_bb))

from .mir import DerefProjection
