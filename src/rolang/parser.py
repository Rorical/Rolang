"""Parser for RoLang using Lark."""

from __future__ import annotations
from pathlib import Path
from typing import Optional
from lark import Lark, Transformer, Token, Tree

from . import ast


class _ConformanceList(list):
    """Marker subclass so the extension transformer can distinguish a list of
    protocol conformances from the extended type or constraint list."""
    pass

# Load grammar from file
GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"


class RoLangTransformer(Transformer):
    """Transforms Lark parse tree to RoLang AST."""

    def _span_from_meta(self, meta) -> Optional[ast.Span]:
        """Extract source location from Lark meta."""
        if meta is None:
            return None
        try:
            return ast.Span(
                line=getattr(meta, 'line', 1),
                column=getattr(meta, 'column', 1),
                end_line=getattr(meta, 'end_line', 1),
                end_column=getattr(meta, 'end_column', 1),
            )
        except Exception:
            return None

    def _transform_tree(self, tree):
        """Transform a tree and attach span info to the resulting AST node."""
        result = super()._transform_tree(tree)
        if isinstance(result, ast.Node) and result.span is None:
            span = self._span_from_meta(tree.meta)
            if span:
                result.span = span
        return result

    def _ensure_transformed(self, item):
        """Ensure a tree item is transformed to an AST node."""
        if isinstance(item, Tree):
            return self.transform(item)
        return item

    def _ensure_all_transformed(self, items: list) -> list:
        """Ensure all items in a list are transformed."""
        return [self._ensure_transformed(i) for i in items]

    def visibility(self, items: list) -> str:
        return str(items[0]) if items else "internal"

    def __default__(self, data, children, meta):
        """Default handler for rules without explicit handlers."""
        # Transform any Tree children first
        transformed_children = [
            self.transform(c) if isinstance(c, Tree) else c
            for c in children
        ]
        # Filter out None values
        non_none = [c for c in transformed_children if c is not None]
        # If single child, unwrap it
        if len(non_none) == 1:
            return non_none[0]
        # Otherwise return as Tree for further processing
        return Tree(data, transformed_children, meta)

    # ========================= Program =========================
    def start(self, items: list) -> ast.Program:
        return ast.Program(items=[i for i in items if i is not None])

    def top_level_item(self, items: list) -> Optional[ast.TopLevelItem]:
        return items[0] if items else None

    # ========================= Imports =========================
    def import_decl(self, items: list) -> ast.ImportDecl:
        # Layout: [optional visibility, path-or-module, optional alias].
        # Lark fills `[visibility]` with None when no prefix is present,
        # so we strip any leading None first.
        vis_raw = items[0] if items and items[0] in ("pub", "private", "internal") else None
        visibility = vis_raw if vis_raw is not None else "internal"
        rest = items[1:] if vis_raw is not None else (
            items[1:] if items and items[0] is None else items
        )
        raw_or_path = rest[0]
        module_parts = []
        if isinstance(raw_or_path, list):
            # module_path: list of IDENT tokens → file path + module parts
            module_parts = [str(t) for t in raw_or_path]
            raw = "/".join(module_parts) + ".rl"
        else:
            # STRING token (includes quotes)
            raw = str(raw_or_path)
            if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                raw = raw[1:-1]
        alias_raw = rest[1] if len(rest) > 1 else None
        alias = str(alias_raw) if alias_raw is not None else None
        return ast.ImportDecl(
            path=raw,
            module=module_parts,
            alias=alias,
            visibility=visibility,
        )

    def module_path(self, items: list) -> list:
        return list(items)

    # ========================= Types =========================
    def type(self, items: list) -> ast.Type:
        return items[0]

    def function_type(self, items: list) -> ast.FunctionType:
        params = []
        return_type = None
        is_async = False
        throws = False

        for item in items:
            if isinstance(item, list):
                params = item
            elif isinstance(item, ast.Type):
                return_type = item
            elif item == "async":
                is_async = True
            elif item == "throws":
                throws = True

        return ast.FunctionType(
            params=params,
            return_type=return_type,
            is_async=is_async,
            throws=throws
        )

    def func_type_params(self, items: list) -> list[ast.Type]:
        return list(items)

    def primary_type(self, items: list) -> ast.Type:
        return items[0]

    def optional_type(self, items: list) -> ast.OptionalType:
        return ast.OptionalType(inner=items[0])

    def non_optional_type(self, items: list) -> ast.Type:
        return items[0]

    def builtin_type(self, items: list) -> ast.BuiltinType:
        # The builtin_type rule matches terminals directly
        if items:
            return ast.BuiltinType(name=str(items[0]))
        return ast.BuiltinType(name="")

    def type_name(self, items: list) -> ast.NamedType:
        parts = []
        generic_args = []
        for item in items:
            if isinstance(item, Token):
                parts.append(str(item))
            elif isinstance(item, list):
                generic_args = item

        name = parts[-1] if parts else ""
        module_path = parts[:-1] if len(parts) > 1 else []
        return ast.NamedType(name=name, module_path=module_path, generic_args=generic_args)

    def generic_args(self, items: list) -> list[ast.Type]:
        return list(items)

    def tuple_type(self, items: list) -> ast.TupleType:
        # First item + rest from tuple_type_elems
        elements = [items[0]] + items[1]
        return ast.TupleType(elements=elements)

    def tuple_type_elems(self, items: list) -> list[tuple]:
        return list(items)

    def labeled_tuple_type(self, items: list) -> tuple:
        return (str(items[0]), items[1])

    def unlabeled_tuple_type(self, items: list) -> tuple:
        return (None, items[0])

    def array_type(self, items: list) -> ast.ArrayType:
        return ast.ArrayType(element=items[0])

    def dict_type(self, items: list) -> ast.DictType:
        return ast.DictType(key=items[0], value=items[1])

    def any_type(self, items: list) -> ast.AnyType:
        return ast.AnyType(protocol=items[0])

    def pointer_type(self, items: list) -> ast.PointerType:
        return ast.PointerType()

    def paren_type(self, items: list) -> ast.Type:
        return items[0]

    def async_mark(self, items: list) -> str:
        return "async"

    def throws_mark(self, items: list) -> str:
        return "throws"

    def static_mark(self, items: list) -> str:
        return "static"

    def unsafe_mark(self, items: list) -> str:
        return "unsafe"

    def return_clause(self, items: list) -> ast.Type:
        return items[0]

    # ========================= Generics =========================
    def generic_params(self, items: list) -> list[ast.GenericParam]:
        return list(items)

    def generic_param(self, items: list) -> ast.GenericParam:
        name = str(items[0])
        bounds = items[1] if len(items) > 1 else []
        return ast.GenericParam(name=name, bounds=bounds)

    def type_bound_list(self, items: list) -> list[ast.NamedType]:
        return list(items)

    def type_constraints(self, items: list) -> list[ast.Constraint]:
        return list(items)

    def conformance_constraint(self, items: list) -> ast.Constraint:
        return ast.Constraint(subject=items[0], kind="conforms", bounds=items[1])

    def equality_constraint(self, items: list) -> ast.Constraint:
        return ast.Constraint(subject=items[0], kind="equals", equal_type=items[1])

    # ========================= Declarations =========================
    def struct_decl(self, items: list) -> ast.StructDecl:
        visibility = "internal"
        name = ""
        generic_params = []
        constraints = []
        members = []

        for item in items:
            if isinstance(item, str) and item in {"pub", "private", "internal"}:
                visibility = item
            elif isinstance(item, Token):
                name = str(item)
            elif isinstance(item, list):
                if item and isinstance(item[0], ast.GenericParam):
                    generic_params = item
                elif item and isinstance(item[0], ast.Constraint):
                    constraints = item
            elif isinstance(item, (ast.StructMember, ast.FuncDecl)):
                # FuncDecl is not a StructMember but can appear in struct bodies
                members.append(item)

        return ast.StructDecl(
            name=name,
            generic_params=generic_params,
            constraints=constraints,
            members=members,
            visibility=visibility
        )

    def struct_member(self, items: list) -> ast.StructMember:
        return items[0] if items else None

    def property_decl(self, items: list) -> ast.PropertyDecl:
        is_mutable = False
        name = ""
        type_ann = None
        initializer = None
        visibility = "internal"

        for item in items:
            if isinstance(item, Token):
                if str(item) in ("let", "var"):
                    is_mutable = str(item) == "var"
                else:
                    name = str(item)
            elif isinstance(item, str) and item in ("pub", "private", "internal"):
                visibility = item
            elif isinstance(item, ast.Type):
                type_ann = item
            elif isinstance(item, ast.Expr):
                initializer = item

        return ast.PropertyDecl(
            name=name,
            type_annotation=type_ann,
            initializer=initializer,
            is_mutable=is_mutable,
            visibility=visibility,
        )

    def func_decl(self, items: list) -> ast.FuncDecl:
        name = ""
        generic_params = []
        params = []
        return_type = None
        constraints = []
        body = None
        is_async = False
        throws = False
        is_static = False
        is_unsafe = False
        visibility = "internal"

        for item in items:
            if isinstance(item, str) and item in {"pub", "private", "internal"}:
                visibility = item
            elif isinstance(item, Token):
                name = str(item)
            elif isinstance(item, list):
                if item and isinstance(item[0], ast.GenericParam):
                    generic_params = item
                elif item and isinstance(item[0], ast.Param):
                    params = item
                elif item and isinstance(item[0], ast.Constraint):
                    constraints = item
            elif isinstance(item, ast.Type):
                return_type = item
            elif isinstance(item, ast.Block):
                body = item
            elif item == "async":
                is_async = True
            elif item == "throws":
                throws = True
            elif item == "static":
                is_static = True
            elif item == "unsafe":
                is_unsafe = True

        return ast.FuncDecl(
            name=name,
            generic_params=generic_params,
            params=params,
            return_type=return_type,
            constraints=constraints,
            body=body,
            is_async=is_async,
            throws=throws,
            is_static=is_static,
            is_unsafe=is_unsafe,
            visibility=visibility
        )

    def extern_func_decl(self, items: list) -> ast.ExternFuncDecl:
        abi = ""
        name = ""
        generic_params = []
        params = []
        return_type = None
        constraints = []
        is_async = False
        throws = False
        visibility = "internal"

        for item in items:
            if isinstance(item, str) and item in {"pub", "private", "internal"}:
                visibility = item
            elif isinstance(item, Token):
                tok = str(item)
                if tok.startswith('"'):
                    abi = tok.strip('"')
                else:
                    name = tok
            elif isinstance(item, str):
                if item.startswith('"'):
                    abi = item.strip('"')
            elif isinstance(item, list):
                if item and isinstance(item[0], ast.GenericParam):
                    generic_params = item
                elif item and isinstance(item[0], ast.Param):
                    params = item
                elif item and isinstance(item[0], ast.Constraint):
                    constraints = item
            elif isinstance(item, ast.Type):
                return_type = item
            elif item == "async":
                is_async = True
            elif item == "throws":
                throws = True

        return ast.ExternFuncDecl(
            abi=abi,
            name=name,
            generic_params=generic_params,
            params=params,
            return_type=return_type,
            constraints=constraints,
            is_async=is_async,
            throws=throws,
            visibility=visibility
        )

    def param_clause(self, items: list) -> list[ast.Param]:
        return items[0] if items else []

    def param_list(self, items: list) -> list[ast.Param]:
        return list(items)

    def labeled_param(self, items: list) -> ast.Param:
        external = str(items[0]) if items[0] != "_" else None
        internal = str(items[1])
        type_ann = items[2]
        default_val = items[3] if len(items) > 3 else None
        return ast.Param(
            external_name=external,
            internal_name=internal,
            type_annotation=type_ann,
            default_value=default_val
        )

    def unlabeled_param(self, items: list) -> ast.Param:
        internal = str(items[0])
        type_ann = items[1]
        default_val = items[2] if len(items) > 2 else None
        return ast.Param(
            external_name=None,
            internal_name=internal,
            type_annotation=type_ann,
            default_value=default_val
        )

    def enum_decl(self, items: list) -> ast.EnumDecl:
        visibility = "internal"
        name = ""
        generic_params = []
        constraints = []
        members = []

        for item in items:
            if isinstance(item, str) and item in {"pub", "private", "internal"}:
                visibility = item
            elif isinstance(item, Token):
                name = str(item)
            elif isinstance(item, list):
                if item and isinstance(item[0], ast.GenericParam):
                    generic_params = item
                elif item and isinstance(item[0], ast.Constraint):
                    constraints = item
            elif isinstance(item, (ast.EnumCaseDecl, ast.FuncDecl)):
                members.append(item)

        return ast.EnumDecl(
            name=name,
            generic_params=generic_params,
            constraints=constraints,
            members=members,
            visibility=visibility
        )

    def enum_member(self, items: list) -> ast.EnumMember:
        return items[0] if items else None

    def enum_case_decl(self, items: list) -> ast.EnumCaseDecl:
        cases = [c for c in items if isinstance(c, ast.EnumCaseDef)]
        return ast.EnumCaseDecl(cases=cases)

    def enum_case(self, items: list) -> ast.EnumCaseDef:
        name = str(items[0])
        payload = items[1] if len(items) > 1 else []
        return ast.EnumCaseDef(name=name, payload=payload or [])

    def enum_case_payload(self, items: list) -> list[tuple]:
        return list(items)

    def labeled_payload(self, items: list) -> tuple:
        return (str(items[0]), items[1])

    def unlabeled_payload(self, items: list) -> tuple:
        return (None, items[0])

    def protocol_decl(self, items: list) -> ast.ProtocolDecl:
        visibility = "internal"
        name = ""
        generic_params = []
        constraints = []
        members = []

        for item in items:
            if isinstance(item, str) and item in {"pub", "private", "internal"}:
                visibility = item
            elif isinstance(item, Token):
                name = str(item)
            elif isinstance(item, list):
                if item and isinstance(item[0], ast.GenericParam):
                    generic_params = item
                elif item and isinstance(item[0], ast.Constraint):
                    constraints = item
            elif isinstance(item, ast.ProtocolMember):
                members.append(item)

        return ast.ProtocolDecl(
            name=name,
            generic_params=generic_params,
            constraints=constraints,
            members=members,
            visibility=visibility
        )

    def protocol_member(self, items: list) -> ast.ProtocolMember:
        return items[0] if items else None

    def protocol_func_req(self, items: list) -> ast.ProtocolFuncReq:
        name = ""
        generic_params = []
        params = []
        return_type = None
        is_async = False
        throws = False

        for item in items:
            if isinstance(item, Token):
                name = str(item)
            elif isinstance(item, list):
                if item and isinstance(item[0], ast.GenericParam):
                    generic_params = item
                elif item and isinstance(item[0], ast.Param):
                    params = item
            elif isinstance(item, ast.Type):
                return_type = item
            elif item == "async":
                is_async = True
            elif item == "throws":
                throws = True

        return ast.ProtocolFuncReq(
            name=name,
            generic_params=generic_params,
            params=params,
            return_type=return_type,
            is_async=is_async,
            throws=throws
        )

    def protocol_prop_req(self, items: list) -> ast.ProtocolPropReq:
        is_mutable = False
        name = ""
        type_ann = None
        has_getter = False
        has_setter = False

        for item in items:
            if isinstance(item, Token):
                tok = str(item)
                if tok == "var":
                    is_mutable = True
                elif tok == "let":
                    is_mutable = False
                else:
                    name = tok
            elif isinstance(item, ast.Type):
                type_ann = item
            elif isinstance(item, tuple):
                has_getter, has_setter = item

        return ast.ProtocolPropReq(
            name=name,
            type_annotation=type_ann,
            # Lark does not pass string literals like "var" through by default
            # in this grammar, so a set accessor is the semantic source of
            # mutability for protocol requirements.
            is_mutable=is_mutable or has_setter,
            has_getter=has_getter,
            has_setter=has_setter
        )

    def accessor_req(self, items: list) -> tuple[bool, bool]:
        kinds = [str(i) for i in items]
        return ("get" in kinds, "set" in kinds)

    def accessor_kind(self, items: list) -> str:
        return str(items[0])

    def associated_type_decl(self, items: list) -> ast.AssociatedTypeDecl:
        name = str(items[0])
        constraints = items[1] if len(items) > 1 and isinstance(items[1], list) else []
        return ast.AssociatedTypeDecl(name=name, constraints=constraints)

    def extension_decl(self, items: list) -> ast.ExtensionDecl:
        visibility = "internal"
        extended_type = None
        conformances: list[ast.NamedType] = []
        constraints = []
        members = []

        for item in items:
            if isinstance(item, str) and item in {"pub", "private", "internal"}:
                visibility = item
            elif isinstance(item, _ConformanceList):
                conformances = list(item)
            elif isinstance(item, ast.NamedType):
                extended_type = item
            elif isinstance(item, list) and item and isinstance(item[0], ast.Constraint):
                constraints = item
            elif isinstance(item, (ast.StructMember, ast.FuncDecl)):
                # FuncDecl is not a StructMember but can appear in extension bodies
                members.append(item)

        return ast.ExtensionDecl(
            extended_type=extended_type,
            conformances=conformances,
            constraints=constraints,
            members=members,
            visibility=visibility
        )

    def conformance_clause(self, items: list) -> "_ConformanceList":
        return _ConformanceList(item for item in items if isinstance(item, ast.NamedType))

    def extension_member(self, items: list) -> ast.StructMember:
        return self.struct_member(items)

    # ========================= Statements =========================
    def block(self, items: list) -> ast.Block:
        statements = []
        for item in items:
            if item is None:
                continue
            # If it's an expression (from trailing_expr), convert to implicit
            # return. Flagged `implicit=True` so loop bodies can demote it to a
            # discarded expression-statement (a trailing expression in a
            # while/for body must not return from the enclosing function).
            if isinstance(item, ast.Expr):
                statements.append(ast.ReturnStmt(value=item, implicit=True))
            else:
                statements.append(item)
        return ast.Block(statements=statements)

    def trailing_expr(self, items: list) -> ast.Expr:
        return items[0] if items else None

    def stmt(self, items: list) -> ast.Stmt:
        return items[0] if items else None

    def simple_stmt(self, items: list) -> ast.Stmt:
        return items[0]

    def var_decl(self, items: list) -> ast.VarDecl:
        is_mutable = False
        pattern = None
        type_ann = None
        initializer = None

        for item in items:
            if item is None:
                continue
            item = self._ensure_transformed(item)
            if isinstance(item, Token):
                if str(item) in ("let", "var"):
                    is_mutable = str(item) == "var"
            elif isinstance(item, ast.Pattern):
                pattern = item
            elif isinstance(item, ast.Type):
                type_ann = item
            elif isinstance(item, ast.Expr):
                initializer = item

        return ast.VarDecl(
            pattern=pattern,
            type_annotation=type_ann,
            initializer=initializer,
            is_mutable=is_mutable
        )

    def assign_stmt(self, items: list) -> ast.Assignment:
        target = items[0]
        op = str(items[1])
        value = items[2]
        return ast.Assignment(target=target, op=op, value=value)

    def assign_op(self, items: list) -> str:
        return str(items[0])

    def expr_stmt(self, items: list) -> ast.ExprStmt:
        return ast.ExprStmt(expr=items[0])

    def return_stmt(self, items: list) -> ast.ReturnStmt:
        value = next((item for item in items if isinstance(item, ast.Expr)), None)
        return ast.ReturnStmt(value=value)

    def break_stmt(self, items: list) -> ast.BreakStmt:
        return ast.BreakStmt()

    def continue_stmt(self, items: list) -> ast.ContinueStmt:
        return ast.ContinueStmt()

    def defer_stmt(self, items: list) -> ast.DeferStmt:
        return ast.DeferStmt(body=items[0])

    def unsafe_stmt(self, items: list) -> ast.Block:
        block = items[0]
        block.is_unsafe = True
        return block

    def if_stmt(self, items: list) -> ast.IfStmt:
        condition = items[0]
        then_block = items[1]
        else_block = items[2] if len(items) > 2 else None
        return ast.IfStmt(condition=condition, then_block=then_block, else_block=else_block)

    def if_let_cond(self, items: list) -> tuple:
        return (items[0], items[1])

    def if_expr_cond(self, items: list) -> ast.Expr:
        return items[0]

    def guard_stmt(self, items: list) -> ast.GuardStmt:
        return ast.GuardStmt(condition=items[0], else_block=items[1])

    def while_stmt(self, items: list) -> ast.WhileStmt:
        return ast.WhileStmt(condition=items[0], body=items[1])

    def for_stmt(self, items: list) -> ast.ForStmt:
        return ast.ForStmt(pattern=items[0], iterable=items[1], body=items[2])

    def switch_stmt(self, items: list) -> ast.SwitchStmt:
        value = items[0]
        cases = [c for c in items[1:] if isinstance(c, ast.SwitchCase)]
        return ast.SwitchStmt(value=value, cases=cases)

    def case_branch(self, items: list) -> ast.SwitchCase:
        patterns = items[0]
        body = [s for s in items[1:] if isinstance(s, ast.Stmt)]
        return ast.SwitchCase(patterns=patterns, body=body, is_default=False)

    def default_branch(self, items: list) -> ast.SwitchCase:
        body = [s for s in items if isinstance(s, ast.Stmt)]
        return ast.SwitchCase(patterns=[], body=body, is_default=True)

    def case_item_list(self, items: list) -> list[tuple]:
        return list(items)

    def case_item(self, items: list) -> tuple:
        pattern = items[0]
        guard = items[1] if len(items) > 1 else None
        return (pattern, guard)

    # ========================= Patterns =========================
    def or_pattern(self, items: list) -> ast.Pattern:
        if len(items) == 1:
            return items[0]
        return ast.OrPattern(patterns=list(items))

    def wildcard_pattern(self, items: list) -> ast.WildcardPattern:
        return ast.WildcardPattern()

    def literal_pattern(self, items: list) -> ast.LiteralPattern:
        return ast.LiteralPattern(value=items[0])

    def let_pattern(self, items: list) -> ast.IdentifierPattern:
        return ast.IdentifierPattern(name=str(items[0]), binding="let")

    def var_pattern(self, items: list) -> ast.IdentifierPattern:
        return ast.IdentifierPattern(name=str(items[0]), binding="var")

    def ident_pattern(self, items: list) -> ast.IdentifierPattern:
        return ast.IdentifierPattern(name=str(items[0]), binding=None)

    def tuple_pattern(self, items: list) -> ast.TuplePattern:
        patterns = items[0] if items else []
        return ast.TuplePattern(elements=[(None, p) for p in patterns])

    def enum_case_pattern(self, items: list) -> ast.EnumCasePattern:
        name = str(items[0])
        payload = items[1] if len(items) > 1 else []
        return ast.EnumCasePattern(case_name=name, payload=payload or [])

    def paren_pattern(self, items: list) -> ast.Pattern:
        return items[0]

    def pattern_list(self, items: list) -> list[ast.Pattern]:
        return list(items)

    # ========================= Expressions =========================
    def expr(self, items: list) -> ast.Expr:
        return items[0]

    def ternary_expr(self, items: list) -> ast.Expr:
        items = self._ensure_all_transformed(items)
        # Filter out None values from optional parts
        non_none = [i for i in items if i is not None and not isinstance(i, Token)]
        if len(non_none) == 1:
            return non_none[0]
        elif len(non_none) >= 3:
            return ast.TernaryOp(condition=non_none[0], then_expr=non_none[1], else_expr=non_none[2])
        return non_none[0] if non_none else ast.Literal(value=None, kind="nil")

    def coalesce_expr(self, items: list) -> ast.Expr:
        exprs = [item for item in items if not isinstance(item, Token)]
        result = exprs[0]
        for item in exprs[1:]:
            result = ast.BinaryOp(left=result, op="??", right=item)
        return result

    def logical_or_expr(self, items: list) -> ast.Expr:
        result = items[0]
        for item in items[1:]:
            result = ast.BinaryOp(left=result, op="||", right=item)
        return result

    def logical_and_expr(self, items: list) -> ast.Expr:
        result = items[0]
        for item in items[1:]:
            result = ast.BinaryOp(left=result, op="&&", right=item)
        return result

    def compare_expr(self, items: list) -> ast.Expr:
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            item = items[i]
            if isinstance(item, tuple):
                # cast_suffix or type_check_suffix
                kind, target = item
                if kind == "cast":
                    result = ast.Cast(expr=result, target_type=target, kind="safe")
                elif kind == "cast_optional":
                    result = ast.Cast(expr=result, target_type=target, kind="optional")
                elif kind == "cast_forced":
                    result = ast.Cast(expr=result, target_type=target, kind="forced")
                elif kind == "type_check":
                    result = ast.TypeCheck(expr=result, checked_type=target)
                i += 1
            else:
                # compare_op + bit_or_expr
                op = str(item)
                right = items[i + 1]
                result = ast.BinaryOp(left=result, op=op, right=right)
                i += 2
        return result

    def cast_safe_suffix(self, items: list) -> tuple:
        return ("cast", items[0])

    def cast_suffix(self, items: list) -> tuple:
        return ("cast", items[0])

    def cast_optional_suffix(self, items: list) -> tuple:
        # First item is the AS_OPTIONAL token, second is the type. Lark
        # generally elides keyword string-literals so the token shows up
        # as a Token instance; defensively skip it if present.
        target = items[-1]
        return ("cast_optional", target)

    def cast_forced_suffix(self, items: list) -> tuple:
        target = items[-1]
        return ("cast_forced", target)

    def type_check_suffix(self, items: list) -> tuple:
        return ("type_check", items[0])

    def compare_op(self, items: list) -> str:
        return str(items[0])

    def bit_or_expr(self, items: list) -> ast.Expr:
        result = items[0]
        for item in items[1:]:
            result = ast.BinaryOp(left=result, op="|", right=item)
        return result

    def bit_xor_expr(self, items: list) -> ast.Expr:
        result = items[0]
        for item in items[1:]:
            result = ast.BinaryOp(left=result, op="^", right=item)
        return result

    def bit_and_expr(self, items: list) -> ast.Expr:
        result = items[0]
        for item in items[1:]:
            result = ast.BinaryOp(left=result, op="&", right=item)
        return result

    def shift_expr(self, items: list) -> ast.Expr:
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = ast.BinaryOp(left=result, op=op, right=right)
            i += 2
        return result

    def shift_op(self, items: list) -> str:
        return str(items[0])

    def additive_expr(self, items: list) -> ast.Expr:
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = ast.BinaryOp(left=result, op=op, right=right)
            i += 2
        return result

    def add_op(self, items: list) -> str:
        return str(items[0])

    def multiplicative_expr(self, items: list) -> ast.Expr:
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = ast.BinaryOp(left=result, op=op, right=right)
            i += 2
        return result

    def mul_op(self, items: list) -> str:
        return str(items[0])

    def prefix_not_expr(self, items: list) -> ast.UnaryOp:
        return ast.UnaryOp(op="!", operand=items[0])

    def prefix_neg_expr(self, items: list) -> ast.UnaryOp:
        return ast.UnaryOp(op="-", operand=items[0])

    def prefix_pos_expr(self, items: list) -> ast.UnaryOp:
        return ast.UnaryOp(op="+", operand=items[0])

    def prefix_bitnot_expr(self, items: list) -> ast.UnaryOp:
        return ast.UnaryOp(op="~", operand=items[0])

    def prefix_await_expr(self, items: list) -> ast.UnaryOp:
        return ast.UnaryOp(op="await", operand=items[0])

    def prefix_try_expr(self, items: list) -> ast.UnaryOp:
        return ast.UnaryOp(op="try", operand=items[0])

    def postfix_expr(self, items: list) -> ast.Expr:
        result = items[0]
        for suffix in items[1:]:
            if isinstance(suffix, tuple):
                kind, data = suffix
                if kind == "call":
                    result = ast.Call(callee=result, arguments=data)
                elif kind == "member":
                    result = ast.MemberAccess(object=result, member=data)
                elif kind == "optional_chain":
                    member, chain_suffix = data
                    result = ast.OptionalChain(object=result, member=member, suffix=chain_suffix)
                elif kind == "subscript":
                    result = ast.Subscript(object=result, indices=data)
                elif kind == "try":
                    result = ast.TryExpr(value=result)
        return result

    def call_suffix(self, items: list) -> tuple:
        args = items[0] if items and items[0] is not None else []
        return ("call", args)

    def argument_list(self, items: list) -> list[ast.Argument]:
        return list(items)

    def labeled_arg(self, items: list) -> ast.Argument:
        return ast.Argument(label=str(items[0]), value=items[1])

    def unlabeled_arg(self, items: list) -> ast.Argument:
        return ast.Argument(label=None, value=items[0])

    def member_suffix(self, items: list) -> tuple:
        return ("member", str(items[0]))

    def tuple_member_suffix(self, items: list) -> tuple:
        # `.N` form for tuple element access (Swift-style). Stored as a string
        # so it flows through the existing MemberAccess pipeline; the MIR
        # builder recognizes the integer-literal form on tuple receivers.
        return ("member", str(items[0]))

    def optional_chain_suffix(self, items: list) -> tuple:
        offset = 1 if items and isinstance(items[0], Token) and items[0].type == "OPTIONAL_CHAIN" else 0
        member = str(items[offset])
        suffix = items[offset + 1] if len(items) > offset + 1 else None
        if suffix:
            kind, data = suffix
            return ("optional_chain", (member, data))
        return ("optional_chain", (member, None))

    def subscript_suffix(self, items: list) -> tuple:
        indices = items[0] if items else []
        return ("subscript", indices)

    def try_suffix(self, items: list) -> tuple:
        return ("try", None)

    def expr_list(self, items: list) -> list[ast.Expr]:
        return list(items)

    def lvalue(self, items: list) -> ast.Expr:
        result = ast.Identifier(name=str(items[0]))
        for suffix in items[1:]:
            kind, data = suffix
            if kind == "member":
                result = ast.MemberAccess(object=result, member=data)
            elif kind == "subscript":
                result = ast.Subscript(object=result, indices=data)
        return result

    def lvalue_member(self, items: list) -> tuple:
        return ("member", str(items[0]))

    def lvalue_tuple_member(self, items: list) -> tuple:
        # `.N` on the lvalue side — stored as a stringified int so MIR resolves
        # it through the same tuple-element lookup as the rvalue form.
        return ("member", str(items[0]))

    def lvalue_subscript(self, items: list) -> tuple:
        return ("subscript", items[0])

    def ident_expr(self, items: list) -> ast.Identifier:
        return ast.Identifier(name=str(items[0]))

    def type_member_expr(self, items: list) -> ast.MemberAccess:
        return ast.MemberAccess(
            object=ast.TypeReference(type_name=items[0]),
            member=str(items[1]),
        )

    def generic_type_name(self, items: list) -> ast.NamedType:
        return self.type_name(items)

    def size_of_expr(self, items: list) -> ast.SizeOfExpr:
        type_arg = None
        for item in items:
            if isinstance(item, ast.Type):
                type_arg = item
                break
        return ast.SizeOfExpr(type_arg=type_arg)

    def align_of_expr(self, items: list) -> ast.AlignOfExpr:
        type_arg = None
        for item in items:
            if isinstance(item, ast.Type):
                type_arg = item
                break
        return ast.AlignOfExpr(type_arg=type_arg)

    def drop_of_expr(self, items: list) -> ast.DropOfExpr:
        type_arg = None
        for item in items:
            if isinstance(item, ast.Type):
                type_arg = item
                break
        return ast.DropOfExpr(type_arg=type_arg)

    def clone_of_expr(self, items: list) -> ast.CloneOfExpr:
        type_arg = None
        for item in items:
            if isinstance(item, ast.Type):
                type_arg = item
                break
        return ast.CloneOfExpr(type_arg=type_arg)

    def paren_expr(self, items: list) -> ast.Expr:
        return items[0]

    def tuple_expr(self, items: list) -> ast.TupleExpr:
        return ast.TupleExpr(elements=items[0])

    def tuple_expr_elems(self, items: list) -> list[tuple]:
        return list(items)

    def labeled_tuple_elem(self, items: list) -> tuple:
        return (str(items[0]), items[1])

    def unlabeled_tuple_elem(self, items: list) -> tuple:
        return (None, items[0])

    def array_literal(self, items: list) -> ast.ArrayLiteral:
        elements = items[0] if items else []
        return ast.ArrayLiteral(elements=elements if isinstance(elements, list) else [])

    def empty_dict_literal(self, items: list) -> ast.DictLiteral:
        return ast.DictLiteral(entries=[])

    def dict_literal(self, items: list) -> ast.DictLiteral:
        entries = [(e[0], e[1]) for e in items if isinstance(e, tuple)]
        return ast.DictLiteral(entries=entries)

    def dict_entry(self, items: list) -> tuple:
        return (items[0], items[1])

    def lambda_expr(self, items: list) -> ast.Lambda:
        params = []
        body = []
        for item in items:
            if isinstance(item, list) and item and isinstance(item[0], tuple):
                params = item
            elif isinstance(item, ast.Stmt):
                body.append(item)
        return ast.Lambda(params=params, body=body)

    def lambda_params(self, items: list) -> list[tuple]:
        return list(items)

    def lambda_param(self, items: list) -> tuple:
        pattern = items[0]
        type_ann = items[1] if len(items) > 1 else None
        return (pattern, type_ann)

    def struct_literal(self, items: list) -> ast.StructLiteral:
        type_name = items[0] if items else None
        args = items[1] if len(items) > 1 and isinstance(items[1], list) else []
        return ast.StructLiteral(type_name=type_name, arguments=args)

    def struct_literal_fields(self, items: list) -> list[ast.Argument]:
        return list(items)

    # ========================= Literals =========================
    def int_literal(self, items: list) -> ast.Literal:
        value = str(items[0]).replace("_", "")
        if value.startswith("0x") or value.startswith("0X"):
            return ast.Literal(value=int(value, 16), kind="int")
        elif value.startswith("0b") or value.startswith("0B"):
            return ast.Literal(value=int(value, 2), kind="int")
        elif value.startswith("0o") or value.startswith("0O"):
            return ast.Literal(value=int(value, 8), kind="int")
        return ast.Literal(value=int(value), kind="int")

    def float_literal(self, items: list) -> ast.Literal:
        value = str(items[0]).replace("_", "")
        return ast.Literal(value=float(value), kind="float")

    def bool_literal(self, items: list) -> ast.Literal:
        return ast.Literal(value=str(items[0]) == "true", kind="bool")

    def string_literal(self, items: list) -> ast.Literal:
        token = items[0]
        raw = token.value if hasattr(token, 'value') else str(token)
        value = raw[1:-1]  # Remove surrounding quotes
        value = self._unescape_string(value)
        return ast.Literal(value=value, kind="string")

    def char_literal(self, items: list) -> ast.Literal:
        token = items[0]
        raw = token.value if hasattr(token, 'value') else str(token)
        value = self._unescape_string(raw[1:-1])
        if len(value) != 1:
            raise ValueError("char literal must contain exactly one character")
        return ast.Literal(value=ord(value), kind="char")

    def _unescape_string(self, s: str) -> str:
        """Process escape sequences in a string literal."""
        result = []
        i = 0
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                next_char = s[i + 1]
                if next_char == 'n':
                    result.append('\n')
                elif next_char == 't':
                    result.append('\t')
                elif next_char == 'r':
                    result.append('\r')
                elif next_char == '\\':
                    result.append('\\')
                elif next_char == '"':
                    result.append('"')
                elif next_char == '0':
                    result.append('\0')
                else:
                    result.append(s[i + 1])
                i += 2
            else:
                result.append(s[i])
                i += 1
        return ''.join(result)

    def nil_literal(self, items: list) -> ast.Literal:
        return ast.Literal(value=None, kind="nil")

    def type_id_expr(self, items: list) -> ast.TypeIdExpr:
        type_arg = None
        for item in items:
            if isinstance(item, ast.Type):
                type_arg = item
                break
        return ast.TypeIdExpr(type_arg=type_arg)

    def literal(self, items: list) -> ast.Literal:
        return items[0]


# Create the parser
def _create_parser() -> Lark:
    """Create and return the Lark parser."""
    grammar_text = GRAMMAR_PATH.read_text()
    return Lark(
        grammar_text,
        start="start",
        parser="earley",
        ambiguity="resolve",
        propagate_positions=True,
    )


# Lazy initialization
_parser: Optional[Lark] = None


def get_parser() -> Lark:
    """Get or create the parser singleton."""
    global _parser
    if _parser is None:
        _parser = _create_parser()
    return _parser


def parse(source: str) -> ast.Program:
    """Parse RoLang source code and return an AST.

    Args:
        source: The RoLang source code to parse.

    Returns:
        The root Program AST node.

    Raises:
        lark.exceptions.LarkError: If parsing fails.
    """
    parser = get_parser()
    tree = parser.parse(source)
    transformer = RoLangTransformer()
    return transformer.transform(tree)


def parse_file(path: Union[str, Path]) -> ast.Program:
    """Parse a RoLang source file and return an AST.

    Args:
        path: Path to the RoLang source file.

    Returns:
        The root Program AST node.
    """
    source = Path(path).read_text()
    return parse(source)
