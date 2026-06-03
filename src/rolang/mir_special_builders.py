"""Specialized MIR builders for lambdas and methods.

Extracted from mir_builder.py to isolate LambdaFunctionBuilder and
MethodMirBuilder.
"""

from typing import Dict, List, Optional, Set, Tuple

from .symbols import SymbolTable, SymbolId, SymbolKind
from .types import (
    TypeId, TypeKind, TypeTable,
)
from .hir import (
    HirFunction, HirParam, HirBlock, HirReturn, HirVar,
    HirStmt, HirExprStmt, HirVarDecl,
    HirExpr, HirLiteral, HirBinaryOp, HirCall,
    HirLambda,
)
from .mir import (
    LocalId, BlockId,
    Local, Block, MirFunction,
    Place, Operand, CopyOperand, ConstantOperand, ConstantKind,
    BinOp, BinOpKind, CallStatic, ExtractClosureCapture,
    Terminator, Return, Unreachable, Assign,
)


# =============================================================================
# HIR Traversal Helpers
# =============================================================================


def _iter_hir_vars(node, seen: Set[int]):
    """Yield every HirVar reachable from a HIR node.

    Walks dataclass fields generically so newly added HIR node types are
    covered without explicit per-class handling. Cycles are guarded via a
    seen-id set.
    """
    if node is None:
        return

    node_id = id(node)
    if node_id in seen:
        return
    seen.add(node_id)

    if isinstance(node, HirVar):
        yield node
        return

    if isinstance(node, (list, tuple)):
        for item in node:
            yield from _iter_hir_vars(item, seen)
        return

    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_hir_vars(value, seen)
        return

    # dataclass instances – recurse into their fields
    import dataclasses
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        for field in dataclasses.fields(node):
            value = getattr(node, field.name)
            yield from _iter_hir_vars(value, seen)


# =============================================================================
# Lambda Function Builder
# =============================================================================


class LambdaFunctionBuilder:
    """
    Builds a MIR function from a lambda expression.

    Lambda functions take the closure object as the first argument,
    followed by the lambda's regular parameters. Captured values are
    loaded from the closure object's payload at function entry.

    The lambda body is lowered through the *full* :class:`MirFunctionBuilder`
    pipeline so every HIR construct (``if`` / ``while`` / ``for`` /
    ``switch`` / ``guard`` / ``defer`` / pattern matching / etc.) is
    handled.
    """

    def __init__(
        self,
        name: str,
        lam: HirLambda,
        captures: List,
        type_table: TypeTable,
        symbol_table: SymbolTable,
        closure_type: TypeId,
    ) -> None:
        self.name = name
        self.lam = lam
        self.captures = captures
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.closure_type = closure_type
        self.errors: List[str] = []
        self.pending_lambdas: List = []

    def build(self) -> Optional[MirFunction]:
        """Build the MIR function for this lambda."""
        # Determine return type from the lambda's function type.
        func_data = self.type_table.get_function_data(self.lam.type_id)
        return_type = (
            func_data.return_type if func_data else self.type_table.void_type
        )

        # Synthesize a HirFunction wrapping the lambda. We prepend an
        # implicit ``__closure`` parameter; captures are not HIR
        # parameters (they're loaded from the closure object at MIR
        # entry — see ``_LambdaInnerBuilder.build`` below).
        closure_param = HirParam(
            name="__closure",
            symbol_id=SymbolId(-1),
            type_id=self.closure_type,
        )
        synth_func = HirFunction(
            name=self.name,
            symbol_id=SymbolId(-1),
            params=[closure_param] + list(self.lam.params),
            return_type=return_type,
            body=self.lam.body,
        )

        inner = _LambdaInnerBuilder(
            synth_func,
            self.type_table,
            self.symbol_table,
            self.captures,
        )
        mir_func = inner.build()
        self.errors.extend(inner.errors)

        # Lift any nested lambdas the body produced so the surrounding
        # build pipeline can emit them as program-level functions.
        nested = getattr(
            getattr(inner, "expr_lowerer", None), "_pending_lambdas", None,
        )
        if nested:
            self.pending_lambdas.extend(nested)

        return mir_func


