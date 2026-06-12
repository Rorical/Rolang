"""Arithmetic and comparison LLVM codegen operations."""

from __future__ import annotations

from llvmlite import ir

from ..mir import (
    BinOp, BinOpKind, CmpOp, CmpOpKind, UnaryOp, UnaryOpKind, CastOp,
    CopyOperand, MoveOperand, operand_type,
)
from ..types import TypeKind


class OpsArithmeticMixin:
    """Mixin for arithmetic, comparison, unary, and cast operations."""

    # Binary operations

    def _emit_binop(self, op: BinOp) -> ir.Value:
        """Emit binary operation."""
        left = self.emit_operand(op.left)
        right = self.emit_operand(op.right)

        # Coerce operands to common type
        left, right = self._coerce_binop_operands(left, right)

        left_type = operand_type(op.left)
        is_float = self.type_cache.is_float(left_type)
        is_signed = self.type_cache.is_signed_integer(left_type)

        match op.op:
            # Float ops carry the `contract` fast-math flag (and ONLY that
            # flag): it licenses fmul+fadd fusion into fma, matching what C
            # compilers do by default (-ffp-contract=on), without any of the
            # value-unsafe reassociation the other fast-math flags allow.
            case BinOpKind.ADD:
                if is_float:
                    result = self.builder.fadd(left, right, name="fadd",
                                               flags=("contract",))
                else:
                    result = self.builder.add(left, right, name="add")

            case BinOpKind.SUB:
                if is_float:
                    result = self.builder.fsub(left, right, name="fsub",
                                               flags=("contract",))
                else:
                    result = self.builder.sub(left, right, name="sub")

            case BinOpKind.MUL:
                if is_float:
                    result = self.builder.fmul(left, right, name="fmul",
                                               flags=("contract",))
                else:
                    result = self.builder.mul(left, right, name="mul")

            case BinOpKind.DIV:
                if is_float:
                    result = self.builder.fdiv(left, right, name="fdiv",
                                               flags=("contract",))
                else:
                    self._emit_div_zero_guard(right, is_remainder=False)
                    if is_signed:
                        result = self.builder.sdiv(left, right, name="sdiv")
                    else:
                        result = self.builder.udiv(left, right, name="udiv")

            case BinOpKind.MOD:
                if is_float:
                    if isinstance(left.type, ir.DoubleType):
                        # Inline guarded fmod expansion (exact; falls back to
                        # libm for the cases the fast path cannot prove).
                        result = self.builder.call(
                            self.runtime.rt_frem_f64, [left, right], name="fmod"
                        )
                    else:
                        result = self.builder.frem(left, right, name="fmod")
                else:
                    self._emit_div_zero_guard(right, is_remainder=True)
                    if is_signed:
                        result = self.builder.srem(left, right, name="srem")
                    else:
                        result = self.builder.urem(left, right, name="urem")

            case BinOpKind.BIT_AND:
                result = self.builder.and_(left, right, name="and")

            case BinOpKind.BIT_OR:
                result = self.builder.or_(left, right, name="or")

            case BinOpKind.BIT_XOR:
                result = self.builder.xor(left, right, name="xor")

            case BinOpKind.SHL:
                # Mask the shift count to (bitwidth - 1) so out-of-range
                # values produce a defined result (consistent with Rust's
                # `wrapping_shl`). Raw shl/ashr/lshr with `count >= bitwidth`
                # is LLVM poison.
                bw = getattr(left.type, "width", 64)
                mask = ir.Constant(right.type, bw - 1)
                shift = self.builder.and_(right, mask, name="shl.mask")
                result = self.builder.shl(left, shift, name="shl")

            case BinOpKind.SHR:
                bw = getattr(left.type, "width", 64)
                mask = ir.Constant(right.type, bw - 1)
                shift = self.builder.and_(right, mask, name="shr.mask")
                if is_signed:
                    result = self.builder.ashr(left, shift, name="ashr")
                else:
                    result = self.builder.lshr(left, shift, name="lshr")

            case _:
                raise NotImplementedError(f"Unknown binop: {op.op}")

        # Store result
        self._store_local(op.result, result)
        return result

    def _emit_cmpop(self, op: CmpOp) -> ir.Value:
        """Emit comparison operation."""
        left = self.emit_operand(op.left)
        right = self.emit_operand(op.right)

        # Handle nil comparison for value-based optionals
        nil_operand = None
        optional_operand = None
        if isinstance(right.type, ir.PointerType) and isinstance(left.type, ir.LiteralStructType):
            nil_operand = right
            optional_operand = left
        elif isinstance(left.type, ir.PointerType) and isinstance(right.type, ir.LiteralStructType):
            nil_operand = left
            optional_operand = right

        if nil_operand is not None and optional_operand is not None:
            # Extract tag (discriminant at index 0)
            tag = self.builder.extract_value(optional_operand, 0, name="opt.tag")
            # nil = tag 0, Some = tag 1
            zero = ir.Constant(self.type_cache.i1, 0)
            if op.op == CmpOpKind.NE:
                result = self.builder.icmp_signed("!=", tag, zero, name="nil_check")
            elif op.op == CmpOpKind.EQ:
                result = self.builder.icmp_signed("==", tag, zero, name="nil_check")
            else:
                # Other comparisons with nil don't make sense
                result = ir.Constant(self.type_cache.i1, 0)
            self._store_local(op.result, result)
            return result

        # Coerce operands to common type
        left, right = self._coerce_binop_operands(left, right)

        left_type = operand_type(op.left)
        is_float = self.type_cache.is_float(left_type)
        is_signed = self.type_cache.is_signed_integer(left_type)

        if is_float:
            # Use ordered comparisons for floats
            fcmp_map = {
                CmpOpKind.EQ: "oeq",
                CmpOpKind.NE: "one",
                CmpOpKind.LT: "olt",
                CmpOpKind.LE: "ole",
                CmpOpKind.GT: "ogt",
                CmpOpKind.GE: "oge",
            }
            result = self.builder.fcmp_ordered(fcmp_map[op.op], left, right, name="fcmp")
        else:
            signed_pred = {
                CmpOpKind.EQ: "==",
                CmpOpKind.NE: "!=",
                CmpOpKind.LT: "<",
                CmpOpKind.LE: "<=",
                CmpOpKind.GT: ">",
                CmpOpKind.GE: ">=",
            }
            unsigned_pred = {
                CmpOpKind.EQ: "==",
                CmpOpKind.NE: "!=",
                CmpOpKind.LT: "<",
                CmpOpKind.LE: "<=",
                CmpOpKind.GT: ">",
                CmpOpKind.GE: ">=",
            }

            pred = signed_pred[op.op] if is_signed else unsigned_pred[op.op]
            result = self.builder.icmp_signed(pred, left, right, name="icmp") if is_signed else self.builder.icmp_unsigned(pred, left, right, name="icmp")

        # Store result
        self._store_local(op.result, result)
        return result

    def _emit_div_zero_guard(self, divisor: ir.Value, is_remainder: bool) -> None:
        """
        Emit an integer divide-by-zero guard.

        Before the actual sdiv/udiv/srem/urem, splits the current basic block:
        if the divisor is zero, calls rt_panic_{divide,remainder}_by_zero (noreturn)
        and emits an unreachable; otherwise falls through to the existing builder
        cursor in the continuation block. After this call, the IRBuilder is
        positioned at the continuation block.
        """
        if not isinstance(divisor.type, ir.IntType):
            # Should not happen for non-float integer paths but be defensive.
            return

        zero = ir.Constant(divisor.type, 0)
        is_zero = self.builder.icmp_signed("==", divisor, zero, name="divz.check")

        fn = self.builder.function
        panic_block = fn.append_basic_block(name="divz.panic")
        cont_block = fn.append_basic_block(name="divz.cont")
        self.builder.cbranch(is_zero, panic_block, cont_block)

        # Panic path: call the appropriate noreturn helper, then unreachable.
        self.builder.position_at_end(panic_block)
        if is_remainder:
            self.builder.call(self.runtime.rt_panic_remainder_by_zero, [])
        else:
            self.builder.call(self.runtime.rt_panic_divide_by_zero, [])
        self.builder.unreachable()

        # Continuation: resume normal codegen at the continuation block.
        self.builder.position_at_end(cont_block)

    def _emit_unaryop(self, op: UnaryOp) -> ir.Value:
        """Emit unary operation."""
        operand = self.emit_operand(op.operand)

        match op.op:
            case UnaryOpKind.NEG:
                if self.type_cache.is_float(operand_type(op.operand)):
                    result = self.builder.fneg(operand, name="fneg")
                else:
                    result = self.builder.neg(operand, name="neg")

            case UnaryOpKind.NOT:
                # Logical not - compare with false
                result = self.builder.icmp_unsigned("==", operand,
                    ir.Constant(self.type_cache.i1, 0), name="not")

            case UnaryOpKind.BIT_NOT:
                # Bitwise not - XOR with all ones
                all_ones = ir.Constant(operand.type, -1)
                result = self.builder.xor(operand, all_ones, name="bitnot")

            case _:
                raise NotImplementedError(f"Unknown unary op: {op.op}")

        self._store_local(op.result, result)
        return result

    def _emit_fp_to_int_saturating(
        self,
        value: ir.Value,
        src_llvm: ir.Type,
        dst_llvm: ir.Type,
        dst_is_signed: bool,
    ) -> ir.Value:
        """Emit a float-to-int conversion using `llvm.fptosi.sat`/
        `llvm.fptoui.sat`. These intrinsics return a well-defined result
        for NaN (→ 0), ±infinity (→ INT_MAX / INT_MIN), and out-of-range
        values (saturated to INT_MAX / INT_MIN). Plain `fptosi`/`fptoui`
        produce LLVM poison in those cases — the language must not expose
        that to user code."""
        # Resolve bit widths for the intrinsic name.
        if isinstance(dst_llvm, ir.IntType):
            dst_bits = dst_llvm.width
        else:  # pragma: no cover — should never happen
            dst_bits = 32
        if isinstance(src_llvm, ir.FloatType):
            src_name = "f32"
        elif isinstance(src_llvm, ir.DoubleType):
            src_name = "f64"
        else:  # pragma: no cover — should never happen
            src_name = "f64"
        op_root = "fptosi.sat" if dst_is_signed else "fptoui.sat"
        intrinsic_name = f"llvm.{op_root}.i{dst_bits}.{src_name}"
        module = self.builder.module
        intrinsic = module.globals.get(intrinsic_name)
        if intrinsic is None:
            intrinsic_type = ir.FunctionType(dst_llvm, [src_llvm])
            intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)
        return self.builder.call(intrinsic, [value], name="fp.sat")

    def _emit_cast(self, op: CastOp) -> ir.Value:
        """Emit type cast."""
        # Special case: casting a local variable to RawPtr -> return its address
        if (isinstance(op.operand, (CopyOperand, MoveOperand))
                and not op.operand.place.projections):
            dst_type = op.target_type
            dst_info = self.type_table.get_type(dst_type)
            if dst_info and dst_info.kind == TypeKind.PRIMITIVE and self.type_table.format_type(dst_type) == "RawPtr":
                if op.operand.place.base in self.local_storage:
                    ptr = self.local_storage[op.operand.place.base]
                    src_llvm = ptr.type
                    dst_llvm = self.type_cache.get_llvm_type(dst_type)
                    if isinstance(src_llvm, ir.PointerType) and isinstance(dst_llvm, ir.PointerType):
                        if src_llvm != dst_llvm:
                            ptr = self.builder.bitcast(ptr, dst_llvm, name="addr_cast")
                        # Remember this temp aliases the address of a value local,
                        # so FFI accessors taking `&out`/`&value` (rt_gvec_get/set)
                        # can recover the pointee element type and inline access.
                        self._raw_addr_src[op.result] = op.operand.place.base
                        self._store_local(op.result, ptr)
                        return ptr

        value = self.emit_operand(op.operand)
        src_type = operand_type(op.operand)
        dst_type = op.target_type

        src_llvm = self.type_cache.get_llvm_type(src_type)
        dst_llvm = self.type_cache.get_llvm_type(dst_type)

        # Determine cast type
        src_is_int = self.type_cache.is_integer(src_type)
        dst_is_int = self.type_cache.is_integer(dst_type)
        src_is_float = self.type_cache.is_float(src_type)
        dst_is_float = self.type_cache.is_float(dst_type)
        src_is_signed = self.type_cache.is_signed_integer(src_type)
        dst_is_signed = self.type_cache.is_signed_integer(dst_type)
        src_is_bool = self.type_cache.is_bool(src_type)
        dst_is_bool = self.type_cache.is_bool(dst_type)

        if src_is_bool and dst_is_int:
            # Bool -> integer: zext i1 to target type
            result = self.builder.zext(value, dst_llvm, name="bool_to_int")

        elif src_is_int and dst_is_bool:
            # Integer -> Bool: compare != 0
            zero = ir.Constant(src_llvm, 0)
            result = self.builder.icmp_unsigned("!=", value, zero, name="int_to_bool")

        elif src_is_bool and dst_is_float:
            # Bool -> float: first zext to i32, then sitofp
            i32_type = ir.IntType(32)
            int_val = self.builder.zext(value, i32_type, name="bool_to_i32")
            result = self.builder.sitofp(int_val, dst_llvm, name="i32_to_float")

        elif src_is_float and dst_is_bool:
            # Float -> Bool: compare != 0.0
            zero = ir.Constant(src_llvm, 0.0)
            result = self.builder.fcmp_ordered("!=", value, zero, name="float_to_bool")

        elif src_is_int and dst_is_int:
            # Integer to integer
            src_bits = src_llvm.width if hasattr(src_llvm, 'width') else 64
            dst_bits = dst_llvm.width if hasattr(dst_llvm, 'width') else 64

            if src_bits < dst_bits:
                if src_is_signed:
                    result = self.builder.sext(value, dst_llvm, name="sext")
                else:
                    result = self.builder.zext(value, dst_llvm, name="zext")
            elif src_bits > dst_bits:
                result = self.builder.trunc(value, dst_llvm, name="trunc")
            else:
                result = value

        elif src_is_float and dst_is_float:
            # Float to float
            src_bits = 32 if isinstance(src_llvm, ir.FloatType) else 64
            dst_bits = 32 if isinstance(dst_llvm, ir.FloatType) else 64

            if src_bits < dst_bits:
                result = self.builder.fpext(value, dst_llvm, name="fpext")
            elif src_bits > dst_bits:
                result = self.builder.fptrunc(value, dst_llvm, name="fptrunc")
            else:
                result = value

        elif src_is_int and dst_is_float:
            # Int to float
            if src_is_signed:
                result = self.builder.sitofp(value, dst_llvm, name="sitofp")
            else:
                result = self.builder.uitofp(value, dst_llvm, name="uitofp")

        elif src_is_float and dst_is_int:
            # Float -> int via saturating intrinsic. Plain `fptosi` / `fptoui`
            # produce LLVM poison on NaN, ±infinity, or values outside the
            # destination integer's range. The saturating variants clamp to
            # INT_MIN / INT_MAX and map NaN to 0.
            result = self._emit_fp_to_int_saturating(
                value, src_llvm, dst_llvm, dst_is_signed,
            )

        else:
            # Pointer casts or other - use bitcast (but not for void)
            if isinstance(dst_llvm, ir.VoidType):
                result = value  # Can't cast to void, just use value
            elif isinstance(dst_llvm, ir.PointerType):
                # Destination is a pointer type
                if isinstance(src_llvm, ir.PointerType):
                    # Pointer to pointer
                    result = self.builder.bitcast(value, dst_llvm, name="bitcast")
                elif src_is_int:
                    # Integer literal/address value to pointer. Do not fall
                    # back to stack-address casts for values like
                    # `0 as RawPtr`; that must become a null pointer.
                    int_val = value
                    if value.type != self.type_cache.i64:
                        int_val = self.builder.zext(value, self.type_cache.i64, name="ptrint")
                    result = self.builder.inttoptr(int_val, dst_llvm, name="inttoptr")
                else:
                    # Value to pointer: allocate on stack and return address
                    tmp = self.builder.alloca(src_llvm, name="cast_tmp")
                    self.builder.store(value, tmp)
                    result = self.builder.bitcast(tmp, dst_llvm, name="valtoptr")
            elif isinstance(src_llvm, ir.PointerType) and not isinstance(dst_llvm, ir.PointerType):
                # Pointer to integer: bitcast-style reinterpretation via
                # ptrtoint. The previous behaviour did `bitcast to i64*` +
                # `load`, which silently *read* the bytes the pointer
                # targeted — a foot-gun (null-deref, type pun) the cast
                # syntax should never imply. `RawPtr as i64` now returns
                # the pointer's address.
                if isinstance(dst_llvm, ir.IntType):
                    result = self.builder.ptrtoint(value, dst_llvm, name="ptrtoint")
                else:
                    raise NotImplementedError(
                        "cannot cast pointer to non-pointer non-integer type"
                    )
            else:
                result = self.builder.bitcast(value, dst_llvm, name="cast")

        self._store_local(op.result, result)
        return result

