"""
Async Lowering Pass for RoLang.

Transforms async functions into state machine form for cooperative multitasking.
- Each async function gets a resume function implementing switch-on-state dispatch
- The entry function allocates a frame, spawns the task, and runs the scheduler
- Await points spawn child tasks and yield control
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .types import TypeId, TypeTable
from .symbols import SymbolTable
from .mir import (
    LocalId, BlockId,
    Local, Place, Block, MirFunction, MirProgram, MirBuildResult,
    MirStruct, MirField,
    Op, Assign, CallStatic, CallVTable, CallWitness, CallClosure,
    Store,
    TaskSpawn, TaskYield, TaskComplete,
    AllocAsyncFrame, SchedulerRun, TaskGetResult,
    Operand, CopyOperand, ConstantOperand, ConstantKind,
    Terminator, Branch, CondBranch, SwitchInt, Return, Unreachable,
    FieldProjection,
)


@dataclass
class AsyncFrame:
    struct_name: str
    state_field_index: int = 0
    handle_field_index: int = 1
    local_fields: List[Tuple[str, TypeId]] = field(default_factory=list)
    task_handle_fields: List[str] = field(default_factory=list)


@dataclass
class AwaitPoint:
    state_id: int
    block_id: BlockId
    op_index: int
    awaited_call: Op  # CallStatic, CallVTable, CallWitness, or CallClosure
    result_local: Optional[LocalId]
    result_type: TypeId


@dataclass
class AsyncLoweringResult:
    program: MirProgram
    type_table: TypeTable
    symbol_table: SymbolTable
    frame_structs: Dict[str, str]
    errors: List[str]

    def has_errors(self) -> bool:
        return len(self.errors) > 0


class AsyncAnalyzer:
    """Find await points in a function: any call op targeting an async function."""

    def __init__(self, func: MirFunction, async_func_names: set) -> None:
        self.func = func
        self.async_func_names = async_func_names
        self.await_points: List[AwaitPoint] = []

    def _is_async_call(self, op: Op) -> bool:
        """Return True if this call op targets a known async function."""
        if isinstance(op, CallStatic):
            return op.func_name in self.async_func_names
        # For indirect calls (VTable, Witness, Closure) we cannot statically
        # determine the callee name, so we conservatively treat them as
        # potential await points when the function itself is async-aware.
        # In practice, async protocol methods are rare; the static case covers
        # the vast majority of programs.  A full solution would require the
        # type-checker to propagate async-ness through function-type values.
        if isinstance(op, (CallVTable, CallWitness, CallClosure)):
            return False  # Conservative: only exact name matches are promoted.
        return False

    def analyze(self) -> List[AwaitPoint]:
        for block_id, block in sorted(self.func.blocks.items(), key=lambda x: x[0].id):
            for op_index, op in enumerate(block.ops):
                if self._is_async_call(op):
                    self.await_points.append(AwaitPoint(
                        state_id=len(self.await_points),
                        block_id=block_id,
                        op_index=op_index,
                        awaited_call=op,
                        result_local=op.result,
                        result_type=op.result_type,
                    ))
        return self.await_points


def lower_async(mir_result: MirBuildResult) -> AsyncLoweringResult:
    """Transform async functions into state machines."""
    errors: List[str] = []
    frame_structs: Dict[str, str] = {}
    frame_type_ids: Dict[str, TypeId] = {}
    async_functions_by_name: Dict[str, MirFunction] = {}
    await_points_by_func: Dict[str, List[AwaitPoint]] = {}
    new_functions: List[MirFunction] = []
    new_structs: List[MirStruct] = list(mir_result.program.structs)
    new_enums = list(mir_result.program.enums)

    async_func_names: set[str] = {
        f.name for f in mir_result.program.functions if f.is_async
    }

    # Analyze once up front. We also pre-create every async frame struct before
    # building resume bodies so a caller can allocate and initialize a spawned
    # callee's frame at MIR level.
    async_with_awaits: set[str] = set()
    for f in mir_result.program.functions:
        if not f.is_async:
            continue
        async_functions_by_name[f.name] = f
        analyzer = AsyncAnalyzer(f, async_func_names)
        await_points = analyzer.analyze()
        # awaits in arbitrary control flow (loops, conditionals, multiple
        # blocks) are now supported by ``_build_resume``: each block
        # containing awaits is split into per-await segments, and the
        # resume function's entry switches on ``frame.state`` to land on
        # the right segment when the scheduler re-runs the task.
        await_points_by_func[f.name] = await_points
        if await_points:
            async_with_awaits.add(f.name)

    if errors:
        return AsyncLoweringResult(
            program=mir_result.program,
            type_table=mir_result.type_table,
            symbol_table=mir_result.symbol_table,
            frame_structs=frame_structs,
            errors=errors,
        )

    type_table = mir_result.type_table
    ptr_type = type_table.get_builtin("RawPtr") or type_table.void_type
    i32_type = type_table.get_builtin("i32") or type_table.void_type

    for func in mir_result.program.functions:
        if not func.is_async:
            continue

        await_points = await_points_by_func[func.name]

        # Build frame struct for the state machine
        frame_name = f"{func.name}_Frame"
        frame_structs[func.name] = frame_name

        # Frame field names use a '$' prefix and positional indices so they can
        # NEVER collide with a user local's name ('$' is illegal in source
        # identifiers, see grammar IDENT). Indexing locals positionally also
        # keeps two same-named (shadowed) source locals in distinct slots.
        # All field access goes through _fplace(<index>, ...), so only these
        # name strings (and ops_async's "$handle" lookup) need to stay in sync.
        frame_fields: List[MirField] = [
            MirField(name="$state", type_id=i32_type, is_mutable=True),
            MirField(name="$handle", type_id=ptr_type, is_mutable=True),
        ]
        for local in func.locals:
            frame_fields.append(MirField(name=f"$f{local.id}", type_id=local.type_id, is_mutable=True))
        for i in range(len(await_points)):
            frame_fields.append(MirField(name=f"$task{i}", type_id=ptr_type, is_mutable=True))

        frame_sym_id = mir_result.symbol_table.create_synthetic_symbol_id()
        frame_type_id = type_table.make_struct(frame_sym_id)
        frame_type_ids[func.name] = frame_type_id
        frame_mir = MirStruct(name=frame_name, symbol_id=frame_sym_id, fields=frame_fields, type_id=frame_type_id)
        new_structs.append(frame_mir)

    for func in mir_result.program.functions:
        if not func.is_async:
            new_functions.append(func)
            continue

        await_points = await_points_by_func[func.name]
        frame_name = frame_structs[func.name]
        frame_type_id = frame_type_ids[func.name]

        # Build resume function (state machine)
        resume_func = _build_resume(
            func,
            await_points,
            mir_result,
            frame_type_id,
            frame_name,
            async_with_awaits,
            frame_type_ids,
            async_functions_by_name,
        )
        new_functions.append(resume_func)

        if not await_points:
            # No await points - entry function just calls body synchronously
            sync_func = MirFunction(
                name=func.name, symbol_id=func.symbol_id,
                args=func.args, locals=func.locals,
                ret_type=func.ret_type, blocks=func.blocks,
                entry_block=func.entry_block,
                is_async=False, is_method=func.is_method,
            )
            new_functions.append(sync_func)
        else:
            # Build async entry function: allocate frame, call resume in loop, return result
            entry_func = _build_entry(func, frame_type_id, frame_name, mir_result, len(await_points))
            new_functions.append(entry_func)

    new_program = MirProgram(
        functions=new_functions, structs=new_structs, enums=new_enums,
        externs=mir_result.program.externs,
    )

    return AsyncLoweringResult(
        program=new_program, type_table=mir_result.type_table,
        symbol_table=mir_result.symbol_table, frame_structs=frame_structs,
        errors=errors,
    )


def _build_entry(
    func: MirFunction,
    frame_type_id: TypeId,
    frame_name: str,
    mir_result: MirBuildResult,
    n_aps: int,
) -> MirFunction:
    """
    Build the entry function for an async program.

    Replaces the old "alloca a pointer slot and read garbage out of it" body
    with a proper spawn + scheduler-driven sequence:

        bb0:
            frame  = AllocAsyncFrame(frame_type_id)      ; rt_obj_alloc
            handle = TaskSpawn(<func>_resume, frame)     ; push to queue
            SchedulerRun(handle)                          ; drain until done
            result = TaskGetResult(handle, ret_type)     ; load handle.result
            return result

    The resume function does the actual state-machine work and calls
    `rt_task_complete(handle, &result)` from its completion block, so by the
    time `SchedulerRun` returns the handle's `result` slot is populated.
    """
    tt = mir_result.type_table
    ptr_t = tt.get_builtin("RawPtr") or tt.void_type
    void_t = tt.void_type
    i32_t = tt.get_builtin("i32") or tt.void_type

    # Use a counter that is strictly above all existing IDs in every related
    # function (entry, resume, and all functions in the program) to guarantee
    # no collision regardless of how many locals are generated.
    all_ids = [l.id.id for f in mir_result.program.functions for l in f.locals]
    nid = max(all_ids, default=0) + 1
    def fresh_id() -> LocalId:
        nonlocal nid; lid = LocalId(nid); nid += 1; return lid

    frame_local = Local(
        id=fresh_id(), symbol_id=None, name="_frame",
        type_id=frame_type_id, is_mutable=False, is_arg=False,
    )
    handle_local = Local(
        id=fresh_id(), symbol_id=None, name="_task",
        type_id=ptr_t, is_mutable=False, is_arg=False,
    )
    ret_local = Local(
        id=fresh_id(), symbol_id=None, name="_ret",
        type_id=func.ret_type, is_mutable=False, is_arg=False,
    )

    locals_list = list(func.args) + [frame_local, handle_local, ret_local]

    resume_name = f"{func.name}_resume"

    arg_stores = _store_args_to_frame(
        frame_local.id,
        func,
        [
            CopyOperand(Place(base=arg.id, projections=[], type_id=arg.type_id))
            for arg in func.args
        ],
    )

    ops: List[Op] = [
        # 1) Heap-allocate the async frame with the canonical ARC header.
        AllocAsyncFrame(result=frame_local.id, frame_type=frame_type_id),
        *arg_stores,

        # 2) Push a task onto the queue with that initialized frame and the resume fn.
        TaskSpawn(
            result=handle_local.id,
            async_func_name=func.name,
            args=[],  # frame is wired in via the `frame` keyword below
            result_type=ptr_t,
            frame=CopyOperand(Place(base=frame_local.id, projections=[], type_id=frame_type_id)),
        ),

        # 3) Stash the task handle into frame._handle so the resume function
        #    can call `rt_task_complete(frame._handle, result)` from its
        #    completion block. Without this the completion call would pass
        #    a null handle and the runtime would never observe completion.
        Store(
            place=Place(
                base=frame_local.id,
                projections=[FieldProjection(field_name="$handle", result_type=ptr_t)],
                type_id=ptr_t,
            ),
            value=CopyOperand(Place(base=handle_local.id, projections=[], type_id=ptr_t)),
        ),

        # 4) Drain the queue until this specific handle completes. For the
        #    common single-task program this is equivalent to running the
        #    whole scheduler, but it also handles the case where the user
        #    has spawned siblings.
        SchedulerRun(
            until_handle=CopyOperand(Place(base=handle_local.id, projections=[], type_id=ptr_t)),
            destroy_after=(func.ret_type == void_t),
        ),
    ]

    # 5) Pull the result back out of the handle, reinterpreted as ret_type.
    # ``Return(value=None)`` is what the codegen lowers to ``ret void``;
    # passing a UNIT operand would route through ``builder.ret(i64 0)`` and
    # produce invalid IR for a void function — historically that bit
    # Void-returning entry functions that had any awaits in their body.
    ret_val: Optional[Operand]
    if func.ret_type != void_t:
        ops.append(TaskGetResult(
            result=ret_local.id,
            task_handle=CopyOperand(Place(base=handle_local.id, projections=[], type_id=ptr_t)),
            result_type=func.ret_type,
        ))
        ret_val = CopyOperand(
            Place(base=ret_local.id, projections=[], type_id=func.ret_type)
        )
    else:
        ret_val = None

    bb0 = BlockId(0)
    blocks = {
        bb0: Block(id=bb0, ops=ops, terminator=Return(value=ret_val)),
    }

    # The unused `resume_name` and `frame_name` arguments are kept so the
    # function's external signature matches the call site; codegen looks
    # the resume function up by name when lowering TaskSpawn.
    _ = resume_name
    _ = frame_name

    return MirFunction(
        name=func.name, symbol_id=func.symbol_id,
        args=func.args, locals=locals_list,
        ret_type=func.ret_type, blocks=blocks, entry_block=bb0,
        is_async=False, is_method=func.is_method,
    )


def _store_args_to_frame(
    frame_id: LocalId,
    callee: MirFunction,
    args: List[Operand],
) -> List[Op]:
    """Copy call arguments into the matching argument fields of an async frame."""
    stores: List[Op] = []
    for arg_local, arg_value in zip(callee.args, args):
        stores.append(Store(
            place=Place(
                base=frame_id,
                projections=[FieldProjection(
                    # Match the id-keyed frame field naming used when the frame
                    # struct is built (args live in callee.locals too).
                    field_name=f"$f{arg_local.id}",
                    result_type=arg_local.type_id,
                )],
                type_id=arg_local.type_id,
            ),
            value=arg_value,
        ))
    return stores


def _build_resume(
    func: MirFunction,
    await_points: List[AwaitPoint],
    mir_result: MirBuildResult,
    frame_type_id: TypeId,
    frame_name: str,
    async_with_awaits: Set[str],
    frame_type_ids: Dict[str, TypeId],
    async_functions_by_name: Dict[str, MirFunction],
) -> MirFunction:
    """Build a resume function for an async function.

    Builds a state machine that supports awaits in *arbitrary* control
    flow — not just the entry block. Every original block that contains
    one or more awaits is split into one segment per await + one tail
    segment. Each segment:

      1. Loads every original local from the frame (cheap and uniform).
      2. Optionally performs a *post-await fixup* — pulls a spawned
         child task's result out of ``frame._task_{i-1}``.
      3. Emits the original ops in its slice of the block, replacing
         the await call with either a synchronous direct call (leaf
         async children) or a ``TaskSpawn`` + ``frame._task_i`` store
         followed by a ``TaskYield`` / ``Return(None)`` exit (non-leaf
         children).
      4. Spills locals to the frame, then either yields (mid-await)
         or runs the block's original terminator. ``Return(X)``
         terminators are converted into ``TaskComplete(handle, X)``
         followed by ``Return(None)`` so the scheduler observes
         completion.

    A dedicated dispatch block at ``BlockId(0)`` switches on
    ``frame.state`` to enter the right segment:

      * state ``0`` → first segment of the original entry block
      * state ``i + 1`` (1..N) → post-segment of await ``i``
    """
    tt = mir_result.type_table
    i32_t = tt.get_builtin("i32") or tt.void_type
    ptr_t = tt.get_builtin("RawPtr") or tt.void_type
    void_t = tt.void_type

    # Use a counter strictly above all IDs in the whole program so the resume
    # function's fresh locals never collide with the entry function's locals.
    all_ids = [l.id.id for f in mir_result.program.functions for l in f.locals]
    nid = max(all_ids, default=0) + 1
    def fresh_local_id() -> LocalId:
        nonlocal nid; lid = LocalId(nid); nid += 1; return lid

    # Block IDs: start above all existing block IDs across the whole program.
    all_block_ids = [bid.id for f in mir_result.program.functions for bid in f.blocks]
    next_block_num = max(all_block_ids, default=0) + 1
    def fresh_block_id() -> BlockId:
        nonlocal next_block_num
        bid = BlockId(next_block_num)
        next_block_num += 1
        return bid

    # Frame pointer (arg 0)
    frame_local = Local(
        id=fresh_local_id(), symbol_id=None, name="_frame",
        type_id=frame_type_id, is_mutable=False, is_arg=True,
    )
    frame_id = frame_local.id

    state_local_id = fresh_local_id()
    state_local = Local(
        id=state_local_id, symbol_id=None, name="_state",
        type_id=i32_t, is_mutable=True, is_arg=False,
    )

    new_locals: List[Local] = [frame_local, state_local] + [
        Local(
            id=l.id, symbol_id=l.symbol_id, name=l.name,
            type_id=l.type_id, is_mutable=l.is_mutable, is_arg=False,
        )
        for l in func.locals
    ]

    # Must mirror the frame_fields naming above exactly (collision-proof names).
    # Locals are keyed by their unique MIR id so that (a) bookkeeping fields can
    # never clash with a user local, and (b) two shadowed source locals that
    # share a debug name still occupy distinct frame slots.
    _ff_names = (
        ["$state", "$handle"]
        + [f"$f{l.id}" for l in func.locals]
        + [f"$task{i}" for i in range(len(await_points))]
    )

    def _fplace(field_idx: int, result_tid: TypeId) -> Place:
        return Place(
            base=frame_id,
            projections=[FieldProjection(
                field_name=_ff_names[field_idx],
                result_type=result_tid,
            )],
            type_id=result_tid,
        )

    def _load_locals_from_frame() -> List[Op]:
        loads: List[Op] = []
        for j, local in enumerate(func.locals):
            loads.append(Assign(
                place=Place(base=local.id, projections=[], type_id=local.type_id),
                value=CopyOperand(_fplace(2 + j, local.type_id)),
            ))
        return loads

    def _store_locals_to_frame() -> List[Op]:
        stores: List[Op] = []
        for j, local in enumerate(func.locals):
            stores.append(Store(
                place=_fplace(2 + j, local.type_id),
                value=CopyOperand(Place(
                    base=local.id, projections=[], type_id=local.type_id,
                )),
            ))
        return stores

    is_spawn: List[bool] = [
        ap.awaited_call.func_name in async_with_awaits for ap in await_points
    ]

    # Group awaits by their containing block, sorted by op_index.
    aps_by_block: Dict[BlockId, List[AwaitPoint]] = {}
    ap_global_idx: Dict[int, int] = {}  # id(ap) → global index in await_points
    for global_idx, ap in enumerate(await_points):
        aps_by_block.setdefault(ap.block_id, []).append(ap)
        ap_global_idx[id(ap)] = global_idx
    for lst in aps_by_block.values():
        lst.sort(key=lambda ap: ap.op_index)

    # Phase 1: assign segment block IDs.
    # segment_block[(orig_block_id, seg_index)] = new BlockId.
    # ap_post_segment[global_idx] = the segment dispatched-to after await `global_idx`.
    segment_block: Dict[Tuple[BlockId, int], BlockId] = {}
    ap_post_segment: Dict[int, BlockId] = {}

    for orig_bid in sorted(func.blocks.keys(), key=lambda b: b.id):
        aps_here = aps_by_block.get(orig_bid, [])
        for seg in range(len(aps_here) + 1):
            segment_block[(orig_bid, seg)] = fresh_block_id()
        for i, ap in enumerate(aps_here):
            ap_post_segment[ap_global_idx[id(ap)]] = segment_block[(orig_bid, i + 1)]

    default_block_id = fresh_block_id()

    def remap_target(orig: BlockId) -> BlockId:
        # Every original block has a (orig, 0) segment, so this lookup
        # always succeeds for in-program edges. Unknown ids pass through
        # unchanged (defensive — shouldn't happen).
        return segment_block.get((orig, 0), orig)

    def remap_terminator(term: Optional[Terminator]) -> Terminator:
        if term is None:
            return Return(value=None)
        if isinstance(term, Branch):
            return Branch(target=remap_target(term.target))
        if isinstance(term, CondBranch):
            return CondBranch(
                condition=term.condition,
                true_target=remap_target(term.true_target),
                false_target=remap_target(term.false_target),
            )
        if isinstance(term, SwitchInt):
            return SwitchInt(
                value=term.value,
                cases=[(v, remap_target(t)) for v, t in term.cases],
                default=remap_target(term.default),
            )
        return term

    def _emit_await(ap: AwaitPoint, global_idx: int, ops: List[Op]) -> None:
        """Emit the spawn-or-direct-call lowering for an await point.

        For spawned children: allocates the child frame (if known),
        copies args, calls TaskSpawn, and stashes the resulting handle
        in ``frame._task_{global_idx}``. For leaf children: emits a
        synchronous CallStatic and assigns the result to
        ``ap.result_local``.
        """
        task_result = fresh_local_id()
        new_locals.append(Local(
            id=task_result, symbol_id=None,
            name=f"_task_res{global_idx}",
            type_id=ptr_t if is_spawn[global_idx] else ap.result_type,
            is_mutable=True, is_arg=False,
        ))

        if is_spawn[global_idx]:
            child_frame_operand: Optional[Operand] = None
            callee_name = ap.awaited_call.func_name if isinstance(ap.awaited_call, CallStatic) else ""
            child_func = async_functions_by_name.get(callee_name)
            child_frame_type = frame_type_ids.get(callee_name)
            if callee_name and (child_func is None or child_frame_type is None):
                errors.append(
                    f"async_lowering: cannot find frame for async callee '{callee_name}'; "
                    "arguments will not be passed correctly"
                )
            if child_func is not None and child_frame_type is not None:
                child_frame = fresh_local_id()
                new_locals.append(Local(
                    id=child_frame, symbol_id=None,
                    name=f"_child_frame_{global_idx}",
                    type_id=child_frame_type,
                    is_mutable=False, is_arg=False,
                ))
                ops.append(AllocAsyncFrame(
                    result=child_frame, frame_type=child_frame_type,
                ))
                ops.extend(_store_args_to_frame(
                    child_frame, child_func, ap.awaited_call.args,
                ))
                child_frame_operand = CopyOperand(Place(
                    base=child_frame, projections=[],
                    type_id=child_frame_type,
                ))

            ops.append(TaskSpawn(
                result=task_result,
                async_func_name=callee_name,
                args=[] if child_frame_operand is not None else ap.awaited_call.args,
                result_type=ptr_t,
                frame=child_frame_operand,
            ))
            task_field_idx = 2 + len(func.locals) + global_idx
            ops.append(Store(
                place=_fplace(task_field_idx, ptr_t),
                value=CopyOperand(Place(
                    base=task_result, projections=[], type_id=ptr_t,
                )),
            ))
        else:
            ops.append(CallStatic(
                result=task_result,
                func_name=ap.awaited_call.func_name,
                func_symbol=ap.awaited_call.func_symbol,
                args=ap.awaited_call.args,
                result_type=ap.result_type,
            ))
            if ap.result_local is not None:
                ops.append(Assign(
                    place=Place(
                        base=ap.result_local, projections=[],
                        type_id=ap.result_type,
                    ),
                    value=CopyOperand(Place(
                        base=task_result, projections=[],
                        type_id=ap.result_type,
                    )),
                ))

    def _emit_post_await_fixup(ap: AwaitPoint, global_idx: int, ops: List[Op]) -> None:
        """Recover a spawned child's result after re-entry.

        For direct (leaf) calls this is a no-op — the result was
        assigned synchronously at the call site. For spawns we read
        the stashed handle out of ``frame._task_{global_idx}`` and run
        ``TaskGetResult`` to pull the value into ``ap.result_local``.
        Void returns still need the call so the runtime can destroy
        the handle and avoid leaking one ``TaskHandle*`` per spawn.
        """
        if not is_spawn[global_idx]:
            return

        handle_local_id = fresh_local_id()
        new_locals.append(Local(
            id=handle_local_id, symbol_id=None,
            name=f"_prev_handle_{global_idx}", type_id=ptr_t,
            is_mutable=False, is_arg=False,
        ))
        task_field_idx = 2 + len(func.locals) + global_idx
        ops.append(Assign(
            place=Place(base=handle_local_id, projections=[], type_id=ptr_t),
            value=CopyOperand(_fplace(task_field_idx, ptr_t)),
        ))

        if ap.result_local is not None:
            result_local_id = ap.result_local
        else:
            result_local_id = fresh_local_id()
            new_locals.append(Local(
                id=result_local_id, symbol_id=None,
                name=f"_void_join_{global_idx}", type_id=void_t,
                is_mutable=False, is_arg=False,
            ))
        ops.append(TaskGetResult(
            result=result_local_id,
            task_handle=CopyOperand(Place(
                base=handle_local_id, projections=[], type_id=ptr_t,
            )),
            result_type=ap.result_type,
        ))

    blocks: Dict[BlockId, Block] = {}

    # ----- Dispatch block -----
    dispatch_id = BlockId(0)
    entry_first_seg = segment_block[(func.entry_block, 0)]
    dispatch_cases: List[Tuple[int, BlockId]] = [(0, entry_first_seg)]
    for global_idx in range(len(await_points)):
        dispatch_cases.append((global_idx + 1, ap_post_segment[global_idx]))

    blocks[dispatch_id] = Block(
        id=dispatch_id,
        ops=[
            Assign(
                place=Place(
                    base=state_local_id, projections=[], type_id=i32_t,
                ),
                value=CopyOperand(_fplace(0, i32_t)),
            ),
        ],
        terminator=SwitchInt(
            value=CopyOperand(Place(
                base=state_local_id, projections=[], type_id=i32_t,
            )),
            cases=dispatch_cases,
            default=default_block_id,
        ),
    )
    blocks[default_block_id] = Block(
        id=default_block_id, ops=[], terminator=Unreachable(),
    )

    # ----- Per-block segments -----
    for orig_bid in sorted(func.blocks.keys(), key=lambda b: b.id):
        orig_block = func.blocks[orig_bid]
        aps_here = aps_by_block.get(orig_bid, [])
        k = len(aps_here)

        for seg_idx in range(k + 1):
            seg_bid = segment_block[(orig_bid, seg_idx)]
            seg_ops: List[Op] = []

            # Every segment loads locals at entry — uniform & cheap.
            seg_ops.extend(_load_locals_from_frame())

            # Post-await fixup belongs at the top of a resume target.
            if seg_idx >= 1:
                prev_ap = aps_here[seg_idx - 1]
                _emit_post_await_fixup(
                    prev_ap, ap_global_idx[id(prev_ap)], seg_ops,
                )

            # Slice of original ops to emit in this segment.
            start = aps_here[seg_idx - 1].op_index + 1 if seg_idx >= 1 else 0
            end = aps_here[seg_idx].op_index if seg_idx < k else len(orig_block.ops)
            for op in orig_block.ops[start:end]:
                # TaskYield markers came from the MIR builder's lowering
                # of `await`. They're identity ops once the state
                # machine itself handles yields, so drop them.
                if isinstance(op, TaskYield):
                    continue
                seg_ops.append(op)

            if seg_idx < k:
                # Mid-block: emit await logic, advance state, spill, yield.
                cur_ap = aps_here[seg_idx]
                cur_global = ap_global_idx[id(cur_ap)]
                _emit_await(cur_ap, cur_global, seg_ops)

                next_state = cur_global + 1
                seg_ops.append(Store(
                    place=_fplace(0, i32_t),
                    value=ConstantOperand(ConstantKind.INT, next_state, i32_t),
                ))
                seg_ops.extend(_store_locals_to_frame())
                seg_ops.append(TaskYield())
                terminator: Terminator = Return(value=None)
            else:
                # Last segment of this block — preserve original terminator,
                # converting Return(X) into TaskComplete + Return(None).
                seg_ops.extend(_store_locals_to_frame())
                orig_term = orig_block.terminator
                if isinstance(orig_term, Return):
                    seg_ops.append(TaskComplete(
                        task_handle=CopyOperand(_fplace(1, ptr_t)),
                        result=orig_term.value,
                    ))
                    terminator = Return(value=None)
                else:
                    terminator = remap_terminator(orig_term)

            blocks[seg_bid] = Block(
                id=seg_bid, ops=seg_ops, terminator=terminator,
            )

    return MirFunction(
        name=f"{func.name}_resume", symbol_id=None,
        args=[frame_local], locals=new_locals, ret_type=void_t,
        blocks=blocks, entry_block=dispatch_id,
        is_async=False, is_method=False,
    )