class _LambdaInnerBuilder:
    """Adapter around :class:`MirFunctionBuilder` that injects the
    closure-pointer arg + capture-load prologue before the lambda body
    is lowered.

    Defined inline rather than as a subclass to side-step the circular
    import between ``mir_builder`` and ``mir_special_builders`` —
    ``MirFunctionBuilder`` lives in ``mir_builder.py`` which already
    imports this module.
    """

    def __init__(
        self,
        func: HirFunction,
        type_table: TypeTable,
        symbol_table: SymbolTable,
        captures: List,
    ) -> None:
        # Import here to avoid the circular import at module load time.
        from .mir_builder import MirFunctionBuilder
        self._builder = MirFunctionBuilder(func, type_table, symbol_table)
        self.captures = captures
        self.errors = self._builder.errors

    @property
    def expr_lowerer(self):
        return self._builder.expr_lowerer

    def build(self) -> MirFunction:
        """Mirror :meth:`MirFunctionBuilder.build` but interleave the
        closure-pointer arg and capture loads before the body is
        lowered.
        """
        b = self._builder

        # 1. Entry block.
        b.entry_block = b.create_block()
        b.switch_to_block(b.entry_block)

        # 2. The synthetic ``__closure`` parameter is the first arg.
        closure_param = b.func.params[0]
        closure_local_id = b.create_local(
            name=closure_param.name,
            type_id=closure_param.type_id,
            is_mutable=False,
            is_arg=True,
            symbol_id=closure_param.symbol_id,
        )
        closure_operand = CopyOperand(Place(
            base=closure_local_id,
            projections=[],
            type_id=closure_param.type_id,
        ))

        # 3. Load every captured value into its own local so the body
        #    can resolve the symbol just like a normal binding.
        for i, cap in enumerate(self.captures):
            cap_local = b.create_local(
                name=cap.name,
                type_id=cap.type_id,
                symbol_id=cap.symbol_id,
            )
            b.emit_op(ExtractClosureCapture(
                result=cap_local,
                closure=closure_operand,
                capture_index=i,
                result_type=cap.type_id,
            ))

        # 4. Create locals for the lambda's own parameters.
        for param in b.func.params[1:]:
            b.create_local(
                name=param.name,
                type_id=param.type_id,
                is_mutable=False,
                is_arg=True,
                symbol_id=param.symbol_id,
            )

        # 5. Lower the body via the full statement/expression dispatcher.
        b.push_defer_scope()
        if b.func.body is not None:
            b.lower_block(b.func.body)
        b.pop_defer_scope()

        # 6. Implicit return / unreachable for unterminated tails.
        if not b.is_terminated():
            void_type = b.type_table.void_type
            if b.func.return_type == void_type:
                b.emit_terminator(Return(value=None))
            else:
                b.emit_terminator(Unreachable())

        return MirFunction(
            name=b.func.name,
            symbol_id=None,  # Lambdas don't have a real source symbol.
            args=b.args,
            locals=b.locals,
            ret_type=b.func.return_type,
            blocks=b.blocks,
            entry_block=b.entry_block,
            is_async=False,
            is_method=False,
        )


# =============================================================================
# Method MIR Builder
# =============================================================================


class MethodMirBuilder:
    """
    Builder for transforming a struct method into a MIR function.

    This handles:
    - Adding 'self' as the first parameter
    - Mangling the function name (e.g., Point.sum)
    """

    def __init__(
        self,
        struct_name: str,
        method: HirFunction,
        type_table: TypeTable,
        symbol_table: SymbolTable,
    ) -> None:
        self.struct_name = struct_name
        self.method = method
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.errors: List[str] = []
        self.pending_lambdas: List = []

    def _find_self_symbol(self) -> Optional[SymbolId]:
        """Find the self symbol by looking at HirVar references in the body.

        Walks every reachable node so a `self` reference buried inside an `if`
        condition, switch arm, or other compound statement is still found.
        """
        if self.method.body is None:
            return None

        seen: Set[int] = set()
        for var in _iter_hir_vars(self.method.body, seen):
            if var.name == "self":
                return var.symbol_id
        return None

    def build(self) -> Optional[MirFunction]:
        """Build a MIR function for the method."""
        struct_symbol = self.symbol_table.get_type_symbol(self.struct_name)
        if struct_symbol is not None:
            sym = self.symbol_table.get_symbol(struct_symbol)
            struct_kind = sym.kind if sym else SymbolKind.STRUCT
        else:
            self.errors.append(f"Cannot find type {self.struct_name} for method")
            return None

        if struct_kind == SymbolKind.ENUM:
            struct_type = self.type_table.make_enum(struct_symbol, ())
        elif struct_kind == SymbolKind.BUILTIN_TYPE:
            struct_type = self.type_table.get_builtin(self.struct_name)
        else:
            struct_type = self.type_table.make_struct(struct_symbol, ())

        params = list(self.method.params)
        if not self.method.is_static:
            self_symbol = self._find_self_symbol()
            params = [
                HirParam(
                    name="self",
                    type_id=struct_type,
                    symbol_id=self_symbol,
                )
            ] + params

        # Mangle name if not already mangled (extension methods aren't mangled by monomorphization)
        method_name = self.method.name
        if not method_name.startswith(f"{self.struct_name}_"):
            method_name = f"{self.struct_name}_{method_name}"

        modified_func = HirFunction(
            name=method_name,
            symbol_id=self.method.symbol_id,
            params=params,
            return_type=self.method.return_type,
            body=self.method.body,
            is_async=self.method.is_async,
            is_method=True,
            is_static=self.method.is_static,
        )

        # Use standard builder
        from .mir_builder import MirFunctionBuilder
        builder = MirFunctionBuilder(modified_func, self.type_table, self.symbol_table)
        mir_func = builder.build()
        self.errors.extend(builder.errors)
        self.pending_lambdas = getattr(
            getattr(builder, "expr_lowerer", None), "_pending_lambdas", None
        ) or []
        return mir_func
