"""
InstructionEmitter - Op-by-op LLVM IR emission.

Handles translation of all MIR operations to LLVM IR instructions.
"""

from __future__ import annotations

from typing import Dict, Optional

from llvmlite import ir

from ..types import TypeId, TypeTable, TypeKind
from ..mir import (
    # ID types
    LocalId,
    BlockId,
    # Place and projections
    Place,
    FieldProjection,
    IndexProjection,
    DerefProjection,
    # Operands
    Operand,
    CopyOperand,
    MoveOperand,
    ConstantOperand,
    ConstantKind,
    operand_type,
    # Operations
    Op,
    BinOp,
    BinOpKind,
    CmpOp,
    CmpOpKind,
    UnaryOp,
    UnaryOpKind,
    CastOp,
    MakeStruct,
    MakeEnum,
    MakeSome,
    MakeNone,
    ExtractField,
    ExtractClosureCapture,
    ExtractEnumPayload,
    GetTag,
    Assign,
    Store,
    Load,
    CallStatic,
    CallVTable,
    CallWitness,
    MakeClosure,
    CallClosure,
)
from .types import TypeLayoutCache
from .runtime import RuntimeABI
from .ops_arc import OpsArcMixin
from .ops_async import OpsAsyncMixin
from .ops_existential import OpsExistentialMixin
from .ops_arithmetic import OpsArithmeticMixin
from .ops_aggregate import OpsAggregateMixin
from .ops_memory import OpsMemoryMixin
from .ops_calls import OpsCallsMixin

# Re-export names needed for isinstance checks in emit_op dispatch
from ..mir import (
    Retain, Release,
    BoxExistential, ExistentialCheckType, ExistentialUnbox,
    TaskJoin, TaskSpawn, TaskYield, TaskComplete, Suspend,
    AllocAsyncFrame, SchedulerRun, TaskGetResult,
    AllocObj, Clone, GCCheck,
)


class InstructionEmitter(OpsArcMixin, OpsAsyncMixin, OpsExistentialMixin, OpsArithmeticMixin, OpsAggregateMixin, OpsMemoryMixin, OpsCallsMixin):
    """
    Emits LLVM instructions for MIR operations.

    Each MIR Op is translated to one or more LLVM instructions.
    """

    def __init__(
        self,
        builder: ir.IRBuilder,
        type_cache: TypeLayoutCache,
        runtime: RuntimeABI,
        type_table: TypeTable,
        local_storage: Dict[LocalId, ir.AllocaInstr],
        local_types: Dict[LocalId, TypeId],
        func_map: Dict[str, ir.Function],
        block_map: Dict[BlockId, ir.Block],
        witness_tables: Optional[Dict[tuple[TypeId, TypeId], ir.GlobalVariable]] = None,
        async_codegen: Optional[object] = None,
        mir_structs: Optional[list] = None,
    ) -> None:
        self.builder = builder
        self.type_cache = type_cache
        self.runtime = runtime
        self.type_table = type_table
        self.local_storage = local_storage
        self.local_types = local_types
        self.func_map = func_map
        self.block_map = block_map
        self.witness_tables = witness_tables or {}
        self.async_codegen = async_codegen

        # Module for global values (string constants)
        self.module = builder.module
        
        # MIR structs for async frame allocation
        self.mir_structs = mir_structs or []

        # String constant counter
        self._string_counter = 0

        # Maps a temp local that holds `someLocal as RawPtr` (an address-of)
        # back to the source value local. Lets call lowering recover the
        # pointee element type when a `&out` / `&value` flows into an FFI
        # accessor (e.g. rt_gvec_get/set), so primitive Vec access can be
        # inlined instead of crossing the opaque C-call boundary.
        self._raw_addr_src: Dict[LocalId, LocalId] = {}

    def emit_op(self, op: Op) -> Optional[ir.Value]:
        """Emit LLVM instructions for a MIR operation."""
        if isinstance(op, BinOp):
            return self._emit_binop(op)
        elif isinstance(op, CmpOp):
            return self._emit_cmpop(op)
        elif isinstance(op, UnaryOp):
            return self._emit_unaryop(op)
        elif isinstance(op, CastOp):
            return self._emit_cast(op)
        elif isinstance(op, MakeStruct):
            return self._emit_make_struct(op)
        elif isinstance(op, MakeEnum):
            return self._emit_make_enum(op)
        elif isinstance(op, MakeSome):
            return self._emit_make_some(op)
        elif isinstance(op, MakeNone):
            return self._emit_make_none(op)
        elif isinstance(op, ExtractField):
            return self._emit_extract_field(op)
        elif isinstance(op, ExtractClosureCapture):
            return self._emit_extract_closure_capture(op)
        elif isinstance(op, ExtractEnumPayload):
            return self._emit_extract_enum_payload(op)
        elif isinstance(op, GetTag):
            return self._emit_get_tag(op)
        elif isinstance(op, Assign):
            return self._emit_assign(op)
        elif isinstance(op, Store):
            return self._emit_store(op)
        elif isinstance(op, Load):
            return self._emit_load(op)
        elif isinstance(op, Retain):
            return self._emit_retain(op)
        elif isinstance(op, Release):
            return self._emit_release(op)
        elif isinstance(op, CallStatic):
            return self._emit_call_static(op)
        elif isinstance(op, CallVTable):
            return self._emit_call_vtable(op)
        elif isinstance(op, CallWitness):
            return self._emit_call_witness(op)
        elif isinstance(op, MakeClosure):
            return self._emit_make_closure(op)
        elif isinstance(op, CallClosure):
            return self._emit_call_closure(op)
        elif isinstance(op, BoxExistential):
            return self._emit_box_existential(op)
        elif isinstance(op, ExistentialCheckType):
            return self._emit_existential_check_type(op)
        elif isinstance(op, ExistentialUnbox):
            return self._emit_existential_unbox(op)
        elif isinstance(op, TaskJoin):
            return self._emit_task_join(op)
        elif isinstance(op, TaskSpawn):
            return self._emit_task_spawn(op)
        elif isinstance(op, TaskYield):
            return self._emit_task_yield(op)
        elif isinstance(op, TaskComplete):
            return self._emit_task_complete(op)
        elif isinstance(op, AllocAsyncFrame):
            return self._emit_alloc_async_frame(op)
        elif isinstance(op, SchedulerRun):
            return self._emit_scheduler_run(op)
        elif isinstance(op, TaskGetResult):
            return self._emit_task_get_result(op)
        elif isinstance(op, Suspend):
            return self._emit_suspend(op)
        elif isinstance(op, AllocObj):
            return self._emit_alloc_obj(op)
        elif isinstance(op, Clone):
            return self._emit_clone(op)
        elif isinstance(op, GCCheck):
            return self._emit_gc_check(op)
        else:
            raise NotImplementedError(f"Unknown operation: {type(op).__name__}")

    def emit_operand(self, operand: Operand) -> ir.Value:
        """Emit LLVM value for an operand."""
        if isinstance(operand, CopyOperand):
            return self._load_place(operand.place)
        elif isinstance(operand, MoveOperand):
            # Move is same as copy at LLVM level (ARC handles ownership)
            return self._load_place(operand.place)
        elif isinstance(operand, ConstantOperand):
            return self._emit_constant(operand)
        else:
            raise NotImplementedError(f"Unknown operand: {type(operand).__name__}")
