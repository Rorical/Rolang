"""Expression lowering for MIR builder.

Extracted from MirFunctionBuilder to isolate HIR expression -> MIR operand logic.
"""

from typing import Dict, List, Optional, Tuple, Union

from .types import (
    TypeId, TypeKind,
    StructTypeData, EnumTypeData, OptionalTypeData,
)
from .hir import (
    HirExpr, HirLiteral, HirVar, HirBinaryOp, HirUnaryOp, HirTernary,
    HirCall, HirMethodCall, HirFieldAccess, HirSubscript,
    HirTuple, HirArray, HirDict, HirLambda, HirClone,
    HirStructInit, HirEnumConstruct, HirCast, HirTypeCheck, HirTryExpr,
    HirOptionalSome, HirOptionalNone, HirOptionalMatch,
)
from .operators import (
    is_short_circuit_and_op,
    is_short_circuit_or_op,
    BINOP_MAP,
    CMPOP_MAP,
    UNARYOP_MAP,
)
from .mir import (
    LocalId, BlockId,
    Operand, CopyOperand, MoveOperand, ConstantOperand, ConstantKind, operand_type,
    Place, PlaceProjection, FieldProjection, IndexProjection, DerefProjection,
    Op, BinOp, CmpOp, CmpOpKind, UnaryOp, CastOp,
    MakeStruct, MakeEnum, MakeSome, MakeNone,
    ExtractField, ExtractEnumPayload, GetTag,
    Assign, Store, Load,
    CallStatic, CallVTable, CallWitness,
    MakeClosure, CallClosure,
    BoxExistential, ExistentialCheckType, ExistentialUnbox,
    TaskYield,
    Clone,
    Terminator, Branch, CondBranch, SwitchInt, Return, Unreachable,
)


def _is_std_collection(struct_name: str, base: str) -> bool:
    """Match a struct name against a std collection family.

    Returns True for `base` itself (e.g. `Vec`) — the pre-monomorphized
    case — or for any monomorphized form `base_*` (e.g. `Vec_i32`,
    `Dict_S90_i32`). The follow-up `_` is what distinguishes the std
    container from a user struct that happens to share a prefix (e.g.
    a user-defined `Vector` is not matched by `_is_std_collection(.., "Vec")`).
    """
    if not struct_name:
        return False
    return struct_name == base or struct_name.startswith(base + "_")


class MirExpressionLowerer:
    """Lower HIR expressions to MIR operands."""

    def __init__(self, builder: "MirFunctionBuilder") -> None:
        self._b = builder

