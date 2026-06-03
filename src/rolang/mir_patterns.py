"""Pattern matching and binding for MIR lowering.

Extracted from MirFunctionBuilder to isolate pattern-match lowering logic.
"""

from typing import Optional

from . import ast
from .hir import (
    HirPattern, HirWildcardPattern, HirBindingPattern, HirLiteralPattern,
    HirTuplePattern, HirEnumCasePattern, HirOrPattern,
)
from .mir import (
    Operand, CopyOperand, ConstantOperand, ConstantKind, Place,
    CmpOp, CmpOpKind, BinOp, BinOpKind,
    ExtractField, ExtractEnumPayload, GetTag,
    Assign,
)
from .types import TypeId, TypeKind, StructTypeData, EnumTypeData


class MirPatternLowerer:
    """Lower pattern matching and binding during MIR construction."""

    def __init__(self, builder: "MirFunctionBuilder") -> None:
        self._b = builder

    def lower_pattern_match(
        self, scrutinee: Operand, pattern: HirPattern
    ) -> Optional[Operand]:
        """Lower a pattern match to a boolean condition.

        Returns ``None`` if the pattern is statically guaranteed to match
        (wildcards, simple bindings, or structural patterns whose every
        sub-pattern is itself irrefutable). Otherwise returns a boolean
        :class:`Operand` that is true iff the pattern matches.
        """
        if isinstance(pattern, HirWildcardPattern):
            return None  # Always matches

        if isinstance(pattern, HirBindingPattern):
            return None  # Always matches

        if isinstance(pattern, HirLiteralPattern):
            # Compare scrutinee to literal
            bool_type = self._b._bool_type()
            cond_local = self._b.create_temp(bool_type)

            literal_operand = self._b._make_constant(pattern.value, pattern.type_id)
            self._b.emit_op(CmpOp(
                result=cond_local,
                op=CmpOpKind.EQ,
                left=scrutinee,
                right=literal_operand,
            ))
            return CopyOperand(Place(base=cond_local, projections=[], type_id=bool_type))

        if isinstance(pattern, HirEnumCasePattern):
            return self._lower_enum_case_test(scrutinee, pattern)

        if isinstance(pattern, HirOrPattern):
            return self._lower_or_pattern(scrutinee, pattern)

        if isinstance(pattern, HirTuplePattern):
            return self._lower_tuple_pattern(scrutinee, pattern)

        # Unknown pattern kind: emit a constant False so the arm is
        # unreachable rather than silently matching everything.
        bool_type = self._b._bool_type()
        return ConstantOperand(ConstantKind.BOOL, False, bool_type)

    def _lower_enum_case_test(
        self, scrutinee: Operand, pattern: HirEnumCasePattern
    ) -> Operand:
        """Emit ``GetTag(scrutinee) == <pattern.case_tag>``.

        Falls back to a constant ``false`` if the enum case cannot be
        resolved — that keeps us conservative when the front-end has
        already produced a type error for the same arm.
        """
        bool_type = self._b._bool_type()
        i32_type = self._b._i32_type()

        tag = self._b._get_enum_case_tag(pattern.enum_type, pattern.case_name)
        if tag is None:
            return ConstantOperand(ConstantKind.BOOL, False, bool_type)

        tag_local = self._b.create_temp(i32_type)
        self._b.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
        tag_operand = CopyOperand(Place(
            base=tag_local, projections=[], type_id=i32_type,
        ))

        # Compare the tag against the case constant.
        cmp_local = self._b.create_temp(bool_type)
        self._b.emit_op(CmpOp(
            result=cmp_local,
            op=CmpOpKind.EQ,
            left=tag_operand,
            right=ConstantOperand(ConstantKind.INT, tag, i32_type),
        ))
        cond: Operand = CopyOperand(Place(
            base=cmp_local, projections=[], type_id=bool_type,
        ))

        # Refine the test using any refutable sub-patterns inside the
        # payload. For example ``.some(.ok(let x))`` needs the tag-of-tag
        # check on the inner payload. Sub-pattern extraction can only run
        # legally when the outer tag has already matched, but since the
        # extracted operands are pure loads it is safe to compute them
        # unconditionally and then AND the conditions together — the
        # surrounding switch will only branch into the body when the
        # final boolean is true.
        for i, sub_pattern in enumerate(pattern.payload):
            payload_type = getattr(sub_pattern, "type_id", None)
            if payload_type is None or self._b.type_table.is_error(payload_type):
                payload_type = self._get_enum_payload_type(
                    pattern.enum_type, pattern.case_name, i,
                )
            if payload_type is None:
                continue

            payload_local = self._b.create_temp(payload_type)
            self._b.emit_op(ExtractEnumPayload(
                result=payload_local,
                enum_val=scrutinee,
                case_name=pattern.case_name,
                payload_index=i,
                result_type=payload_type,
            ))
            payload_operand = CopyOperand(Place(
                base=payload_local, projections=[], type_id=payload_type,
            ))

            sub_cond = self.lower_pattern_match(payload_operand, sub_pattern)
            if sub_cond is not None:
                cond = self._and(cond, sub_cond)

        return cond

    def _lower_or_pattern(
        self, scrutinee: Operand, pattern: HirOrPattern
    ) -> Optional[Operand]:
        """Lower an or-pattern ``p1 | p2 | ...`` to a disjunction.

        Returns ``None`` if any alternative is statically irrefutable —
        that matches the existing ``None`` semantics ("always matches").
        """
        if not pattern.patterns:
            bool_type = self._b._bool_type()
            return ConstantOperand(ConstantKind.BOOL, False, bool_type)

        result: Optional[Operand] = None
        for alt in pattern.patterns:
            alt_cond = self.lower_pattern_match(scrutinee, alt)
            if alt_cond is None:
                # This alternative always matches → the whole or-pattern
                # is irrefutable. Drop any earlier accumulated OR.
                return None
            result = alt_cond if result is None else self._or(result, alt_cond)
        return result

    def _lower_tuple_pattern(
        self, scrutinee: Operand, pattern: HirTuplePattern
    ) -> Optional[Operand]:
        """AND together refutable sub-patterns of an anonymous-struct pattern.

        The tuple's shape always matches (it's statically typed), so the
        refutability is determined entirely by the element patterns.
        """
        result: Optional[Operand] = None
        for i, (_label, sub_pattern) in enumerate(pattern.elements):
            elem_type = self._get_tuple_element_type(pattern.type_id, i)
            if elem_type is None:
                continue

            fname = self._get_tuple_field_name(pattern.type_id, i)
            elem_local = self._b.create_temp(elem_type)
            self._b.emit_op(ExtractField(
                result=elem_local,
                aggregate=scrutinee,
                field_name=fname,
                field_index=i,
                result_type=elem_type,
            ))
            elem_operand = CopyOperand(Place(
                base=elem_local, projections=[], type_id=elem_type,
            ))

            sub_cond = self.lower_pattern_match(elem_operand, sub_pattern)
            if sub_cond is None:
                continue
            result = sub_cond if result is None else self._and(result, sub_cond)

        return result  # None means every element was irrefutable

    def _and(self, left: Operand, right: Operand) -> Operand:
        """Emit ``left & right`` over i1 values (logical AND without short-circuit)."""
        bool_type = self._b._bool_type()
        out = self._b.create_temp(bool_type)
        self._b.emit_op(BinOp(
            result=out,
            op=BinOpKind.BIT_AND,
            left=left,
            right=right,
            result_type=bool_type,
        ))
        return CopyOperand(Place(base=out, projections=[], type_id=bool_type))

    def _or(self, left: Operand, right: Operand) -> Operand:
        """Emit ``left | right`` over i1 values (logical OR without short-circuit)."""
        bool_type = self._b._bool_type()
        out = self._b.create_temp(bool_type)
        self._b.emit_op(BinOp(
            result=out,
            op=BinOpKind.BIT_OR,
            left=left,
            right=right,
            result_type=bool_type,
        ))
        return CopyOperand(Place(base=out, projections=[], type_id=bool_type))

    def bind_pattern(self, pattern: HirPattern, value: Operand) -> None:
        """Bind variables in a pattern to a value."""
        if isinstance(pattern, HirWildcardPattern):
            pass  # Nothing to bind

        elif isinstance(pattern, HirBindingPattern):
            local_id = self._b.create_local(
                name=pattern.name,
                type_id=pattern.type_id,
                is_mutable=pattern.is_mutable,
                symbol_id=pattern.symbol_id,
            )
            place = Place(base=local_id, projections=[], type_id=pattern.type_id)
            self._b.emit_op(Assign(place=place, value=value))

        elif isinstance(pattern, HirTuplePattern):
            for i, (label, sub_pattern) in enumerate(pattern.elements):
                # Extract tuple element (anonymous struct field by positional name)
                elem_type = self._get_tuple_element_type(pattern.type_id, i)
                if elem_type is not None:
                    fname = self._get_tuple_field_name(pattern.type_id, i)
                    elem_local = self._b.create_temp(elem_type)
                    self._b.emit_op(ExtractField(
                        result=elem_local,
                        aggregate=value,
                        field_name=fname,
                        field_index=i,
                        result_type=elem_type,
                    ))
                    elem_operand = CopyOperand(Place(
                        base=elem_local,
                        projections=[],
                        type_id=elem_type,
                    ))
                    self.bind_pattern(sub_pattern, elem_operand)

        elif isinstance(pattern, HirEnumCasePattern):
            self._bind_enum_pattern(pattern, value)

        elif isinstance(pattern, HirOrPattern):
            # For or patterns, bind using first alternative
            # (all alternatives must bind same variables)
            if pattern.patterns:
                self.bind_pattern(pattern.patterns[0], value)

        elif isinstance(pattern, HirLiteralPattern):
            pass  # Literals don't bind anything

    def _bind_enum_pattern(self, pattern: HirPattern, scrutinee: Operand) -> None:
        """Bind variables in an enum pattern."""
        if isinstance(pattern, HirEnumCasePattern):
            for i, sub_pattern in enumerate(pattern.payload):
                # Extract payload element
                payload_type = getattr(sub_pattern, "type_id", None)
                if payload_type is None or self._b.type_table.is_error(payload_type):
                    payload_type = self._get_enum_payload_type(pattern.enum_type, pattern.case_name, i)
                if payload_type is not None:
                    payload_local = self._b.create_temp(payload_type)
                    self._b.emit_op(ExtractEnumPayload(
                        result=payload_local,
                        enum_val=scrutinee,
                        case_name=pattern.case_name,
                        payload_index=i,
                        result_type=payload_type,
                    ))
                    payload_operand = CopyOperand(Place(
                        base=payload_local,
                        projections=[],
                        type_id=payload_type,
                    ))
                    self.bind_pattern(sub_pattern, payload_operand)

    def _get_enum_payload_type(
        self, enum_type: TypeId, case_name: str, index: int
    ) -> Optional[TypeId]:
        """Get the type of an enum case payload element."""
        info = self._b.type_table.get_type(enum_type)
        if info is None or info.kind != TypeKind.ENUM:
            return None

        data = info.data
        if not isinstance(data, EnumTypeData):
            return None

        symbol = self._b.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.EnumDecl):
            return None

        for member in symbol.decl_node.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case in member.cases:
                    if case.name == case_name and index < len(case.payload):
                        _, type_node = case.payload[index]
                        return self._resolve_payload_type(type_node)

        return None

    def _resolve_payload_type(self, type_node: ast.Type) -> TypeId:
        """Resolve enum payload AST type nodes needed during MIR pattern binding."""
        return self._b.type_resolver.resolve_type_node(type_node)

    def _get_tuple_element_type(self, tuple_type: TypeId, index: int) -> Optional[TypeId]:
        """Get the type of an anonymous struct (tuple) element."""
        info = self._b.type_table.get_type(tuple_type)
        if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            fields = info.data.anon_fields or ()
            if index < len(fields):
                return fields[index][1]
        return None

    def _get_tuple_field_name(self, tuple_type: TypeId, index: int) -> str:
        """Get the field name for an anonymous struct element at the given index."""
        info = self._b.type_table.get_type(tuple_type)
        if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            fields = info.data.anon_fields or ()
            if index < len(fields):
                return fields[index][0]
        return str(index)
