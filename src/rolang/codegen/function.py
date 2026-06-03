"""
FunctionCodegen - Translates MIR functions to LLVM IR.

Handles:
- Block mapping and ordering
- Local variable allocation
- Control flow translation
"""

from __future__ import annotations

from typing import Dict, List, Optional

from llvmlite import ir

from ..types import TypeId, TypeTable
from ..mir import (
    MirFunction,
    Block,
    BlockId,
    LocalId,
    Local,
    Terminator,
    Branch,
    CondBranch,
    SwitchInt,
    Return,
    Unreachable,
)
from .types import TypeLayoutCache
from .runtime import RuntimeABI
from .instructions import InstructionEmitter


class FunctionCodegen:
    """
    Generates LLVM IR for a single MIR function.

    Responsibilities:
    - Create entry block with allocas for all locals
    - Map MIR blocks to LLVM basic blocks
    - Process operations via InstructionEmitter
    - Generate terminators
    """

    def __init__(
        self,
        mir_func: MirFunction,
        llvm_func: ir.Function,
        type_cache: TypeLayoutCache,
        runtime: RuntimeABI,
        type_table: TypeTable,
        func_map: Dict[str, ir.Function],
        witness_tables: Optional[Dict[tuple[TypeId, TypeId], ir.GlobalVariable]] = None,
        async_codegen: Optional[object] = None,
        mir_structs: Optional[list] = None,
    ) -> None:
        self.mir_func = mir_func
        self.llvm_func = llvm_func
        self.type_cache = type_cache
        self.runtime = runtime
        self.type_table = type_table
        self.func_map = func_map
        self.witness_tables = witness_tables or {}
        self.async_codegen = async_codegen
        self.mir_structs = mir_structs or []

        # Block mapping: MIR BlockId -> LLVM BasicBlock
        self.block_map: Dict[BlockId, ir.Block] = {}

        # Local storage: LocalId -> alloca instruction
        self.local_storage: Dict[LocalId, ir.AllocaInstr] = {}

        # Local types: LocalId -> TypeId
        self.local_types: Dict[LocalId, TypeId] = {}

        # Current builder
        self.builder: Optional[ir.IRBuilder] = None

        # Errors collected during generation
        self.errors: List[str] = []

    def generate(self) -> List[str]:
        """Generate LLVM IR for the function. Returns list of errors."""
        # Create all basic blocks first (for forward references)
        self._create_blocks()

        # Create entry block with allocas
        self._create_entry_block()

        # Generate code for each MIR block
        for block_id, mir_block in self.mir_func.blocks.items():
            self._generate_block(block_id, mir_block)

        return self.errors

    def _create_blocks(self) -> None:
        """Create LLVM basic blocks for all MIR blocks."""
        # Create blocks in a deterministic order
        block_ids = sorted(self.mir_func.blocks.keys(), key=lambda b: b.id)

        # Entry block first
        entry_id = self.mir_func.entry_block
        if entry_id in self.mir_func.blocks:
            entry_bb = self.llvm_func.append_basic_block(f"bb{entry_id.id}")
            self.block_map[entry_id] = entry_bb

        # Then other blocks
        for block_id in block_ids:
            if block_id not in self.block_map:
                bb = self.llvm_func.append_basic_block(f"bb{block_id.id}")
                self.block_map[block_id] = bb

    def _create_entry_block(self) -> None:
        """Create allocas for all locals in the entry block."""
        if not self.mir_func.entry_block in self.block_map:
            return

        entry_bb = self.block_map[self.mir_func.entry_block]
        self.builder = ir.IRBuilder(entry_bb)

        # Allocate storage for all locals
        for local in self.mir_func.locals:
            self.local_types[local.id] = local.type_id
            llvm_type = self.type_cache.get_llvm_type(local.type_id)

            # Don't allocate void type
            if isinstance(llvm_type, ir.VoidType):
                continue

            # Create alloca
            alloca = self.builder.alloca(llvm_type, name=local.name)
            self.local_storage[local.id] = alloca

            # Store function arguments into their allocas (standard
            # alloca-then-store pattern — every arg, including async-resume
            # `_frame`, is stored normally now that frames are single-level
            # heap pointers).
            if local.is_arg:
                arg_index = next(
                    (i for i, arg in enumerate(self.mir_func.args)
                     if arg.id == local.id),
                    None
                )
                if arg_index is not None and arg_index < len(self.llvm_func.args):
                    llvm_arg = self.llvm_func.args[arg_index]
                    self.builder.store(llvm_arg, alloca)

    def _generate_block(self, block_id: BlockId, mir_block: Block) -> None:
        """Generate code for a single MIR block."""
        if block_id not in self.block_map:
            return

        llvm_bb = self.block_map[block_id]

        # Position builder at the end of the block
        # (entry block already has allocas, other blocks are empty)
        if block_id == self.mir_func.entry_block:
            # Continue after allocas
            pass
        else:
            self.builder = ir.IRBuilder(llvm_bb)

        if self.builder is None:
            return

        # Create instruction emitter
        emitter = InstructionEmitter(
            builder=self.builder,
            type_cache=self.type_cache,
            runtime=self.runtime,
            type_table=self.type_table,
            local_storage=self.local_storage,
            local_types=self.local_types,
            func_map=self.func_map,
            block_map=self.block_map,
            witness_tables=self.witness_tables,
            async_codegen=self.async_codegen,
            mir_structs=self.mir_structs,
        )

        # Generate code for each operation
        for op in mir_block.ops:
            try:
                emitter.emit_op(op)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                self.errors.append(f"Error in {self.mir_func.name}, block {block_id.id}: {e}\n{tb}")

        # Generate terminator
        if mir_block.terminator is not None:
            self._generate_terminator(mir_block.terminator, emitter)
        else:
            # Block without terminator - add unreachable
            self.builder.unreachable()

    def _generate_terminator(
        self,
        term: Terminator,
        emitter: InstructionEmitter,
    ) -> None:
        """Generate code for a terminator."""
        if self.builder is None:
            return

        if isinstance(term, Branch):
            target_bb = self.block_map.get(term.target)
            if target_bb:
                self.builder.branch(target_bb)
            else:
                self.errors.append(f"Unknown branch target: {term.target.id}")
                self.builder.unreachable()

        elif isinstance(term, CondBranch):
            # Get condition value
            cond = emitter.emit_operand(term.condition)

            # Ensure condition is i1
            if not isinstance(cond.type, ir.IntType) or cond.type.width != 1:
                cond = self.builder.trunc(cond, ir.IntType(1), name="cond")

            true_bb = self.block_map.get(term.true_target)
            false_bb = self.block_map.get(term.false_target)

            if true_bb and false_bb:
                self.builder.cbranch(cond, true_bb, false_bb)
            else:
                self.errors.append(f"Unknown branch targets in CondBranch")
                self.builder.unreachable()

        elif isinstance(term, SwitchInt):
            # Get switch value
            val = emitter.emit_operand(term.value)

            default_bb = self.block_map.get(term.default)
            if default_bb is None:
                self.errors.append(f"Unknown default target in SwitchInt")
                self.builder.unreachable()
                return

            switch = self.builder.switch(val, default_bb)

            for case_val, target_id in term.cases:
                target_bb = self.block_map.get(target_id)
                if target_bb:
                    case_const = ir.Constant(val.type, case_val)
                    switch.add_case(case_const, target_bb)
                else:
                    self.errors.append(f"Unknown case target: {target_id.id}")

        elif isinstance(term, Return):
            if term.value is not None:
                ret_val = emitter.emit_operand(term.value)
                # Coerce to function return type if needed
                ret_type = self.llvm_func.return_value.type
                ret_val = emitter._coerce_int(
                    ret_val, ret_type,
                    signed=emitter._operand_is_signed(term.value))
                self.builder.ret(ret_val)
            else:
                self.builder.ret_void()

        elif isinstance(term, Unreachable):
            self.builder.unreachable()

        else:
            self.errors.append(f"Unknown terminator type: {type(term).__name__}")
            self.builder.unreachable()