# Expression Lowering
    # -------------------------------------------------------------------------

    def lower_expr(self, expr: HirExpr) -> Operand:
        """Lower an expression to an operand."""
        if isinstance(expr, HirLiteral):
            return self._lower_literal(expr)

        elif isinstance(expr, HirVar):
            return self._lower_var(expr)

        elif isinstance(expr, HirBinaryOp):
            return self._lower_binary_op(expr)

        elif isinstance(expr, HirUnaryOp):
            return self._lower_unary_op(expr)

        elif isinstance(expr, HirTernary):
            return self._lower_ternary(expr)

        elif isinstance(expr, HirCall):
            return self._lower_call(expr)

        elif isinstance(expr, HirMethodCall):
            return self._lower_method_call(expr)

        elif isinstance(expr, HirFieldAccess):
            return self._lower_field_access(expr)

        elif isinstance(expr, HirSubscript):
            return self._lower_subscript(expr)

        elif isinstance(expr, HirTuple):
            return self._lower_tuple(expr)

        elif isinstance(expr, HirArray):
            return self._lower_array(expr)

        elif isinstance(expr, HirDict):
            return self._lower_dict(expr)

        elif isinstance(expr, HirStructInit):
            return self._lower_struct_init(expr)

        elif isinstance(expr, HirEnumConstruct):
            return self._lower_enum_construct(expr)

        elif isinstance(expr, HirCast):
            return self._lower_cast(expr)

        elif isinstance(expr, HirTryExpr):
            return self._lower_try_expr(expr)

        elif isinstance(expr, HirOptionalSome):
            return self._lower_optional_some(expr)

        elif isinstance(expr, HirOptionalNone):
            return self._lower_optional_none(expr)

        elif isinstance(expr, HirOptionalMatch):
            return self._lower_optional_match(expr)

        elif isinstance(expr, HirLambda):
            return self._lower_lambda(expr)

        elif isinstance(expr, HirTypeCheck):
            return self._lower_type_check(expr)

        elif isinstance(expr, HirClone):
            return self._lower_clone(expr)

        else:
            self._b.errors.append(f"Unknown expression type: {type(expr).__name__}")
            # Return a placeholder
            return ConstantOperand(ConstantKind.NIL, None, self._b.type_table.error_type)

    def _lower_literal(self, lit: HirLiteral) -> Operand:
        """Lower a literal expression."""
        if lit.kind == "type_id" and isinstance(lit.value, TypeId):
            return ConstantOperand(
                ConstantKind.INT,
                self._b.type_table.runtime_type_id(lit.value),
                lit.type_id,
            )
        operand = self._b._make_constant(lit.value, lit.type_id)
        # A string literal lowers to a freshly-allocated, OWNED String heap
        # object (rc=1) — unlike int/bool/float constants it must be released
        # when no longer used. When it is consumed by a binding (`let s = "x"`,
        # `return "x"`, struct field init) ARC balances it. But when passed as a
        # *borrowed* operand (call argument, method receiver, `+`) there is no
        # local for ARC to attach a release to, so it leaked once per evaluation
        # (e.g. `f("x")` / `v.push("x")` in a loop). Materialize it into a temp
        # local so ARC tracks and releases it like any other owned temporary, in
        # every position. (Bindings keep working — the temp is just copied into
        # the destination and released; only borrowed positions change behaviour.)
        if isinstance(operand, ConstantOperand) and operand.kind == ConstantKind.STRING:
            tmp = self._b.create_temp(lit.type_id, prefix="__strlit")
            place = Place(base=tmp, projections=[], type_id=lit.type_id)
            self._b.emit_op(Assign(place=place, value=operand))
            return CopyOperand(place)
        return operand

    def _make_constant(self, value: Union[int, float, bool, str, None], type_id: TypeId) -> Operand:
        """Create a constant operand from a value."""
        if value is None:
            return ConstantOperand(ConstantKind.NIL, None, type_id)
        elif isinstance(value, bool):
            return ConstantOperand(ConstantKind.BOOL, value, type_id)
        elif isinstance(value, int):
            return ConstantOperand(ConstantKind.INT, value, type_id)
        elif isinstance(value, float):
            return ConstantOperand(ConstantKind.FLOAT, value, type_id)
        elif isinstance(value, str):
            return ConstantOperand(ConstantKind.STRING, value, type_id)
        else:
            return ConstantOperand(ConstantKind.NIL, None, type_id)

    def _lower_var(self, var: HirVar) -> Operand:
        """Lower a variable reference."""
        local_id = self._b.get_local_for_symbol(var.symbol_id)
        if local_id is not None:
            # Use the local's actual type (may have been updated for closures)
            actual_type = self._b.locals[local_id.id].type_id
            place = Place(base=local_id, projections=[], type_id=actual_type)
            return CopyOperand(place)
        else:
            self._b.errors.append(f"Undefined variable: {var.name}")
            return ConstantOperand(ConstantKind.NIL, None, var.type_id)

    def _lower_binary_op(self, binop: HirBinaryOp) -> Operand:
        """Lower a binary operation."""
        op = binop.op

        # Handle short-circuit operators
        if is_short_circuit_and_op(op):
            return self._lower_short_circuit_and(binop)
        elif is_short_circuit_or_op(op):
            return self._lower_short_circuit_or(binop)

        left = self.lower_expr(binop.left)
        right = self.lower_expr(binop.right)

        result_local = self._b.create_temp(binop.type_id)

        if op in BINOP_MAP:
            self._b.emit_op(BinOp(
                result=result_local,
                op=BINOP_MAP[op],
                left=left,
                right=right,
                result_type=binop.type_id,
            ))
        elif op in CMPOP_MAP:
            self._b.emit_op(CmpOp(
                result=result_local,
                op=CMPOP_MAP[op],
                left=left,
                right=right,
            ))
        else:
            self._b.errors.append(f"Unknown binary operator: {op}")

        return CopyOperand(Place(base=result_local, projections=[], type_id=binop.type_id))

    def _lower_short_circuit_and(self, binop: HirBinaryOp) -> Operand:
        """Lower short-circuit AND (&&)."""
        bool_type = self._b._bool_type()
        result_local = self._b.create_temp(bool_type)
        result_place = Place(base=result_local, projections=[], type_id=bool_type)

        left = self.lower_expr(binop.left)

        right_bb = self._b.create_block()
        short_circuit_bb = self._b.create_block()
        merge_bb = self._b.create_block()

        # If left is false, short-circuit to false
        self._b.emit_terminator(CondBranch(
            condition=left,
            true_target=right_bb,
            false_target=short_circuit_bb,
        ))

        # Short-circuit path: result = false
        self._b.switch_to_block(short_circuit_bb)
        false_const = ConstantOperand(ConstantKind.BOOL, False, bool_type)
        self._b.emit_op(Assign(place=result_place, value=false_const))
        self._b.emit_terminator(Branch(target=merge_bb))

        # Evaluate right
        self._b.switch_to_block(right_bb)
        right = self.lower_expr(binop.right)
        self._b.emit_op(Assign(place=result_place, value=right))
        self._b.emit_terminator(Branch(target=merge_bb))

        # Merge block
        self._b.switch_to_block(merge_bb)

        return CopyOperand(result_place)

    def _lower_short_circuit_or(self, binop: HirBinaryOp) -> Operand:
        """Lower short-circuit OR (||)."""
        bool_type = self._b._bool_type()
        result_local = self._b.create_temp(bool_type)
        result_place = Place(base=result_local, projections=[], type_id=bool_type)

        left = self.lower_expr(binop.left)

        short_circuit_bb = self._b.create_block()
        right_bb = self._b.create_block()
        merge_bb = self._b.create_block()

        # If left is true, short-circuit to true
        self._b.emit_terminator(CondBranch(
            condition=left,
            true_target=short_circuit_bb,
            false_target=right_bb,
        ))

        # Short-circuit path: result = true
        self._b.switch_to_block(short_circuit_bb)
        true_const = ConstantOperand(ConstantKind.BOOL, True, bool_type)
        self._b.emit_op(Assign(place=result_place, value=true_const))
        self._b.emit_terminator(Branch(target=merge_bb))

        # Evaluate right
        self._b.switch_to_block(right_bb)
        right = self.lower_expr(binop.right)
        self._b.emit_op(Assign(place=result_place, value=right))
        self._b.emit_terminator(Branch(target=merge_bb))

        self._b.switch_to_block(merge_bb)

        return CopyOperand(result_place)

    def _lower_unary_op(self, unop: HirUnaryOp) -> Operand:
        """Lower a unary operation."""
        operand = self.lower_expr(unop.operand)

        if unop.op == "await":
            operand = self._b._coerce_operand(operand, unop.type_id)
            # Emit TaskYield to allow cooperative scheduling.
            # The async lowering pass will later transform this into
            # a full state machine for true concurrency.
            self._b.emit_op(TaskYield())
            return operand

        result_local = self._b.create_temp(unop.type_id)

        if unop.op in UNARYOP_MAP:
            self._b.emit_op(UnaryOp(
                result=result_local,
                op=UNARYOP_MAP[unop.op],
                operand=operand,
                result_type=unop.type_id,
            ))
        else:
            self._b.errors.append(f"Unknown unary operator: {unop.op}")

        return CopyOperand(Place(base=result_local, projections=[], type_id=unop.type_id))

    def _lower_ternary(self, tern: HirTernary) -> Operand:
        """Lower a ternary expression (cond ? then : else)."""
        cond = self.lower_expr(tern.condition)

        result_local = self._b.create_temp(tern.type_id)
        result_place = Place(base=result_local, projections=[], type_id=tern.type_id)

        then_bb = self._b.create_block()
        else_bb = self._b.create_block()
        merge_bb = self._b.create_block()

        self._b.emit_terminator(CondBranch(
            condition=cond,
            true_target=then_bb,
            false_target=else_bb,
        ))

        # Then
        self._b.switch_to_block(then_bb)
        then_val = self.lower_expr(tern.then_expr)
        self._b.emit_op(Assign(place=result_place, value=then_val))
        self._b.emit_terminator(Branch(target=merge_bb))

        # Else
        self._b.switch_to_block(else_bb)
        else_val = self.lower_expr(tern.else_expr)
        self._b.emit_op(Assign(place=result_place, value=else_val))
        self._b.emit_terminator(Branch(target=merge_bb))

        self._b.switch_to_block(merge_bb)

        return CopyOperand(result_place)

    def _lower_call(self, call: HirCall) -> Operand:
        """Lower a function call."""
        # Lower arguments
        args = [self.lower_expr(arg) for _, arg in call.arguments]

        # Check if the callee is a first-class callable value (closure or
        # function-typed variable / parameter). FUNCTION-typed locals share
        # the closure heap-object layout (typed pointer whose payload is
        # ``{fn_ptr, captures...}`` at offset 32), so they are dispatched
        # through the same CallClosure path.
        callee_type = call.callee.type_id
        is_closure_call = (
            self._b.type_table.is_closure(callee_type)
            or self._b.type_table.is_function(callee_type)
        )

        # If the callee is a HirVar referencing a *named function* (e.g. a
        # top-level def or extern), we still want to dispatch via CallStatic
        # since we have the name directly. Detect this case by checking that
        # the symbol resolves to a function declaration rather than a local.
        if is_closure_call and isinstance(call.callee, HirVar):
            symbol_id = call.callee.symbol_id
            if symbol_id is not None:
                local_id = self._b._symbol_to_local.get(symbol_id)
                if local_id is None:
                    # Not a local - it's a reference to a named function.
                    # Fall through to CallStatic.
                    is_closure_call = False
                else:
                    # Use the local's actual stored type (more precise than
                    # the HIR type for lambdas where MIR upgrades to CLOSURE).
                    local_type = self._b.locals[local_id.id].type_id
                    if (
                        self._b.type_table.is_closure(local_type)
                        or self._b.type_table.is_function(local_type)
                    ):
                        is_closure_call = True
                        callee_type = local_type

        # Coerce arguments to declared parameter types so implicit conversions
        # (Optional wrapping, existential boxing) are inserted at the call site.
        param_types = self._b._callee_param_types(callee_type)
        if param_types is not None:
            args = [
                self._b._coerce_operand(arg, pt) if i < len(param_types) else arg
                for i, (arg, pt) in enumerate(zip(args, param_types))
            ] + args[len(param_types):]

        if is_closure_call:
            # Lower the callee expression to get the closure value
            closure = self.lower_expr(call.callee)

            result_local: Optional[LocalId] = None
            void_type = self._b.type_table.void_type
            if call.type_id != void_type:
                result_local = self._b.create_temp(call.type_id)

            self._b.emit_op(CallClosure(
                result=result_local,
                closure=closure,
                args=args,
                result_type=call.type_id,
            ))

            if result_local is not None:
                return CopyOperand(Place(base=result_local, projections=[], type_id=call.type_id))
            else:
                return ConstantOperand(ConstantKind.UNIT, None, void_type)

        # Get callee name for static call
        callee_name = "<unknown>"
        if isinstance(call.callee, HirVar):
            callee_name = call.callee.name
        elif call.callee_symbol is not None:
            symbol = self._b.symbol_table.get_symbol(call.callee_symbol)
            if symbol:
                callee_name = symbol.name

        result_local: Optional[LocalId] = None
        void_type = self._b.type_table.void_type
        if call.type_id != void_type:
            result_local = self._b.create_temp(call.type_id)

        self._b.emit_op(CallStatic(
            result=result_local,
            func_name=callee_name,
            func_symbol=call.callee_symbol,
            args=args,
            result_type=call.type_id,
        ))

        if result_local is not None:
            return CopyOperand(Place(base=result_local, projections=[], type_id=call.type_id))
        else:
            return ConstantOperand(ConstantKind.UNIT, None, void_type)

    def _lower_method_call(self, call: HirMethodCall) -> Operand:
        """
        Lower a method call.

        Every receiver is already an ARC reference into the heap, so a method
        that updates one of `self`'s fields updates the object visible to the
        caller in place.
        No automatic write-back to the call site is needed; the call lowers
        like any other function call with `self` as its first argument.
        """
        receiver = None if call.is_static else self.lower_expr(call.receiver)
        receiver_type = call.receiver.type_id if call.is_static else operand_type(receiver)
        args = [self.lower_expr(arg) for _, arg in call.arguments]

        result_local: Optional[LocalId] = None
        void_type = self._b.type_table.void_type
        call_result_type = call.type_id
        if call_result_type != void_type:
            result_local = self._b.create_temp(call_result_type)

        # Check if receiver is an existential type (any Protocol)
        receiver_info = self._b.type_table.get_type(receiver_type)
        if receiver_info and receiver_info.kind == TypeKind.EXISTENTIAL:
            # Use virtual table dispatch
            self._b.emit_op(CallVTable(
                result=result_local,
                receiver=receiver,
                method_name=call.method_name,
                args=args,
                result_type=call.type_id,
            ))
        else:
            # Static dispatch with receiver as first arg
            func_name = call.method_name
            dispatch_type = receiver_type
            actual_info = self._b.type_table.get_type(dispatch_type)

            if actual_info and actual_info.kind == TypeKind.STRUCT:
                data = actual_info.data
                if isinstance(data, StructTypeData):
                    struct_symbol = self._b.symbol_table.get_symbol(data.symbol_id)
                    if struct_symbol:
                        func_name = f"{struct_symbol.name}_{call.method_name}"
            elif actual_info and actual_info.kind == TypeKind.ENUM:
                data = actual_info.data
                if isinstance(data, EnumTypeData):
                    enum_symbol = self._b.symbol_table.get_symbol(data.symbol_id)
                    if enum_symbol:
                        func_name = f"{enum_symbol.name}_{call.method_name}"
            elif actual_info and actual_info.kind == TypeKind.PRIMITIVE:
                from .types import PrimitiveTypeData
                if isinstance(actual_info.data, PrimitiveTypeData):
                    func_name = f"{actual_info.data.primitive.value}_{call.method_name}"

            full_args = args if call.is_static else [receiver] + args
            self._b.emit_op(CallStatic(
                result=result_local,
                func_name=func_name,
                func_symbol=call.method_symbol,
                args=full_args,
                result_type=call_result_type,
            ))

        if result_local is not None:
            return CopyOperand(Place(base=result_local, projections=[], type_id=call.type_id))
        else:
            return ConstantOperand(ConstantKind.UNIT, None, void_type)

    def _lower_field_access(self, access: HirFieldAccess) -> Operand:
        """Lower a field access.

        Property-style `.len` / `.count` access on the old builtin array
        and dict types was removed when those types were folded into
        `Vec<T>` / `Dict<K, V>`. Users now call `.len()` as a method.
        """
        obj = self.lower_expr(access.object)
        obj_type = operand_type(obj)

        # Resolve the receiver type and handle field access
        info = self._b.type_table.get_type(obj_type)

        # Look up field index from struct type
        field_index = 0
        if info and info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                # Look up field position in struct
                from .members import MemberResolver
                resolver = MemberResolver(self._b.type_table, self._b.symbol_table)
                field_info = resolver.get_field(obj_type, access.field_name)
                if field_info:
                    field_index = field_info.index

        result_local = self._b.create_temp(access.type_id)
        self._b.emit_op(ExtractField(
            result=result_local,
            aggregate=obj,
            field_name=access.field_name,
            field_index=field_index,
            result_type=access.type_id,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=access.type_id))

    def _lower_subscript(self, subscript: HirSubscript) -> Operand:
        """Lower a subscript expression.

            * anonymous struct (tuple) subscript -> ExtractField by field name
            * `Vec<T>` -> `Vec_<T>_get(vec, i)`
            * `Dict<K, V>` -> `Dict_<K>_<V>_get(d, k)` (returns `V?`)

        Monomorphization renames the struct to its mangled form
        (`Vec_i32`, `Dict_S90_i32`, ...), so we recognize the receiver by
        the name prefix `Vec_` / `Dict_` (or exactly `Vec` / `Dict`
        when the receiver is still generic).
        """
        obj = self.lower_expr(subscript.object)
        obj_type = operand_type(obj)

        if not subscript.indices:
            return obj

        info = self._b.type_table.get_type(obj_type)

        # Anonymous struct (tuple) subscript: `t[N]` where N is an integer
        # literal. Resolved at compile time to an ExtractField with the
        # positional field name.
        if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            if info.data.symbol_id is None and info.data.anon_fields is not None:
                literal_idx = self._integer_literal_value(subscript.indices[0])
                if literal_idx is not None and 0 <= literal_idx < len(info.data.anon_fields):
                    fname, _ = info.data.anon_fields[literal_idx]
                    result_local = self._b.create_temp(subscript.type_id)
                    self._b.emit_op(ExtractField(
                        result=result_local,
                        aggregate=obj,
                        field_name=fname,
                        field_index=literal_idx,
                        result_type=subscript.type_id,
                    ))
                    return CopyOperand(Place(
                        base=result_local, projections=[], type_id=subscript.type_id
                    ))

        index = self.lower_expr(subscript.indices[0])
        result_local = self._b.create_temp(subscript.type_id)

        if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            sym = self._b.symbol_table.get_symbol(info.data.symbol_id)
            struct_name = sym.name if sym is not None else ""
            if _is_std_collection(struct_name, "Vec") or _is_std_collection(struct_name, "Dict"):
                prefix = self._struct_mangled_prefix(obj_type)
                self._b.emit_op(CallStatic(
                    result=result_local,
                    func_name=f"{prefix}_get",
                    func_symbol=None,
                    args=[obj, index],
                    result_type=subscript.type_id,
                ))
                return CopyOperand(Place(
                    base=result_local, projections=[], type_id=subscript.type_id
                ))
            # __get__ dunder method fallback for user structs
            member_resolver = getattr(self._b, "member_resolver", None)
            if member_resolver is not None:
                get_method = member_resolver.get_method(obj_type, "__get__")
                if get_method is not None:
                    func_name = f"{struct_name}___get__"
                    self._b.emit_op(CallStatic(
                        result=result_local,
                        func_name=func_name,
                        func_symbol=get_method.symbol_id,
                        args=[obj, index],
                        result_type=subscript.type_id,
                    ))
                    return CopyOperand(Place(
                        base=result_local, projections=[], type_id=subscript.type_id
                    ))

        self._b.errors.append(
            f"Cannot subscript type {self._b.type_table.format_type(obj_type)}"
        )
        return CopyOperand(Place(base=result_local, projections=[], type_id=subscript.type_id))

    def _integer_literal_value(self, expr: HirExpr) -> Optional[int]:
        """Return an integer literal value if ``expr`` is one, else None."""
        if isinstance(expr, HirLiteral) and isinstance(expr.value, int) and not isinstance(expr.value, bool):
            return expr.value
        return None

    def _lower_tuple(self, tup: HirTuple) -> Operand:
        """Lower a tuple expression to an anonymous MakeStruct."""
        info = self._b.type_table.get_type(tup.type_id)
        fields_meta = info.data.anon_fields if (info and hasattr(info.data, 'anon_fields')) else None
        fields: list = []
        for i, (_, elem_expr) in enumerate(tup.elements):
            fname = fields_meta[i][0] if (fields_meta and i < len(fields_meta)) else str(i)
            fields.append((fname, self.lower_expr(elem_expr)))

        result_local = self._b.create_temp(tup.type_id)
        self._b.emit_op(MakeStruct(
            result=result_local,
            struct_type=tup.type_id,
            fields=fields,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=tup.type_id))

    def _lower_array(self, arr: HirArray) -> Operand:
        """Lower `[e0, e1, ...]` to `Vec<T>` construction.

        An array literal is sugar for a `Vec<T>` allocated via
        `Vec<T>.with_capacity(N)` and filled with `push` calls in source
        order.
        """
        elements = [self.lower_expr(e) for e in arr.elements]
        vec_type = arr.type_id
        n_const = ConstantOperand(
            ConstantKind.INT, len(elements), self._b._i32_type()
        )

        prefix = self._struct_mangled_prefix(vec_type)

        result_local = self._b.create_temp(vec_type)
        self._b.emit_op(CallStatic(
            result=result_local,
            func_name=f"{prefix}_with_capacity",
            func_symbol=None,
            args=[n_const],
            result_type=vec_type,
        ))

        vec_place = Place(base=result_local, projections=[], type_id=vec_type)
        for elem in elements:
            self._b.emit_op(CallStatic(
                result=None,
                func_name=f"{prefix}_push",
                func_symbol=None,
                args=[CopyOperand(vec_place), elem],
                result_type=self._b.type_table.void_type,
            ))

        return CopyOperand(vec_place)

    def _lower_dict(self, d: HirDict) -> Operand:
        """Lower `[k0: v0, ...]` to `Dict<K, V>` construction.

        Sugar for `Dict<K, V>.with_capacity(N, key_kind)` plus a `set`
        call per entry. The key_kind is derived from the key's type:
        `1` for `String` keys (content comparison), `0` otherwise
        (byte comparison).
        """
        entries = [(self.lower_expr(k), self.lower_expr(v)) for k, v in d.entries]
        dict_type = d.type_id
        i32 = self._b._i32_type()
        n_const = ConstantOperand(ConstantKind.INT, len(entries), i32)
        key_kind = 1 if self._b.type_table.is_string(d.key_type) else 0
        key_kind_const = ConstantOperand(ConstantKind.INT, key_kind, i32)

        prefix = self._struct_mangled_prefix(dict_type)

        result_local = self._b.create_temp(dict_type)
        self._b.emit_op(CallStatic(
            result=result_local,
            func_name=f"{prefix}_with_capacity",
            func_symbol=None,
            args=[n_const, key_kind_const],
            result_type=dict_type,
        ))

        dict_place = Place(base=result_local, projections=[], type_id=dict_type)
        for key_op, val_op in entries:
            self._b.emit_op(CallStatic(
                result=None,
                func_name=f"{prefix}_set",
                func_symbol=None,
                args=[CopyOperand(dict_place), key_op, val_op],
                result_type=self._b.type_table.void_type,
            ))

        return CopyOperand(dict_place)

    def _struct_mangled_prefix(self, type_id: TypeId) -> str:
        """Return the monomorphized-name prefix for methods on a struct.

        After monomorphization the struct's source-level symbol name is
        already the mangled form (e.g. `Vec_i32`), so `_struct_mangled_prefix`
        just returns that name. Before monomorphization — or when no
        substitution has happened yet — the struct still carries the
        original name and its `type_args`, in which case we fall back
        to `monomorphize.mangle_name` to compose the prefix.
        """
        from .monomorphize import mangle_name

        info = self._b.type_table.get_type(type_id)
        if info is None or info.kind != TypeKind.STRUCT:
            return "<error>"
        data = info.data
        if not isinstance(data, StructTypeData):
            return "<error>"
        sym = self._b.symbol_table.get_symbol(data.symbol_id)
        base = sym.name if sym is not None else "struct"
        if not data.type_args:
            return base
        return mangle_name(base, data.type_args, self._b.type_table)

    def _lower_struct_init(self, init: HirStructInit) -> Operand:
        """Lower struct initialization."""
        from .members import MemberResolver
        resolver = MemberResolver(self._b.type_table, self._b.symbol_table)
        members = resolver.get_members(init.type_id)

        fields: List[Tuple[str, Operand]] = []
        for i, (label, arg) in enumerate(init.arguments):
            value = self.lower_expr(arg)
            field_name = label or f"_{i}"
            field_info = members.fields.get(field_name)
            if field_info is not None:
                value = self._b._coerce_operand(value, field_info.type_id)
            fields.append((field_name, value))

        result_local = self._b.create_temp(init.type_id)
        self._b.emit_op(MakeStruct(
            result=result_local,
            struct_type=init.struct_type,
            fields=fields,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=init.type_id))

    def _lower_enum_construct(self, construct: HirEnumConstruct) -> Operand:
        """Lower enum construction."""
        payload = [self.lower_expr(e) for _, e in construct.payload]
        tag = self._b._get_enum_case_tag(construct.enum_type, construct.case_name) or 0

        result_local = self._b.create_temp(construct.type_id)
        self._b.emit_op(MakeEnum(
            result=result_local,
            enum_type=construct.enum_type,
            case_name=construct.case_name,
            tag=tag,
            payload=payload,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=construct.type_id))

    def _lower_cast(self, cast: HirCast) -> Operand:
        """Lower a type cast."""
        kind = getattr(cast, "kind", "safe")
        if kind in ("optional", "forced"):
            return self._lower_runtime_downcast(cast, kind)

        value = self.lower_expr(cast.expr)

        result_local = self._b.create_temp(cast.type_id)
        self._b.emit_op(CastOp(
            result=result_local,
            operand=value,
            target_type=cast.target_type,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=cast.type_id))

    def _lower_runtime_downcast(self, cast: HirCast, kind: str) -> Operand:
        """Lower ``e as? T`` / ``e as! T`` to an explicit CFG.

        Strategy::

            cond = ExistentialCheckType(e, T)
            cond_branch cond -> match_bb, miss_bb

            match_bb:
              concrete = ExistentialUnbox(e, T)
              # kind == "optional":
              result = MakeSome(concrete)
              # kind == "forced":
              result = concrete (assigned directly)
              br merge

            miss_bb:
              # kind == "optional":
              result = MakeNone
              # kind == "forced":
              call rt_panic_invalid_cast(); unreachable

            merge: produce `result`
        """
        existential = self.lower_expr(cast.expr)

        # Resolve the protocol that the source existential carries —
        # ExistentialCheckType / ExistentialUnbox both need it to look up
        # the witness table emitted at codegen time.
        source_type = operand_type(existential)
        src_info = self._b.type_table.get_type(source_type)
        from .types import ExistentialTypeData
        if src_info is None or src_info.kind != TypeKind.EXISTENTIAL or not isinstance(
            src_info.data, ExistentialTypeData
        ):
            # Type checker should have caught this; emit a sentinel and
            # keep going so we don't crash later passes.
            self._b.errors.append(
                "internal: runtime downcast source is not an existential "
                "(should have been rejected by the type checker)"
            )
            result_local = self._b.create_temp(cast.type_id)
            return CopyOperand(Place(base=result_local, projections=[], type_id=cast.type_id))
        protocol_type = src_info.data.protocol_id

        # The MIR target type for `as!` is the concrete type directly; for
        # `as?` it's Optional<concrete>, and the concrete is the inner of
        # that Optional.
        if kind == "forced":
            concrete_type = cast.target_type
        else:
            inner = self._b.type_table.get_optional_inner(cast.type_id)
            concrete_type = inner if inner is not None else cast.target_type

        bool_type = self._b._bool_type()
        cond_local = self._b.create_temp(bool_type, "__cast_match")
        self._b.emit_op(ExistentialCheckType(
            result=cond_local,
            existential=existential,
            concrete_type=concrete_type,
            protocol_type=protocol_type,
        ))
        cond_operand = CopyOperand(Place(
            base=cond_local, projections=[], type_id=bool_type,
        ))

        result_local = self._b.create_temp(cast.type_id, "__cast_result")
        result_place = Place(base=result_local, projections=[], type_id=cast.type_id)

        match_bb = self._b.create_block()
        miss_bb = self._b.create_block()
        merge_bb = self._b.create_block()

        self._b.emit_terminator(CondBranch(
            condition=cond_operand,
            true_target=match_bb,
            false_target=miss_bb,
        ))

        # ---- Match path ----
        self._b.switch_to_block(match_bb)
        unbox_local = self._b.create_temp(concrete_type, "__cast_unboxed")
        self._b.emit_op(ExistentialUnbox(
            result=unbox_local,
            existential=existential,
            concrete_type=concrete_type,
            protocol_type=protocol_type,
            result_type=concrete_type,
        ))
        unbox_operand = CopyOperand(Place(
            base=unbox_local, projections=[], type_id=concrete_type,
        ))

        if kind == "optional":
            some_local = self._b.create_temp(cast.type_id, "__cast_some")
            self._b.emit_op(MakeSome(
                result=some_local,
                value=unbox_operand,
                result_type=cast.type_id,
            ))
            self._b.emit_op(Assign(
                place=result_place,
                value=CopyOperand(Place(
                    base=some_local, projections=[], type_id=cast.type_id,
                )),
            ))
        else:  # forced
            self._b.emit_op(Assign(place=result_place, value=unbox_operand))

        self._b.emit_terminator(Branch(target=merge_bb))

        # ---- Mismatch path ----
        self._b.switch_to_block(miss_bb)
        if kind == "optional":
            none_local = self._b.create_temp(cast.type_id, "__cast_none")
            self._b.emit_op(MakeNone(
                result=none_local,
                result_type=cast.type_id,
            ))
            self._b.emit_op(Assign(
                place=result_place,
                value=CopyOperand(Place(
                    base=none_local, projections=[], type_id=cast.type_id,
                )),
            ))
            self._b.emit_terminator(Branch(target=merge_bb))
        else:
            # `as!` on mismatch: noreturn panic.
            self._b.emit_op(CallStatic(
                result=None,
                func_name="rt_panic_invalid_cast",
                func_symbol=None,
                args=[],
                result_type=self._b.type_table.void_type,
            ))
            self._b.emit_terminator(Unreachable())

        self._b.switch_to_block(merge_bb)
        return CopyOperand(result_place)

    def _lower_try_expr(self, expr: HirTryExpr) -> Operand:
        """Lower try expression (x?): extract value or return error."""
        scrutinee = self.lower_expr(expr.expr)
        
        # Allocate result local EARLY so it's available in all blocks
        result_local = self._b.create_temp(expr.result_type, "__try_ok")
        
        # Get discriminant tag (0 = ok, 1+ = err)
        tag_type = self._b._i32_type()
        tag_local = self._b.create_temp(tag_type)
        self._b.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
        
        # Compare tag == 0
        bool_type = self._b._bool_type()
        is_ok_local = self._b.create_temp(bool_type)
        self._b.emit_op(CmpOp(
            result=is_ok_local,
            op=CmpOpKind.EQ,
            left=CopyOperand(Place(base=tag_local, projections=[], type_id=tag_type)),
            right=ConstantOperand(ConstantKind.INT, 0, tag_type),
        ))
        
        ok_bb = self._b.create_block()
        err_bb = self._b.create_block()
        merge_bb = self._b.create_block()
        
        self._b.emit_terminator(CondBranch(
            condition=CopyOperand(Place(base=is_ok_local, projections=[], type_id=bool_type)),
            true_target=ok_bb,
            false_target=err_bb,
        ))
        
        # Error block: return the entire scrutinee (which is the error value)
        self._b.switch_to_block(err_bb)
        self._b.emit_defers()
        self._b.emit_terminator(Return(value=scrutinee))
        
        # Success block: extract payload from 'ok' case, store in result_local
        self._b.switch_to_block(ok_bb)
        self._b.emit_op(ExtractEnumPayload(
            result=result_local,
            enum_val=scrutinee,
            case_name="ok",
            payload_index=0,
            result_type=expr.result_type,
        ))
        self._b.emit_terminator(Branch(target=merge_bb))
        
        # Continue in merge block
        self._b.switch_to_block(merge_bb)
        return CopyOperand(Place(base=result_local, projections=[], type_id=expr.result_type))

    def _lower_optional_some(self, some: HirOptionalSome) -> Operand:
        """Lower Optional.Some(value)."""
        value = self.lower_expr(some.value)

        result_local = self._b.create_temp(some.type_id)
        self._b.emit_op(MakeSome(
            result=result_local,
            value=value,
            result_type=some.type_id,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=some.type_id))

    def _lower_optional_none(self, none: HirOptionalNone) -> Operand:
        """Lower Optional.None."""
        result_local = self._b.create_temp(none.type_id)
        self._b.emit_op(MakeNone(
            result=result_local,
            result_type=none.type_id,
        ))

        return CopyOperand(Place(base=result_local, projections=[], type_id=none.type_id))

    def _lower_optional_match(self, match: HirOptionalMatch) -> Operand:
        """Lower desugared optional match (from ?. or ??)."""
        scrutinee = self.lower_expr(match.scrutinee)

        result_local = self._b.create_temp(match.type_id)
        result_place = Place(base=result_local, projections=[], type_id=match.type_id)

        some_bb = self._b.create_block()
        none_bb = self._b.create_block()
        merge_bb = self._b.create_block()

        # Check if Some or None
        tag_type = self._b._i32_type()
        tag_local = self._b.create_temp(tag_type)
        self._b.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
        tag_operand = CopyOperand(Place(base=tag_local, projections=[], type_id=tag_type))

        self._b.emit_terminator(SwitchInt(
            value=tag_operand,
            cases=[(1, some_bb)],  # Some = 1
            default=none_bb,       # None = 0
        ))

        # Some case
        self._b.switch_to_block(some_bb)
        inner_type = match.inner_type

        # Extract payload and bind to some_binding
        payload_local = self._b.create_local(
            name="__some_val",
            type_id=inner_type,
            symbol_id=match.some_binding,
        )
        self._b.emit_op(ExtractEnumPayload(
            result=payload_local,
            enum_val=scrutinee,
            case_name="Some",
            payload_index=0,
            result_type=inner_type,
        ))

        some_val = self.lower_expr(match.some_expr)
        self._b.emit_op(Assign(place=result_place, value=some_val))
        self._b.emit_terminator(Branch(target=merge_bb))

        # None case
        self._b.switch_to_block(none_bb)
        none_val = self.lower_expr(match.none_expr)
        self._b.emit_op(Assign(place=result_place, value=none_val))
        self._b.emit_terminator(Branch(target=merge_bb))

        self._b.switch_to_block(merge_bb)

        return CopyOperand(result_place)

    def _lower_lambda(self, lam: HirLambda) -> Operand:
        """Lower a lambda expression to a closure."""
        from .capture_analysis import analyze_captures
        from .types import ClosureTypeData

        # Collect outer scope symbols
        outer_scope_symbols = set(self._b._symbol_to_local.keys())

        # Build symbol types map from our locals
        symbol_types = {}
        for local in self._b.locals:
            if local.symbol_id is not None:
                symbol_types[local.symbol_id] = local.type_id

        # Analyze captures
        captures = analyze_captures(
            lam,
            outer_scope_symbols,
            symbol_types,
            self._b.type_table,
            self._b.symbol_table,
        )

        # Generate unique name for the lambda function
        lambda_name = f"__lambda_{self._b._next_value_id}"
        self._b._next_value_id += 1

        # Lower captured values to operands
        capture_operands: List[Operand] = []
        capture_types: List[TypeId] = []
        for cap in captures:
            local_id = self._b._symbol_to_local.get(cap.symbol_id)
            if local_id is not None:
                place = Place(base=local_id, projections=[], type_id=cap.type_id)
                capture_operands.append(CopyOperand(place))
                capture_types.append(cap.type_id)

        # Create the closure type
        # Extract return type from the lambda's function type
        param_types = tuple(p.type_id for p in lam.params)
        lambda_func_type = lam.type_id
        func_data = self._b.type_table.get_function_data(lambda_func_type)
        if func_data:
            return_type = func_data.return_type
        else:
            return_type = self._b.type_table.void_type

        closure_type = self._b.type_table.make_closure(
            params=param_types,
            return_type=return_type,
            captures=tuple(capture_types),
            is_async=False,
        )

        # Register lambda for later processing
        # Store lambda info for the MIR program builder to generate the function
        if not hasattr(self, '_pending_lambdas'):
            self._pending_lambdas = []
        self._pending_lambdas.append((lambda_name, lam, captures, closure_type))

        # Emit MakeClosure operation
        result = self._b.create_temp(closure_type)
        result_place = Place(base=result, projections=[], type_id=closure_type)
        self._b.emit_op(MakeClosure(
            result=result,
            func_name=lambda_name,
            captures=capture_operands,
            result_type=closure_type,
        ))

        return CopyOperand(result_place)

    def _lower_type_check(self, check: HirTypeCheck) -> Operand:
        """Lower a type check (expr is Type)."""
        from .types import TypeKind, OptionalTypeData, EnumTypeData

        bool_type = self._b._bool_type()
        expr_type = check.expr.type_id
        checked_type = check.checked_type

        # Get type info
        expr_info = self._b.type_table.get_type(expr_type)

        # If types are equal at compile time, return true
        if expr_type == checked_type:
            return ConstantOperand(ConstantKind.BOOL, True, bool_type)

        # Check if expr is an optional and we're checking for the inner type
        if expr_info and expr_info.kind == TypeKind.OPTIONAL:
            data = expr_info.data
            if isinstance(data, OptionalTypeData):
                # Checking if Optional<T> is T means checking if it's Some
                if data.inner == checked_type:
                    # Lower the expression and check if tag != 0 (not None)
                    operand = self.lower_expr(check.expr)
                    result = self._b.create_temp(bool_type)

                    # Get the discriminant tag and compare to None (0)
                    tag_result = self._b.create_temp(self._b._i64_type())
                    self._b.emit_op(GetTag(
                        result=tag_result,
                        enum_val=operand,
                    ))

                    # Tag 0 = None, Tag 1 = Some
                    # is T means tag == 1 (Some)
                    zero = ConstantOperand(ConstantKind.INT, 0, self._b._i64_type())
                    self._b.emit_op(CmpOp(
                        result=result,
                        op=CmpOpKind.NE,  # Not equal to 0 means Some
                        left=CopyOperand(Place(base=tag_result, projections=[], type_id=self._b._i64_type())),
                        right=zero,
                    ))
                    return CopyOperand(Place(base=result, projections=[], type_id=bool_type))

        # Check if expr is an existential (any P) — runtime witness check
        if expr_info and expr_info.kind == TypeKind.EXISTENTIAL:
            from .types import ExistentialTypeData
            data = expr_info.data
            if isinstance(data, ExistentialTypeData):
                operand = self.lower_expr(check.expr)
                result = self._b.create_temp(bool_type)
                self._b.emit_op(ExistentialCheckType(
                    result=result,
                    existential=operand,
                    concrete_type=checked_type,
                    protocol_type=data.protocol_id,
                ))
                return CopyOperand(Place(base=result, projections=[], type_id=bool_type))

        # Check if we're checking if an enum has a specific variant
        if expr_info and expr_info.kind == TypeKind.ENUM:
            data = expr_info.data
            if isinstance(data, EnumTypeData):
                # The checked_type should be the same enum type for variant checks
                # For now, return a static check
                pass

        # Default: types don't match at compile time
        # This handles cases like `42 is String` -> false
        return ConstantOperand(ConstantKind.BOOL, False, bool_type)

    def _lower_clone(self, expr: HirClone) -> Operand:
        """Lower a .clone() call to the Clone MIR op (rt_obj_clone)."""
        value = self.lower_expr(expr.value)
        result_local = self._b.create_temp(expr.type_id, "__clone")
        self._b.emit_op(Clone(
            result=result_local,
            value=value,
            result_type=expr.type_id,
        ))
        return CopyOperand(Place(base=result_local, projections=[], type_id=expr.type_id))

    # -------------------------------------------------------------------------
