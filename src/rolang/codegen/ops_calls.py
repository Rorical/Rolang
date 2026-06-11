"""Call LLVM codegen operations."""

from __future__ import annotations

from typing import Optional

from llvmlite import ir

from ..mir import (
    Operand, operand_type,
    CopyOperand, MoveOperand,
    CallStatic, CallVTable, CallWitness,
    MakeClosure, CallClosure,
)


def _witness_type_func_name(emitter, witness_type, method_name: str) -> Optional[str]:
    """Build the mangled function name for a method on a concrete type.

    Uses the type's symbol name to avoid ambiguity when multiple structs
    share a method suffix (e.g. ``CharIter___iter__`` vs ``Range___iter__``).
    """
    from ..types import TypeKind, StructTypeData, EnumTypeData, PrimitiveTypeData
    from ..monomorphize import mangle_name
    type_table = emitter.type_table
    sym_table = emitter.type_cache.symbol_table
    info = type_table.get_type(witness_type)
    if info is None:
        return None
    if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
        sym = sym_table.get_symbol(info.data.symbol_id)
        if sym:
            # Mangle with the type args so a *monomorphized* generic instance
            # (e.g. DictIter<String>) maps to its specialized function name
            # (DictIter_S112___next__), matching how monomorphize.py names it.
            # mangle_name is identity when there are no type args, so concrete
            # types (Range, CharIter) are unaffected. Without this the bare
            # generic name ("DictIter___next__") misses and the caller falls
            # back to a loose suffix search that can pick the wrong __next__.
            base = mangle_name(sym.name, info.data.type_args, type_table)
            return f"{base}_{method_name}"
    elif info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
        sym = sym_table.get_symbol(info.data.symbol_id)
        if sym:
            base = mangle_name(sym.name, info.data.type_args, type_table)
            return f"{base}_{method_name}"
    elif info.kind == TypeKind.PRIMITIVE and isinstance(info.data, PrimitiveTypeData):
        return f"{info.data.primitive.value}_{method_name}"
    return None


