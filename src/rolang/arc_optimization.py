"""
ARC (Automatic Reference Counting) Optimization Pass for RoLang.

This module implements optimizations to reduce ARC overhead:

1. Adjacent Pair Cancellation:
   retain _5
   release _5  -> (remove both)

2. Redundant Retain Elimination:
   _10 = Ref.new(...)  // Creates owned
   retain _10          -> (remove - unnecessary)
   return _10

3. Last-Use Release Motion:
   let r = Ref.new(x)
   let v = r.value     // Last use
   release r           // <- Move release here
   // ... 50 lines ...
   // <- Instead of at scope end

Pipeline position:
    MIR (from mir_builder)
            ↓
       ARC Insertion
            ↓
       ARC Optimization    ← THIS PHASE
            ↓
          Codegen
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .types import TypeTable
from .mir import (
    # ID types
    LocalId, BlockId,
    # Core types
    Block, MirFunction, MirProgram,
    # Operations
    Op, Retain, Release, Assign, CallStatic,
    Load, Store, ExtractField, ExtractEnumPayload,
    ExistentialCheckType, ExistentialUnbox,
    MakeStruct, MakeEnum, MakeSome, MakeNone, MakeClosure,
    CallVTable, CallWitness, CallClosure,
    BoxExistential, GetTag, Clone, CastOp,
    BinOp, CmpOp, UnaryOp, AllocObj,
    TaskSpawn, TaskJoin, TaskComplete, TaskGetResult,
    SchedulerRun, AllocAsyncFrame,
    ExtractClosureCapture,
    # Operands
    Operand, CopyOperand, MoveOperand, ConstantOperand,
    # Terminators
    Return, CondBranch, SwitchInt,
    # Helpers
    get_terminator_targets,
)
from .arc_insertion import LocalInfo, collect_ref_locals


# =============================================================================
# Optimization Statistics
# =============================================================================

@dataclass
class ArcOptStats:
    """Statistics from ARC optimization."""
    adjacent_pairs_removed: int = 0
    redundant_retains_removed: int = 0
    releases_moved: int = 0
    total_retains_before: int = 0
    total_releases_before: int = 0
    total_retains_after: int = 0
    total_releases_after: int = 0


# =============================================================================
# Use-Def Analysis for ARC Optimization
# =============================================================================

@dataclass
class UseInfo:
    """Information about a use of a ref-typed local."""
    block_id: BlockId
    op_index: int
    is_last_use: bool = False


@dataclass
class LocalUseInfo:
    """Use information for a single local variable."""
    local_id: LocalId
    definition_block: Optional[BlockId] = None
    definition_op_index: int = -1
    uses: List[UseInfo] = field(default_factory=list)
    last_use_block: Optional[BlockId] = None
    last_use_op_index: int = -1


def analyze_uses(
    func: MirFunction,
    ref_locals: Dict[LocalId, LocalInfo],
) -> Dict[LocalId, LocalUseInfo]:
    """
    Analyze uses of ref-typed locals.

    Returns a map from LocalId to use information.
    """
    use_info: Dict[LocalId, LocalUseInfo] = {
        lid: LocalUseInfo(local_id=lid) for lid in ref_locals
    }

    def record_use(local_id: LocalId, block_id: BlockId, op_index: int) -> None:
        if local_id in use_info:
            use_info[local_id].uses.append(UseInfo(block_id, op_index))

    def record_def(local_id: LocalId, block_id: BlockId, op_index: int) -> None:
        if local_id in use_info:
            info = use_info[local_id]
            if info.definition_block is None:
                info.definition_block = block_id
                info.definition_op_index = op_index

    def check_operand(op: Operand, block_id: BlockId, op_index: int) -> None:
        if isinstance(op, (CopyOperand, MoveOperand)):
            record_use(op.place.base, block_id, op_index)

    # Traverse all blocks
    for block_id, block in func.blocks.items():
        for op_index, op in enumerate(block.ops):
            # Check for definitions
            if isinstance(op, Assign):
                if not op.place.projections:
                    record_def(op.place.base, block_id, op_index)
                check_operand(op.value, block_id, op_index)
            elif isinstance(op, CallStatic):
                if op.result is not None:
                    record_def(op.result, block_id, op_index)
                for arg in op.args:
                    check_operand(arg, block_id, op_index)
            elif isinstance(op, (Retain, Release)):
                check_operand(op.operand, block_id, op_index)
            elif isinstance(op, Load):
                record_def(op.result, block_id, op_index)
            elif isinstance(op, Store):
                check_operand(op.value, block_id, op_index)
            elif isinstance(op, ExtractField):
                check_operand(op.aggregate, block_id, op_index)
                record_def(op.result, block_id, op_index)
            elif isinstance(op, ExtractEnumPayload):
                check_operand(op.enum_val, block_id, op_index)
                record_def(op.result, block_id, op_index)
            elif isinstance(op, ExistentialCheckType):
                check_operand(op.existential, block_id, op_index)
                record_def(op.result, block_id, op_index)
            elif isinstance(op, ExistentialUnbox):
                check_operand(op.existential, block_id, op_index)
                record_def(op.result, block_id, op_index)

        # Check terminator
        if block.terminator:
            if isinstance(block.terminator, Return) and block.terminator.value:
                check_operand(block.terminator.value, block_id, len(block.ops))
            elif isinstance(block.terminator, CondBranch):
                check_operand(block.terminator.condition, block_id, len(block.ops))
            elif isinstance(block.terminator, SwitchInt):
                check_operand(block.terminator.value, block_id, len(block.ops))

    # Determine last use for each local. The intra-block release-sinking
    # pass below only acts when ``last_use_block == this_block``, so it is
    # safe to mark a "last use" only when every use lives in the same block.
    # Cross-block sinking would require a CFG-aware reverse-postorder
    # traversal (block IDs are NOT guaranteed to follow execution order —
    # async lowering, for example, renumbers blocks to ``1000+`` which
    # breaks the old ``max(block_id.id, op_index)`` heuristic and could
    # have nominated a use that CFG-precedes another as the "last").
    for local_id, info in use_info.items():
        if not info.uses:
            continue
        first_block = info.uses[0].block_id
        if any(u.block_id != first_block for u in info.uses):
            # Multi-block use — don't attempt to sink anything.
            continue
        last = max(info.uses, key=lambda u: u.op_index)
        last.is_last_use = True
        info.last_use_block = last.block_id
        info.last_use_op_index = last.op_index

    return use_info


def _is_arc_op(func: MirFunction, block_id: BlockId, op_index: int) -> bool:
    """Check whether an op at a given position is an ARC retain or release."""
    block = func.blocks.get(block_id)
    if block is None or op_index < 0 or op_index >= len(block.ops):
        return False
    return isinstance(block.ops[op_index], (Retain, Release))


# =============================================================================
# Optimization Passes
# =============================================================================

class ArcOptimizer:
    """
    Optimizes ARC operations in MIR functions.

    Implements three optimization passes:
    1. Adjacent pair cancellation
    2. Redundant retain elimination
    3. Last-use release motion
    """

    def __init__(self, type_table: TypeTable) -> None:
        self.type_table = type_table
        self.stats = ArcOptStats()

    def optimize(self, func: MirFunction) -> MirFunction:
        """
        Apply all ARC optimizations to a function.

        Args:
            func: The MIR function to optimize

        Returns:
            The optimized function
        """
        # Count initial ARC ops
        self._count_arc_ops(func, before=True)

        # Collect ref-typed locals
        ref_locals = collect_ref_locals(func, self.type_table)
        if not ref_locals:
            return func

        # Analyze uses
        use_info = analyze_uses(func, ref_locals)

        # Apply optimizations in order
        func = self._eliminate_adjacent_pairs(func, ref_locals)
        func = self._eliminate_borrowed_single_use(func, ref_locals, use_info)
        func = self._move_releases_to_last_use(func, ref_locals, use_info)

        # Count final ARC ops
        self._count_arc_ops(func, before=False)

        return func

    def _count_arc_ops(self, func: MirFunction, before: bool) -> None:
        """Count retain/release operations."""
        retains = 0
        releases = 0

        for block in func.blocks.values():
            for op in block.ops:
                if isinstance(op, Retain):
                    retains += 1
                elif isinstance(op, Release):
                    releases += 1

        if before:
            self.stats.total_retains_before = retains
            self.stats.total_releases_before = releases
        else:
            self.stats.total_retains_after = retains
            self.stats.total_releases_after = releases

    # Ops that can sit between a Retain(x) and Release(x) without invalidating
    # pair cancellation. The requirement is "cannot decrement ANY refcount and
    # cannot free memory": plain data movement and arithmetic qualify; calls
    # (the callee may release aliases of x), Release of any local (its teardown
    # may transitively release x), Retain (overlapping pairs), and allocating
    # ops (cycle-GC trigger point) do not. A raw Store never releases — ARC
    # emits explicit Release ops for overwritten heap slots.
    _PAIR_WINDOW_SAFE_OPS = (Assign, Load, Store, BinOp, CmpOp, UnaryOp,
                             CastOp, GetTag)
    _PAIR_WINDOW_LIMIT = 8

    def _eliminate_adjacent_pairs(
        self,
        func: MirFunction,
        ref_locals: Dict[LocalId, LocalInfo],
    ) -> MirFunction:
        """
        Eliminate retain/release pairs on the same local separated only by
        ops that cannot change any reference count.

        Pattern (ownership transfer through a temp — every `let y = f()`
        binding lowers to it):
            _t = call f()
            retain _t          ; +1 for the copy into y
            y = _t
            release _t         ; temp dies
        ->
            _t = call f()
            y = _t             ; the call's +1 transfers to y

        The window between the pair must not contain calls, other ARC ops,
        allocations, or a redefinition of the local (locals are mutable
        slots, so a redefined local names a different value).
        """
        new_blocks: Dict[BlockId, Block] = {}

        for block_id, block in func.blocks.items():
            ops = block.ops
            to_remove: Set[int] = set()

            for i, op in enumerate(ops):
                if i in to_remove or not isinstance(op, Retain):
                    continue
                local = self._get_operand_local(op.operand)
                if local is None:
                    continue

                j = i + 1
                scanned = 0
                while j < len(ops) and scanned <= self._PAIR_WINDOW_LIMIT:
                    nxt = ops[j]
                    if j in to_remove:
                        # Already-cancelled ARC op: it will not execute, so it
                        # is transparent for this window.
                        j += 1
                        continue
                    if isinstance(nxt, Release):
                        if self._get_operand_local(nxt.operand) == local:
                            to_remove.add(i)
                            to_remove.add(j)
                            self.stats.adjacent_pairs_removed += 1
                        # Any other Release could transitively free `local`'s
                        # object once the pair is gone — stop either way.
                        break
                    if isinstance(nxt, Retain):
                        break
                    if not isinstance(nxt, self._PAIR_WINDOW_SAFE_OPS):
                        break
                    if self._def_in_op(nxt) == local:
                        break
                    j += 1
                    scanned += 1

            new_ops = [op for k, op in enumerate(ops) if k not in to_remove]

            new_blocks[block_id] = Block(
                id=block.id,
                ops=new_ops,
                terminator=block.terminator,
            )

        return MirFunction(
            name=func.name,
            symbol_id=func.symbol_id,
            args=func.args,
            locals=func.locals,
            ret_type=func.ret_type,
            blocks=new_blocks,
            entry_block=func.entry_block,
            is_async=func.is_async,
            is_method=func.is_method,
        )

    def _eliminate_borrowed_single_use(
        self,
        func: MirFunction,
        ref_locals: Dict[LocalId, LocalInfo],
        use_info: Dict[LocalId, LocalUseInfo],
    ) -> MirFunction:
        """Eliminate retain/release when a ref local from ExtractField
        is only used once as a read-only call argument."""
        single_use_borrowed: Set[LocalId] = set()

        # Elimination removes BOTH the Retain and the Release of the local, so
        # it is only balanced if a Retain on *this exact local* exists. Borrowed
        # reads (ExtractField / ExtractEnumPayload / ExtractClosureCapture /
        # ExistentialUnbox / Load) get a post-retain ON the local, so they
        # qualify. An OWNED value copied into the local — e.g. `let t = make()`,
        # lowered to `_3 = _5` (Assign) whose retain is on the SOURCE `_5`, not
        # `_3` — has no Retain(_3); removing only `_3`'s release would leave the
        # object's refcount permanently elevated (a leak). Require Retain(local).
        retained: Set[LocalId] = set()
        for _blk in func.blocks.values():
            for _o in _blk.ops:
                if isinstance(_o, Retain):
                    _rl = self._get_operand_local(_o.operand)
                    if _rl is not None:
                        retained.add(_rl)

        for local_id, info in use_info.items():
            if local_id not in ref_locals or not ref_locals[local_id].needs_arc:
                continue
            if local_id not in retained:
                continue
            # Filter out Retain/Release uses — they are ARC overhead, not semantic uses
            non_arc_uses = [u for u in info.uses
                          if not _is_arc_op(func, u.block_id, u.op_index)]
            if len(non_arc_uses) != 1:
                continue
            use = non_arc_uses[0]
            block = func.blocks.get(use.block_id)
            if block is None:
                continue
            if use.op_index < 0 or use.op_index >= len(block.ops):
                continue
            op = block.ops[use.op_index]
            if not isinstance(op, (CallStatic, CallVTable, CallClosure)):
                continue
            if getattr(op, 'result', None) == local_id:
                continue
            single_use_borrowed.add(local_id)

        if not single_use_borrowed:
            return func

        new_blocks: Dict[BlockId, Block] = {}
        removed = 0

        for block_id, block in func.blocks.items():
            new_ops: List[Op] = []
            for op in block.ops:
                if isinstance(op, Retain):
                    local = self._get_operand_local(op.operand)
                    if local is not None and local in single_use_borrowed:
                        removed += 1
                        continue
                elif isinstance(op, Release):
                    local = self._get_operand_local(op.operand)
                    if local is not None and local in single_use_borrowed:
                        removed += 1
                        continue
                new_ops.append(op)
            new_blocks[block_id] = Block(
                id=block.id,
                ops=new_ops,
                terminator=block.terminator,
            )

        self.stats.borrowed_uses_removed = removed
        return MirFunction(
            name=func.name,
            symbol_id=func.symbol_id,
            args=func.args,
            locals=func.locals,
            ret_type=func.ret_type,
            blocks=new_blocks,
            entry_block=func.entry_block,
            is_async=func.is_async,
            is_method=func.is_method,
        )
    def _move_releases_to_last_use(
        self,
        func: MirFunction,
        ref_locals: Dict[LocalId, LocalInfo],
        use_info: Dict[LocalId, LocalUseInfo],
    ) -> MirFunction:
        """
        Move release operations to immediately after the last use.

        This reduces the lifetime of references, allowing earlier deallocation.

        Both intra-block and cross-block sinking are supported. Cross-block
        sinking is restricted to walking backward through a chain of
        single-pred / single-succ blocks: the edge between the source and
        target blocks must be the *only* way control can reach the
        release, so moving the release earlier in that chain is equivalent
        to releasing at the start of the original block.
        """
        # Build per-block predecessor / successor maps. Successors come
        # straight from the terminator; predecessors are the inverse.
        succs: Dict[BlockId, List[BlockId]] = {
            bid: list(get_terminator_targets(b.terminator))
            for bid, b in func.blocks.items()
        }
        preds: Dict[BlockId, List[BlockId]] = {bid: [] for bid in func.blocks}
        for bid, ss in succs.items():
            for s in ss:
                if s in preds:
                    preds[s].append(bid)

        # Precompute per-block, per-local "use indices" and "def indices".
        # `block_uses[bid][local] = sorted list of op-indices where local is
        # read by an op or by the block's terminator (treated as op-index
        # ``len(ops)``).
        # `block_defs[bid][local] = list of op-indices where the local is
        # written (Assign with no projections / CallStatic result / Load).
        block_uses: Dict[BlockId, Dict[LocalId, List[int]]] = {}
        block_defs: Dict[BlockId, Dict[LocalId, List[int]]] = {}

        for block_id, block in func.blocks.items():
            uses_in_block: Dict[LocalId, List[int]] = {}
            defs_in_block: Dict[LocalId, List[int]] = {}
            for op_idx, op in enumerate(block.ops):
                for u in self._uses_in_op(op):
                    if u in ref_locals:
                        uses_in_block.setdefault(u, []).append(op_idx)
                d = self._def_in_op(op)
                if d is not None and d in ref_locals:
                    defs_in_block.setdefault(d, []).append(op_idx)
            # Terminator reads at position len(ops)
            term = block.terminator
            term_idx = len(block.ops)
            if term is not None:
                if isinstance(term, Return) and term.value is not None:
                    base = self._get_operand_local(term.value)
                    if base is not None and base in ref_locals:
                        uses_in_block.setdefault(base, []).append(term_idx)
                elif isinstance(term, CondBranch):
                    base = self._get_operand_local(term.condition)
                    if base is not None and base in ref_locals:
                        uses_in_block.setdefault(base, []).append(term_idx)
                elif isinstance(term, SwitchInt):
                    base = self._get_operand_local(term.value)
                    if base is not None and base in ref_locals:
                        uses_in_block.setdefault(base, []).append(term_idx)
            block_uses[block_id] = uses_in_block
            block_defs[block_id] = defs_in_block

        # Decide where each release should land.
        # plan_inserts[block_id] = list of (insert_at_index, Release op) — the
        # release is inserted *before* the op currently at that index.
        # plan_deletes[block_id] = set of original op indices to drop.
        plan_inserts: Dict[BlockId, List[Tuple[int, Op]]] = {bid: [] for bid in func.blocks}
        plan_deletes: Dict[BlockId, Set[int]] = {bid: set() for bid in func.blocks}

        for block_id, block in func.blocks.items():
            for op_idx, op in enumerate(block.ops):
                if not isinstance(op, Release):
                    continue
                local = self._get_operand_local(op.operand)
                if local is None or local not in ref_locals:
                    continue

                target = self._find_sink_target(
                    func=func,
                    preds=preds,
                    succs=succs,
                    block_uses=block_uses,
                    block_defs=block_defs,
                    plan_inserts=plan_inserts,
                    plan_deletes=plan_deletes,
                    local=local,
                    src_block=block_id,
                    src_idx=op_idx,
                )
                if target is None:
                    continue
                tgt_block, tgt_idx = target
                if tgt_block == block_id and tgt_idx == op_idx:
                    continue  # No-op (already at the right place)
                plan_deletes[block_id].add(op_idx)
                plan_inserts[tgt_block].append((tgt_idx, op))
                self.stats.releases_moved += 1

        if not any(plan_deletes.values()) and not any(
            ins for ins in plan_inserts.values()
        ):
            return func

        # Apply: rebuild each block's op list in one pass.
        new_blocks: Dict[BlockId, Block] = {}
        for block_id, block in func.blocks.items():
            deletes = plan_deletes[block_id]
            inserts = sorted(plan_inserts[block_id], key=lambda x: x[0])
            if not deletes and not inserts:
                new_blocks[block_id] = block
                continue

            new_ops: List[Op] = []
            insert_iter = iter(inserts)
            next_insert: Optional[Tuple[int, Op]] = next(insert_iter, None)
            for i, op in enumerate(block.ops):
                while next_insert is not None and next_insert[0] == i:
                    new_ops.append(next_insert[1])
                    next_insert = next(insert_iter, None)
                if i not in deletes:
                    new_ops.append(op)
            # Insertions targeting end-of-block (idx == len(ops)).
            while next_insert is not None:
                new_ops.append(next_insert[1])
                next_insert = next(insert_iter, None)
            new_blocks[block_id] = Block(
                id=block.id,
                ops=new_ops,
                terminator=block.terminator,
            )

        return MirFunction(
            name=func.name,
            symbol_id=func.symbol_id,
            args=func.args,
            locals=func.locals,
            ret_type=func.ret_type,
            blocks=new_blocks,
            entry_block=func.entry_block,
            is_async=func.is_async,
            is_method=func.is_method,
        )

    def _find_sink_target(
        self,
        func: MirFunction,
        preds: Dict[BlockId, List[BlockId]],
        succs: Dict[BlockId, List[BlockId]],
        block_uses: Dict[BlockId, Dict[LocalId, List[int]]],
        block_defs: Dict[BlockId, Dict[LocalId, List[int]]],
        plan_inserts: Dict[BlockId, List[Tuple[int, Op]]],
        plan_deletes: Dict[BlockId, Set[int]],
        local: LocalId,
        src_block: BlockId,
        src_idx: int,
    ) -> Optional[Tuple[BlockId, int]]:
        """Find the best (block, op-index) to sink a release of ``local``.

        The release currently lives at ``(src_block, src_idx)``. Returns
        ``None`` if no useful sink target exists (or if sinking would not
        shorten the lifetime).
        """
        # 1. Intra-block sink: scan backward in src_block from src_idx-1
        #    looking for the latest use of local (skipping ops slated for
        #    deletion in this same pass, so two releases of the same
        #    local don't both target the same slot).
        intra = self._latest_use_in_block_before(
            block_uses, block_defs, local, src_block, src_idx,
            deletes=plan_deletes.get(src_block, set()),
        )
        if intra is not None:
            return (src_block, intra + 1)

        # 2. Cross-block sink: walk backward through single-pred /
        #    single-succ chains. We start "above" src_block (no use found
        #    inside it before src_idx, and no def either).
        visited: Set[BlockId] = {src_block}
        cur_block = src_block

        # Refuse to leave src_block if it contains a def of local before
        # src_idx (the value flowing through the release is born inside
        # src_block — sinking past predecessors would be wrong).
        cur_defs = block_defs.get(src_block, {}).get(local, [])
        if any(d < src_idx for d in cur_defs):
            return None

        while True:
            pred_list = preds.get(cur_block, [])
            if len(pred_list) != 1:
                return None
            pred = pred_list[0]
            if pred in visited:
                return None  # loop / back-edge — bail
            if len(succs.get(pred, [])) != 1:
                return None  # forked predecessor — sinking would skip a branch
            visited.add(pred)

            # In pred, look for the latest use (within the whole block,
            # since pred ends at its terminator — we may freely walk back
            # over every op).
            pred_use = self._latest_use_in_block_before(
                block_uses, block_defs, local, pred,
                # No upper bound when entering a fully-traversable
                # predecessor: terminator uses are at op-index len(ops),
                # which `_latest_use_in_block_before` accepts.
                idx_exclusive=len(func.blocks[pred].ops) + 1,
                deletes=plan_deletes.get(pred, set()),
            )
            if pred_use is not None:
                # Clamp to op-area (don't try to insert "between" terminator
                # operations — sink to just before the terminator at worst).
                op_count = len(func.blocks[pred].ops)
                insert_idx = min(pred_use + 1, op_count)
                return (pred, insert_idx)

            # No use here. If pred contains a def of local, stop — we
            # can't sink past the def into its predecessor (different
            # lifetime).
            if local in block_defs.get(pred, {}):
                return None

            cur_block = pred

        # Unreachable
        return None

    @staticmethod
    def _latest_use_in_block_before(
        block_uses: Dict[BlockId, Dict[LocalId, List[int]]],
        block_defs: Dict[BlockId, Dict[LocalId, List[int]]],
        local: LocalId,
        block_id: BlockId,
        idx_exclusive: int,
        deletes: Set[int] = frozenset(),
    ) -> Optional[int]:
        """Latest op-index in ``block_id`` strictly before ``idx_exclusive``
        that uses ``local`` and is not slated for deletion. ``None`` if
        none exists, or if a def of ``local`` lies between the last use
        and ``idx_exclusive`` (in which case we'd be crossing a kill
        boundary — not safe).
        """
        uses = block_uses.get(block_id, {}).get(local, [])
        defs = block_defs.get(block_id, {}).get(local, [])
        latest_use: Optional[int] = None
        for u in uses:
            if u < idx_exclusive and u not in deletes:
                if latest_use is None or u > latest_use:
                    latest_use = u
        if latest_use is None:
            return None
        # If a def of local happens between latest_use and idx_exclusive,
        # the value we'd be holding alive past idx_exclusive isn't the
        # one we're releasing — bail.
        for d in defs:
            if latest_use < d < idx_exclusive:
                return None
        return latest_use

    @staticmethod
    def _uses_in_op(op: Op) -> List[LocalId]:
        """Locals read by this op (operand bases without projections)."""
        out: List[LocalId] = []

        def add(operand: Operand) -> None:
            if isinstance(operand, (CopyOperand, MoveOperand)):
                if not operand.place.projections:
                    out.append(operand.place.base)

        if isinstance(op, Assign):
            add(op.value)
            # Stores to a place with projections are also a "use" of the
            # base — the base local has to be alive for the address calc.
            if op.place.projections:
                out.append(op.place.base)
        elif isinstance(op, Store):
            add(op.value)
            out.append(op.place.base)
        elif isinstance(op, Load):
            out.append(op.place.base)
        elif isinstance(op, CallStatic):
            for arg in op.args:
                add(arg)
        elif isinstance(op, CallVTable):
            add(op.receiver)
            for arg in op.args:
                add(arg)
        elif isinstance(op, CallWitness):
            for arg in op.args:
                add(arg)
        elif isinstance(op, CallClosure):
            add(op.closure)
            for arg in op.args:
                add(arg)
        elif isinstance(op, (Retain, Release)):
            add(op.operand)
        elif isinstance(op, ExtractField):
            add(op.aggregate)
        elif isinstance(op, ExtractClosureCapture):
            add(op.closure)
        elif isinstance(op, ExtractEnumPayload):
            add(op.enum_val)
        elif isinstance(op, (ExistentialCheckType, ExistentialUnbox)):
            add(op.existential)
        elif isinstance(op, MakeStruct):
            for _, val in op.fields:
                add(val)
        elif isinstance(op, MakeEnum):
            for payload in op.payload:
                add(payload)
        elif isinstance(op, MakeSome):
            add(op.value)
        elif isinstance(op, MakeClosure):
            for cap in op.captures:
                add(cap)
        elif isinstance(op, BoxExistential):
            add(op.value)
        elif isinstance(op, GetTag):
            add(op.enum_val)
        elif isinstance(op, Clone):
            add(op.value)
        elif isinstance(op, CastOp):
            add(op.operand)
        elif isinstance(op, (BinOp, CmpOp)):
            add(op.left)
            add(op.right)
        elif isinstance(op, UnaryOp):
            add(op.operand)
        elif isinstance(op, TaskSpawn):
            if op.frame is not None:
                add(op.frame)
            for arg in op.args:
                add(arg)
        elif isinstance(op, TaskJoin):
            add(op.task_handle)
        elif isinstance(op, TaskComplete):
            add(op.task_handle)
            if op.result is not None:
                add(op.result)
        elif isinstance(op, TaskGetResult):
            add(op.task_handle)
        elif isinstance(op, SchedulerRun):
            if op.until_handle is not None:
                add(op.until_handle)

        return out

    @staticmethod
    def _def_in_op(op: Op) -> Optional[LocalId]:
        """Local written by this op without projections, if any."""
        if isinstance(op, Assign):
            if not op.place.projections:
                return op.place.base
        elif isinstance(op, (CallStatic, CallVTable, CallWitness, CallClosure)):
            return op.result
        elif isinstance(op, Load):
            return op.result
        elif isinstance(op, ExtractField):
            return op.result
        elif isinstance(op, ExtractClosureCapture):
            return op.result
        elif isinstance(op, ExtractEnumPayload):
            return op.result
        elif isinstance(op, (ExistentialCheckType, ExistentialUnbox)):
            return op.result
        elif isinstance(op, (MakeStruct, MakeEnum, MakeSome, MakeNone)):
            return op.result
        elif isinstance(op, MakeClosure):
            return op.result
        elif isinstance(op, BoxExistential):
            return op.result
        elif isinstance(op, GetTag):
            return op.result
        elif isinstance(op, Clone):
            return op.result
        elif isinstance(op, (CastOp, BinOp, CmpOp, UnaryOp)):
            return op.result
        elif isinstance(op, AllocObj):
            return op.result
        elif isinstance(op, AllocAsyncFrame):
            return op.result
        elif isinstance(op, (TaskSpawn, TaskJoin, TaskGetResult)):
            return op.result
        return None

    def _get_operand_local(self, operand: Operand) -> Optional[LocalId]:
        """Get the local ID from an operand if it's a simple place."""
        if isinstance(operand, (CopyOperand, MoveOperand)):
            if not operand.place.projections:
                return operand.place.base
        return None


