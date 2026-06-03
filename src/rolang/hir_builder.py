"""HIR Builder - Transforms AST to HIR with desugaring.

This module converts the type-checked AST into HIR, desugaring syntactic
sugar constructs (optional chaining, nil coalescing) along the way.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple, Union

from . import ast
from .hir import (
    HirProgram, HirItem, HirFunction, HirExternFunc,
    HirParam, HirStruct, HirField, HirEnum, HirEnumCase,
    HirProtocol, HirFuncRequirement, HirPropRequirement, HirExtension,
    HirStmt, HirBlock, HirVarDecl, HirAssign, HirExprStmt, HirReturn,
    HirBreak, HirContinue, HirIf, HirIfLet, HirGuard,
    HirWhile, HirFor, HirSwitchCase, HirSwitch, HirDefer,
    HirExpr, HirLiteral, HirVar, HirBinaryOp, HirUnaryOp, HirTernary,
    HirCall, HirMethodCall, HirFieldAccess, HirSubscript,
    HirTuple, HirArray, HirDict, HirLambda, HirClone,
    HirStructInit, HirEnumConstruct, HirCast, HirTypeCheck, HirTryExpr,
    HirOptionalSome, HirOptionalNone, HirOptionalMatch,
    HirPattern, HirWildcardPattern, HirBindingPattern, HirLiteralPattern,
    HirTuplePattern, HirEnumCasePattern, HirOrPattern,
)
from .types import (
    TypeId, TypeTable, TypeKind,
    StructTypeData, EnumTypeData,
    FunctionTypeData,
    OptionalTypeData, TypeVariableData,
)
from .symbols import (
    SymbolTable, Symbol, SymbolId, SymbolKind, Namespace,
    ResolutionResult,
)
from .checker_core import TypeCheckResult, CalleeKind
from .operators import NIL_COALESCING_OPERATOR, OPERATOR_TO_METHOD
from .type_resolver import TypeResolver


# ========================= Build Result =========================

@dataclass
class HirBuildResult:
    """Result of HIR building."""
    program: HirProgram
    type_table: TypeTable
    symbol_table: SymbolTable
    errors: List[str]

    def has_errors(self) -> bool:
        return len(self.errors) > 0


# ========================= HIR Builder =========================

class HirBuilder:
    """Builds HIR from type-checked AST, performing desugaring."""

    def __init__(
        self,
        type_result: TypeCheckResult,
        symbol_table: SymbolTable,
        node_symbols: Dict[int, SymbolId],
    ) -> None:
        self.type_table = type_result.type_table
        self.expr_types = type_result.expr_types
        self.call_targets = type_result.call_targets
        self.operator_targets = getattr(type_result, 'operator_targets', {})
        self.member_method_symbols = getattr(type_result, 'member_method_symbols', {})
        self.symbol_table = symbol_table
        self.node_symbols = node_symbols
        self.type_resolver = TypeResolver(
            self.type_table,
            self.symbol_table,
            self.node_symbols,
        )

        self.errors: List[str] = []
        self._temp_counter = 0

    def build(self, program: ast.Program) -> HirBuildResult:
        """Build HIR from AST program."""
        items: List[HirItem] = []

        for item in program.items:
            hir_item = self._build_item(item)
            if hir_item is not None:
                items.append(hir_item)

        return HirBuildResult(
            program=HirProgram(items=items),
            type_table=self.type_table,
            symbol_table=self.symbol_table,
            errors=self.errors,
        )

    # ========================= Error Handling =========================

    def _error(self, message: str) -> None:
        """Record a build error."""
        self.errors.append(message)

    # ========================= Temporary Generation =========================

    def _fresh_temp(self, prefix: str = "__tmp") -> str:
        """Generate a fresh temporary variable name."""
        self._temp_counter += 1
        return f"{prefix}_{self._temp_counter}"

    def _create_temp_symbol(self, name: str, type_id: TypeId) -> SymbolId:
        """Create a temporary symbol in the symbol table."""
        symbol = self.symbol_table.create_symbol(
            name=name,
            kind=SymbolKind.VARIABLE,
            namespace=Namespace.VALUE,
            is_mutable=False,
        )
        return symbol.id

    # ========================= Type Helpers =========================

    def _get_expr_type(self, expr: ast.Expr) -> TypeId:
        """Get the type of an AST expression."""
        type_id = self.expr_types.get(id(expr))
        if type_id is None:
            return self.type_table.error_type
        return type_id

    def _get_symbol(self, node: ast.Node) -> Optional[SymbolId]:
        """Get the symbol ID for an AST node."""
        return self.node_symbols.get(id(node))

    def _get_optional_inner(self, type_id: TypeId) -> Optional[TypeId]:
        """Get the inner type of an optional, or None."""
        return self.type_table.get_optional_inner(type_id)

    # ========================= Item Building =========================

    def _build_item(self, item: ast.TopLevelItem) -> Optional[HirItem]:
        """Build an HIR item from an AST item."""
        if isinstance(item, ast.FuncDecl):
            return self._build_func_decl(item)
        elif isinstance(item, ast.ExternFuncDecl):
            return self._build_extern_func(item)
        elif isinstance(item, ast.StructDecl):
            return self._build_struct_decl(item)
        elif isinstance(item, ast.EnumDecl):
            return self._build_enum_decl(item)
        elif isinstance(item, ast.ProtocolDecl):
            return self._build_protocol_decl(item)
        elif isinstance(item, ast.ExtensionDecl):
            return self._build_extension_decl(item)
        elif isinstance(item, ast.ImportDecl):
            return None
        elif isinstance(item, ast.TypeAliasDecl):
            return None
        elif isinstance(item, ast.Stmt):
            self.errors.append(
                f"Top-level statements are not supported"
            )
            return None
        return None

    def _build_func_decl(
        self,
        func: ast.FuncDecl,
        is_method: bool = False,
    ) -> HirFunction:
        """Build an HIR function from an AST function declaration."""
        symbol_id = self._get_symbol(func)
        if symbol_id is None:
            # Create a placeholder symbol
            symbol = self.symbol_table.create_symbol(
                name=func.name,
                kind=SymbolKind.FUNCTION,
                namespace=Namespace.VALUE,
            )
            symbol_id = symbol.id

        # Build parameters
        params = [self._build_param(p) for p in func.params]

        # Determine return type
        return_type = self.type_table.void_type
        if func.return_type:
            return_type = self._resolve_type_node(func.return_type)

        # Build body
        body = None
        if func.body:
            body = self._build_block(func.body)

        return HirFunction(
            name=func.name,
            symbol_id=symbol_id,
            params=params,
            return_type=return_type,
            body=body,
            is_async=func.is_async,
            is_method=is_method,
            is_static=func.is_static,
        )

    def _build_extern_func(self, func: ast.ExternFuncDecl) -> HirExternFunc:
        """Build an HIR extern function."""
        symbol_id = self._get_symbol(func)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=func.name,
                kind=SymbolKind.EXTERN_FUNC,
                namespace=Namespace.VALUE,
            )
            symbol_id = symbol.id

        params = [self._build_param(p) for p in func.params]

        return_type = self.type_table.void_type
        if func.return_type:
            return_type = self._resolve_type_node(func.return_type)

        return HirExternFunc(
            name=func.name,
            symbol_id=symbol_id,
            abi=func.abi,
            params=params,
            return_type=return_type,
        )

    def _build_param(self, param: ast.Param) -> HirParam:
        """Build an HIR parameter."""
        symbol_id = self._get_symbol(param)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=param.internal_name,
                kind=SymbolKind.PARAMETER,
                namespace=Namespace.VALUE,
            )
            symbol_id = symbol.id

        type_id = self.type_table.error_type
        if param.type_annotation:
            type_id = self._resolve_type_node(param.type_annotation)

        return HirParam(
            name=param.internal_name,
            symbol_id=symbol_id,
            type_id=type_id,
            external_name=param.external_name,
            has_default=param.default_value is not None,
        )

    def _build_struct_decl(self, struct: ast.StructDecl) -> HirStruct:
        """Build an HIR struct."""
        symbol_id = self._get_symbol(struct)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=struct.name,
                kind=SymbolKind.STRUCT,
                namespace=Namespace.TYPE,
            )
            symbol_id = symbol.id

        fields: List[HirField] = []
        methods: List[HirFunction] = []

        # `__release__` (destructor) and `__gc_trace__` (cycle-collector
        # trace hook) are just regular methods at this stage — codegen
        # picks them up by name when emitting the type descriptor.
        for member in struct.members:
            if isinstance(member, ast.PropertyDecl):
                field = self._build_field(member)
                fields.append(field)
            elif isinstance(member, ast.FuncDecl):
                method = self._build_func_decl(member, is_method=True)
                methods.append(method)

        return HirStruct(
            name=struct.name,
            symbol_id=symbol_id,
            fields=fields,
            methods=methods,
        )

    def _build_field(self, prop: ast.PropertyDecl) -> HirField:
        """Build an HIR field from a property declaration."""
        symbol_id = self._get_symbol(prop)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=prop.name,
                kind=SymbolKind.VARIABLE,
                namespace=Namespace.VALUE,
            )
            symbol_id = symbol.id

        type_id = self.type_table.error_type
        if prop.type_annotation:
            type_id = self._resolve_type_node(prop.type_annotation)

        default_value = None
        if prop.initializer:
            default_value = self._build_expr(prop.initializer)

        return HirField(
            name=prop.name,
            symbol_id=symbol_id,
            type_id=type_id,
            is_mutable=prop.is_mutable,
            default_value=default_value,
        )

    def _build_enum_decl(self, enum: ast.EnumDecl) -> HirEnum:
        """Build an HIR enum."""
        symbol_id = self._get_symbol(enum)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=enum.name,
                kind=SymbolKind.ENUM,
                namespace=Namespace.TYPE,
            )
            symbol_id = symbol.id

        cases: List[HirEnumCase] = []
        methods: List[HirFunction] = []

        for member in enum.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case_def in member.cases:
                    case = self._build_enum_case(case_def)
                    cases.append(case)
            elif isinstance(member, ast.FuncDecl):
                method = self._build_func_decl(member, is_method=True)
                methods.append(method)

        return HirEnum(
            name=enum.name,
            symbol_id=symbol_id,
            cases=cases,
            methods=methods,
        )

    def _build_enum_case(self, case_def: ast.EnumCaseDef) -> HirEnumCase:
        """Build an HIR enum case."""
        # Try to find existing symbol or create new one
        case_symbol: Optional[Symbol] = self._get_symbol(case_def)
        if case_symbol is None:
            case_symbol = self.symbol_table.create_symbol(
                name=case_def.name,
                kind=SymbolKind.ENUM_CASE,
                namespace=Namespace.VALUE,
            )

        payload: List[Tuple[Optional[str], TypeId]] = []
        for label, type_node in case_def.payload:
            type_id = self._resolve_type_node(type_node)
            payload.append((label, type_id))

        return HirEnumCase(
            name=case_def.name,
            symbol_id=case_symbol.id,
            payload=payload,
        )

    def _build_protocol_decl(self, proto: ast.ProtocolDecl) -> HirProtocol:
        """Build an HIR protocol."""
        symbol_id = self._get_symbol(proto)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=proto.name,
                kind=SymbolKind.PROTOCOL,
                namespace=Namespace.TYPE,
            )
            symbol_id = symbol.id

        func_reqs: List[HirFuncRequirement] = []
        prop_reqs: List[HirPropRequirement] = []

        for member in proto.members:
            if isinstance(member, ast.ProtocolFuncReq):
                params: List[Tuple[Optional[str], TypeId]] = []
                for p in member.params:
                    type_id = self.type_table.error_type
                    if p.type_annotation:
                        type_id = self._resolve_type_node(p.type_annotation)
                    params.append((p.external_name, type_id))

                return_type = self.type_table.void_type
                if member.return_type:
                    return_type = self._resolve_type_node(member.return_type)

                func_reqs.append(HirFuncRequirement(
                    name=member.name,
                    params=params,
                    return_type=return_type,
                    is_async=member.is_async,
                ))
            elif isinstance(member, ast.ProtocolPropReq):
                type_id = self.type_table.error_type
                if member.type_annotation:
                    type_id = self._resolve_type_node(member.type_annotation)

                prop_reqs.append(HirPropRequirement(
                    name=member.name,
                    type_id=type_id,
                    has_getter=member.has_getter,
                    has_setter=member.has_setter,
                ))

        return HirProtocol(
            name=proto.name,
            symbol_id=symbol_id,
            func_requirements=func_reqs,
            prop_requirements=prop_reqs,
        )

    def _build_extension_decl(self, ext: ast.ExtensionDecl) -> HirExtension:
        """Build an HIR extension."""
        extended_type = self.type_table.error_type
        if ext.extended_type:
            extended_type = self._resolve_named_type(ext.extended_type)

        methods: List[HirFunction] = []
        for member in ext.members:
            if isinstance(member, ast.FuncDecl):
                method = self._build_func_decl(member, is_method=True)
                methods.append(method)

        return HirExtension(
            extended_type=extended_type,
            methods=methods,
        )

    # ========================= Statement Building =========================

    def _build_block(self, block: ast.Block) -> HirBlock:
        """Build an HIR block."""
        stmts = [self._build_stmt(s) for s in block.statements]
        return HirBlock(statements=stmts)

    def _build_stmt(self, stmt: ast.Stmt) -> HirStmt:
        """Build an HIR statement."""
        if isinstance(stmt, ast.VarDecl):
            return self._build_var_decl(stmt)
        elif isinstance(stmt, ast.Assignment):
            return self._build_assignment(stmt)
        elif isinstance(stmt, ast.ExprStmt):
            return self._build_expr_stmt(stmt)
        elif isinstance(stmt, ast.ReturnStmt):
            return self._build_return(stmt)
        elif isinstance(stmt, ast.BreakStmt):
            return HirBreak()
        elif isinstance(stmt, ast.ContinueStmt):
            return HirContinue()
        elif isinstance(stmt, ast.Block):
            return self._build_block(stmt)
        elif isinstance(stmt, ast.IfStmt):
            return self._build_if_stmt(stmt)
        elif isinstance(stmt, ast.GuardStmt):
            return self._build_guard_stmt(stmt)
        elif isinstance(stmt, ast.WhileStmt):
            return self._build_while_stmt(stmt)
        elif isinstance(stmt, ast.ForStmt):
            return self._build_for_stmt(stmt)
        elif isinstance(stmt, ast.SwitchStmt):
            return self._build_switch_stmt(stmt)
        elif isinstance(stmt, ast.DeferStmt):
            return self._build_defer_stmt(stmt)
        else:
            self._error(f"Unknown statement type: {type(stmt)}")
            return HirBlock(statements=[])

    def _build_var_decl(self, var_decl: ast.VarDecl) -> HirStmt:
        """Build an HIR variable declaration."""
        # For simple identifier patterns, create a single VarDecl
        if var_decl.pattern and isinstance(var_decl.pattern, ast.IdentifierPattern):
            pattern = var_decl.pattern
            symbol_id = self._get_symbol(pattern)
            if symbol_id is None:
                symbol = self.symbol_table.create_symbol(
                    name=pattern.name,
                    kind=SymbolKind.VARIABLE,
                    namespace=Namespace.VALUE,
                    is_mutable=var_decl.is_mutable,
                )
                symbol_id = symbol.id

            # Determine type
            type_id = self.type_table.error_type
            if var_decl.type_annotation:
                type_id = self._resolve_type_node(var_decl.type_annotation)
            elif var_decl.initializer:
                type_id = self._get_expr_type(var_decl.initializer)

            initializer = None
            if var_decl.initializer:
                initializer = self._build_expr(var_decl.initializer)

            return HirVarDecl(
                name=pattern.name,
                symbol_id=symbol_id,
                type_id=type_id,
                initializer=initializer,
                is_mutable=var_decl.is_mutable,
            )

        # For complex patterns, we need to destructure
        # For now, treat as simple case
        name = "__pattern"
        if var_decl.pattern and isinstance(var_decl.pattern, ast.IdentifierPattern):
            name = var_decl.pattern.name

        symbol = self.symbol_table.create_symbol(
            name=name,
            kind=SymbolKind.VARIABLE,
            namespace=Namespace.VALUE,
            is_mutable=var_decl.is_mutable,
        )

        type_id = self.type_table.error_type
        if var_decl.type_annotation:
            type_id = self._resolve_type_node(var_decl.type_annotation)
        elif var_decl.initializer:
            type_id = self._get_expr_type(var_decl.initializer)

        initializer = None
        if var_decl.initializer:
            initializer = self._build_expr(var_decl.initializer)

        return HirVarDecl(
            name=name,
            symbol_id=symbol.id,
            type_id=type_id,
            initializer=initializer,
            is_mutable=var_decl.is_mutable,
        )

    def _build_assignment(self, assign: ast.Assignment) -> HirAssign:
        """Build an HIR assignment."""
        target = self._build_expr(assign.target) if assign.target else self._error_expr()
        value = self._build_expr(assign.value) if assign.value else self._error_expr()

        compound_op = None
        if assign.op != "=":
            compound_op = assign.op[:-1]  # Remove trailing '='

        return HirAssign(
            target=target,
            value=value,
            compound_op=compound_op,
        )

    def _build_expr_stmt(self, stmt: ast.ExprStmt) -> HirExprStmt:
        """Build an HIR expression statement."""
        expr = self._build_expr(stmt.expr) if stmt.expr else self._error_expr()
        return HirExprStmt(expr=expr)

    def _build_return(self, ret: ast.ReturnStmt) -> HirReturn:
        """Build an HIR return statement."""
        value = None
        if ret.value:
            value = self._build_expr(ret.value)
        return HirReturn(value=value)

    def _build_if_stmt(self, if_stmt: ast.IfStmt) -> Union[HirIf, HirIfLet]:
        """Build an HIR if statement."""
        if isinstance(if_stmt.condition, tuple):
            # if let pattern = expr
            pattern, expr = if_stmt.condition
            scrutinee_type = self._get_expr_type(expr)
            pattern_type = self.type_table.get_optional_inner(scrutinee_type) or scrutinee_type
            hir_pattern = self._build_pattern(pattern, pattern_type)
            hir_expr = self._build_expr(expr)

            then_block = self._build_block(if_stmt.then_block) if if_stmt.then_block else HirBlock()

            else_block = None
            if if_stmt.else_block:
                if isinstance(if_stmt.else_block, ast.IfStmt):
                    else_block = self._build_if_stmt(if_stmt.else_block)
                else:
                    else_block = self._build_block(if_stmt.else_block)

            return HirIfLet(
                pattern=hir_pattern,
                scrutinee=hir_expr,
                then_block=then_block,
                else_block=else_block,
            )
        else:
            # Regular if condition
            condition = self._build_expr(if_stmt.condition) if if_stmt.condition else self._error_expr()
            then_block = self._build_block(if_stmt.then_block) if if_stmt.then_block else HirBlock()

            else_block = None
            if if_stmt.else_block:
                if isinstance(if_stmt.else_block, ast.IfStmt):
                    else_block = self._build_if_stmt(if_stmt.else_block)
                else:
                    else_block = self._build_block(if_stmt.else_block)

            return HirIf(
                condition=condition,
                then_block=then_block,
                else_block=else_block,
            )

    def _build_guard_stmt(self, guard: ast.GuardStmt) -> HirGuard:
        """Build an HIR guard statement."""
        condition = self._build_expr(guard.condition) if guard.condition else self._error_expr()
        else_block = self._build_block(guard.else_block) if guard.else_block else HirBlock()
        return HirGuard(condition=condition, else_block=else_block)

    @staticmethod
    def _demote_implicit_tail_return(block: Optional[ast.Block]) -> Optional[ast.Block]:
        """A trailing expression in a loop body is per-iteration and must not
        return from the enclosing function. Rewrite a trailing *implicit*
        return back into a discarded expression-statement. Explicit `return`s
        are left untouched."""
        if block is None or not block.statements:
            return block
        last = block.statements[-1]
        if isinstance(last, ast.ReturnStmt) and last.implicit and last.value is not None:
            new_statements = list(block.statements[:-1])
            new_statements.append(ast.ExprStmt(expr=last.value))
            return ast.Block(statements=new_statements, is_unsafe=block.is_unsafe)
        return block

    def _build_while_stmt(self, while_stmt: ast.WhileStmt) -> HirWhile:
        """Build an HIR while loop."""
        condition = self._build_expr(while_stmt.condition) if while_stmt.condition else self._error_expr()
        body_ast = self._demote_implicit_tail_return(while_stmt.body)
        body = self._build_block(body_ast) if body_ast else HirBlock()
        return HirWhile(condition=condition, body=body)

    def _build_for_stmt(self, for_stmt: ast.ForStmt) -> HirFor:
        """Build an HIR for loop."""
        iterable = self._build_expr(for_stmt.iterable) if for_stmt.iterable else self._error_expr()

        # Get element type from iterable
        iter_type = self._get_expr_type(for_stmt.iterable) if for_stmt.iterable else self.type_table.error_type
        elem_type = self._get_iterable_element(iter_type)

        pattern = self._build_pattern(for_stmt.pattern, elem_type) if for_stmt.pattern else HirWildcardPattern()
        body_ast = self._demote_implicit_tail_return(for_stmt.body)
        body = self._build_block(body_ast) if body_ast else HirBlock()

        return HirFor(pattern=pattern, iterable=iterable, body=body)

    def _build_switch_stmt(self, switch: ast.SwitchStmt) -> HirSwitch:
        """Build an HIR switch statement."""
        scrutinee = self._build_expr(switch.value) if switch.value else self._error_expr()
        scrutinee_type = self._get_expr_type(switch.value) if switch.value else self.type_table.error_type

        cases = [self._build_switch_case(c, scrutinee_type) for c in switch.cases]

        return HirSwitch(
            scrutinee=scrutinee,
            scrutinee_type=scrutinee_type,
            cases=cases,
        )

    def _build_switch_case(
        self,
        case: ast.SwitchCase,
        scrutinee_type: TypeId,
    ) -> HirSwitchCase:
        """Build an HIR switch case."""
        patterns: List[Tuple[HirPattern, Optional[HirExpr]]] = []
        for pattern, guard in case.patterns:
            hir_pattern = self._build_pattern(pattern, scrutinee_type)
            hir_guard = self._build_expr(guard) if guard else None
            patterns.append((hir_pattern, hir_guard))

        body = HirBlock(statements=[self._build_stmt(s) for s in case.body])

        return HirSwitchCase(
            patterns=patterns,
            body=body,
            is_default=case.is_default,
        )

    def _build_defer_stmt(self, defer: ast.DeferStmt) -> HirDefer:
        """Build an HIR defer statement."""
        body = self._build_block(defer.body) if defer.body else HirBlock()
        return HirDefer(body=body)

    # ========================= Expression Building =========================

    def _build_expr(self, expr: ast.Expr) -> HirExpr:
        """Build an HIR expression from an AST expression."""
        if isinstance(expr, ast.Literal):
            return self._build_literal(expr)
        elif isinstance(expr, ast.Identifier):
            return self._build_identifier(expr)
        elif isinstance(expr, ast.TypeReference):
            return self._build_type_reference(expr)
        elif isinstance(expr, ast.BinaryOp):
            return self._build_binary_op(expr)
        elif isinstance(expr, ast.UnaryOp):
            return self._build_unary_op(expr)
        elif isinstance(expr, ast.TernaryOp):
            return self._build_ternary_op(expr)
        elif isinstance(expr, ast.Call):
            return self._build_call(expr)
        elif isinstance(expr, ast.MemberAccess):
            return self._build_member_access(expr)
        elif isinstance(expr, ast.OptionalChain):
            return self._desugar_optional_chain(expr)
        elif isinstance(expr, ast.Subscript):
            return self._build_subscript(expr)
        elif isinstance(expr, ast.TupleExpr):
            return self._build_tuple(expr)
        elif isinstance(expr, ast.ArrayLiteral):
            return self._build_array(expr)
        elif isinstance(expr, ast.DictLiteral):
            return self._build_dict(expr)
        elif isinstance(expr, ast.Lambda):
            return self._build_lambda(expr)
        elif isinstance(expr, ast.StructLiteral):
            return self._build_struct_literal(expr)
        elif isinstance(expr, ast.Cast):
            return self._build_cast(expr)
        elif isinstance(expr, ast.TypeCheck):
            return self._build_type_check(expr)
        elif isinstance(expr, ast.TryExpr):
            return self._build_try_expr(expr)
        elif isinstance(expr, ast.SizeOfExpr):
            return self._build_size_of_expr(expr)
        elif isinstance(expr, ast.TypeIdExpr):
            return self._build_type_id_expr(expr)
        elif isinstance(expr, ast.AlignOfExpr):
            return self._build_align_of_expr(expr)
        elif isinstance(expr, ast.DropOfExpr):
            return self._build_drop_of_expr(expr)
        elif isinstance(expr, ast.CloneOfExpr):
            return self._build_clone_of_expr(expr)
        else:
            self._error(f"Unknown expression type: {type(expr)}")
            return self._error_expr()

    def _error_expr(self) -> HirExpr:
        """Create an error placeholder expression."""
        return HirLiteral(
            type_id=self.type_table.error_type,
            value=None,
            kind="nil",
        )

    def _build_literal(self, lit: ast.Literal) -> HirLiteral:
        """Build an HIR literal."""
        type_id = self._get_expr_type(lit)
        return HirLiteral(
            type_id=type_id,
            value=lit.value,
            kind=lit.kind,
        )

    def _build_identifier(self, ident: ast.Identifier) -> HirVar:
        """Build an HIR variable reference."""
        type_id = self._get_expr_type(ident)
        symbol_id = self._get_symbol(ident)
        if symbol_id is None:
            symbol = self.symbol_table.create_symbol(
                name=ident.name,
                kind=SymbolKind.VARIABLE,
                namespace=Namespace.VALUE,
            )
            symbol_id = symbol.id

        return HirVar(
            type_id=type_id,
            name=ident.name,
            symbol_id=symbol_id,
        )

    def _build_type_reference(self, type_ref: ast.TypeReference) -> HirVar:
        type_id = self._get_expr_type(type_ref)
        symbol_id = SymbolId(-1)
        info = self.type_table.get_type(type_id)
        if info and info.kind in (TypeKind.STRUCT, TypeKind.ENUM):
            data = info.data
            if isinstance(data, (StructTypeData, EnumTypeData)):
                symbol_id = data.symbol_id

        name = type_ref.type_name.name if type_ref.type_name else "<type>"
        return HirVar(type_id=type_id, name=name, symbol_id=symbol_id)

    def _build_binary_op(self, binop: ast.BinaryOp) -> HirExpr:
        """Build an HIR binary operation, potentially desugaring ??."""
        if binop.op == NIL_COALESCING_OPERATOR:
            return self._desugar_nil_coalescing(binop)

        type_id = self._get_expr_type(binop)

        # Check if this operator was resolved to a method call
        op_target = self.operator_targets.get(id(binop))
        if op_target is not None:
            left = self._build_expr(binop.left) if binop.left else self._error_expr()
            right = self._build_expr(binop.right) if binop.right else self._error_expr()
            method_name = OPERATOR_TO_METHOD.get(binop.op, binop.op)
            return HirMethodCall(
                type_id=type_id,
                receiver=left,
                method_name=method_name,
                arguments=[(None, right)],
                method_symbol=op_target.symbol_id,
            )

        left = self._build_expr(binop.left) if binop.left else self._error_expr()
        right = self._build_expr(binop.right) if binop.right else self._error_expr()

        return HirBinaryOp(
            type_id=type_id,
            left=left,
            op=binop.op,
            right=right,
        )

    def _build_unary_op(self, unop: ast.UnaryOp) -> HirExpr:
        """Build an HIR unary operation."""
        type_id = self._get_expr_type(unop)
        operand = self._build_expr(unop.operand) if unop.operand else self._error_expr()

        # ``try expr`` (prefix form) is desugared into the same HirTryExpr
        # node used for postfix ``expr?``: unwrap the ``ok`` payload of a
        # ``Result<T, E>`` or propagate the ``err`` to the caller.
        if unop.op == "try":
            inner_type = operand.type_id
            inner_t = self.type_table.error_type
            info = self.type_table.get_type(inner_type)
            if info and info.kind == TypeKind.ENUM:
                from .types import EnumTypeData
                if isinstance(info.data, EnumTypeData):
                    if info.data.type_args and len(info.data.type_args) > 0:
                        inner_t = info.data.type_args[0]
                    else:
                        # Non-generic Result, look up payload type of `ok`.
                        symbol = self.symbol_table.get_symbol(info.data.symbol_id)
                        if symbol and symbol.decl_node:
                            for member in symbol.decl_node.members:
                                if hasattr(member, 'cases'):
                                    for case in member.cases:
                                        if case.name == "ok" and case.payload:
                                            _, payload_type = case.payload[0]
                                            inner_t = self._resolve_type_node(payload_type)
                                            break
            return HirTryExpr(
                type_id=inner_t,
                expr=operand,
                result_type=inner_t,
            )

        return HirUnaryOp(
            type_id=type_id,
            op=unop.op,
            operand=operand,
        )

    def _build_ternary_op(self, ternop: ast.TernaryOp) -> HirTernary:
        """Build an HIR ternary expression."""
        type_id = self._get_expr_type(ternop)
        condition = self._build_expr(ternop.condition) if ternop.condition else self._error_expr()
        then_expr = self._build_expr(ternop.then_expr) if ternop.then_expr else self._error_expr()
        else_expr = self._build_expr(ternop.else_expr) if ternop.else_expr else self._error_expr()

        return HirTernary(
            type_id=type_id,
            condition=condition,
            then_expr=then_expr,
            else_expr=else_expr,
        )

    def _build_call(self, call: ast.Call) -> HirExpr:
        """Build an HIR call expression."""
        type_id = self._get_expr_type(call)

        arguments: List[Tuple[Optional[str], HirExpr]] = []
        if call.arguments:
            for arg in call.arguments:
                arg_expr = self._build_expr(arg.value) if arg.value else self._error_expr()
                arguments.append((arg.label, arg_expr))

        call_target = self.call_targets.get(id(call))
        # Check if this is an enum case construction: EnumName.case(args)
        if call_target and call_target.kind == CalleeKind.ENUM_CTOR:
            return HirEnumConstruct(
                type_id=type_id,
                enum_type=type_id,
                case_name=call_target.case_name or "",
                payload=arguments,
            )

        callee = self._build_expr(call.callee) if call.callee else self._error_expr()

        # Check if this is a method call (callee is MemberAccess)
        if call.callee and isinstance(call.callee, ast.MemberAccess):
            # Check if this is an import-alias call (e.g., lib.get_value())
            if self._is_import_alias_member_access(call.callee):
                callee = self._build_import_alias_callee(call.callee)
                callee_symbol = self.node_symbols.get(id(call.callee))
                arguments = self._with_default_arguments(arguments, callee_symbol)
                return HirCall(
                    type_id=type_id,
                    callee=callee,
                    arguments=arguments,
                    callee_symbol=callee_symbol,
                )
            receiver = self._build_expr(call.callee.object) if call.callee.object else self._error_expr()

            # Desugar built-in methods on Optional<T> into HirOptionalMatch
            # so the rest of the pipeline only sees primitive optional ops.
            receiver_info = self.type_table.get_type(receiver.type_id)
            if (
                receiver_info is not None
                and receiver_info.kind == TypeKind.OPTIONAL
                and call.callee.member in ("is_some", "is_none", "unwrap_or")
            ):
                from .types import OptionalTypeData
                data = receiver_info.data
                if isinstance(data, OptionalTypeData):
                    inner_type = data.inner
                    method = call.callee.member
                    bool_type = (
                        self.type_table.get_builtin("Bool")
                        or self.type_table.error_type
                    )
                    temp_name = self._fresh_temp(f"__opt_{method}")
                    temp_symbol_id = self._create_temp_symbol(
                        temp_name, inner_type
                    )

                    if method == "is_some":
                        return HirOptionalMatch(
                            type_id=bool_type,
                            scrutinee=receiver,
                            inner_type=inner_type,
                            some_binding=temp_symbol_id,
                            some_expr=HirLiteral(
                                type_id=bool_type, value=True, kind="bool"
                            ),
                            none_expr=HirLiteral(
                                type_id=bool_type, value=False, kind="bool"
                            ),
                        )
                    if method == "is_none":
                        return HirOptionalMatch(
                            type_id=bool_type,
                            scrutinee=receiver,
                            inner_type=inner_type,
                            some_binding=temp_symbol_id,
                            some_expr=HirLiteral(
                                type_id=bool_type, value=False, kind="bool"
                            ),
                            none_expr=HirLiteral(
                                type_id=bool_type, value=True, kind="bool"
                            ),
                        )
                    # unwrap_or(default)
                    if arguments:
                        default_expr = arguments[0][1]
                    else:
                        default_expr = self._error_expr()
                    return HirOptionalMatch(
                        type_id=inner_type,
                        scrutinee=receiver,
                        inner_type=inner_type,
                        some_binding=temp_symbol_id,
                        some_expr=HirVar(
                            type_id=inner_type,
                            name=temp_name,
                            symbol_id=temp_symbol_id,
                        ),
                        none_expr=default_expr,
                    )

            # Carry the resolved method's SymbolId forward so MIR/codegen
            # can disambiguate same-named methods across modules (e.g.
            # `Box.doit` declared in two different modules). The type
            # checker stashes the binding in `member_method_symbols`
            # keyed by `id(MemberAccess)`.
            method_symbol = self.member_method_symbols.get(id(call.callee))
            arguments = self._with_default_arguments(arguments, method_symbol)
            is_static = self._is_static_method_access(call.callee)

            # .clone() on a heap type lowers to the Clone MIR op via HirClone.
            if (call.callee.member == "clone"
                    and not is_static
                    and not arguments
                    and self.type_table.is_heap_type(type_id)):
                return HirClone(type_id=type_id, value=receiver)

            return HirMethodCall(
                type_id=type_id,
                receiver=receiver,
                method_name=call.callee.member,
                arguments=arguments,
                method_symbol=method_symbol,
                is_static=is_static,
            )

        callee_symbol = None
        if call_target and call_target.symbol_id:
            callee_symbol = call_target.symbol_id
        arguments = self._with_default_arguments(arguments, callee_symbol)

        return HirCall(
            type_id=type_id,
            callee=callee,
            arguments=arguments,
            callee_symbol=callee_symbol,
        )

    def _with_default_arguments(
        self,
        arguments: List[Tuple[Optional[str], HirExpr]],
        callee_symbol: Optional[SymbolId],
    ) -> List[Tuple[Optional[str], HirExpr]]:
        """Append omitted trailing parameter defaults for a resolved callee."""
        if callee_symbol is None:
            return arguments
        symbol = self.symbol_table.get_symbol(callee_symbol)
        if symbol is None or not isinstance(symbol.decl_node, (ast.FuncDecl, ast.ExternFuncDecl)):
            return arguments

        completed = list(arguments)
        for param in symbol.decl_node.params[len(completed):]:
            if param.default_value is None:
                break
            completed.append((param.external_name, self._build_expr(param.default_value)))
        return completed

    def _is_import_alias_member_access(self, access: ast.MemberAccess) -> bool:
        """Check if a MemberAccess resolves to an import alias (lib.get_value, std.io.println)."""
        symbol_id = self.node_symbols.get(id(access))
        return symbol_id is not None

    def _build_import_alias_callee(self, access: ast.MemberAccess) -> HirExpr:
        """Build the callee expression for an import-alias member access."""
        symbol_id = self.node_symbols.get(id(access))
        return HirVar(
            type_id=self._get_expr_type(access),
            name=access.member,
            symbol_id=symbol_id,
        )

    def _is_static_method_access(self, access: ast.MemberAccess) -> bool:
        method_symbol = self.member_method_symbols.get(id(access))
        if method_symbol is None:
            return False
        symbol = self.symbol_table.get_symbol(method_symbol)
        return (
            symbol is not None
            and isinstance(symbol.decl_node, ast.FuncDecl)
            and symbol.decl_node.is_static
        )

    def _build_member_access(self, access: ast.MemberAccess) -> HirExpr:
        """Build an HIR field access."""
        type_id = self._get_expr_type(access)

        # No-payload enum case used as a value (e.g., `let x = MyOpt.A`).
        access_target = self.call_targets.get(id(access))
        if access_target and access_target.kind == CalleeKind.ENUM_CTOR:
            return HirEnumConstruct(
                type_id=type_id,
                enum_type=type_id,
                case_name=access_target.case_name or access.member,
                payload=[],
            )

        obj = self._build_expr(access.object) if access.object else self._error_expr()

        return HirFieldAccess(
            type_id=type_id,
            object=obj,
            field_name=access.member,
            field_symbol=None,
        )

    def _build_subscript(self, sub: ast.Subscript) -> HirSubscript:
        """Build an HIR subscript expression."""
        type_id = self._get_expr_type(sub)
        obj = self._build_expr(sub.object) if sub.object else self._error_expr()
        indices = [self._build_expr(idx) for idx in sub.indices]

        return HirSubscript(
            type_id=type_id,
            object=obj,
            indices=indices,
        )

    def _build_tuple(self, tup: ast.TupleExpr) -> HirTuple:
        """Build an HIR tuple expression."""
        type_id = self._get_expr_type(tup)
        elements: List[Tuple[Optional[str], HirExpr]] = []
        for label, elem in tup.elements:
            hir_elem = self._build_expr(elem)
            elements.append((label, hir_elem))

        return HirTuple(
            type_id=type_id,
            elements=elements,
        )

    def _build_array(self, arr: ast.ArrayLiteral) -> HirArray:
        """Build an HIR array literal."""
        type_id = self._get_expr_type(arr)
        elements = [self._build_expr(elem) for elem in arr.elements]

        elem_type = self.type_table.error_type
        info = self.type_table.get_type(type_id)
        if info and info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData) and data.type_args:
                elem_type = data.type_args[0]

        return HirArray(
            type_id=type_id,
            elements=elements,
            element_type=elem_type,
        )

    def _build_dict(self, dict_lit: ast.DictLiteral) -> HirDict:
        """Build an HIR dict literal."""
        type_id = self._get_expr_type(dict_lit)
        entries: List[Tuple[HirExpr, HirExpr]] = []
        for key, value in dict_lit.entries:
            hir_key = self._build_expr(key)
            hir_value = self._build_expr(value)
            entries.append((hir_key, hir_value))

        key_type = self.type_table.error_type
        value_type = self.type_table.error_type
        info = self.type_table.get_type(type_id)
        if info and info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData) and len(data.type_args) >= 2:
                key_type = data.type_args[0]
                value_type = data.type_args[1]

        return HirDict(
            type_id=type_id,
            entries=entries,
            key_type=key_type,
            value_type=value_type,
        )

    def _build_lambda(self, lam: ast.Lambda) -> HirLambda:
        """Build an HIR lambda expression."""
        type_id = self._get_expr_type(lam)

        params: List[HirParam] = []
        for pattern, type_ann in lam.params:
            param_type = self.type_table.error_type
            if type_ann:
                param_type = self._resolve_type_node(type_ann)

            name = "__param"
            if isinstance(pattern, ast.IdentifierPattern):
                name = pattern.name

            symbol_id = self._get_symbol(pattern)
            if symbol_id is None:
                symbol = self.symbol_table.create_symbol(
                    name=name,
                    kind=SymbolKind.PARAMETER,
                    namespace=Namespace.VALUE,
                )
                symbol_id = symbol.id

            params.append(HirParam(
                name=name,
                symbol_id=symbol_id,
                type_id=param_type,
            ))

        body = HirBlock(statements=[self._build_stmt(s) for s in lam.body])

        return HirLambda(
            type_id=type_id,
            params=params,
            body=body,
            captures=[],
        )

    def _build_struct_literal(self, literal: ast.StructLiteral) -> HirStructInit:
        """Build an HIR struct literal."""
        type_id = self._get_expr_type(literal)

        arguments: List[Tuple[Optional[str], HirExpr]] = []
        for arg in literal.arguments:
            arg_expr = self._build_expr(arg.value) if arg.value else self._error_expr()
            arguments.append((arg.label, arg_expr))

        # Get struct symbol
        struct_symbol = SymbolId(-1)
        info = self.type_table.get_type(type_id)
        if info and info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                struct_symbol = data.symbol_id

        return HirStructInit(
            type_id=type_id,
            struct_type=type_id,
            struct_symbol=struct_symbol,
            arguments=arguments,
        )

    def _build_cast(self, cast: ast.Cast) -> HirCast:
        """Build an HIR type cast."""
        type_id = self._get_expr_type(cast)
        expr = self._build_expr(cast.expr) if cast.expr else self._error_expr()

        target_type = self.type_table.error_type
        if cast.target_type:
            target_type = self._resolve_type_node(cast.target_type)

        return HirCast(
            type_id=type_id,
            expr=expr,
            target_type=target_type,
            kind=getattr(cast, "kind", "safe"),
        )

    def _build_type_check(self, check: ast.TypeCheck) -> HirTypeCheck:
        """Build an HIR type check."""
        type_id = self._get_expr_type(check)
        expr = self._build_expr(check.expr) if check.expr else self._error_expr()

        checked_type = self.type_table.error_type
        if check.checked_type:
            checked_type = self._resolve_type_node(check.checked_type)

        return HirTypeCheck(
            type_id=type_id,
            expr=expr,
            checked_type=checked_type,
        )

    def _build_try_expr(self, expr: ast.TryExpr) -> HirTryExpr:
        """Build an HIR try expression (x? operator)."""
        inner = self._build_expr(expr.value) if expr.value else self._error_expr()
        inner_type = inner.type_id

        # Extract T from Result<T, E> (the 'ok' case payload type)
        inner_t = self.type_table.error_type
        info = self.type_table.get_type(inner_type)
        if info and info.kind == TypeKind.ENUM:
            from .types import EnumTypeData
            if isinstance(info.data, EnumTypeData):
                # Try type_args first (for generic Result<T,E>)
                if info.data.type_args and len(info.data.type_args) > 0:
                    inner_t = info.data.type_args[0]
                else:
                    # For non-generic Result, look up the 'ok' case payload
                    symbol = self.symbol_table.get_symbol(info.data.symbol_id)
                    if symbol and symbol.decl_node:
                        for member in symbol.decl_node.members:
                            if hasattr(member, 'cases'):
                                for case in member.cases:
                                    if case.name == "ok" and case.payload:
                                        _, payload_type = case.payload[0]
                                        inner_t = self._resolve_type_node(payload_type)
                                        break

        return HirTryExpr(
            type_id=inner_t,
            expr=inner,
            result_type=inner_t,
        )

    def _build_size_of_expr(self, expr: ast.SizeOfExpr) -> HirLiteral:
        """Build an HIR literal for size_of(T)."""
        type_id = self._get_expr_type(expr)
        sizeof_type = getattr(expr, '_sizeof_type_id', None)
        if isinstance(sizeof_type, TypeId) and self._has_type_variables(sizeof_type):
            return HirLiteral(
                type_id=type_id,
                value=sizeof_type,
                kind="size_of",
            )

        size = getattr(expr, '_computed_size', 0)
        return HirLiteral(
            type_id=type_id,
            value=size,
            kind="int",
        )

    def _build_type_id_expr(self, expr: ast.TypeIdExpr) -> HirLiteral:
        """Build an HIR literal for type_id(T)."""
        type_id = self._get_expr_type(expr)
        target_type = getattr(expr, '_typeid_type_id', None)
        if not isinstance(target_type, TypeId) and expr.type_arg is not None:
            target_type = self._resolve_type_node(expr.type_arg)
        if isinstance(target_type, TypeId):
            return HirLiteral(
                type_id=type_id,
                value=target_type,
                kind="type_id",
            )
        return HirLiteral(
            type_id=type_id,
            value=0,
            kind="int",
        )

    def _build_align_of_expr(self, expr: ast.AlignOfExpr) -> HirLiteral:
        """Build an HIR literal for align_of(T)."""
        type_id = self._get_expr_type(expr)
        alignof_type = getattr(expr, '_alignof_type_id', None)
        if isinstance(alignof_type, TypeId) and self._has_type_variables(alignof_type):
            return HirLiteral(type_id=type_id, value=alignof_type, kind="align_of")
        align = getattr(expr, '_computed_align', 8)
        return HirLiteral(type_id=type_id, value=align, kind="int")

    def _build_drop_of_expr(self, expr: ast.DropOfExpr) -> HirLiteral:
        """Build an HIR Bool literal for drop_of(T)."""
        type_id = self._get_expr_type(expr)
        has_drop = getattr(expr, '_has_drop', False)
        return HirLiteral(type_id=type_id, value=has_drop, kind="bool")

    def _build_clone_of_expr(self, expr: ast.CloneOfExpr) -> HirLiteral:
        """Build an HIR Bool literal for clone_of(T)."""
        type_id = self._get_expr_type(expr)
        has_clone = getattr(expr, '_has_clone', False)
        return HirLiteral(type_id=type_id, value=has_clone, kind="bool")

    def _has_type_variables(self, type_id: TypeId) -> bool:
        """Return true when a type still depends on generic parameters."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return False
        if info.kind == TypeKind.TYPE_VARIABLE:
            return True
        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            if any(self._has_type_variables(arg) for arg in info.data.type_args):
                return True
            if info.data.symbol_id is None:
                return any(self._has_type_variables(t) for _, t in (info.data.anon_fields or ()))
            return False
        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            return any(self._has_type_variables(arg) for arg in info.data.type_args)
        if info.kind == TypeKind.FUNCTION and isinstance(info.data, FunctionTypeData):
            return any(self._has_type_variables(p) for p in info.data.params) or self._has_type_variables(info.data.return_type)
        if info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
            return self._has_type_variables(info.data.inner)
        return False

    # ========================= Desugaring =========================

    def _desugar_optional_chain(self, chain: ast.OptionalChain) -> HirOptionalMatch:
        """Desugar optional chaining (a?.b) into HirOptionalMatch.

        Input:  p?.x  where p: Point?
        Output: HirOptionalMatch(
                  scrutinee = p,
                  inner_type = Point,
                  some_binding = __tmp,
                  some_expr = HirOptionalSome(HirFieldAccess(__tmp, "x")),
                  none_expr = HirOptionalNone
                )
        """
        if chain.object is None:
            return HirOptionalMatch(
                type_id=self.type_table.error_type,
                scrutinee=self._error_expr(),
                inner_type=self.type_table.error_type,
                some_binding=SymbolId(-1),
                some_expr=self._error_expr(),
                none_expr=self._error_expr(),
            )

        # Build the scrutinee
        scrutinee = self._build_expr(chain.object)
        scrutinee_type = scrutinee.type_id

        # Get the inner type (unwrap optional)
        inner_type = self._get_optional_inner(scrutinee_type)
        if inner_type is None:
            # Not optional - treat as direct access
            inner_type = scrutinee_type

        # Create temporary binding for unwrapped value
        temp_name = self._fresh_temp("__opt")
        temp_symbol_id = self._create_temp_symbol(temp_name, inner_type)

        # Create variable reference for the temporary
        temp_var = HirVar(
            type_id=inner_type,
            name=temp_name,
            symbol_id=temp_symbol_id,
        )

        # Build the field access on the unwrapped value
        field_access_type = self._get_expr_type(chain)
        # Unwrap the result type if it's optional (to get the Some content type)
        field_inner_type = self._get_optional_inner(field_access_type)
        if field_inner_type is None:
            field_inner_type = field_access_type

        field_access = HirFieldAccess(
            type_id=field_inner_type,
            object=temp_var,
            field_name=chain.member,
            field_symbol=None,
        )

        # Handle suffix (call or subscript)
        some_content: HirExpr
        if chain.suffix is not None:
            if isinstance(chain.suffix, list):
                # It's a call: a?.method(args)
                arguments: List[Tuple[Optional[str], HirExpr]] = []
                for arg in chain.suffix:
                    if isinstance(arg, ast.Argument):
                        arg_expr = self._build_expr(arg.value) if arg.value else self._error_expr()
                        arguments.append((arg.label, arg_expr))

                some_content = HirMethodCall(
                    type_id=field_inner_type,
                    receiver=temp_var,
                    method_name=chain.member,
                    arguments=arguments,
                    method_symbol=None,
                )
            else:
                # It's a subscript: a?[index]
                index_expr = self._build_expr(chain.suffix)
                some_content = HirSubscript(
                    type_id=field_inner_type,
                    object=field_access,
                    indices=[index_expr],
                )
        else:
            # Simple field access
            some_content = field_access

        # Wrap in Some
        some_expr = HirOptionalSome(
            type_id=field_access_type,
            value=some_content,
            inner_type=field_inner_type,
        )

        # Create None for the none case
        none_expr = HirOptionalNone(
            type_id=field_access_type,
            inner_type=field_inner_type,
        )

        return HirOptionalMatch(
            type_id=field_access_type,
            scrutinee=scrutinee,
            inner_type=inner_type,
            some_binding=temp_symbol_id,
            some_expr=some_expr,
            none_expr=none_expr,
        )

    def _desugar_nil_coalescing(self, binop: ast.BinaryOp) -> HirOptionalMatch:
        """Desugar nil coalescing (a ?? b) into HirOptionalMatch.

        Input:  x ?? 0  where x: i32?
        Output: HirOptionalMatch(
                  scrutinee = x,
                  inner_type = i32,
                  some_binding = __tmp,
                  some_expr = HirVar(__tmp),
                  none_expr = HirLiteral(0)
                )
        """
        if binop.left is None or binop.right is None:
            return HirOptionalMatch(
                type_id=self.type_table.error_type,
                scrutinee=self._error_expr(),
                inner_type=self.type_table.error_type,
                some_binding=SymbolId(-1),
                some_expr=self._error_expr(),
                none_expr=self._error_expr(),
            )

        # Build the scrutinee (left side)
        scrutinee = self._build_expr(binop.left)
        scrutinee_type = scrutinee.type_id

        # Get the inner type (unwrap optional)
        inner_type = self._get_optional_inner(scrutinee_type)
        if inner_type is None:
            # Not optional - just return the left side
            inner_type = scrutinee_type

        # Create temporary binding for unwrapped value
        temp_name = self._fresh_temp("__coal")
        temp_symbol_id = self._create_temp_symbol(temp_name, inner_type)

        # Some case: just return the unwrapped value
        some_expr = HirVar(
            type_id=inner_type,
            name=temp_name,
            symbol_id=temp_symbol_id,
        )

        # None case: return the right side
        none_expr = self._build_expr(binop.right)

        # Result type is the inner type (same as right side type)
        result_type = self._get_expr_type(binop)

        return HirOptionalMatch(
            type_id=result_type,
            scrutinee=scrutinee,
            inner_type=inner_type,
            some_binding=temp_symbol_id,
            some_expr=some_expr,
            none_expr=none_expr,
        )

    # ========================= Pattern Building =========================

    def _build_pattern(
        self,
        pattern: ast.Pattern,
        expected_type: TypeId,
    ) -> HirPattern:
        """Build an HIR pattern."""
        if isinstance(pattern, ast.WildcardPattern):
            return HirWildcardPattern()

        elif isinstance(pattern, ast.IdentifierPattern):
            symbol_id = self._get_symbol(pattern)
            if symbol_id is None:
                symbol = self.symbol_table.create_symbol(
                    name=pattern.name,
                    kind=SymbolKind.VARIABLE,
                    namespace=Namespace.VALUE,
                    is_mutable=pattern.binding == "var",
                )
                symbol_id = symbol.id

            return HirBindingPattern(
                name=pattern.name,
                symbol_id=symbol_id,
                type_id=expected_type,
                is_mutable=pattern.binding == "var",
            )

        elif isinstance(pattern, ast.LiteralPattern):
            if pattern.value:
                return HirLiteralPattern(
                    value=pattern.value.value,
                    type_id=expected_type,
                )
            return HirWildcardPattern()

        elif isinstance(pattern, ast.TuplePattern):
            elements: List[Tuple[Optional[str], HirPattern]] = []

            # Get tuple element types from anonymous struct
            elem_types: List[Tuple[Optional[str], TypeId]] = []
            info = self.type_table.get_type(expected_type)
            if (info and info.kind == TypeKind.STRUCT
                    and isinstance(info.data, StructTypeData) and info.data.symbol_id is None):
                elem_types = [(fname, t) for fname, t in (info.data.anon_fields or ())]

            for i, (label, elem_pattern) in enumerate(pattern.elements):
                elem_type = elem_types[i][1] if i < len(elem_types) else self.type_table.error_type
                hir_elem = self._build_pattern(elem_pattern, elem_type)
                elements.append((label, hir_elem))

            return HirTuplePattern(
                elements=elements,
                type_id=expected_type,
            )

        elif isinstance(pattern, ast.EnumCasePattern):
            payload: List[HirPattern] = []

            # Get payload types from enum case
            payload_types = self._get_enum_case_payload_types(expected_type, pattern.case_name)

            for i, p in enumerate(pattern.payload):
                p_type = payload_types[i] if i < len(payload_types) else self.type_table.error_type
                hir_p = self._build_pattern(p, p_type)
                payload.append(hir_p)

            return HirEnumCasePattern(
                case_name=pattern.case_name,
                case_symbol=None,
                payload=payload,
                enum_type=expected_type,
            )

        elif isinstance(pattern, ast.TypedPattern):
            if pattern.pattern:
                typed_type = expected_type
                if pattern.type_annotation:
                    typed_type = self._resolve_type_node(pattern.type_annotation)
                return self._build_pattern(pattern.pattern, typed_type)
            return HirWildcardPattern()

        elif isinstance(pattern, ast.OrPattern):
            patterns = [self._build_pattern(p, expected_type) for p in pattern.patterns]
            return HirOrPattern(
                patterns=patterns,
                type_id=expected_type,
            )

        return HirWildcardPattern()

    # ========================= Type Resolution Helpers =========================

    def _resolve_type_node(self, type_node: ast.Type) -> TypeId:
        """Resolve an AST type node to a TypeId."""
        return self.type_resolver.resolve(type_node)

    def _resolve_named_type(self, named: ast.NamedType) -> TypeId:
        """Resolve a named type reference."""
        return self.type_resolver.resolve_named(named)

    def _get_iterable_element(self, type_id: TypeId) -> TypeId:
        """Get the element type bound to a `for x in <iter>` pattern."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return self.type_table.error_type

        if info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                sym = self.symbol_table.get_symbol(data.symbol_id)
                struct_name = sym.name if sym is not None else ""
                # The first type argument is the element/key type for
                # both Vec<T> and Dict<K, V>. Post-monomorphization the
                # type args may have been folded into the symbol name
                # itself (e.g. `Vec_i32`), in which case fall back to
                # the symbol-decl walk below.
                if (struct_name == "Vec" or struct_name.startswith("Vec_")) and data.type_args:
                    return data.type_args[0]
                if (struct_name == "Dict" or struct_name.startswith("Dict_")) and data.type_args:
                    return data.type_args[0]

        # Check for protocol-based iteration
        iterable_symbol = self.symbol_table.get_builtin("Iterable")
        if iterable_symbol:
            iterable_type = self.type_table.get_protocol_type(iterable_symbol)
            if iterable_type:
                from .conformance import ConformanceChecker
                conformance_checker = ConformanceChecker(self.type_table, self.symbol_table)
                result = conformance_checker.check_conformance(type_id, iterable_type)
                if result.conforms:
                    # Use member resolver to find Iterator type
                    from .members import MemberResolver
                    resolver = MemberResolver(self.type_table, self.symbol_table)
                    method = resolver.get_method(type_id, "__iter__")
                    if method:
                        func_data = self.type_table.get_function_data(method.signature)
                        if func_data:
                            iterator_type = func_data.return_type
                            next_method = resolver.get_method(iterator_type, "__next__")
                            if next_method:
                                next_data = self.type_table.get_function_data(next_method.signature)
                                if next_data:
                                    from .types import OptionalTypeData
                                    ret_info = self.type_table.get_type(next_data.return_type)
                                    if ret_info and ret_info.kind == TypeKind.OPTIONAL:
                                        opt_data = ret_info.data
                                        if isinstance(opt_data, OptionalTypeData):
                                            return opt_data.inner

        return self.type_table.error_type

    def _get_enum_case_payload_types(
        self,
        enum_type: TypeId,
        case_name: str,
    ) -> List[TypeId]:
        """Get the payload types for an enum case, substituting generic args."""
        info = self.type_table.get_type(enum_type)
        if info is None:
            return []
        if info.kind == TypeKind.OPTIONAL:
            # `case .Some(let v):` against an Optional<T> gives v: T.
            if case_name == "Some" and isinstance(info.data, OptionalTypeData):
                return [info.data.inner]
            return []
        if info.kind != TypeKind.ENUM:
            return []

        data = info.data
        if not isinstance(data, EnumTypeData):
            return []

        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.EnumDecl):
            return []

        enum_decl = symbol.decl_node
        subst: Dict[str, TypeId] = {}
        if len(data.type_args) == len(enum_decl.generic_params):
            for param, arg in zip(enum_decl.generic_params, data.type_args):
                subst[param.name] = arg

        for member in enum_decl.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case in member.cases:
                    if case.name == case_name:
                        return [
                            self._substitute_generic(self._resolve_type_node(t), subst)
                            for _, t in case.payload
                        ]

        return []

    def _substitute_generic(
        self, type_id: TypeId, subst: Dict[str, TypeId]
    ) -> TypeId:
        """Apply a generic substitution map to a TypeId."""
        if not subst:
            return type_id
        info = self.type_table.get_type(type_id)
        if info is None:
            return type_id

        if info.kind == TypeKind.TYPE_VARIABLE and isinstance(info.data, TypeVariableData):
            return subst.get(info.data.name, type_id)

        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            args = tuple(self._substitute_generic(a, subst) for a in info.data.type_args)
            return self.type_table.make_struct(info.data.symbol_id, args)

        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            args = tuple(self._substitute_generic(a, subst) for a in info.data.type_args)
            return self.type_table.make_enum(info.data.symbol_id, args)

        if info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
            return self.type_table.make_optional(self._substitute_generic(info.data.inner, subst))

        return type_id


# ========================= Public API =========================

def build_hir(
    program: ast.Program,
    resolution: ResolutionResult,
    type_result: TypeCheckResult,
) -> HirBuildResult:
    """Build HIR from a type-checked AST.

    Args:
        program: The parsed AST.
        resolution: Result from name resolution.
        type_result: Result from type checking.

    Returns:
        HirBuildResult containing the HIR program and any errors.
    """
    builder = HirBuilder(
        type_result=type_result,
        symbol_table=resolution.symbol_table,
        node_symbols=resolution.node_symbols,
    )
    return builder.build(program)
