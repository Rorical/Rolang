"""
MIR Builder - Transforms HIR into MIR (CFG-based IR).

This module converts monomorphized HIR into MIR, which represents the program
as a control flow graph with explicit operations and terminators.

Pipeline position:
    HIR (monomorphized)
            ↓
       MIR Builder    ← THIS PHASE
            ↓
          MIR (CFG)
            ↓
    Lowering Passes (ARC, patterns, async)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union, Set

from . import ast
from .symbols import SymbolTable, SymbolId, SymbolKind
from .types import (
    TypeId, TypeKind, TypeTable,
    StructTypeData, EnumTypeData, OptionalTypeData, PrimitiveTypeData,
)
from .hir import (
    HirProgram, HirItem,
    HirFunction, HirExternFunc, HirParam,
    HirStruct, HirField, HirEnum, HirEnumCase,
    HirProtocol, HirExtension,
    HirStmt, HirBlock, HirVarDecl, HirAssign, HirExprStmt, HirReturn,
    HirBreak, HirContinue, HirIf, HirIfLet, HirGuard,
    HirWhile, HirFor, HirSwitchCase, HirSwitch, HirDefer,
    HirExpr, HirLiteral, HirVar, HirBinaryOp, HirUnaryOp, HirTernary,
    HirCall, HirMethodCall, HirFieldAccess, HirSubscript,
    HirTuple, HirArray, HirDict, HirLambda,
    HirStructInit, HirEnumConstruct, HirCast, HirTypeCheck, HirTryExpr,
    HirOptionalSome, HirOptionalNone, HirOptionalMatch,
    HirPattern, HirWildcardPattern, HirBindingPattern, HirLiteralPattern,
    HirTuplePattern, HirEnumCasePattern, HirOrPattern,
)
from .monomorphize import MonomorphizationResult
from .operators import (
    BINOP_MAP,
    CMPOP_MAP,
    UNARYOP_MAP,
)
from .type_resolver import TypeResolver
from .mir_patterns import MirPatternLowerer
from .mir_expr_lowerer import MirExpressionLowerer
from .mir_for_loops import MirForLoopLowerer
from .mir_special_builders import LambdaFunctionBuilder, MethodMirBuilder, _iter_hir_vars
from .mir import (
    # ID types
    LocalId, BlockId, ValueId,
    # Local/Place
    Local, Place, PlaceProjection,
    FieldProjection, IndexProjection, DerefProjection,
    # Operands
    Operand, CopyOperand, MoveOperand, ConstantOperand, ConstantKind, operand_type,
    # Operations
    Op, BinOp, CmpOp, UnaryOp, CastOp,
    BinOpKind, CmpOpKind, UnaryOpKind,
    MakeStruct, MakeEnum, MakeSome, MakeNone,
    ExtractField, ExtractEnumPayload, GetTag,
    Assign, Store, Load,
    AllocObj, Clone, GCCheck,
    CallStatic, CallVTable, CallWitness,
    MakeClosure, CallClosure,
    BoxExistential,
    TaskJoin, TaskYield,
    # Terminators
    Terminator, Branch, CondBranch, SwitchInt, Return, Unreachable,
    # Blocks/Functions
    Block, MirFunction, MirField, MirStruct, MirEnumCase, MirEnum, MirExternFunc,
    MirProgram, MirBuildResult,
    # Validation
    validate_program,
)


# =============================================================================
# Loop Context (for break/continue)
# =============================================================================

@dataclass
class LoopContext:
    """Context for a loop, tracking break/continue targets."""
    header_block: BlockId   # Loop header (condition check)
    exit_block: BlockId     # Block after the loop
    defer_depth: int = 0    # Defer stack depth when loop was entered


# =============================================================================
# Defer Context (for tracking deferred statements)
# =============================================================================

@dataclass
class DeferContext:
    """Context for tracking deferred blocks."""
    deferred_blocks: List[HirBlock] = field(default_factory=list)


# =============================================================================
# MIR Function Builder
# =============================================================================

class MirFunctionBuilder:
    """
    Builds a single MIR function from HIR.

    This class manages the state needed while lowering a function:
    - Locals (parameters and variables)
    - Basic blocks and current block
    - Loop stack for break/continue
    - Defer stack
    """

    def __init__(
        self,
        func: HirFunction,
        type_table: TypeTable,
        symbol_table: SymbolTable,
    ) -> None:
        self.func = func
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.type_resolver = TypeResolver(
            type_table,
            symbol_table,
            allow_symbol_table_lookup=True,
        )
        # Lazy member resolver so for-loop lowering can look up
        # ``makeIterator()`` / ``next()`` signatures on the iterable.
        from .members import MemberResolver
        self.member_resolver = MemberResolver(type_table, symbol_table)

        self.pattern_lowerer = MirPatternLowerer(self)
        self.expr_lowerer = MirExpressionLowerer(self)
        self.for_loop_lowerer = MirForLoopLowerer(self)

        # ID counters
        self._next_local_id = 0
        self._next_block_id = 0
        self._next_value_id = 0

        # Function state
        self.args: List[Local] = []
        self.locals: List[Local] = []
        self.blocks: Dict[BlockId, Block] = {}

        # Symbol to local mapping
        self._symbol_to_local: Dict[SymbolId, LocalId] = {}

        # Current block being built
        self._current_block: Optional[BlockId] = None

        # Loop stack for break/continue
        self._loop_stack: List[LoopContext] = []

        # Defer stack (one per scope)
        self._defer_stack: List[DeferContext] = []

        # Return block (for handling defers before return)
        self._return_block: Optional[BlockId] = None
        self._return_value_local: Optional[LocalId] = None

        # Entry block
        self.entry_block: Optional[BlockId] = None

        # Errors
        self.errors: List[str] = []

    # -------------------------------------------------------------------------
    # Type Helpers
    # -------------------------------------------------------------------------

    def _bool_type(self) -> TypeId:
        """Get the Bool type."""
        return self.type_table.get_builtin("Bool") or self.type_table.error_type

    def _i32_type(self) -> TypeId:
        """Helper to get the built-in i32 type."""
        return self.type_table.get_builtin("i32") or self.type_table.error_type

    def _i64_type(self) -> TypeId:
        """Helper to get the built-in i64 type."""
        return self.type_table.get_builtin("i64") or self.type_table.error_type

    # -------------------------------------------------------------------------
    # ID Generation
    # -------------------------------------------------------------------------

    def fresh_local_id(self) -> LocalId:
        """Generate a fresh local ID."""
        id = LocalId(self._next_local_id)
        self._next_local_id += 1
        return id

    def fresh_block_id(self) -> BlockId:
        """Generate a fresh block ID."""
        id = BlockId(self._next_block_id)
        self._next_block_id += 1
        return id

    def fresh_value_id(self) -> ValueId:
        """Generate a fresh value ID."""
        id = ValueId(self._next_value_id)
        self._next_value_id += 1
        return id

    # -------------------------------------------------------------------------
    # Local Management
    # -------------------------------------------------------------------------

    def create_local(
        self,
        name: str,
        type_id: TypeId,
        is_mutable: bool = False,
        is_arg: bool = False,
        symbol_id: Optional[SymbolId] = None,
    ) -> LocalId:
        """Create a new local variable."""
        local_id = self.fresh_local_id()
        local = Local(
            id=local_id,
            symbol_id=symbol_id,
            name=name,
            type_id=type_id,
            is_mutable=is_mutable,
            is_arg=is_arg,
        )
        self.locals.append(local)
        if is_arg:
            self.args.append(local)
        if symbol_id is not None:
            self._symbol_to_local[symbol_id] = local_id
        return local_id

    def create_temp(self, type_id: TypeId, prefix: str = "__tmp") -> LocalId:
        """Create a temporary local variable."""
        name = f"{prefix}_{self._next_local_id}"
        return self.create_local(name, type_id, is_mutable=False)

    def get_local_for_symbol(self, symbol_id: SymbolId) -> Optional[LocalId]:
        """Get the local ID for a symbol."""
        return self._symbol_to_local.get(symbol_id)

    def get_local(self, local_id: LocalId) -> Optional[Local]:
        """Get a local by ID."""
        for local in self.locals:
            if local.id == local_id:
                return local
        return None

    # -------------------------------------------------------------------------
    # Block Management
    # -------------------------------------------------------------------------

    def create_block(self) -> BlockId:
        """Create a new basic block."""
        block_id = self.fresh_block_id()
        block = Block(id=block_id)
        self.blocks[block_id] = block
        return block_id

    def switch_to_block(self, block_id: BlockId) -> None:
        """Switch to a block for emitting instructions."""
        self._current_block = block_id

    def current_block(self) -> Optional[Block]:
        """Get the current block."""
        if self._current_block is None:
            return None
        return self.blocks.get(self._current_block)

    def is_terminated(self) -> bool:
        """Check if current block is terminated."""
        block = self.current_block()
        return block is not None and block.is_terminated()

    def emit_op(self, op: Op) -> None:
        """Emit an operation to the current block."""
        block = self.current_block()
        if block is not None and not block.is_terminated():
            block.ops.append(op)

    def emit_terminator(self, term: Terminator) -> None:
        """Emit a terminator to the current block."""
        block = self.current_block()
        if block is not None and not block.is_terminated():
            block.terminator = term

    # -------------------------------------------------------------------------
    # Loop Management
    # -------------------------------------------------------------------------

    def push_loop(self, header: BlockId, exit_block: BlockId) -> None:
        """Push a loop context."""
        self._loop_stack.append(LoopContext(header, exit_block, defer_depth=len(self._defer_stack)))

    def pop_loop(self) -> Optional[LoopContext]:
        """Pop a loop context."""
        if self._loop_stack:
            return self._loop_stack.pop()
        return None

    def current_loop(self) -> Optional[LoopContext]:
        """Get the current loop context."""
        if self._loop_stack:
            return self._loop_stack[-1]
        return None

    # -------------------------------------------------------------------------
    # Defer Management
    # -------------------------------------------------------------------------

    def push_defer_scope(self) -> None:
        """Push a new defer scope."""
        self._defer_stack.append(DeferContext())

    def pop_defer_scope(self) -> List[HirBlock]:
        """Pop a defer scope, returning its deferred blocks."""
        if self._defer_stack:
            ctx = self._defer_stack.pop()
            return ctx.deferred_blocks
        return []

    def add_defer(self, block: HirBlock) -> None:
        """Add a deferred block to the current scope."""
        if self._defer_stack:
            self._defer_stack[-1].deferred_blocks.append(block)

    def emit_defers(self, up_to_scope: int = 0) -> None:
        """Emit deferred blocks from current scope down to up_to_scope."""
        # Execute defers in reverse order (LIFO)
        for i in range(len(self._defer_stack) - 1, up_to_scope - 1, -1):
            if i < len(self._defer_stack):
                ctx = self._defer_stack[i]
                for defer_block in reversed(ctx.deferred_blocks):
                    self.lower_block(defer_block)

    # -------------------------------------------------------------------------
    # Main Entry Point
    # -------------------------------------------------------------------------

    def build(self) -> MirFunction:
        """Build the MIR function."""
        # Create entry block
        self.entry_block = self.create_block()
        self.switch_to_block(self.entry_block)

        # Create locals for parameters
        for param in self.func.params:
            self.create_local(
                name=param.name,
                type_id=param.type_id,
                is_mutable=False,
                is_arg=True,
                symbol_id=param.symbol_id,
            )

        # Push initial defer scope
        self.push_defer_scope()

        # Lower the function body
        if self.func.body is not None:
            self.lower_block(self.func.body)

        # Pop defer scope
        self.pop_defer_scope()

        # If the block isn't terminated, add an implicit return
        if not self.is_terminated():
            void_type = self.type_table.void_type
            if self.func.return_type == void_type:
                self.emit_terminator(Return(value=None))
            else:
                # Non-void function with unterminated block: this can happen for
                # the merge block of an exhaustive switch where every case
                # diverges (e.g. all returns). Mark unreachable rather than
                # emitting a malformed `ret void`.
                self.emit_terminator(Unreachable())

        return MirFunction(
            name=self.func.name,
            symbol_id=self.func.symbol_id,
            args=self.args,
            locals=self.locals,
            ret_type=self.func.return_type,
            blocks=self.blocks,
            entry_block=self.entry_block,
            is_async=self.func.is_async,
            is_method=self.func.is_method,
        )

    # -------------------------------------------------------------------------
    # Block Lowering
    # -------------------------------------------------------------------------

    def lower_block(self, block: HirBlock) -> None:
        """Lower an HIR block to MIR operations."""
        self.push_defer_scope()

        for stmt in block.statements:
            if self.is_terminated():
                break
            self.lower_stmt(stmt)

        # Emit defers for this scope
        defers = self.pop_defer_scope()
        if defers and not self.is_terminated():
            for defer_block in reversed(defers):
                self.lower_block(defer_block)

    # -------------------------------------------------------------------------
    # Statement Lowering
    # -------------------------------------------------------------------------

    def lower_stmt(self, stmt: HirStmt) -> None:
        """Lower a statement to MIR."""
        if isinstance(stmt, HirBlock):
            self.lower_block(stmt)

        elif isinstance(stmt, HirVarDecl):
            self.lower_var_decl(stmt)

        elif isinstance(stmt, HirAssign):
            self.lower_assign(stmt)

        elif isinstance(stmt, HirExprStmt):
            self.expr_lowerer.lower_expr(stmt.expr)  # Discard result

        elif isinstance(stmt, HirReturn):
            self.lower_return(stmt)

        elif isinstance(stmt, HirBreak):
            self.lower_break()

        elif isinstance(stmt, HirContinue):
            self.lower_continue()

        elif isinstance(stmt, HirIf):
            self.lower_if(stmt)

        elif isinstance(stmt, HirIfLet):
            self.lower_if_let(stmt)

        elif isinstance(stmt, HirGuard):
            self.lower_guard(stmt)

        elif isinstance(stmt, HirWhile):
            self.lower_while(stmt)

        elif isinstance(stmt, HirFor):
            self.lower_for(stmt)

        elif isinstance(stmt, HirSwitch):
            self.lower_switch(stmt)

        elif isinstance(stmt, HirDefer):
            self.add_defer(stmt.body)

        else:
            self.errors.append(f"Unknown statement type: {type(stmt).__name__}")

    def lower_var_decl(self, decl: HirVarDecl) -> None:
        """Lower a variable declaration."""
        local_id = self.create_local(
            name=decl.name,
            type_id=decl.type_id,
            is_mutable=decl.is_mutable,
            symbol_id=decl.symbol_id,
        )

        if decl.initializer is not None:
            init_operand = self.expr_lowerer.lower_expr(decl.initializer)
            init_operand = self._coerce_operand(init_operand, decl.type_id)

            # Check if the initializer has a more specific type (e.g., CLOSURE vs FUNCTION)
            init_type = operand_type(init_operand)
            if self.type_table.is_closure(init_type):
                # Update the local's type to be the closure type
                self.locals[local_id.id] = Local(
                    id=local_id,
                    name=decl.name,
                    type_id=init_type,
                    is_mutable=decl.is_mutable,
                    symbol_id=decl.symbol_id,
                )

            actual_type = self.locals[local_id.id].type_id
            place = Place(base=local_id, projections=[], type_id=actual_type)
            self.emit_op(Assign(place=place, value=init_operand))
        else:
            # v2: Every declared local must have a well-defined initial value
            # to prevent reading uninitialized stack memory through the
            # `var x: T;` form. The behaviour mirrors Swift/Kotlin and Go:
            #
            #   * heap-typed locals -> a fresh zero-initialized heap object;
            #   * primitive / RawPtr / Optional locals -> the canonical zero
            #     value (0, false, 0.0, "", null, nil).
            #
            # The compiler injects this initializer transparently so existing
            # `var out: V; <call that populates out>` patterns keep working
            # while no path can observe an undefined value.
            self._emit_default_init(local_id, decl.type_id, decl.name, decl.is_mutable, decl.symbol_id)

    def _emit_default_init(
        self,
        local_id: LocalId,
        type_id: TypeId,
        name: str,
        is_mutable: bool,
        symbol_id: Optional[SymbolId],
    ) -> None:
        """Assign a canonical zero/nil/default value to a freshly declared local."""
        if self.type_table.is_heap_type(type_id):
            result_local = self.create_temp(type_id)
            self.emit_op(AllocObj(
                result=result_local,
                type_id=0,  # Filled in by codegen.
                payload_size=0,
                result_type=type_id,
            ))
            actual_type = self.locals[local_id.id].type_id
            place = Place(base=local_id, projections=[], type_id=actual_type)
            self.emit_op(Assign(
                place=place,
                value=CopyOperand(Place(base=result_local, projections=[], type_id=type_id)),
            ))
            return

        actual_type = self.locals[local_id.id].type_id
        place = Place(base=local_id, projections=[], type_id=actual_type)

        info = self.type_table.get_type(type_id)
        if info is None:
            return

        if info.kind == TypeKind.OPTIONAL:
            # Use MakeNone so the codegen knows how to lay out the value
            # (heap-inner Optionals are bare pointers; primitive-inner
            # Optionals use {i1, T} aggregates).
            none_local = self.create_temp(type_id)
            self.emit_op(MakeNone(result=none_local, result_type=type_id))
            self.emit_op(Assign(
                place=place,
                value=CopyOperand(Place(base=none_local, projections=[], type_id=type_id)),
            ))
            return

        if info.kind == TypeKind.PRIMITIVE:
            data = info.data
            from .types import PrimitiveType
            if isinstance(data, PrimitiveTypeData):
                if data.primitive == PrimitiveType.BOOL:
                    self.emit_op(Assign(
                        place=place,
                        value=ConstantOperand(ConstantKind.BOOL, False, type_id),
                    ))
                    return
                if data.primitive in (
                    PrimitiveType.F32,
                    PrimitiveType.F64,
                ):
                    self.emit_op(Assign(
                        place=place,
                        value=ConstantOperand(ConstantKind.FLOAT, 0.0, type_id),
                    ))
                    return
                if data.primitive == PrimitiveType.VOID:
                    return
                if data.primitive == PrimitiveType.RAW_PTR:
                    # RawPtr needs a NIL operand so codegen emits a null
                    # pointer constant rather than an integer 0.
                    self.emit_op(Assign(
                        place=place,
                        value=ConstantOperand(ConstantKind.NIL, None, type_id),
                    ))
                    return
                # Integers → zero.
                self.emit_op(Assign(
                    place=place,
                    value=ConstantOperand(ConstantKind.INT, 0, type_id),
                ))
                return

        # Unknown kind — leave it; later codegen will produce an error if read.
        return

    def lower_assign(self, assign: HirAssign) -> None:
        """Lower an assignment statement."""
        # Indexed assignment to a container (`v[i] = x`) must route to the
        # collection's `set` method / `__set__` dunder, mirroring how the read
        # path (`v[i]`) routes to `get` / `__get__`. Lowering it as a raw
        # IndexProjection would index into the struct payload (Vec/Dict keep
        # their elements behind an opaque handle, not in a GEP-addressable
        # slot), producing invalid MIR that crashes codegen.
        if isinstance(assign.target, HirSubscript) and self._try_lower_subscript_set(assign):
            return

        value = self.expr_lowerer.lower_expr(assign.value)
        target_place = self.lower_place(assign.target)
        value = self._coerce_operand(value, target_place.type_id)

        if assign.compound_op:
            # Handle compound assignment (+=, -=, etc.)
            base_op = assign.compound_op[0]  # Strip the '='
            if base_op in BINOP_MAP:
                # Load current value
                current = CopyOperand(target_place)
                # Compute new value
                result_local = self.create_temp(target_place.type_id)
                self.emit_op(BinOp(
                    result=result_local,
                    op=BINOP_MAP[base_op],
                    left=current,
                    right=value,
                    result_type=target_place.type_id,
                ))
                value = CopyOperand(Place(
                    base=result_local,
                    projections=[],
                    type_id=target_place.type_id,
                ))

        self.emit_op(Assign(place=target_place, value=value))

    def _try_lower_subscript_set(self, assign: HirAssign) -> bool:
        """Lower `container[index] = value` (and compound forms) to the
        container's `set` method / `__set__` dunder. Returns True if handled.

        Anonymous-struct (tuple) subscripts are genuine addressable places and
        are deliberately left to ``lower_place``.
        """
        from .mir_expr_lowerer import _is_std_collection

        target = assign.target
        if not isinstance(target, HirSubscript) or not target.indices:
            return False

        obj_type = target.object.type_id
        info = self.type_table.get_type(obj_type)
        if not (info and info.kind == TypeKind.STRUCT
                and isinstance(info.data, StructTypeData)):
            return False
        if info.data.symbol_id is None:
            # Anonymous tuple subscript: a real place, handled by lower_place.
            return False

        sym = self.symbol_table.get_symbol(info.data.symbol_id)
        struct_name = sym.name if sym is not None else ""

        set_func: Optional[str] = None
        set_symbol: Optional[SymbolId] = None
        get_func: Optional[str] = None
        if _is_std_collection(struct_name, "Vec") or _is_std_collection(struct_name, "Dict"):
            prefix = self.expr_lowerer._struct_mangled_prefix(obj_type)
            set_func = f"{prefix}_set"
            # Vec.get returns the element type directly (usable for compound
            # forms); Dict.get returns V? so compound is not wired up for it.
            if _is_std_collection(struct_name, "Vec"):
                get_func = f"{prefix}_get"
        else:
            set_method = self.member_resolver.get_method(obj_type, "__set__")
            if set_method is not None:
                set_func = f"{struct_name}___set__"
                set_symbol = set_method.symbol_id
                if self.member_resolver.get_method(obj_type, "__get__") is not None:
                    get_func = f"{struct_name}___get__"
        if set_func is None:
            return False

        # Do not coerce the value: like ordinary method-call arguments
        # (see _lower_method_call), the RHS already type-checks against the
        # container's element type. Coercing here would, for Dict, wrap the
        # value into the V? type reported by the subscript expression.
        obj = self.expr_lowerer.lower_expr(target.object)
        index = self.expr_lowerer.lower_expr(target.indices[0])
        value = self.expr_lowerer.lower_expr(assign.value)

        if assign.compound_op:
            base_op = assign.compound_op[0]
            # Compound forms need a get returning the element type directly.
            if base_op in BINOP_MAP and get_func is not None:
                elem_type = target.type_id
                current = self.create_temp(elem_type)
                self.emit_op(CallStatic(
                    result=current, func_name=get_func, func_symbol=None,
                    args=[obj, index], result_type=elem_type,
                ))
                new_local = self.create_temp(elem_type)
                self.emit_op(BinOp(
                    result=new_local, op=BINOP_MAP[base_op],
                    left=CopyOperand(Place(base=current, projections=[], type_id=elem_type)),
                    right=value, result_type=elem_type,
                ))
                value = CopyOperand(Place(base=new_local, projections=[], type_id=elem_type))
            else:
                self.errors.append(
                    f"compound assignment '{assign.compound_op}' to a "
                    f"'{struct_name}' subscript is not supported"
                )
                return True

        self.emit_op(CallStatic(
            result=None,
            func_name=set_func,
            func_symbol=set_symbol,
            args=[obj, index, value],
            result_type=self.type_table.void_type,
        ))
        return True

    def lower_return(self, ret: HirReturn) -> None:
        """Lower a return statement."""
        # Emit defers before returning
        self.emit_defers()

        if ret.value is not None:
            value = self.expr_lowerer.lower_expr(ret.value)
            value = self._coerce_operand(value, self.func.return_type)
            self.emit_terminator(Return(value=value))
        else:
            self.emit_terminator(Return(value=None))

    def _callee_param_types(self, callee_type: TypeId) -> Optional[Tuple[TypeId, ...]]:
        """Return parameter types for a callable type, or None if not callable."""
        func_data = self.type_table.get_function_data(callee_type)
        if func_data is not None:
            return func_data.params
        closure_data = self.type_table.get_closure_data(callee_type)
        if closure_data is not None:
            return closure_data.params
        return None

    def _coerce_operand(self, operand: Operand, target_type: TypeId) -> Operand:
        """Insert MIR operations for implicit representation-changing coercions."""
        source_type = operand_type(operand)
        if source_type == target_type:
            return operand

        target_info = self.type_table.get_type(target_type)
        if target_info and target_info.kind == TypeKind.EXISTENTIAL:
            from .types import ExistentialTypeData

            data = target_info.data
            if not isinstance(data, ExistentialTypeData):
                return operand

            result_local = self.create_temp(target_type)
            self.emit_op(BoxExistential(
                result=result_local,
                value=operand,
                concrete_type=source_type,
                protocol_type=data.protocol_id,
                result_type=target_type,
            ))
            return CopyOperand(Place(
                base=result_local,
                projections=[],
                type_id=target_type,
            ))

        # Implicit Optional wrapping: T -> T?, nil -> T?
        inner = self.type_table.get_optional_inner(target_type)
        if inner is not None:
            # nil literal -> None
            if isinstance(operand, ConstantOperand) and operand.kind == ConstantKind.NIL:
                result_local = self.create_temp(target_type)
                self.emit_op(MakeNone(result=result_local, result_type=target_type))
                return CopyOperand(Place(base=result_local, projections=[], type_id=target_type))

            # T -> T?: wrap in Some. Recursively coerce inner first
            # (handles e.g. i32 -> i64? via int widening, or nested Optionals).
            if source_type != inner:
                operand = self._coerce_operand(operand, inner)
                source_type = operand_type(operand)
            if source_type == inner:
                result_local = self.create_temp(target_type)
                self.emit_op(MakeSome(
                    result=result_local,
                    value=operand,
                    result_type=target_type,
                ))
                return CopyOperand(Place(base=result_local, projections=[], type_id=target_type))

        # Implicit integer widening (e.g. i32 -> i64).
        if self.type_table.can_widen_int(source_type, target_type):
            result_local = self.create_temp(target_type)
            self.emit_op(CastOp(
                result=result_local,
                operand=operand,
                target_type=target_type,
            ))
            return CopyOperand(Place(base=result_local, projections=[], type_id=target_type))

        return operand

    def lower_break(self) -> None:
        """Lower a break statement."""
        loop = self.current_loop()
        if loop is None:
            self.errors.append("break outside of loop")
            return

        # Emit defers for all scopes from current down to loop scope
        self.emit_defers(up_to_scope=loop.defer_depth)
        self.emit_terminator(Branch(target=loop.exit_block))

    def lower_continue(self) -> None:
        """Lower a continue statement."""
        loop = self.current_loop()
        if loop is None:
            self.errors.append("continue outside of loop")
            return

        # Emit defers for all scopes from current down to loop scope
        self.emit_defers(up_to_scope=loop.defer_depth)
        self.emit_terminator(Branch(target=loop.header_block))

    # -------------------------------------------------------------------------
    # Control Flow Lowering
    # -------------------------------------------------------------------------

    def lower_if(self, if_stmt: HirIf) -> None:
        """Lower an if statement to CFG."""
        # Evaluate condition
        cond = self.expr_lowerer.lower_expr(if_stmt.condition)

        # Create blocks
        then_bb = self.create_block()
        merge_bb = self.create_block()

        if if_stmt.else_block is not None:
            else_bb = self.create_block()
            self.emit_terminator(CondBranch(
                condition=cond,
                true_target=then_bb,
                false_target=else_bb,
            ))

            # Then block
            self.switch_to_block(then_bb)
            self.lower_block(if_stmt.then_block)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

            # Else block
            self.switch_to_block(else_bb)
            if isinstance(if_stmt.else_block, HirBlock):
                self.lower_block(if_stmt.else_block)
            elif isinstance(if_stmt.else_block, HirIf):
                self.lower_if(if_stmt.else_block)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))
        else:
            self.emit_terminator(CondBranch(
                condition=cond,
                true_target=then_bb,
                false_target=merge_bb,
            ))

            # Then block
            self.switch_to_block(then_bb)
            self.lower_block(if_stmt.then_block)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

        # Continue in merge block
        self.switch_to_block(merge_bb)

    def lower_if_let(self, if_let: HirIfLet) -> None:
        """Lower an if-let statement (pattern matching on optional or enum)."""
        scrutinee = self.expr_lowerer.lower_expr(if_let.scrutinee)
        scrutinee_type = operand_type(scrutinee)

        # Handle enum pattern matching (custom enums like Option)
        info = self.type_table.get_type(scrutinee_type)
        pattern = if_let.pattern

        if info and info.kind == TypeKind.ENUM and isinstance(pattern, HirEnumCasePattern):
            # if let .case(let x) = value: dispatch on tag
            tag = self._get_enum_case_tag(scrutinee_type, pattern.case_name)
            if tag is not None:
                match_bb = self.create_block()
                merge_bb = self.create_block()
                else_bb = self.create_block() if if_let.else_block is not None else merge_bb

                tag_local = self.create_temp(self._i32_type())
                self.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
                self.emit_terminator(SwitchInt(
                    value=CopyOperand(Place(base=tag_local, projections=[], type_id=self._i32_type())),
                    cases=[(tag, match_bb)],
                    default=else_bb,
                ))

                self.switch_to_block(match_bb)
                self._bind_enum_pattern(pattern, scrutinee)
                self.lower_block(if_let.then_block)
                if not self.is_terminated():
                    self.emit_terminator(Branch(target=merge_bb))

                if if_let.else_block is not None:
                    self.switch_to_block(else_bb)
                    if isinstance(if_let.else_block, HirBlock):
                        self.lower_block(if_let.else_block)
                    elif isinstance(if_let.else_block, HirIf):
                        self.lower_if(if_let.else_block)
                    if not self.is_terminated():
                        self.emit_terminator(Branch(target=merge_bb))

                self.switch_to_block(merge_bb)
                return

        # Handle Optional<T> pattern matching
        inner_type = self._get_optional_inner_type(scrutinee_type)

        some_bb = self.create_block()
        merge_bb = self.create_block()
        none_bb = self.create_block() if if_let.else_block is not None else merge_bb

        tag_local = self.create_temp(self._i32_type())
        self.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
        tag_operand = CopyOperand(Place(base=tag_local, projections=[], type_id=self._i32_type()))

        self.emit_terminator(SwitchInt(
            value=tag_operand,
            cases=[(1, some_bb)],
            default=none_bb,
        ))

        self.switch_to_block(some_bb)
        if inner_type is not None:
            payload_local = self.create_temp(inner_type)
            self.emit_op(ExtractEnumPayload(
                result=payload_local, enum_val=scrutinee,
                case_name="Some", payload_index=0, result_type=inner_type,
            ))
            self.bind_pattern(if_let.pattern, CopyOperand(Place(base=payload_local, projections=[], type_id=inner_type)))

        self.lower_block(if_let.then_block)
        if not self.is_terminated():
            self.emit_terminator(Branch(target=merge_bb))

        if if_let.else_block is not None:
            self.switch_to_block(none_bb)
            if isinstance(if_let.else_block, HirBlock):
                self.lower_block(if_let.else_block)
            elif isinstance(if_let.else_block, HirIf):
                self.lower_if(if_let.else_block)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

        self.switch_to_block(merge_bb)

    def lower_guard(self, guard: HirGuard) -> None:
        """Lower a guard statement."""
        cond = self.expr_lowerer.lower_expr(guard.condition)

        continue_bb = self.create_block()
        else_bb = self.create_block()

        self.emit_terminator(CondBranch(
            condition=cond,
            true_target=continue_bb,
            false_target=else_bb,
        ))

        # Else block (guard failed). The checker requires it to diverge
        # (return/break/continue), but emit a defensive Unreachable if it
        # somehow falls through so we never produce an unterminated block.
        self.switch_to_block(else_bb)
        self.lower_block(guard.else_block)
        if not self.is_terminated():
            self.emit_terminator(Unreachable())

        # Continue on success
        self.switch_to_block(continue_bb)

    def lower_while(self, while_stmt: HirWhile) -> None:
        """Lower a while loop to CFG."""
        header_bb = self.create_block()
        body_bb = self.create_block()
        exit_bb = self.create_block()

        # Branch to header
        self.emit_terminator(Branch(target=header_bb))

        # Header: check condition
        self.switch_to_block(header_bb)
        cond = self.expr_lowerer.lower_expr(while_stmt.condition)
        self.emit_terminator(CondBranch(
            condition=cond,
            true_target=body_bb,
            false_target=exit_bb,
        ))

        # Body
        self.switch_to_block(body_bb)
        self.push_loop(header_bb, exit_bb)
        self.lower_block(while_stmt.body)
        self.pop_loop()
        if not self.is_terminated():
            self.emit_terminator(Branch(target=header_bb))

        # Continue after loop
        self.switch_to_block(exit_bb)

    def lower_for(self, for_stmt: HirFor) -> None:
        """Lower a for loop to CFG."""
        self.for_loop_lowerer.lower_for(for_stmt)



    def lower_switch(self, switch: HirSwitch) -> None:
        """Lower a switch statement to CFG."""
        scrutinee = self.expr_lowerer.lower_expr(switch.scrutinee)
        scrutinee_type = switch.scrutinee_type

        merge_bb = self.create_block()

        # Check if scrutinee is an enum or optional
        info = self.type_table.get_type(scrutinee_type)
        if info and info.kind == TypeKind.ENUM:
            self._lower_enum_switch(scrutinee, switch.cases, merge_bb)
        elif info and info.kind == TypeKind.OPTIONAL:
            self._lower_optional_switch(scrutinee, scrutinee_type, switch.cases, merge_bb)
        else:
            # For other types (int, etc.), use direct comparison
            self._lower_value_switch(scrutinee, switch.cases, merge_bb)

        self.switch_to_block(merge_bb)

    def _lower_enum_switch(
        self,
        scrutinee: Operand,
        cases: List[HirSwitchCase],
        merge_bb: BlockId,
    ) -> None:
        """Lower a switch on an enum type."""
        # Get discriminant
        tag_type = self._i32_type()
        tag_local = self.create_temp(tag_type)
        self.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
        tag_operand = CopyOperand(Place(base=tag_local, projections=[], type_id=tag_type))

        # Build case blocks and switch cases
        case_blocks: List[Tuple[int, BlockId, HirSwitchCase]] = []
        default_block: Optional[BlockId] = None
        default_case: Optional[HirSwitchCase] = None

        seen_tags: Set[int] = set()
        for case in cases:
            if case.is_default:
                default_block = self.create_block()
                default_case = case
            else:
                case_bb = self.create_block()
                # Collect every tag this case can match, expanding or-patterns
                # (`.a | .b`) so each alternative gets its own switch arm
                # pointing at the shared case block. Without this, an
                # or-pattern (a single HirOrPattern) yields no tag and the
                # case block is left orphaned and unterminated.
                case_tags: List[int] = []
                for pattern, guard in case.patterns:
                    case_tags.extend(self._collect_pattern_tags(pattern))
                for tag in case_tags:
                    if tag in seen_tags:
                        continue  # avoid duplicate SwitchInt keys
                    seen_tags.add(tag)
                    case_blocks.append((tag, case_bb, case))

        if default_block is None:
            default_block = merge_bb

        # Emit switch
        switch_cases = [(tag, bb) for tag, bb, _ in case_blocks]
        self.emit_terminator(SwitchInt(
            value=tag_operand,
            cases=switch_cases,
            default=default_block,
        ))

        # Lower each case block
        seen_blocks: Set[BlockId] = set()
        for tag, case_bb, case in case_blocks:
            if case_bb in seen_blocks:
                continue
            seen_blocks.add(case_bb)

            self.switch_to_block(case_bb)

            # Bind pattern variables
            for pattern, guard in case.patterns:
                self._bind_enum_pattern(pattern, scrutinee)
                if guard is not None:
                    # Handle guard
                    guard_val = self.expr_lowerer.lower_expr(guard)
                    guard_continue = self.create_block()
                    self.emit_terminator(CondBranch(
                        condition=guard_val,
                        true_target=guard_continue,
                        false_target=default_block,
                    ))
                    self.switch_to_block(guard_continue)
                break  # Only bind first pattern

            self.lower_block(case.body)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

        # Default case
        if default_case is not None:
            self.switch_to_block(default_block)
            self.lower_block(default_case.body)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

    def _lower_optional_switch(
        self,
        scrutinee: Operand,
        scrutinee_type: TypeId,
        cases: List[HirSwitchCase],
        merge_bb: BlockId,
    ) -> None:
        """Lower a switch on an Optional<T> by dispatching on its tag.

        Tag convention (matches `_emit_get_tag`):
            0 = None / nil
            1 = Some(payload)

        Recognised patterns:
            * `HirEnumCasePattern(case_name="Some" | "None")`
            * `HirLiteralPattern(value=None)` (i.e. `case nil:`) treated as None
            * Wildcard / binding patterns treated as default (match-all).
        """
        inner_type = self._get_optional_inner_type(scrutinee_type)
        tag_type = self._i32_type()
        tag_local = self.create_temp(tag_type)
        self.emit_op(GetTag(result=tag_local, enum_val=scrutinee))
        tag_operand = CopyOperand(Place(base=tag_local, projections=[], type_id=tag_type))

        # Discover which tags are matched and a default block, if any.
        case_blocks: List[Tuple[int, BlockId, HirSwitchCase, HirPattern]] = []
        default_block: Optional[BlockId] = None
        default_case: Optional[HirSwitchCase] = None
        default_pattern: Optional[HirPattern] = None
        seen_tags: Set[int] = set()

        for case in cases:
            if case.is_default:
                if default_block is None:
                    default_block = self.create_block()
                    default_case = case
                continue

            for pattern, _guard in case.patterns:
                # Patterns that always match -> treat as default
                if isinstance(pattern, (HirWildcardPattern, HirBindingPattern)):
                    if default_block is None:
                        default_block = self.create_block()
                        default_case = case
                        default_pattern = pattern
                    break

                tag = self._optional_pattern_tag(pattern)
                if tag is None or tag in seen_tags:
                    continue
                seen_tags.add(tag)
                case_bb = self.create_block()
                case_blocks.append((tag, case_bb, case, pattern))
                break  # one pattern per case for tag

        if default_block is None:
            default_block = merge_bb

        switch_cases = [(tag, bb) for tag, bb, _, _ in case_blocks]
        self.emit_terminator(SwitchInt(
            value=tag_operand,
            cases=switch_cases,
            default=default_block,
        ))

        for tag, case_bb, case, pattern in case_blocks:
            self.switch_to_block(case_bb)

            if tag == 1 and isinstance(pattern, HirEnumCasePattern) and inner_type is not None:
                # Bind Some(<payload>) — payload is a single value.
                if pattern.payload:
                    payload_local = self.create_temp(inner_type)
                    self.emit_op(ExtractEnumPayload(
                        result=payload_local,
                        enum_val=scrutinee,
                        case_name="Some",
                        payload_index=0,
                        result_type=inner_type,
                    ))
                    payload_operand = CopyOperand(Place(
                        base=payload_local, projections=[], type_id=inner_type,
                    ))
                    self.bind_pattern(pattern.payload[0], payload_operand)

            # Handle guard if present (find it for the matching pattern)
            for p, guard in case.patterns:
                if p is pattern and guard is not None:
                    guard_val = self.expr_lowerer.lower_expr(guard)
                    guard_continue = self.create_block()
                    self.emit_terminator(CondBranch(
                        condition=guard_val,
                        true_target=guard_continue,
                        false_target=default_block,
                    ))
                    self.switch_to_block(guard_continue)
                    break

            self.lower_block(case.body)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

        if default_case is not None:
            self.switch_to_block(default_block)
            if default_pattern is not None and isinstance(default_pattern, HirBindingPattern):
                # `case let x:` binds the whole Optional value.
                self.bind_pattern(default_pattern, scrutinee)
            self.lower_block(default_case.body)
            if not self.is_terminated():
                self.emit_terminator(Branch(target=merge_bb))

    def _optional_pattern_tag(self, pattern: HirPattern) -> Optional[int]:
        """Map a pattern against an Optional<T> scrutinee to its tag (0 = None, 1 = Some)."""
        if isinstance(pattern, HirEnumCasePattern):
            if pattern.case_name == "Some":
                return 1
            if pattern.case_name in ("None", "nil"):
                return 0
            return None
        if isinstance(pattern, HirLiteralPattern):
            # `case nil:`
            if pattern.value is None:
                return 0
        return None

    def _lower_value_switch(
        self,
        scrutinee: Operand,
        cases: List[HirSwitchCase],
        merge_bb: BlockId,
    ) -> None:
        """Lower a switch on a value type (int, string, etc.)."""
        # Create chain of if-else comparisons
        for case in cases:
            if case.is_default:
                self.lower_block(case.body)
                if not self.is_terminated():
                    self.emit_terminator(Branch(target=merge_bb))
                return

            for pattern, guard in case.patterns:
                # Compare scrutinee to pattern value
                match_bb = self.create_block()
                next_bb = self.create_block()

                cond = self._lower_pattern_match(scrutinee, pattern)
                if cond is not None:
                    if guard is not None:
                        # Check pattern first, then guard
                        pattern_match_bb = self.create_block()
                        self.emit_terminator(CondBranch(
                            condition=cond,
                            true_target=pattern_match_bb,
                            false_target=next_bb,
                        ))
                        self.switch_to_block(pattern_match_bb)
                        guard_val = self.expr_lowerer.lower_expr(guard)
                        self.emit_terminator(CondBranch(
                            condition=guard_val,
                            true_target=match_bb,
                            false_target=next_bb,
                        ))
                    else:
                        self.emit_terminator(CondBranch(
                            condition=cond,
                            true_target=match_bb,
                            false_target=next_bb,
                        ))

                    # Match block
                    self.switch_to_block(match_bb)
                    self.bind_pattern(pattern, scrutinee)
                    self.lower_block(case.body)
                    if not self.is_terminated():
                        self.emit_terminator(Branch(target=merge_bb))

                    self.switch_to_block(next_bb)
                else:
                    # Wildcard or binding pattern - always matches
                    self.bind_pattern(pattern, scrutinee)
                    self.lower_block(case.body)
                    if not self.is_terminated():
                        self.emit_terminator(Branch(target=merge_bb))
                    return

        # If no case matched, fall through to merge
        if not self.is_terminated():
            self.emit_terminator(Branch(target=merge_bb))

    def _get_pattern_tag(self, pattern: HirPattern) -> Optional[int]:
        """Get the enum tag for a pattern."""
        if isinstance(pattern, HirEnumCasePattern):
            # Look up the tag from the enum definition
            return self._get_enum_case_tag(pattern.enum_type, pattern.case_name)
        return None

    def _collect_pattern_tags(self, pattern: HirPattern) -> List[int]:
        """Collect every enum tag a pattern can match, expanding or-patterns
        (`.a | .b`) recursively. Returns an empty list for patterns that carry
        no enum tag."""
        if isinstance(pattern, HirOrPattern):
            tags: List[int] = []
            for sub in pattern.patterns:
                tags.extend(self._collect_pattern_tags(sub))
            return tags
        tag = self._get_pattern_tag(pattern)
        return [tag] if tag is not None else []

    def _get_enum_case_tag(self, enum_type: TypeId, case_name: str) -> Optional[int]:
        """Get the tag value for an enum case."""
        info = self.type_table.get_type(enum_type)
        if info is None or info.kind != TypeKind.ENUM:
            return None

        data = info.data
        if not isinstance(data, EnumTypeData):
            return None

        # Look up the enum definition
        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or symbol.decl_node is None:
            return None

        if not isinstance(symbol.decl_node, ast.EnumDecl):
            return None

        tag = 0
        for member in symbol.decl_node.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case in member.cases:
                    if case.name == case_name:
                        return tag
                    tag += 1

        return None

    def _bind_enum_pattern(self, pattern: HirPattern, scrutinee: Operand) -> None:
        """Bind variables in an enum pattern."""
        self.pattern_lowerer._bind_enum_pattern(pattern, scrutinee)

    def _get_enum_payload_type(
        self, enum_type: TypeId, case_name: str, index: int
    ) -> Optional[TypeId]:
        """Get the type of an enum case payload element."""
        return self.pattern_lowerer._get_enum_payload_type(enum_type, case_name, index)

    def _resolve_payload_type(self, type_node: ast.Type) -> TypeId:
        """Resolve enum payload AST type nodes needed during MIR pattern binding."""
        return self.pattern_lowerer._resolve_payload_type(type_node)

    def _lower_pattern_match(
        self, scrutinee: Operand, pattern: HirPattern
    ) -> Optional[Operand]:
        """Lower a pattern match to a boolean condition."""
        return self.pattern_lowerer.lower_pattern_match(scrutinee, pattern)

    def bind_pattern(self, pattern: HirPattern, value: Operand) -> None:
        """Bind variables in a pattern to a value."""
        self.pattern_lowerer.bind_pattern(pattern, value)

    def _get_tuple_element_type(self, tuple_type: TypeId, index: int) -> Optional[TypeId]:
        """Get the type of a tuple element."""
        return self.pattern_lowerer._get_tuple_element_type(tuple_type, index)
    # Expression Lowering
    # -------------------------------------------------------------------------

    def lower_expr(self, expr: HirExpr) -> Operand:
        """Lower an expression to an operand."""
        return self.expr_lowerer.lower_expr(expr)

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

    def _anon_struct_field_index(self, data: StructTypeData, field_name: str) -> Optional[int]:
        """Return positional index for an anon-struct (tuple) field name."""
        fields = data.anon_fields or ()
        for i, (fname, _) in enumerate(fields):
            if fname == field_name:
                return i
        # Fallback: numeric string index
        if field_name.isdigit():
            idx = int(field_name)
            if 0 <= idx < len(fields):
                return idx
        return None

    def _integer_literal_value(self, expr: HirExpr) -> Optional[int]:
        """Extract integer value from a literal expression, if it is one."""
        if isinstance(expr, HirLiteral) and expr.kind == "int":
            return expr.value
        return None

    # Place Lowering
    # -------------------------------------------------------------------------

    def lower_place(self, expr: HirExpr) -> Place:
        """Lower an expression to a place (lvalue)."""
        if isinstance(expr, HirVar):
            local_id = self.get_local_for_symbol(expr.symbol_id)
            if local_id is not None:
                return Place(base=local_id, projections=[], type_id=expr.type_id)
            else:
                # Create a temporary for error recovery
                temp = self.create_temp(expr.type_id)
                return Place(base=temp, projections=[], type_id=expr.type_id)

        elif isinstance(expr, HirFieldAccess):
            # Get base place and add the right projection. Tuple receivers go
            # through FieldProjection using the positional field name
            # GEP; structs use FieldProjection keyed by name.
            base_place = self.lower_place(expr.object)
            base_info = self.type_table.get_type(base_place.type_id)
            proj: PlaceProjection
            if (base_info and base_info.kind == TypeKind.STRUCT
                    and isinstance(base_info.data, StructTypeData)
                    and base_info.data.symbol_id is None):
                # Anonymous struct (tuple): resolve to positional FieldProjection
                idx = self._anon_struct_field_index(base_info.data, expr.field_name)
                fields = base_info.data.anon_fields or ()
                fname = fields[idx][0] if idx is not None and idx < len(fields) else expr.field_name
                proj = FieldProjection(field_name=fname, result_type=expr.type_id)
            else:
                proj = FieldProjection(field_name=expr.field_name, result_type=expr.type_id)
            return Place(
                base=base_place.base,
                projections=base_place.projections + [proj],
                type_id=expr.type_id,
            )

        elif isinstance(expr, HirSubscript):
            # Get base place and add index projection
            base_place = self.lower_place(expr.object)
            if expr.indices:
                base_info = self.type_table.get_type(base_place.type_id)
                # Anonymous struct (tuple) subscript with integer literal →
                # FieldProjection with the positional field name.
                if (base_info and base_info.kind == TypeKind.STRUCT
                        and isinstance(base_info.data, StructTypeData)
                        and base_info.data.symbol_id is None
                        and base_info.data.anon_fields is not None):
                    literal_idx = self._integer_literal_value(expr.indices[0])
                    if literal_idx is not None and 0 <= literal_idx < len(base_info.data.anon_fields):
                        fname = base_info.data.anon_fields[literal_idx][0]
                        proj = FieldProjection(field_name=fname, result_type=expr.type_id)
                        return Place(
                            base=base_place.base,
                            projections=base_place.projections + [proj],
                            type_id=expr.type_id,
                        )
                index = self.expr_lowerer.lower_expr(expr.indices[0])
                proj = IndexProjection(index=index, result_type=expr.type_id)
                return Place(
                    base=base_place.base,
                    projections=base_place.projections + [proj],
                    type_id=expr.type_id,
                )
            return base_place

        else:
            # Not a place - evaluate and store in temp
            value = self.expr_lowerer.lower_expr(expr)
            temp = self.create_temp(expr.type_id)
            place = Place(base=temp, projections=[], type_id=expr.type_id)
            self.emit_op(Assign(place=place, value=value))
            return place

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _get_optional_inner_type(self, optional_type: TypeId) -> Optional[TypeId]:
        """Get the inner type T from Optional[T]."""
        info = self.type_table.get_type(optional_type)
        if info and info.kind == TypeKind.OPTIONAL:
            data = info.data
            if isinstance(data, OptionalTypeData):
                return data.inner
        return None


# =============================================================================
# MIR Program Builder
# =============================================================================

class MirBuilder:
    """
    Main builder for transforming a monomorphized HIR program into MIR.
    """

    def __init__(self, mono_result: MonomorphizationResult) -> None:
        self.program = mono_result.program
        self.type_table = mono_result.type_table
        self.symbol_table = mono_result.symbol_table
        self.function_instances = mono_result.function_instances
        self.struct_instances = mono_result.struct_instances
        self.enum_instances = mono_result.enum_instances

        self.errors: List[str] = []
        self._lambda_functions: List[MirFunction] = []

    def build(self) -> MirBuildResult:
        """Build the MIR program."""
        functions: List[MirFunction] = []
        structs: List[MirStruct] = []
        enums: List[MirEnum] = []
        externs: List[MirExternFunc] = []

        for item in self.program.items:
            if isinstance(item, HirFunction):
                mir_func = self._build_function(item)
                if mir_func is not None:
                    functions.append(mir_func)

            elif isinstance(item, HirExternFunc):
                mir_extern = self._build_extern(item)
                externs.append(mir_extern)

            elif isinstance(item, HirStruct):
                mir_struct = self._build_struct(item)
                structs.append(mir_struct)
                # Build MIR functions for struct methods. The
                # destructor (``__release__``) and the GC trace hook
                # (``__gc_trace__``) are ordinary methods here —
                # codegen picks them up via name convention when
                # emitting the type descriptor table.
                for method in item.methods:
                    mir_method = self._build_method(item.name, method)
                    if mir_method is not None:
                        functions.append(mir_method)

            elif isinstance(item, HirEnum):
                mir_enum = self._build_enum(item)
                enums.append(mir_enum)
                # Build MIR functions for enum methods (mirrors the struct path).
                for method in item.methods:
                    mir_method = self._build_method(item.name, method)
                    if mir_method is not None:
                        functions.append(mir_method)

            elif isinstance(item, HirExtension):
                # Get the type name for method mangling
                type_name = self._get_type_name(item.extended_type)
                # Build MIR functions for extension methods
                for method in item.methods:
                    mir_method = self._build_method(type_name, method)
                    if mir_method is not None:
                        functions.append(mir_method)

        # Add lambda functions
        functions.extend(self._lambda_functions)

        program = MirProgram(
            functions=functions,
            structs=structs,
            enums=enums,
            externs=externs,
        )

        # Validate
        validation_errors = validate_program(program)
        self.errors.extend(validation_errors)

        return MirBuildResult(
            program=program,
            type_table=self.type_table,
            symbol_table=self.symbol_table,
            errors=self.errors,
        )

    def _build_function(self, func: HirFunction) -> Optional[MirFunction]:
        """Build a MIR function from HIR."""
        builder = MirFunctionBuilder(func, self.type_table, self.symbol_table)
        mir_func = builder.build()
        self.errors.extend(builder.errors)

        # Process any pending lambdas from this function.
        # _pending_lambdas is attached to the expression lowerer (see
        # MirExpressionLowerer._lower_lambda), not directly to the
        # MirFunctionBuilder. Look it up on the lowerer.
        pending = getattr(
            getattr(builder, "expr_lowerer", None), "_pending_lambdas", None
        )
        if pending:
            for lambda_name, lam, captures, closure_type in pending:
                lambda_func = self._build_lambda_function(
                    lambda_name, lam, captures, builder, closure_type
                )
                if lambda_func is not None:
                    self._lambda_functions.append(lambda_func)

        return mir_func

    def _build_method(
        self,
        struct_name: str,
        method: HirFunction,
    ) -> Optional[MirFunction]:
        """Build a MIR function for a struct method."""
        # Create a copy of the method with the struct name prefix
        # so it can be looked up by the mangled name
        builder = MethodMirBuilder(
            struct_name, method, self.type_table, self.symbol_table
        )
        mir_func = builder.build()
        self.errors.extend(builder.errors)

        # Methods can also contain lambda literals. Lift those into the
        # program-level lambda function list so codegen can find them.
        pending = getattr(builder, "pending_lambdas", None)
        if pending:
            for lambda_name, lam, captures, closure_type in pending:
                lambda_func = self._build_lambda_function(
                    lambda_name, lam, captures, builder, closure_type
                )
                if lambda_func is not None:
                    self._lambda_functions.append(lambda_func)

        return mir_func

    def _get_type_name(self, type_id: TypeId) -> str:
        """Get the name of a type for method mangling."""
        info = self.type_table.get_type(type_id)
        if info:
            if info.kind == TypeKind.STRUCT:
                data = info.data
                if isinstance(data, StructTypeData):
                    symbol = self.symbol_table.get_symbol(data.symbol_id)
                    if symbol:
                        return symbol.name
            elif info.kind == TypeKind.ENUM:
                data = info.data
                if isinstance(data, EnumTypeData):
                    symbol = self.symbol_table.get_symbol(data.symbol_id)
                    if symbol:
                        return symbol.name
            elif info.kind == TypeKind.PRIMITIVE:
                from .types import PrimitiveTypeData
                if isinstance(info.data, PrimitiveTypeData):
                    return info.data.primitive.value
        return "Unknown"

    def _build_lambda_function(
        self,
        name: str,
        lam: HirLambda,
        captures: List,
        parent_builder: MirFunctionBuilder,
        closure_type: TypeId,
    ) -> Optional[MirFunction]:
        """Build a MIR function for a lambda expression."""
        # Build the lambda as a synthetic function that takes env_ptr as first arg
        lambda_builder = LambdaFunctionBuilder(
            name=name,
            lam=lam,
            captures=captures,
            type_table=self.type_table,
            symbol_table=self.symbol_table,
            closure_type=closure_type,
        )
        mir_func = lambda_builder.build()
        self.errors.extend(lambda_builder.errors)

        # Lambdas defined inside a lambda body now flow through the same
        # full lowering pipeline, so they can themselves enqueue further
        # pending lambdas. Lift those into the top-level lambda list.
        nested = getattr(lambda_builder, "pending_lambdas", None)
        if nested:
            for nested_name, nested_lam, nested_caps, nested_closure_type in nested:
                nested_func = self._build_lambda_function(
                    nested_name, nested_lam, nested_caps,
                    parent_builder, nested_closure_type,
                )
                if nested_func is not None:
                    self._lambda_functions.append(nested_func)

        return mir_func

    def _build_extern(self, ext: HirExternFunc) -> MirExternFunc:
        """Build a MIR extern function declaration."""
        params = [(p.name, p.type_id) for p in ext.params]
        return MirExternFunc(
            name=ext.name,
            symbol_id=ext.symbol_id,
            abi=ext.abi,
            params=params,
            ret_type=ext.return_type,
        )

    def _build_struct(self, struct: HirStruct) -> MirStruct:
        """Build a MIR struct definition."""
        fields = [
            MirField(name=f.name, type_id=f.type_id, is_mutable=f.is_mutable)
            for f in struct.fields
        ]

        # Get struct type
        struct_type = self.type_table.make_struct(struct.symbol_id, ())

        return MirStruct(
            name=struct.name,
            symbol_id=struct.symbol_id,
            fields=fields,
            type_id=struct_type,
        )

    def _build_enum(self, enum: HirEnum) -> MirEnum:
        """Build a MIR enum definition."""
        cases: List[MirEnumCase] = []
        for i, case in enumerate(enum.cases):
            mir_case = MirEnumCase(
                name=case.name,
                tag=i,
                payload_types=case.payload,
            )
            cases.append(mir_case)

        # Get enum type
        enum_type = self.type_table.make_enum(enum.symbol_id, ())

        return MirEnum(
            name=enum.name,
            symbol_id=enum.symbol_id,
            cases=cases,
            type_id=enum_type,
        )


# =============================================================================
# Public API
# =============================================================================

def build_mir(mono_result: MonomorphizationResult) -> MirBuildResult:
    """
    Transform a monomorphized HIR program into MIR.

    Args:
        mono_result: The result from the monomorphization phase

    Returns:
        MirBuildResult with the MIR program
    """
    builder = MirBuilder(mono_result)
    return builder.build()