# =============================================================================
# Main Entry Point
# =============================================================================

def optimize_arc(func: MirFunction, type_table: TypeTable) -> Tuple[MirFunction, ArcOptStats]:
    """
    Apply ARC optimizations to a MIR function.

    Args:
        func: The MIR function to optimize
        type_table: The type table for type queries

    Returns:
        Tuple of (optimized function, optimization statistics)
    """
    optimizer = ArcOptimizer(type_table)
    optimized_func = optimizer.optimize(func)
    return optimized_func, optimizer.stats


def optimize_arc_program(
    program: MirProgram,
    type_table: TypeTable,
) -> Tuple[MirProgram, Dict[str, ArcOptStats]]:
    """
    Apply ARC optimizations to all functions in a program.

    Args:
        program: The MIR program to optimize
        type_table: The type table for type queries

    Returns:
        Tuple of (optimized program, per-function statistics)
    """
    stats: Dict[str, ArcOptStats] = {}
    new_functions: List[MirFunction] = []

    for func in program.functions:
        optimized_func, func_stats = optimize_arc(func, type_table)
        new_functions.append(optimized_func)
        stats[func.name] = func_stats

    new_program = MirProgram(
        functions=new_functions,
        structs=program.structs,
        enums=program.enums,
        externs=program.externs,
    )

    return new_program, stats