class OpsCallsMixin:
    """Mixin for call operations: static, vtable, witness, closure."""

    # Call operations

    def _emit_inline_string_char_at(self, op: CallStatic) -> Optional[ir.Value]:
        """Emit inline LLVM IR for rt_string_char_at — no C call boundary."""
        if len(op.args) < 2:
            return None
        str_ptr = self.emit_operand(op.args[0])
        index = self.emit_operand(op.args[1])
        int64_type = self.type_cache.i64

        # Branching approach: guard loads behind null check
        cont_block = self.builder.append_basic_block(name="char_at.cont")
        fail_block = self.builder.append_basic_block(name="char_at.fail")
        merge_block = self.builder.append_basic_block(name="char_at.merge")

        is_null = self.builder.icmp_signed(
            "==",
            self.builder.ptrtoint(str_ptr, int64_type),
            ir.Constant(int64_type, 0),
        )
        self.builder.cbranch(is_null, fail_block, cont_block)

        self.builder.position_at_end(cont_block)
        raw = self.builder.bitcast(str_ptr, self.type_cache.ptr, name="char.raw")
        payload = self.builder.gep(raw, [ir.Constant(int64_type, self.type_cache.OBJ_HEADER_SIZE)], name="char.payload")
        data = self.builder.load(
            self.builder.bitcast(payload, ir.PointerType(self.type_cache.ptr)),
            name="char.data",
        )
        length = self.builder.load(
            self.builder.bitcast(
                self.builder.gep(payload, [ir.Constant(int64_type, 8)]),
                ir.PointerType(int64_type),
            ),
            name="char.len",
        )
        idx64 = self.builder.sext(index, int64_type, name="char.idx")
        in_bounds = self.builder.icmp_unsigned("<", idx64, length, name="char.ok")
        read_block = self.builder.append_basic_block(name="char_at.read")
        self.builder.cbranch(in_bounds, read_block, fail_block)

        self.builder.position_at_end(read_block)
        ch = self.builder.load(
            self.builder.gep(data, [idx64], name="char.ch.ptr"),
            name="char.ch",
        )
        result = self.builder.zext(ch, self.type_cache.i32, name="char.as.i32")
        self.builder.branch(merge_block)

        self.builder.position_at_end(fail_block)
        self.builder.branch(merge_block)

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(self.type_cache.i32, name="char_at.phi")
        phi.add_incoming(ir.Constant(self.type_cache.i32, -1), fail_block)
        phi.add_incoming(result, read_block)

        if op.result is not None:
            self._store_local(op.result, phi)
        return phi

    def _emit_inline_char_classify(self, op: CallStatic) -> Optional[ir.Value]:
        """Emit inline LLVM IR for rt_char_is_* — no C call boundary."""
        if len(op.args) < 1:
            return None
        ch = self.emit_operand(op.args[0])
        i32_type = self.type_cache.i32

        if op.func_name == "rt_char_is_digit":
            ge = self.builder.icmp_signed(">=", ch, ir.Constant(i32_type, 48))
            le = self.builder.icmp_signed("<=", ch, ir.Constant(i32_type, 57))
            cond = self.builder.and_(ge, le)
        elif op.func_name == "rt_char_is_space":
            c32 = self.builder.icmp_signed("==", ch, ir.Constant(i32_type, 32))
            c9 = self.builder.icmp_signed("==", ch, ir.Constant(i32_type, 9))
            c10 = self.builder.icmp_signed("==", ch, ir.Constant(i32_type, 10))
            c13 = self.builder.icmp_signed("==", ch, ir.Constant(i32_type, 13))
            cond = self.builder.or_(self.builder.or_(self.builder.or_(c32, c9), c10), c13)
        elif op.func_name == "rt_char_is_alpha":
            ge = self.builder.icmp_signed(">=", ch, ir.Constant(i32_type, 65))
            le = self.builder.icmp_signed("<=", ch, ir.Constant(i32_type, 90))
            upper = self.builder.and_(ge, le)
            ge2 = self.builder.icmp_signed(">=", ch, ir.Constant(i32_type, 97))
            le2 = self.builder.icmp_signed("<=", ch, ir.Constant(i32_type, 122))
            lower = self.builder.and_(ge2, le2)
            cond = self.builder.or_(upper, lower)
        elif op.func_name == "rt_char_is_alnum":
            ge = self.builder.icmp_signed(">=", ch, ir.Constant(i32_type, 48))
            le = self.builder.icmp_signed("<=", ch, ir.Constant(i32_type, 57))
            dig = self.builder.and_(ge, le)
            ge2 = self.builder.icmp_signed(">=", ch, ir.Constant(i32_type, 65))
            le2 = self.builder.icmp_signed("<=", ch, ir.Constant(i32_type, 90))
            upper = self.builder.and_(ge2, le2)
            ge3 = self.builder.icmp_signed(">=", ch, ir.Constant(i32_type, 97))
            le3 = self.builder.icmp_signed("<=", ch, ir.Constant(i32_type, 122))
            lower = self.builder.and_(ge3, le3)
            cond = self.builder.or_(self.builder.or_(dig, upper), lower)
        else:
            cond = ir.Constant(self.type_cache.i1, 0)

        result = self.builder.zext(cond, i32_type, name="cls.result")
        if op.result is not None:
            self._store_local(op.result, result)
        return result

    # ---- Inline primitive Vec<T> element access ------------------------------
    #
    # `Vec<T>.get/set` lower to the out-param idiom `rt_gvec_get(h,i,&out)` /
    # `rt_gvec_set(h,i,&val)`. Across that opaque C call the `&out` alloca
    # escapes, which blocks mem2reg (the slot can't be promoted), LICM (loop-
    # invariant element reads can't be hoisted) and alias analysis (distinct
    # buffers can't be proven disjoint) — so tight numeric loops spill heavily
    # instead of staying in registers. For PRIMITIVE element types we know the
    # element size statically, so we emit an inline bounds-checked load/store
    # and skip the call entirely; the optimizer then promotes/hoists like C.
    #
    # Heap/ARC element types keep the call: the runtime retains/releases the
    # element, which must not be elided.

    _GVEC_HEADER_BYTES = 16  # {i32 len, i32 capacity, i32 elem_size, i32 elem_type_id}

    # ---- TBAA for inline collection access -----------------------------------
    #
    # The inline Vec/Dict accessors load header fields (len, key_size, ...)
    # and load/store element slots. Without aliasing info LLVM must assume an
    # element store may overwrite any header, so loop-invariant header loads
    # (e.g. `len` for the bounds check) are re-loaded on every iteration of
    # numeric loops. Headers and element data are disjoint by construction —
    # the runtime never overlays them — so we publish that as two TBAA
    # branches. C-side accesses carry no TBAA and conservatively alias both.

    def _tbaa_tags(self):
        tags = getattr(self.module, "_rolang_tbaa", None)
        if tags is None:
            i64 = self.type_cache.i64
            root = self.module.add_metadata(["Rolang TBAA"])
            header_ty = self.module.add_metadata(
                ["rolang collection header", root, ir.Constant(i64, 0)])
            elem_ty = self.module.add_metadata(
                ["rolang collection element", root, ir.Constant(i64, 0)])
            tags = {
                "header": self.module.add_metadata(
                    [header_ty, header_ty, ir.Constant(i64, 0)]),
                "element": self.module.add_metadata(
                    [elem_ty, elem_ty, ir.Constant(i64, 0)]),
            }
            self.module._rolang_tbaa = tags
        return tags

    def _tag_header(self, instr):
        instr.set_metadata("tbaa", self._tbaa_tags()["header"])
        return instr

    def _tag_element(self, instr):
        instr.set_metadata("tbaa", self._tbaa_tags()["element"])
        return instr

    def _gvec_scalar_elem(self, ptr_operand: Operand, allow_heap: bool = False):
        """If `ptr_operand` is `&local` for a scalar local, return
        (src_local, elem_llvm_type, is_heap); otherwise None (caller falls
        back to the opaque call).

        Heap-managed elements are only returned when ``allow_heap`` is set —
        the caller must then emit the element retain itself (read accessors).
        Mutating accessors keep the C call, which also releases the
        overwritten element."""
        if not isinstance(ptr_operand, (CopyOperand, MoveOperand)):
            return None
        place = ptr_operand.place
        if place.projections:
            return None
        src = self._raw_addr_src.get(place.base)
        if src is None or src not in self.local_storage:
            return None
        elem_type_id = self.local_types.get(src)
        if elem_type_id is None:
            return None
        is_heap = self.type_table.is_heap_type(elem_type_id)
        if is_heap and not allow_heap:
            return None
        elem_llvm = self.type_cache.get_llvm_type(elem_type_id)
        if not isinstance(elem_llvm, (ir.IntType, ir.FloatType, ir.DoubleType,
                                      ir.HalfType, ir.PointerType)):
            return None
        return src, elem_llvm, is_heap

    def _gvec_panic_index_fn(self) -> ir.Function:
        f = self.func_map.get("rt_panic_index_out_of_bounds")
        if f is None:
            f = self.module.globals.get("rt_panic_index_out_of_bounds")
        if f is None:
            i64 = self.type_cache.i64
            f = ir.Function(self.module, ir.FunctionType(ir.VoidType(), [i64, i64]),
                            name="rt_panic_index_out_of_bounds")
            f.attributes.add("noreturn")
        self.func_map["rt_panic_index_out_of_bounds"] = f
        return f

    def _gvec_bounds_check_then_slot(self, vec_ptr, index_val, elem_llvm):
        """Emit the bounds check (panic on OOB) and return a typed pointer to
        element `index_val`. Leaves the builder positioned in the in-bounds
        continuation block."""
        i32 = self.type_cache.i32
        i64 = self.type_cache.i64
        vec_ptr = self.builder.bitcast(vec_ptr, self.type_cache.ptr, name="gvec.h")
        if index_val.type != i32:
            index_val = self._coerce_int(index_val, i32, signed=True)

        # len lives at offset 0 of the GVecHeader.
        len_ptr = self.builder.bitcast(vec_ptr, ir.PointerType(i32), name="gvec.len.ptr")
        length = self._tag_header(self.builder.load(len_ptr, name="gvec.len"))
        # Unsigned compare folds the `index < 0` and `index >= len` checks into one.
        oob = self.builder.icmp_unsigned(">=", index_val, length, name="gvec.oob")

        fn = self.builder.function
        panic_bb = fn.append_basic_block(name="gvec.oob")
        ok_bb = fn.append_basic_block(name="gvec.ok")
        self.builder.cbranch(oob, panic_bb, ok_bb)

        self.builder.position_at_end(panic_bb)
        self.builder.call(self._gvec_panic_index_fn(),
                          [self.builder.sext(index_val, i64),
                           self.builder.sext(length, i64)])
        self.builder.unreachable()

        self.builder.position_at_end(ok_bb)
        data_i8 = self.builder.gep(vec_ptr, [ir.Constant(i64, self._GVEC_HEADER_BYTES)],
                                   name="gvec.data")
        data = self.builder.bitcast(data_i8, ir.PointerType(elem_llvm), name="gvec.data.t")
        # zext, not sext: the bounds check just proved index ∈ [0, len), so the
        # offset is provably non-negative — alias analysis can then prove the
        # slot never reaches back into this vector's own header.
        slot = self.builder.gep(data, [self.builder.zext(index_val, i64)], name="gvec.slot")
        return slot

    def _emit_inline_gvec_len(self, op: CallStatic) -> bool:
        if op.result is None or len(op.args) < 1:
            return False
        vec_ptr = self.builder.bitcast(self.emit_operand(op.args[0]),
                                       self.type_cache.ptr, name="gvec.h")
        len_ptr = self.builder.bitcast(vec_ptr, ir.PointerType(self.type_cache.i32),
                                       name="gvec.len.ptr")
        length = self._tag_header(self.builder.load(len_ptr, name="gvec.len"))
        self._store_local(op.result, length)
        return True

    def _emit_inline_gvec_get(self, op: CallStatic) -> bool:
        if len(op.args) < 3:
            return False
        info = self._gvec_scalar_elem(op.args[2], allow_heap=True)
        if info is None:
            return False
        src, elem_llvm, is_heap = info
        vec_ptr = self.emit_operand(op.args[0])
        index_val = self.emit_operand(op.args[1])
        slot = self._gvec_bounds_check_then_slot(vec_ptr, index_val, elem_llvm)
        value = self._tag_element(self.builder.load(slot, name="gvec.elem"))
        if is_heap:
            # rt_gvec_get retains heap elements for the caller; mirror that
            # with the inline retain (null-safe rc++) so ownership matches.
            self.runtime.emit_obj_retain(self.builder, value)
        self._store_local(src, value)
        return True

    def _emit_inline_gvec_set(self, op: CallStatic) -> bool:
        if len(op.args) < 3:
            return False
        info = self._gvec_scalar_elem(op.args[2])
        if info is None:
            return False
        src, elem_llvm, _ = info
        vec_ptr = self.emit_operand(op.args[0])
        index_val = self.emit_operand(op.args[1])
        value = self.builder.load(self.local_storage[src], name="gvec.val")
        slot = self._gvec_bounds_check_then_slot(vec_ptr, index_val, elem_llvm)
        self._tag_element(self.builder.store(value, slot))
        return True

    # ---- Inline primitive Dict<K,V> value access by entry index -------------
    #
    # `Dict<K,V>.value_at/set_value_at` (the O(1) hash-free counterpart to
    # entry_index, used by the word_freq counter loop) lower to the same
    # out-param idiom as Vec. For PRIMITIVE value types we inline the slot
    # math and load/store, dropping the opaque rt_dict_get_at/set_at call and
    # its escaping `&out`. Heap value types keep the call (the runtime
    # retains/releases the element). Layout (RolangDict, runtime ABI):
    #   len @ 0, key_size @ 16, value_size @ 24, entries data @ 56;
    #   value slot = data + index*(key_size+value_size) + key_size.
    # Both runtime accessors are lenient on out-of-range (get zero-fills, set
    # no-ops), so we replicate that rather than panic.

    _DICT_DATA_BYTES = 56
    _DICT_OFF_LEN = 0
    _DICT_OFF_KEY_SIZE = 16
    _DICT_OFF_VALUE_SIZE = 24

    def _dict_load_i64_field(self, dict_ptr, byte_off):
        i64 = self.type_cache.i64
        p = self.builder.gep(dict_ptr, [ir.Constant(i64, byte_off)], name="dict.f")
        return self._tag_header(
            self.builder.load(self.builder.bitcast(p, ir.PointerType(i64)),
                              name="dict.fv"))

    def _dict_value_slot(self, dict_ptr, index_i64, elem_llvm):
        """Typed pointer to the value of entry `index_i64` (assumes in-bounds)."""
        i64 = self.type_cache.i64
        key_size = self._dict_load_i64_field(dict_ptr, self._DICT_OFF_KEY_SIZE)
        value_size = self._dict_load_i64_field(dict_ptr, self._DICT_OFF_VALUE_SIZE)
        stride = self.builder.add(key_size, value_size, name="dict.stride")
        off = self.builder.add(self.builder.mul(index_i64, stride), key_size,
                               name="dict.voff")
        data = self.builder.gep(dict_ptr, [ir.Constant(i64, self._DICT_DATA_BYTES)],
                                name="dict.data")
        slot = self.builder.gep(data, [off], name="dict.vslot")
        return self.builder.bitcast(slot, ir.PointerType(elem_llvm), name="dict.vslot.t")

    @staticmethod
    def _zero_of(llvm_type):
        if isinstance(llvm_type, (ir.FloatType, ir.DoubleType, ir.HalfType)):
            return ir.Constant(llvm_type, 0.0)
        if isinstance(llvm_type, ir.PointerType):
            return ir.Constant(llvm_type, None)
        return ir.Constant(llvm_type, 0)

    def _dict_index_arg(self, operand):
        i64 = self.type_cache.i64
        v = self.emit_operand(operand)
        if v.type != i64:
            v = self._coerce_int(v, i64, signed=True)
        return v

    def _emit_inline_dict_get_at(self, op: CallStatic) -> bool:
        if len(op.args) < 3:
            return False
        info = self._gvec_scalar_elem(op.args[2])
        if info is None:
            return False
        src, elem_llvm, _ = info
        dict_ptr = self.builder.bitcast(self.emit_operand(op.args[0]),
                                        self.type_cache.ptr, name="dict.h")
        index = self._dict_index_arg(op.args[1])
        length = self._dict_load_i64_field(dict_ptr, self._DICT_OFF_LEN)
        oob = self.builder.icmp_unsigned(">=", index, length, name="dict.oob")

        fn = self.builder.function
        oob_bb = fn.append_basic_block(name="dict.get.oob")
        ok_bb = fn.append_basic_block(name="dict.get.ok")
        cont_bb = fn.append_basic_block(name="dict.get.cont")
        self.builder.cbranch(oob, oob_bb, ok_bb)

        self.builder.position_at_end(oob_bb)          # lenient: zero-fill on OOB
        self._store_local(src, self._zero_of(elem_llvm))
        self.builder.branch(cont_bb)

        self.builder.position_at_end(ok_bb)
        slot = self._dict_value_slot(dict_ptr, index, elem_llvm)
        self._store_local(src, self._tag_element(
            self.builder.load(slot, name="dict.val")))
        self.builder.branch(cont_bb)

        self.builder.position_at_end(cont_bb)
        return True

    def _emit_inline_dict_set_at(self, op: CallStatic) -> bool:
        if len(op.args) < 3:
            return False
        info = self._gvec_scalar_elem(op.args[2])
        if info is None:
            return False
        src, elem_llvm, _ = info
        dict_ptr = self.builder.bitcast(self.emit_operand(op.args[0]),
                                        self.type_cache.ptr, name="dict.h")
        index = self._dict_index_arg(op.args[1])
        value = self.builder.load(self.local_storage[src], name="dict.setval")
        length = self._dict_load_i64_field(dict_ptr, self._DICT_OFF_LEN)
        oob = self.builder.icmp_unsigned(">=", index, length, name="dict.oob")

        fn = self.builder.function
        store_bb = fn.append_basic_block(name="dict.set.store")
        cont_bb = fn.append_basic_block(name="dict.set.cont")
        self.builder.cbranch(oob, cont_bb, store_bb)  # lenient: no-op on OOB

        self.builder.position_at_end(store_bb)
        slot = self._dict_value_slot(dict_ptr, index, elem_llvm)
        self._tag_element(self.builder.store(value, slot))
        self.builder.branch(cont_bb)

        self.builder.position_at_end(cont_bb)
        return True

    def _emit_call_static(self, op: CallStatic) -> Optional[ir.Value]:
        """Emit static function call."""
        if op.func_name == "rt_string_char_at":
            return self._emit_inline_string_char_at(op)
        if op.func_name in ("rt_char_is_digit", "rt_char_is_alpha",
                             "rt_char_is_alnum", "rt_char_is_space"):
            return self._emit_inline_char_classify(op)
        if op.func_name == "rt_gvec_get" and self._emit_inline_gvec_get(op):
            return None
        if op.func_name == "rt_gvec_set" and self._emit_inline_gvec_set(op):
            return None
        if op.func_name == "rt_gvec_len" and self._emit_inline_gvec_len(op):
            return None
        if op.func_name == "rt_dict_get_at" and self._emit_inline_dict_get_at(op):
            return None
        if op.func_name == "rt_dict_set_at" and self._emit_inline_dict_set_at(op):
            return None

        # Look up function
        func = self.func_map.get(op.func_name)
        if func is None:
            # Try to find it in the module
            for f in self.module.functions:
                if f.name == op.func_name:
                    func = f
                    break

        if func is None:
            # Cross-module call: declare the function from available info
            ret_type = self.type_cache.get_llvm_type(op.result_type)
            arg_types = []
            for arg in op.args:
                from ..mir import operand_type as _op_type
                arg_types.append(self.type_cache.get_llvm_type(_op_type(arg)))
            func_type = ir.FunctionType(ret_type, arg_types)
            func = ir.Function(self.module, func_type, name=op.func_name)
            self.func_map[op.func_name] = func

        # Emit arguments and coerce to match parameter types
        args = []
        for i, arg in enumerate(op.args):
            arg_val = self.emit_operand(arg)
            # Coerce to parameter type if needed
            if i < len(func.args):
                param_type = func.args[i].type
                if isinstance(arg_val.type, ir.PointerType) and isinstance(param_type, ir.PointerType):
                    if arg_val.type != param_type:
                        arg_val = self.builder.bitcast(arg_val, param_type, name="cast")
                else:
                    arg_val = self._coerce_int(arg_val, param_type,
                                                signed=self._operand_is_signed(arg))
            args.append(arg_val)

        # Call function
        if isinstance(func.return_value.type, ir.VoidType):
            self.builder.call(func, args)
            return None
        else:
            result = self.builder.call(func, args, name="call")
            if op.result is not None:
                self._store_local(op.result, result)
            return result

    def _emit_call_vtable(self, op: CallVTable) -> Optional[ir.Value]:
        """Emit existential witness-table dispatch."""
        receiver = self.emit_operand(op.receiver)
        receiver_type = operand_type(op.receiver)
        method_index = self._existential_method_index(receiver_type, op.method_name)
        ret_type = self.type_cache.get_llvm_type(op.result_type)

        if method_index is None:
            if op.result is not None and not isinstance(ret_type, ir.VoidType):
                result = ir.Constant(ret_type, ir.Undefined)
                self._store_local(op.result, result)
                return result
            return None

        witness_ptr, value_ptr = self._emit_existential_parts(receiver)

        table_ptr_type = ir.PointerType(self.type_cache.ptr)
        table_ptr = self.builder.bitcast(witness_ptr, table_ptr_type, name="witness.table")
        entry_ptr = self.builder.gep(
            table_ptr,
            [ir.Constant(self.type_cache.i64, method_index)],
            name="witness.entry.ptr",
        )
        erased_fn = self.builder.load(entry_ptr, name="witness.fn")

        args = [value_ptr]
        arg_types = [self.type_cache.ptr]
        for arg in op.args:
            arg_value = self.emit_operand(arg)
            args.append(arg_value)
            arg_types.append(self.type_cache.get_llvm_type(operand_type(arg)))

        func_type = ir.FunctionType(ret_type, arg_types)
        typed_fn = self.builder.bitcast(erased_fn, func_type.as_pointer(), name="witness.fn.typed")

        if isinstance(ret_type, ir.VoidType):
            self.builder.call(typed_fn, args)
            return None

        result = self.builder.call(typed_fn, args, name="witness.call")
        if op.result is not None:
            self._store_local(op.result, result)
        return result

    def _emit_call_witness(self, op: CallWitness) -> Optional[ir.Value]:
        """Emit witness table call (generic constraint or existential)."""
        # The first argument should be the existential box.
        if not op.args:
            # Handle static method on protocol if there are no args?
            # Not supported in v0.1
            if op.result is not None:
                llvm_type = self.type_cache.get_llvm_type(op.result_type)
                result = ir.Constant(llvm_type, ir.Undefined)
                self._store_local(op.result, result)
                return result
            return None

        # The receiver is the first argument
        receiver = self.emit_operand(op.args[0])
        
        method_index = self._existential_method_index(op.witness_type, op.method_name)
        ret_type = self.type_cache.get_llvm_type(op.result_type)

        if method_index is None:
            # Try to get it assuming witness_type is a Protocol instead of Existential
            # (Though typically witness_type is the existential type)
            from ..types import ProtocolTypeData, TypeKind
            info = self.type_table.get_type(op.witness_type)
            if info and info.kind == TypeKind.PROTOCOL and isinstance(info.data, ProtocolTypeData):
                for index, req in enumerate(info.data.func_requirements):
                    if req.name == op.method_name:
                        method_index = index
                        break
            
        if method_index is None:
            # Maybe the witness_type is actually a CONCRETE type (due to monomorphization)
            # and we should just call its method directly!
            from ..types import TypeKind
            info = self.type_table.get_type(op.witness_type)
            if info and info.kind in (TypeKind.STRUCT, TypeKind.ENUM, TypeKind.PRIMITIVE):
                # Build the expected mangled name: {StructName}_{method_name}
                func_name = _witness_type_func_name(self, op.witness_type, op.method_name)
                func = None
                if func_name is not None:
                    func = self.func_map.get(func_name)
                    if func is None:
                        for f in self.module.functions:
                            if f.name == func_name:
                                func = f
                                break

                if func is None:
                    # Fallback: suffix search (for cases where the witness_type
                    # doesn't give us a useful struct name).
                    target_suffix = f"_{op.method_name}"
                    receiver_type = receiver.type
                    receiver_is_ptr = isinstance(receiver_type, ir.PointerType)
                    for name, f in self.func_map.items():
                        if name.endswith(target_suffix) and list(f.args):
                            first_arg_type = f.args[0].type
                            if first_arg_type == receiver_type or (
                                receiver_is_ptr and isinstance(first_arg_type, ir.PointerType)
                            ):
                                func = f
                                break
                    if func is None:
                        for f in self.module.functions:
                            if f.name.endswith(target_suffix) and list(f.args):
                                first_arg_type = f.args[0].type
                                if first_arg_type == receiver_type or (
                                    receiver_is_ptr and isinstance(first_arg_type, ir.PointerType)
                                ):
                                    func = f
                                    break
                            
                if func is not None:
                    args = []
                    arg_types = []
                    for arg in op.args:
                        arg_value = self.emit_operand(arg)
                        args.append(arg_value)
                        arg_types.append(self.type_cache.get_llvm_type(operand_type(arg)))

                    # Bitcast arguments to match the function signature when
                    # monomorphized types differ from their generic erasures.
                    if list(func.args) and len(args) == len(func.args):
                        for i in range(min(len(args), len(func.args))):
                            expected = func.args[i].type
                            if isinstance(expected, ir.PointerType) and args[i].type != expected:
                                args[i] = self.builder.bitcast(args[i], expected, name="arg.cast")

                    if isinstance(ret_type, ir.VoidType):
                        self.builder.call(func, args)
                        return None

                    result = self.builder.call(func, args, name="witness.static.call")
                    if op.result is not None:
                        self._store_local(op.result, result)
                    return result

            # If we still can't find it, return undef
            if op.result is not None and not isinstance(ret_type, ir.VoidType):
                result = ir.Constant(ret_type, ir.Undefined)
                self._store_local(op.result, result)
                return result
            return None

        witness_ptr, value_ptr = self._emit_existential_parts(receiver)

        table_ptr_type = ir.PointerType(self.type_cache.ptr)
        table_ptr = self.builder.bitcast(witness_ptr, table_ptr_type, name="witness.table")
        entry_ptr = self.builder.gep(
            table_ptr,
            [ir.Constant(self.type_cache.i64, method_index)],
            name="witness.entry.ptr",
        )
        erased_fn = self.builder.load(entry_ptr, name="witness.fn")

        args = [value_ptr]
        arg_types = [self.type_cache.ptr]
        for arg in op.args[1:]:
            arg_value = self.emit_operand(arg)
            args.append(arg_value)
            arg_types.append(self.type_cache.get_llvm_type(operand_type(arg)))

        func_type = ir.FunctionType(ret_type, arg_types)
        typed_fn = self.builder.bitcast(erased_fn, func_type.as_pointer(), name="witness.fn.typed")

        if isinstance(ret_type, ir.VoidType):
            self.builder.call(typed_fn, args)
            return None

        result = self.builder.call(typed_fn, args, name="witness.call")
        if op.result is not None:
            self._store_local(op.result, result)
        return result

    # Closure operations

    def _emit_make_closure(self, op: MakeClosure) -> ir.Value:
        """
        Emit closure creation.

        Closures are typed heap objects. The payload layout is:
        { fn_ptr, captures... }
        """
        # Look up the closure function
        func = self.func_map.get(op.func_name)
        if func is None:
            for f in self.module.functions:
                if f.name == op.func_name:
                    func = f
                    break

        payload_type = self.type_cache.get_closure_payload_type(op.result_type)
        payload_size = self.type_cache.get_closure_payload_size(op.result_type)
        closure_ptr = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, payload_size),
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, self.type_cache.get_or_assign_descriptor_id(op.result_type)),
        )

        payload_byte_ptr = self.builder.gep(
            closure_ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="closure.payload",
        )
        payload_ptr = self.builder.bitcast(
            payload_byte_ptr,
            ir.PointerType(payload_type),
            name="closure.payload.typed",
        )

        # Build the closure struct
        if func is not None:
            # Bitcast function pointer to opaque i8* for the closure struct
            fn_ptr = self.builder.bitcast(func, self.type_cache.ptr, name="fn_ptr")
        else:
            fn_ptr = ir.Constant(self.type_cache.ptr, None)

        fn_slot = self.builder.gep(
            payload_ptr,
            [ir.Constant(self.type_cache.i32, 0), ir.Constant(self.type_cache.i32, 0)],
            name="closure.fn.ptr",
        )
        self.builder.store(fn_ptr, fn_slot)

        for i, cap in enumerate(op.captures):
            cap_val = self.emit_operand(cap)
            cap_slot = self.builder.gep(
                payload_ptr,
                [
                    ir.Constant(self.type_cache.i32, 0),
                    ir.Constant(self.type_cache.i32, i + 1),
                ],
                name=f"closure.capture.{i}.ptr",
            )
            if not isinstance(cap_val.type, ir.VoidType):
                self.builder.store(cap_val, cap_slot)

        self._store_local(op.result, closure_ptr)
        return closure_ptr

    def _emit_call_closure(self, op: CallClosure) -> Optional[ir.Value]:
        """
        Emit a call through a first-class callable value (closure or
        function-typed variable).

        Both CLOSURE and FUNCTION values are closure heap-object references.
        The payload starts with fn_ptr. Calls pass the closure object itself as
        the hidden first argument so the callee can load captures from it.
        """
        closure_val = self.emit_operand(op.closure)

        raw_ptr = self.builder.bitcast(closure_val, self.type_cache.ptr, name="closure.raw")
        payload_byte_ptr = self.builder.gep(
            raw_ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="closure.payload",
        )
        fn_slot = self.builder.bitcast(
            payload_byte_ptr,
            ir.PointerType(self.type_cache.ptr),
            name="closure.fn.slot",
        )
        fn_ptr = self.builder.load(fn_slot, name="fn_ptr")

        # Build argument list: closure object, regular args...
        args = [closure_val]
        arg_types = [self.type_cache.ptr]

        # Add regular arguments
        for arg in op.args:
            args.append(self.emit_operand(arg))
            arg_type = operand_type(arg)
            arg_types.append(self.type_cache.get_llvm_type(arg_type))

        # Build function type for the call
        ret_llvm = self.type_cache.get_llvm_type(op.result_type)
        func_type = ir.FunctionType(ret_llvm, arg_types)
        fn_ptr_typed = self.builder.bitcast(fn_ptr, func_type.as_pointer(), name="fn_typed")

        # Call the function
        if isinstance(ret_llvm, ir.VoidType):
            self.builder.call(fn_ptr_typed, args)
            return None
        else:
            result = self.builder.call(fn_ptr_typed, args, name="closure_call")
            if op.result is not None:
                self._store_local(op.result, result)
            return result


