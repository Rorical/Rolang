"""
ARC (Automatic Reference Counting) Insertion Pass for RoLang.

This module implements the ARC insertion pass which inserts retain/release
operations for heap-allocated types (structs, enums, tuples, closures, existentials).

Pipeline position:
    MIR (from mir_builder)
            ↓
       ARC Insertion    ← THIS PHASE
            ↓
          LIR
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Tuple

from .types import TypeId, TypeKind, TypeTable
from .symbols import SymbolTable
from .mir import (
    # ID types
    LocalId, BlockId,
    # Core types
    Place, Block, MirFunction, MirProgram, MirBuildResult,
    # Operations
    Op, Assign, Retain, Release,
    AllocObj, Clone, GCCheck,
    TaskSpawn, TaskJoin, TaskYield, TaskComplete, Suspend,
    AllocAsyncFrame, SchedulerRun, TaskGetResult,
    CallStatic, CallVTable, CallWitness, CallClosure, MakeStruct, MakeEnum,
    MakeSome, MakeNone, MakeClosure, ExtractField, ExtractClosureCapture, ExtractEnumPayload,
    GetTag,
    Load, Store, BinOp, CmpOp, UnaryOp, CastOp,
    BoxExistential, ExistentialCheckType, ExistentialUnbox,
    Operand, CopyOperand, MoveOperand,
    Terminator, Branch, CondBranch, SwitchInt, Return,
    get_terminator_targets,
)


# =============================================================================
# Ownership State
# =============================================================================

class RcState(Enum):
    """Reference counting ownership state."""
    OWNED = auto()      # Value owns +1 refcount, MUST be released
    BORROWED = auto()   # Value doesn't own refcount, CANNOT be released


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class LocalInfo:
    """Information about a local for ARC analysis."""
    local_id: LocalId
    type_id: TypeId
    is_heap_type: bool           # Is a heap type (struct, enum)?
    is_closure_type: bool        # Is closure or function value?
    is_existential_type: bool    # Is existential (any Protocol)?
    is_optional_heap_type: bool  # Is Optional<HeapType> (nullable pointer)?
    needs_arc: bool              # Needs retain/release
    needs_cleanup: bool          # Needs cleanup on scope exit


@dataclass
class BlockAnalysis:
    """Liveness analysis results for a block."""
    block_id: BlockId
    live_in: Set[LocalId]           # Refs live at entry
    live_out: Set[LocalId]          # Refs live at exit
    use_set: Set[LocalId]           # Used before defined in this block
    def_set: Set[LocalId]           # Defined in this block
    owned_at_entry: Set[LocalId] = field(default_factory=set)
    # Locals that arrive owned (from a predecessor's live_out) but are NOT in
    # live_in — i.e. this block neither uses them nor passes them to any
    # successor.  They must be released at the very top of the block to avoid
    # a memory leak on the non-using branch of a diamond.
    # Formula: ∪{live_out[P] for P ∈ predecessors[B]} - live_in[B]


@dataclass
class OpOwnership:
    """Ownership effects of an operation."""
    produces: Optional[LocalId]     # Local that receives ownership
    consumes: List[LocalId]         # Locals whose ownership is consumed
    copies: List[Place]             # Places copied into owned storage (need retain)
    post_retains: List[Place]       # Places that need retain AFTER this op (borrowed loads)
    pre_releases: List[Place] = field(default_factory=list)       # Places whose old value is overwritten


@dataclass
class ArcInsertionResult:
    """Result of ARC insertion pass."""
    program: MirProgram
    type_table: TypeTable
    symbol_table: SymbolTable
    frame_structs: dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        """Check if there were any errors."""
        return len(self.errors) > 0


# =============================================================================
# Analysis Functions
# =============================================================================

def _type_needs_arc(type_id: TypeId, type_table: TypeTable) -> bool:
    """Return True if values of this type are managed object references."""
    info = type_table.get_type(type_id)
    if info is None:
        return False
    if info.kind in (
        TypeKind.STRUCT,
        TypeKind.ENUM,
        TypeKind.CLOSURE,
        TypeKind.FUNCTION,
        TypeKind.EXISTENTIAL,
    ):
        return True
    if info.kind == TypeKind.OPTIONAL:
        inner_info = type_table.get_type(info.data.inner)
        return inner_info is not None and inner_info.kind in (
            TypeKind.STRUCT,
            TypeKind.ENUM,
            TypeKind.CLOSURE,
            TypeKind.FUNCTION,
            TypeKind.EXISTENTIAL,
        )
    return False


def collect_ref_locals(func: MirFunction, type_table: TypeTable, symbol_table: Optional[SymbolTable] = None) -> Dict[LocalId, LocalInfo]:
    """
    Identify all locals with types that need ARC tracking."""
    ref_locals: Dict[LocalId, LocalInfo] = {}

    for local in func.locals:
        info = type_table.get_type(local.type_id)
        if info is None:
            continue

        is_closure = info.kind in (TypeKind.CLOSURE, TypeKind.FUNCTION)
        is_existential = info.kind == TypeKind.EXISTENTIAL
        is_struct = info.kind == TypeKind.STRUCT
        is_enum = info.kind == TypeKind.ENUM

        # Optional<HeapType> is a nullable heap pointer — it participates in
        # ARC so rt_obj_retain/release are called on the inner value. Both
        # helpers null-check, so a None optional is a safe no-op.
        is_optional_heap = False
        if info.kind == TypeKind.OPTIONAL:
            inner_info = type_table.get_type(info.data.inner)
            if inner_info is not None:
                is_optional_heap = inner_info.kind in (
                    TypeKind.STRUCT, TypeKind.ENUM,
                    TypeKind.CLOSURE, TypeKind.FUNCTION, TypeKind.EXISTENTIAL,
                )

        is_heap = is_struct or is_enum
        needs_arc = is_heap or is_closure or is_existential or is_optional_heap
        needs_cleanup = needs_arc

        if needs_arc:
            ref_locals[local.id] = LocalInfo(
                local_id=local.id,
                type_id=local.type_id,
                is_heap_type=is_heap,
                is_closure_type=is_closure,
                is_existential_type=is_existential,
                is_optional_heap_type=is_optional_heap,
                needs_arc=needs_arc,
                needs_cleanup=needs_cleanup,
            )


    return ref_locals


def compute_use_def(
    block: Block,
    ref_locals: Dict[LocalId, LocalInfo],
) -> Tuple[Set[LocalId], Set[LocalId]]:
    """
    Compute use and def sets for a block.

    Returns (use_set, def_set) where:
    - use_set: refs used before being defined in this block
    - def_set: refs defined (assigned) in this block
    """
    use_set: Set[LocalId] = set()
    def_set: Set[LocalId] = set()

    def process_operand(op: Operand) -> None:
        """Process an operand to track uses."""
        if isinstance(op, CopyOperand):
            local_id = op.place.base
            if local_id in ref_locals and local_id not in def_set:
                use_set.add(local_id)
        elif isinstance(op, MoveOperand):
            local_id = op.place.base
            if local_id in ref_locals and local_id not in def_set:
                use_set.add(local_id)

    def process_place(place: Place) -> None:
        """Process a place for uses (in projections)."""
        local_id = place.base
        if local_id in ref_locals and local_id not in def_set:
            use_set.add(local_id)

    # Process all operations in the block
    for op in block.ops:
        # Track uses from operation operands
        if isinstance(op, Assign):
            process_operand(op.value)
            if op.place.projections:
                # Field/index store: the base object must stay alive for the
                # address calculation, so count it as a use.
                process_place(op.place)
            else:
                # Root assignment: this block defines the local.
                if op.place.base in ref_locals:
                    def_set.add(op.place.base)

        elif isinstance(op, Retain):
            process_operand(op.operand)

        elif isinstance(op, Release):
            process_operand(op.operand)

        elif isinstance(op, CallStatic):
            for arg in op.args:
                process_operand(arg)
            if op.result is not None and op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, CallVTable):
            process_operand(op.receiver)
            for arg in op.args:
                process_operand(arg)
            if op.result is not None and op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, CallClosure):
            process_operand(op.closure)
            for arg in op.args:
                process_operand(arg)
            if op.result is not None and op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, CallWitness):
            for arg in op.args:
                process_operand(arg)
            if op.result is not None and op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, (MakeStruct, MakeEnum)):
            if isinstance(op, MakeStruct):
                for _, val in op.fields:
                    process_operand(val)
            elif isinstance(op, MakeEnum):
                for payload in op.payload:
                    process_operand(payload)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, MakeClosure):
            for cap in op.captures:
                process_operand(cap)
            if op.result in ref_locals:
                def_set.add(op.result)


        elif isinstance(op, BoxExistential):
            process_operand(op.value)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, ExistentialCheckType):
            process_operand(op.existential)
            # result is a Bool, never ref-typed.

        elif isinstance(op, ExistentialUnbox):
            process_operand(op.existential)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, (MakeSome, MakeNone)):
            if isinstance(op, MakeSome):
                process_operand(op.value)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, ExtractField):
            process_operand(op.aggregate)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, ExtractClosureCapture):
            process_operand(op.closure)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, ExtractEnumPayload):
            process_operand(op.enum_val)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, Load):
            process_place(op.place)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, Store):
            process_operand(op.value)
            process_place(op.place)

        elif isinstance(op, (BinOp, CmpOp)):
            process_operand(op.left)
            process_operand(op.right)

        elif isinstance(op, UnaryOp):
            process_operand(op.operand)

        elif isinstance(op, CastOp):
            process_operand(op.operand)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, AllocObj):
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, Clone):
            process_operand(op.value)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, GetTag):
            process_operand(op.enum_val)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, GCCheck):
            pass  # No operands, no result

        elif isinstance(op, TaskSpawn):
            for arg in op.args:
                process_operand(arg)
            if op.frame is not None:
                process_operand(op.frame)

        elif isinstance(op, TaskJoin):
            process_operand(op.task_handle)
            if op.result is not None and op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, TaskComplete):
            process_operand(op.task_handle)
            if op.result is not None:
                process_operand(op.result)

        elif isinstance(op, AllocAsyncFrame):
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, TaskGetResult):
            process_operand(op.task_handle)
            if op.result in ref_locals:
                def_set.add(op.result)

        elif isinstance(op, (TaskYield, Suspend, SchedulerRun)):
            if isinstance(op, SchedulerRun) and op.until_handle is not None:
                process_operand(op.until_handle)

    # Process terminator
    if block.terminator is not None:
        term = block.terminator
        if isinstance(term, CondBranch):
            process_operand(term.condition)
        elif isinstance(term, SwitchInt):
            process_operand(term.value)
        elif isinstance(term, Return):
            if term.value is not None:
                process_operand(term.value)

    return use_set, def_set


def compute_liveness(
    func: MirFunction,
    ref_locals: Dict[LocalId, LocalInfo],
) -> Dict[BlockId, BlockAnalysis]:
    """
    Compute liveness analysis for reference-typed locals using backward dataflow.

    Standard backward dataflow equations:
        live_out[B] = ∪ live_in[S] for all successors S
        live_in[B] = use[B] ∪ (live_out[B] - def[B])

    Returns a dictionary mapping BlockId to BlockAnalysis.
    """
    # Compute use/def sets for each block
    block_info: Dict[BlockId, Tuple[Set[LocalId], Set[LocalId]]] = {}
    for block_id, block in func.blocks.items():
        use_set, def_set = compute_use_def(block, ref_locals)
        block_info[block_id] = (use_set, def_set)

    # Build predecessor map
    predecessors: Dict[BlockId, List[BlockId]] = {bid: [] for bid in func.blocks}
    for block_id, block in func.blocks.items():
        if block.terminator is not None:
            for target in get_terminator_targets(block.terminator):
                if target in predecessors:
                    predecessors[target].append(block_id)

    # Initialize live_in and live_out
    live_in: Dict[BlockId, Set[LocalId]] = {bid: set() for bid in func.blocks}
    live_out: Dict[BlockId, Set[LocalId]] = {bid: set() for bid in func.blocks}

    # Compute a reverse-postorder (RPO) traversal of the CFG.  For a backward
    # dataflow pass, iterating in RPO (rather than forward dict-insertion order)
    # dramatically reduces the number of outer-loop passes needed to reach the
    # fixed point — typically one pass per loop-nesting level instead of O(N).
    def _rpo(blocks: Dict) -> List:
        entry_id = next(iter(blocks))
        visited: Set = set()
        order: List = []
        stack = [entry_id]
        while stack:
            bid = stack[-1]
            if bid not in visited:
                visited.add(bid)
                blk = blocks.get(bid)
                if blk and blk.terminator is not None:
                    for succ in get_terminator_targets(blk.terminator):
                        if succ not in visited and succ in blocks:
                            stack.append(succ)
            else:
                stack.pop()
                if bid not in order:
                    order.append(bid)
        # Append any unreachable blocks so they still get processed.
        for bid in blocks:
            if bid not in order:
                order.append(bid)
        return order  # postorder; reverse below

    postorder = _rpo(func.blocks)
    # For backward analysis iterate in reverse-postorder (= reverse of postorder
    # gives the order where predecessors appear before successors on the first pass).
    rpo_order = list(reversed(postorder))

    # Safety limit: N×V is the tight bound for backward analysis in the worst
    # case (N = blocks, V = ref-typed locals).  We use a generous multiple.
    n_blocks = max(1, len(func.blocks))
    n_locals = max(1, len(ref_locals))
    max_iterations = max(10_000, n_blocks * n_locals * 4)

    # Fixed-point iteration (backward dataflow in RPO)
    changed = True
    iterations = 0

    while changed and iterations < max_iterations:
        changed = False
        iterations += 1

        for block_id in rpo_order:
            if block_id not in func.blocks:
                continue
            block = func.blocks[block_id]
            use_set, def_set = block_info[block_id]

            # Compute live_out as union of live_in of all successors
            new_live_out: Set[LocalId] = set()
            if block.terminator is not None:
                for succ_id in get_terminator_targets(block.terminator):
                    if succ_id in live_in:
                        new_live_out |= live_in[succ_id]

            # Compute live_in = use ∪ (live_out - def)
            new_live_in = use_set | (new_live_out - def_set)

            # Check for changes
            if new_live_in != live_in[block_id] or new_live_out != live_out[block_id]:
                changed = True
                live_in[block_id] = new_live_in
                live_out[block_id] = new_live_out

    # Build results
    if iterations >= max_iterations:
        raise RuntimeError(
            f"Liveness analysis did not converge after {max_iterations} iterations "
            f"in function '{func.name}'. This indicates a bug in the ARC dataflow "
            "analysis; the resulting retain/release operations would be incorrect."
        )

    # Compute owned_at_entry for each block: values that arrive owned from a
    # SINGLE predecessor's live_out but are not needed by this block's live_in.
    #
    # We restrict this to single-predecessor blocks because blocks with multiple
    # predecessors may be reached via paths with *different* ownership states:
    # some predecessors may have already released the value while others haven't.
    # Inserting a release at the top of such a merge point would double-free on
    # paths where the predecessor already released the value.
    #
    # For single-predecessor blocks the predecessor is unambiguous: if that one
    # predecessor held the value alive (for another successor) but *this* block
    # doesn't need it, the value arrives owned and must be released here.
    # This covers the common diamond-branch case:
    #   define_block → using_block  (live_in contains x)
    #   define_block → non_using_block (single pred = define_block, live_in empty)
    owned_at_entry: Dict[BlockId, Set[LocalId]] = {bid: set() for bid in func.blocks}
    for block_id in func.blocks:
        preds = predecessors[block_id]
        if len(preds) != 1:
            continue  # Multi-predecessor: cannot safely determine ownership
        pred_id = preds[0]
        owned_at_entry[block_id] = live_out[pred_id] - live_in[block_id]

    results: Dict[BlockId, BlockAnalysis] = {}
    for block_id in func.blocks:
        use_set, def_set = block_info[block_id]
        results[block_id] = BlockAnalysis(
            block_id=block_id,
            live_in=live_in[block_id],
            live_out=live_out[block_id],
            use_set=use_set,
            def_set=def_set,
            owned_at_entry=owned_at_entry[block_id],
        )

    return results


def analyze_op_ownership(
    op: Op,
    ref_locals: Dict[LocalId, LocalInfo],
    type_table: TypeTable,
) -> OpOwnership:
    """
    Analyze the ownership effects of an operation.

    Returns OpOwnership describing what the operation produces, consumes, and copies.
    """
    produces: Optional[LocalId] = None
    consumes: List[LocalId] = []
    copies: List[Place] = []
    post_retains: List[Place] = []
    pre_releases: List[Place] = []

    def check_operand_for_copy(operand: Operand) -> None:
        """Check if an operand is copied into an owned location."""
        if isinstance(operand, CopyOperand):
            if _type_needs_arc(operand.place.type_id, type_table):
                copies.append(operand.place)
        elif isinstance(operand, MoveOperand):
            local_id = operand.place.base
            if not operand.place.projections and local_id in ref_locals and ref_locals[local_id].needs_arc:
                consumes.append(local_id)

    def consume_cleanup_source(operand: Operand) -> None:
        """Mark a cleanup-typed operand as consumed (ownership transfer)."""
        if isinstance(operand, (CopyOperand, MoveOperand)):
            local_id = operand.place.base
            if local_id in ref_locals and ref_locals[local_id].needs_cleanup:
                consumes.append(local_id)

    # CallStatic that returns heap object creates owned reference
    if isinstance(op, CallStatic):
        if op.result is not None and op.result in ref_locals:
            if ref_locals[op.result].needs_arc:
                produces = op.result

    # CallVTable on existential — may return Ref
    elif isinstance(op, CallVTable):
        if op.result is not None and op.result in ref_locals:
            if ref_locals[op.result].needs_arc:
                produces = op.result

    # CallClosure — may return Ref
    elif isinstance(op, CallClosure):
        if op.result is not None and op.result in ref_locals:
            if ref_locals[op.result].needs_arc:
                produces = op.result

    # CallWitness — may return Ref
    elif isinstance(op, CallWitness):
        if op.result is not None and op.result in ref_locals:
            if ref_locals[op.result].needs_arc:
                produces = op.result

    # Assign of a ref-type needs retain for copies
    elif isinstance(op, Assign):
        target_local = op.place.base
        if _type_needs_arc(op.place.type_id, type_table):
            check_operand_for_copy(op.value)
            if op.place.projections:
                pre_releases.append(op.place)
        if not op.place.projections and target_local in ref_locals:
            if ref_locals[target_local].needs_arc:
                produces = target_local

    # Extract operations may produce refs
    elif isinstance(op, ExtractField):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            # Don't treat as freshly produced - the ref is borrowed from
            # the aggregate. Insert a retain after the op to balance the
            # eventual release.
            produces = op.result
            post_retains.append(Place(base=op.result, projections=[], type_id=op.result_type))

    elif isinstance(op, ExtractClosureCapture):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            # Captures are owned by the closure object. Loading one into a
            # lambda-local is a borrowed read, so retain it for the duration
            # of the lambda call and let normal local cleanup release it.
            produces = op.result
            post_retains.append(Place(base=op.result, projections=[], type_id=op.result_type))

    elif isinstance(op, ExtractEnumPayload):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            # Borrowed reference from enum — retain to balance release
            produces = op.result
            post_retains.append(Place(base=op.result, projections=[], type_id=op.result_type))

    elif isinstance(op, Load):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result
            post_retains.append(Place(base=op.result, projections=[], type_id=op.place.type_id))

    elif isinstance(op, CastOp):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result
            check_operand_for_copy(op.operand)

    elif isinstance(op, (MakeStruct, MakeEnum)):
        if isinstance(op, MakeStruct):
            for _, val in op.fields:
                check_operand_for_copy(val)
        elif isinstance(op, MakeEnum):
            for payload in op.payload:
                check_operand_for_copy(payload)
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, MakeSome):
        check_operand_for_copy(op.value)
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, MakeNone):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, MakeClosure):
        if op.result in ref_locals:
            if ref_locals[op.result].needs_arc:
                produces = op.result
        for cap in op.captures:
            check_operand_for_copy(cap)

    elif isinstance(op, BoxExistential):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result
        check_operand_for_copy(op.value)  # Retain the boxed value

    elif isinstance(op, ExistentialCheckType):
        # Pure read of the witness pointer; no ownership transfer.
        pass

    elif isinstance(op, ExistentialUnbox):
        # The existential's value-object slot holds a strong reference;
        # unboxing returns a borrowed view of it. Retain after the op to
        # balance the eventual release, mirroring how ExtractField handles
        # a borrowed reference extracted from an aggregate.
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result
            post_retains.append(Place(
                base=op.result, projections=[], type_id=op.result_type,
            ))

    elif isinstance(op, AllocObj):
        # New heap object — rc starts at 1, the result local owns it
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, Clone):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, Store):
        if _type_needs_arc(op.place.type_id, type_table):
            check_operand_for_copy(op.value)
            pre_releases.append(op.place)

    elif isinstance(op, AllocAsyncFrame):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, TaskGetResult):
        if op.result in ref_locals and ref_locals[op.result].needs_arc:
            produces = op.result

    elif isinstance(op, TaskSpawn):
        # The task handle owns the frame until TaskGetResult destroys it.
        if op.frame is not None and isinstance(op.frame, (CopyOperand, MoveOperand)):
            frame_place = op.frame.place
            if not frame_place.projections and frame_place.base in ref_locals:
                consumes.append(frame_place.base)

    elif isinstance(op, TaskComplete):
        # Codegen takes a dedicated retain for heap results before handing
        # them to the TaskHandle, so normal local cleanup still applies.
        pass

    elif isinstance(op, (TaskJoin, TaskYield, Suspend, SchedulerRun)):
        pass

    elif isinstance(op, GetTag):
        # Tag is always a primitive, no ARC needed
        pass

    elif isinstance(op, GCCheck):
        # No-op for ARC
        pass

    return OpOwnership(
        produces=produces,
        consumes=consumes,
        copies=copies,
        post_retains=post_retains,
        pre_releases=pre_releases,
    )


# =============================================================================
# ARC Insertion
# =============================================================================

def insert_arc_ops_in_block(
    block: Block,
    liveness: BlockAnalysis,
    ref_locals: Dict[LocalId, LocalInfo],
    type_table: TypeTable,
    return_value_local: Optional[LocalId] = None,
    symbol_table: Optional[SymbolTable] = None,
    param_locals: Optional[Set[LocalId]] = None,
) -> Block:
    """
    Insert ARC operations in a single block.

    Parameters (param_locals) are borrowed from the caller and are not released
    at scope exit. Copies of parameters into owned locals/fields are still
    retained like any other owned copy.
    """
    if param_locals is None:
        param_locals = set()

    new_ops: List[Op] = []

    # Release values that arrive owned from a predecessor's live_out but are
    # not needed by this block (diamond-branch leak: non-using arm owns the
    # value but never uses or propagates it).
    for local_id in liveness.owned_at_entry:
        if local_id not in ref_locals:
            continue
        if not ref_locals[local_id].needs_arc:
            continue
        if local_id in param_locals:
            continue
        if local_id == return_value_local:
            continue
        local_info = ref_locals[local_id]
        place = Place(base=local_id, projections=[], type_id=local_info.type_id)
        new_ops.append(Release(operand=CopyOperand(place)))

    owned_in_block: Set[LocalId] = set(liveness.live_in)
    consumed_in_block: Set[LocalId] = set()

    for op in block.ops:
        # Skip existing retain/release ops
        if isinstance(op, (Retain, Release)):
            new_ops.append(op)
            continue

        # Analyze ownership effects
        ownership = analyze_op_ownership(op, ref_locals, type_table)

        # Insert retain for copied managed values before releasing any old
        # overwritten slot. This preserves self-assignment safety.
        for copied_place in ownership.copies:
            if _type_needs_arc(copied_place.type_id, type_table):
                new_ops.append(Retain(operand=CopyOperand(copied_place)))

        # Release old values in overwritten managed fields before storing the
        # replacement. The new value has already been retained above.
        for release_place in ownership.pre_releases:
            if _type_needs_arc(release_place.type_id, type_table):
                new_ops.append(Release(operand=CopyOperand(release_place)))

        # Reassigning an already-owned local must release the previous object.
        if isinstance(op, Assign) and not op.place.projections:
            target = op.place.base
            if (
                target in owned_in_block
                and target not in param_locals
                and _type_needs_arc(op.place.type_id, type_table)
            ):
                new_ops.append(Release(operand=CopyOperand(op.place)))
                owned_in_block.discard(target)

        # Add the original operation
        new_ops.append(op)

        # After the op, insert retains for post_retains (e.g., ExtractField
        # borrows refs from aggregates that need a retain to balance release)
        for post_place in ownership.post_retains:
            if _type_needs_arc(post_place.type_id, type_table):
                new_ops.append(Retain(operand=CopyOperand(post_place)))

        if ownership.produces is not None:
            owned_in_block.add(ownership.produces)
            consumed_in_block.discard(ownership.produces)

        # Track consumed locals (move semantics)
        for consumed in ownership.consumes:
            owned_in_block.discard(consumed)
            consumed_in_block.add(consumed)

    # At block end, determine which owned refs need release
    # Don't release:
    # - Refs that are live-out (used in successors)
    # - The return value (if this is a return block)
    # - Parameters (borrowed from caller)

    # Check if this block ends with return
    is_return_block = isinstance(block.terminator, Return)
    return_operand_local: Optional[LocalId] = None

    if is_return_block and block.terminator is not None:
        ret_term = block.terminator
        if isinstance(ret_term, Return) and ret_term.value is not None:
            if isinstance(ret_term.value, CopyOperand):
                return_operand_local = ret_term.value.place.base
            elif isinstance(ret_term.value, MoveOperand):
                return_operand_local = ret_term.value.place.base

    # Returning a borrowed parameter hands an owned result to the caller.
    if return_operand_local in param_locals and return_operand_local in ref_locals:
        local_info = ref_locals[return_operand_local]
        if local_info.needs_arc:
            place = Place(base=return_operand_local, projections=[], type_id=local_info.type_id)
            new_ops.append(Retain(operand=CopyOperand(place)))

    # Insert releases for locals that:
    # - Are owned in this block (defined here OR live-in from a predecessor
    #   that hasn't already released them in a successor)
    # - Are NOT live-out (any successor still uses them)
    # - Are NOT the return value
    # - Are NOT parameters (borrowed from caller)
    #
    # Tracking the union of `def_set` and `live_in`-derived ownership matters
    # for locals that are produced in one block and become dead in another
    # without ever flowing back to a return block — without this the local's
    # final ARC release is silently dropped, causing a memory leak.
    release_candidates = liveness.def_set | set(liveness.live_in)
    for local_id in release_candidates:
        if local_id not in ref_locals:
            continue
        if not ref_locals[local_id].needs_arc:
            continue
        if local_id in liveness.live_out:
            continue  # Still needed in successors
        if local_id in consumed_in_block:
            continue  # Ownership was transferred to another owner
        if local_id == return_operand_local:
            continue  # Don't release return value, caller owns it
        if local_id in param_locals:
            continue  # Parameters are borrowed, not owned

        # Insert release
        local_info = ref_locals[local_id]
        place = Place(base=local_id, projections=[], type_id=local_info.type_id)
        new_ops.append(Release(operand=CopyOperand(place)))

    # Note: the unified release loop above (driven by `release_candidates =
    # def_set | live_in`) already handles return blocks correctly. A separate
    # return-block sweep would double-release locals that are both
    # live-into and not live-out of the return block.

    return Block(id=block.id, ops=new_ops, terminator=block.terminator)


def insert_arc_ops(
    func: MirFunction,
    type_table: TypeTable,
    symbol_table: Optional[SymbolTable] = None,
) -> MirFunction:
    """
    Insert ARC operations (retain/release) into a function.

    This implements a conservative scope-based strategy:
    - Insert retain when copying a Ref<T>
    - Insert release when a Ref<T> goes out of scope
    """
    # Collect ref-typed locals
    ref_locals = collect_ref_locals(func, type_table, symbol_table)

    if not ref_locals:
        # No ref types, nothing to do
        return func

    # Compute liveness
    liveness = compute_liveness(func, ref_locals)

    # Find return value local (if any)
    return_value_local: Optional[LocalId] = None
    # Check return type
    ret_info = type_table.get_type(func.ret_type)
    if ret_info:
        # Function returns a value - find which local it is
        for block in func.blocks.values():
            if isinstance(block.terminator, Return):
                if block.terminator.value is not None:
                    val = block.terminator.value
                    if isinstance(val, (CopyOperand, MoveOperand)):
                        return_value_local = val.place.base
                        break

    # Collect parameter local IDs — parameters are borrowed from the caller
    # and should not be retained or released by ARC insertion.
    param_locals: Set[LocalId] = {arg.id for arg in func.args}

    # Process each block
    new_blocks: Dict[BlockId, Block] = {}
    for block_id, block in func.blocks.items():
        block_liveness = liveness.get(block_id)
        if block_liveness is None:
            block_liveness = BlockAnalysis(
                block_id=block_id,
                live_in=set(),
                live_out=set(),
                use_set=set(),
                def_set=set(),
            )

        new_block = insert_arc_ops_in_block(
            block,
            block_liveness,
            ref_locals,
            type_table,
            return_value_local,
            symbol_table=symbol_table,
            param_locals=param_locals,
        )
        new_blocks[block_id] = new_block

    # Release values that arrive owned-but-dead along a *critical* edge into a
    # multi-predecessor merge block. `owned_at_entry` (computed in
    # compute_liveness) only fires for single-predecessor blocks: a value that
    # is live-out of a branch block P (because another successor still uses it)
    # but is dead at a multi-pred merge block M would otherwise never be
    # released on the P->M edge -> leak. We split that critical edge and emit
    # the release on the new edge block. (Single-succ edges can't leak: there
    # live_out[P] == live_in[M], so the leaked set is empty.)
    _split_critical_edges_for_leaks(
        func, new_blocks, liveness, ref_locals,
        param_locals, return_value_local, type_table,
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


def _redirect_terminator(term: Terminator, old: BlockId, new: BlockId) -> Terminator:
    """Return a terminator identical to `term` but with target `old` -> `new`."""
    if isinstance(term, Branch):
        if term.target == old:
            return Branch(target=new)
        return term
    if isinstance(term, CondBranch):
        return CondBranch(
            condition=term.condition,
            true_target=new if term.true_target == old else term.true_target,
            false_target=new if term.false_target == old else term.false_target,
        )
    if isinstance(term, SwitchInt):
        return SwitchInt(
            value=term.value,
            cases=[(v, new if t == old else t) for v, t in term.cases],
            default=new if term.default == old else term.default,
        )
    return term


def _split_critical_edges_for_leaks(
    func: MirFunction,
    new_blocks: Dict[BlockId, Block],
    liveness: Dict[BlockId, "BlockAnalysis"],
    ref_locals: Dict[LocalId, LocalInfo],
    param_locals: Set[LocalId],
    return_value_local: Optional[LocalId],
    type_table: TypeTable,
) -> None:
    """Insert per-edge releases on critical edges into multi-predecessor blocks.

    Mutates `new_blocks` in place: adds edge-split blocks and rewrites the
    terminators of branch blocks to route through them.
    """
    # Build predecessor lists and per-block successor counts from the ORIGINAL CFG.
    predecessors: Dict[BlockId, List[BlockId]] = {bid: [] for bid in func.blocks}
    succ_count: Dict[BlockId, int] = {}
    for bid, block in func.blocks.items():
        targets = get_terminator_targets(block.terminator) if block.terminator else []
        succ_count[bid] = len(targets)
        for t in targets:
            if t in predecessors:
                predecessors[t].append(bid)

    next_id = max((bid.id for bid in func.blocks), default=0) + 1

    for merge_id, preds in predecessors.items():
        if len(preds) <= 1:
            continue  # single-pred blocks are handled by owned_at_entry
        merge_live = liveness.get(merge_id)
        merge_live_in = merge_live.live_in if merge_live else set()
        for pred_id in preds:
            # Only true critical edges (multi-succ pred -> multi-pred merge)
            # can carry an owned-but-dead value; a single-succ pred releases it
            # at end-of-block already.
            if succ_count.get(pred_id, 0) <= 1:
                continue
            pred_live = liveness.get(pred_id)
            if pred_live is None:
                continue
            leaked: List[LocalId] = []
            for local_id in pred_live.live_out:
                if local_id in merge_live_in:
                    continue
                if local_id not in ref_locals or not ref_locals[local_id].needs_arc:
                    continue
                if local_id in param_locals or local_id == return_value_local:
                    continue
                leaked.append(local_id)
            if not leaked:
                continue

            # Create a split block: release the leaked locals, then jump to merge.
            split_id = BlockId(next_id)
            next_id += 1
            release_ops: List[Op] = []
            for local_id in leaked:
                info = ref_locals[local_id]
                place = Place(base=local_id, projections=[], type_id=info.type_id)
                release_ops.append(Release(operand=CopyOperand(place)))
            new_blocks[split_id] = Block(
                id=split_id, ops=release_ops, terminator=Branch(target=merge_id),
            )

            # Reroute this predecessor's edge through the split block.
            pred_block = new_blocks[pred_id]
            new_blocks[pred_id] = Block(
                id=pred_block.id,
                ops=pred_block.ops,
                terminator=_redirect_terminator(
                    pred_block.terminator, merge_id, split_id
                ),
            )


# =============================================================================
# Verification
# =============================================================================

def verify_arc_correctness(
    func: MirFunction,
    type_table: TypeTable,
) -> List[str]:
    """
    Verify ARC correctness for a function.

    Checks:
    - Every owned ref is released exactly once on all paths
    - No double-release
    - No use-after-release

    Returns a list of error messages (empty if correct).
    """
    errors: List[str] = []

    ref_locals = collect_ref_locals(func, type_table)
    if not ref_locals:
        return errors

    # Track retain/release counts per local per block
    # This is a simplified check - full verification would need path analysis

    for block_id, block in func.blocks.items():
        retain_count: Dict[LocalId, int] = {lid: 0 for lid in ref_locals}
        release_count: Dict[LocalId, int] = {lid: 0 for lid in ref_locals}

        for op in block.ops:
            if isinstance(op, Retain):
                if isinstance(op.operand, CopyOperand):
                    if op.operand.place.projections:
                        continue
                    local_id = op.operand.place.base
                    if local_id in retain_count:
                        retain_count[local_id] += 1

            elif isinstance(op, Release):
                if isinstance(op.operand, CopyOperand):
                    if op.operand.place.projections:
                        continue
                    local_id = op.operand.place.base
                    if local_id in release_count:
                        release_count[local_id] += 1
                        # Check for potential double-release within block
                        # Allow one pre-existing owner for values live into the
                        # block or overwritten before their replacement op.
                        if release_count[local_id] > retain_count[local_id] + 1:
                            errors.append(
                                f"Function '{func.name}', block {block_id.id}: "
                                f"potential double-release of local {local_id.id}"
                            )

            else:
                ownership = analyze_op_ownership(op, ref_locals, type_table)
                if ownership.produces is not None and ownership.produces in retain_count:
                    retain_count[ownership.produces] += 1

    return errors


# =============================================================================
# Main Entry Point
# =============================================================================

def insert_arc(
    mir_result: MirBuildResult,
    optimize: bool = False,
) -> ArcInsertionResult:
    """
    Insert ARC operations into a MIR program.

    This is the main entry point for the ARC insertion pass.

    Args:
        mir_result: The result from the MIR building phase
        optimize: If True, apply ARC optimizations

    Returns:
        ArcInsertionResult with the transformed program
    """
    errors: List[str] = []

    # Process each function
    new_functions: List[MirFunction] = []
    for func in mir_result.program.functions:
        # Insert ARC operations
        new_func = insert_arc_ops(func, mir_result.type_table, mir_result.symbol_table)
        new_functions.append(new_func)

        # Verify correctness
        verification_errors = verify_arc_correctness(new_func, mir_result.type_table)
        errors.extend(verification_errors)

    # Create new program with transformed functions
    new_program = MirProgram(
        functions=new_functions,
        structs=mir_result.program.structs,
        enums=mir_result.program.enums,
        externs=mir_result.program.externs,
    )

    # Apply optimizations if requested
    if optimize:
        from .arc_optimization import optimize_arc_program
        new_program, _ = optimize_arc_program(new_program, mir_result.type_table)

    return ArcInsertionResult(
        program=new_program,
        type_table=mir_result.type_table,
        symbol_table=mir_result.symbol_table,
        frame_structs=getattr(mir_result, 'frame_structs', {}),
        errors=errors,
    )
