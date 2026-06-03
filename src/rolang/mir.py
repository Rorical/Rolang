"""
MIR (Mid-level IR) definitions for the Rolang compiler.

MIR is a CFG-based intermediate representation with SSA-like values.
It bridges HIR and LLVM IR, making control flow and operations explicit.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple, Union

from .types import TypeId, TypeTable
from .symbols import SymbolId, SymbolTable


# =============================================================================
# ID Types (frozen for use as dict keys)
# =============================================================================

@dataclass(frozen=True)
class LocalId:
    """Unique identifier for a local variable within a function."""
    id: int

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(frozen=True)
class BlockId:
    """Unique identifier for a basic block within a function."""
    id: int

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(frozen=True)
class ValueId:
    """Unique identifier for an SSA value (result of an operation)."""
    id: int

    def __hash__(self) -> int:
        return hash(self.id)


# =============================================================================
# Local Variables
# =============================================================================

@dataclass
class Local:
    """
    A local variable in MIR (includes function arguments).

    Unlike HIR variables which are scoped, MIR locals are flat and
    live for the entire function.
    """
    id: LocalId
    symbol_id: Optional[SymbolId]
    name: str
    type_id: TypeId
    is_mutable: bool
    is_arg: bool = False


# =============================================================================
# Place Projections (for accessing parts of values)
# =============================================================================

class PlaceProjectionKind(Enum):
    """Kind of place projection."""
    FIELD = auto()   # .field
    INDEX = auto()   # [index]
    DEREF = auto()   # *ptr


@dataclass
class FieldProjection:
    """Access a struct field by name."""
    field_name: str
    result_type: TypeId


@dataclass
class IndexProjection:
    """Array/dict indexing."""
    index: 'Operand'
    result_type: TypeId



@dataclass
class DerefProjection:
    """Dereference a pointer/reference."""
    result_type: TypeId


PlaceProjection = Union[FieldProjection, IndexProjection, DerefProjection]


@dataclass
class Place:
    """
    A memory location that can be read from or written to.

    A place consists of a base local variable and zero or more projections
    that navigate to a specific part of the value.

    Examples:
        x           -> Place(base=_0, projections=[])
        x.field     -> Place(base=_0, projections=[FieldProjection("field")])
        x.a.b       -> Place(base=_0, projections=[Field("a"), Field("b")])
        x[i]        -> Place(base=_0, projections=[IndexProjection(i)])
    """
    base: LocalId
    projections: List[PlaceProjection]
    type_id: TypeId


# =============================================================================
# Operands (inputs to operations)
# =============================================================================

@dataclass
class CopyOperand:
    """Read a value from a place (for Copy types or when we need a copy)."""
    place: Place


@dataclass
class MoveOperand:
    """Move a value from a place (transfers ownership)."""
    place: Place


class ConstantKind(Enum):
    """Kind of constant value."""
    INT = auto()
    FLOAT = auto()
    BOOL = auto()
    STRING = auto()
    NIL = auto()
    UNIT = auto()


@dataclass
class ConstantOperand:
    """A compile-time constant value."""
    kind: ConstantKind
    value: Union[int, float, bool, str, None]
    type_id: TypeId


Operand = Union[CopyOperand, MoveOperand, ConstantOperand]


def operand_type(op: Operand) -> TypeId:
    """Get the type of an operand."""
    if isinstance(op, CopyOperand):
        return op.place.type_id
    elif isinstance(op, MoveOperand):
        return op.place.type_id
    else:
        return op.type_id


# =============================================================================
# Binary and Unary Operations
# =============================================================================

class BinOpKind(Enum):
    """Binary operation kinds."""
    ADD = auto()      # +
    SUB = auto()      # -
    MUL = auto()      # *
    DIV = auto()      # /
    MOD = auto()      # %
    BIT_AND = auto()  # &
    BIT_OR = auto()   # |
    BIT_XOR = auto()  # ^
    SHL = auto()      # <<
    SHR = auto()      # >>


class CmpOpKind(Enum):
    """Comparison operation kinds."""
    EQ = auto()   # ==
    NE = auto()   # !=
    LT = auto()   # <
    LE = auto()   # <=
    GT = auto()   # >
    GE = auto()   # >=


class UnaryOpKind(Enum):
    """Unary operation kinds."""
    NEG = auto()      # -
    NOT = auto()      # !
    BIT_NOT = auto()  # ~


class LogicOpKind(Enum):
    """Logical operation kinds (short-circuiting)."""
    AND = auto()  # &&
    OR = auto()   # ||


# =============================================================================
# Operations (instructions that don't change control flow)
# =============================================================================

@dataclass
class Op(ABC):
    """Base class for all MIR operations."""
    pass


# --- Compute Operations ---

@dataclass
class BinOp(Op):
    """Binary arithmetic/bitwise operation: result = left op right"""
    result: LocalId
    op: BinOpKind
    left: Operand
    right: Operand
    result_type: TypeId


@dataclass
class CmpOp(Op):
    """Comparison operation: result = left cmp right"""
    result: LocalId
    op: CmpOpKind
    left: Operand
    right: Operand


@dataclass
class UnaryOp(Op):
    """Unary operation: result = op operand"""
    result: LocalId
    op: UnaryOpKind
    operand: Operand
    result_type: TypeId


@dataclass
class CastOp(Op):
    """Type cast: result = operand as target_type"""
    result: LocalId
    operand: Operand
    target_type: TypeId


# --- Aggregate Operations ---

@dataclass
class MakeStruct(Op):
    """Create a struct: result = Struct { field: value, ... }"""
    result: LocalId
    struct_type: TypeId
    fields: List[Tuple[str, Operand]]  # (field_name, value) pairs


@dataclass
class MakeEnum(Op):
    """Create an enum variant: result = .case(payload)"""
    result: LocalId
    enum_type: TypeId
    case_name: str
    tag: int
    payload: List[Operand]


@dataclass
class MakeSome(Op):
    """Wrap a value in Some: result = Some(value)"""
    result: LocalId
    value: Operand
    result_type: TypeId  # Optional[T]


@dataclass
class MakeNone(Op):
    """Create a None value: result = None"""
    result: LocalId
    result_type: TypeId  # Optional[T]


@dataclass
class ExtractField(Op):
    """Extract a struct field: result = struct.field"""
    result: LocalId
    aggregate: Operand
    field_name: str
    field_index: int  # Position in struct layout
    result_type: TypeId


@dataclass
class ExtractClosureCapture(Op):
    """Extract a captured value from a closure object payload."""
    result: LocalId
    closure: Operand
    capture_index: int
    result_type: TypeId


@dataclass
class ExtractEnumPayload(Op):
    """Extract enum variant payload: result = enum.payload (after tag check)"""
    result: LocalId
    enum_val: Operand
    case_name: str
    payload_index: int  # Which payload element (for multi-element payloads)
    result_type: TypeId


@dataclass
class GetTag(Op):
    """Get enum discriminant: result = discriminant(enum)"""
    result: LocalId
    enum_val: Operand


# --- Memory Operations ---

@dataclass
class Assign(Op):
    """Assign to a local: place = value"""
    place: Place
    value: Operand


@dataclass
class Store(Op):
    """Store to a place with projections: *place = value"""
    place: Place
    value: Operand


@dataclass
class Load(Op):
    """Load from a place: result = *place"""
    result: LocalId
    place: Place


# --- ARC Operations ---

@dataclass
class Retain(Op):
    """Increment reference count. Used in both v1 and v2."""
    operand: Operand


@dataclass
class Release(Op):
    """Decrement reference count (may deallocate). Used in both v1 and v2."""
    operand: Operand

# --- v2 Heap Object Operations ---

@dataclass
class AllocObj(Op):
    """Allocate a new typed heap object: result = rt_obj_alloc(type_id, size)."""
    result: LocalId
    type_id: TypeId       # Type descriptor index for GC tracing
    payload_size: int      # Size of payload in bytes
    result_type: TypeId    # Type of the result (pointer type)


@dataclass
class Clone(Op):
    """Deep-copy a heap-allocated object: result = rt_obj_clone(value)."""
    result: LocalId
    value: Operand         # The object to clone
    result_type: TypeId    # Type of the result (same as value type)


@dataclass
class GCCheck(Op):
    """Conditional GC trigger point for cycle detection."""
    pass


# --- Call Operations ---

@dataclass
class CallStatic(Op):
    """Static function call: result = func(args)"""
    result: Optional[LocalId]  # None for void functions
    func_name: str
    func_symbol: Optional[SymbolId]
    args: List[Operand]
    result_type: TypeId


@dataclass
class CallVTable(Op):
    """Virtual table dispatch: result = obj.method(args)"""
    result: Optional[LocalId]
    receiver: Operand
    method_name: str
    args: List[Operand]
    result_type: TypeId


@dataclass
class CallWitness(Op):
    """Witness table call (generic constraint): result = T::method(args)"""
    result: Optional[LocalId]
    witness_type: TypeId
    method_name: str
    args: List[Operand]
    result_type: TypeId


# --- Closure Operations ---

@dataclass
class MakeClosure(Op):
    """
    Create a closure: result = closure(func_ptr, captured_env).

    The closure is represented as a struct containing:
    - A pointer to the underlying function
    - The captured environment (values from enclosing scope)
    """
    result: LocalId
    func_name: str  # Name of the generated closure function
    captures: List[Operand]  # Values captured from enclosing scope
    result_type: TypeId  # The closure type


@dataclass
class CallClosure(Op):
    """
    Call a closure: result = closure(args).

    Extracts the function pointer and environment from the closure,
    then calls the function with the environment as an implicit first argument.
    """
    result: Optional[LocalId]  # None for void-returning closures
    closure: Operand  # The closure to call
    args: List[Operand]  # Arguments to pass
    result_type: TypeId


# --- Protocol/Existential Operations ---

@dataclass
class BoxExistential(Op):
    """
    Box a value as an existential: result = value as any Protocol.

    Creates a type-erased container with:
    - A pointer to the witness table for (ConcreteType, Protocol)
    - A managed object pointer for the boxed value
    """
    result: LocalId
    value: Operand
    concrete_type: TypeId  # The actual type of the value
    protocol_type: TypeId  # The protocol being conformed to
    result_type: TypeId  # The existential type


@dataclass
class ExistentialCheckType(Op):
    """Test whether an existential carries a value of ``concrete_type``.

    Compares the existential's witness-table pointer against the witness
    table emitted for ``(concrete_type, protocol_type)``. ``result`` is a
    Bool that is true on a match.
    """
    result: LocalId
    existential: Operand
    concrete_type: TypeId
    protocol_type: TypeId


@dataclass
class ExistentialUnbox(Op):
    """Extract the concrete value from an existential.

    Reads the existential's value-object pointer and reinterprets it as
    ``concrete_type``. The caller is expected to have already verified
    (via :class:`ExistentialCheckType`) that the existential carries a
    matching value — otherwise behaviour is unspecified.
    """
    result: LocalId
    existential: Operand
    concrete_type: TypeId
    protocol_type: TypeId
    result_type: TypeId


# --- Async Operations ---

@dataclass
class Suspend(Op):
    """
    Suspend at an await point.

    This operation saves the current state and yields control.
    When resumed, execution continues from the next instruction.
    """
    state_id: int  # State machine state number
    result: Optional[LocalId]  # Where to store the awaited result
    result_type: TypeId  # Type of the awaited result


@dataclass
class TaskSpawn(Op):
    """
    Spawn an async task.

    Creates a new task that will execute the given async function.
    Returns a task handle that can be awaited later.
    """
    result: LocalId  # Task handle
    async_func_name: str  # Name of the async function to call
    args: List[Operand]  # Arguments to pass
    result_type: TypeId  # Type of TaskHandle<T>
    frame: Optional[Operand] = None  # Pre-allocated frame (None = allocate automatically)


@dataclass
class TaskJoin(Op):
    """
    Join (await) a task.

    Waits for the task to complete and retrieves its result.
    May suspend if the task is not yet complete.
    """
    result: Optional[LocalId]  # Where to store the task result
    task_handle: Operand  # The task handle to await
    result_type: TypeId  # Type of the result


@dataclass
class TaskYield(Op):
    """
    Yield control to the scheduler.

    Allows other tasks to run without completing the current task.
    """
    pass


@dataclass
class TaskComplete(Op):
    """
    Mark the current task as complete.

    Sets the task's result and signals completion to waiters.
    """
    task_handle: Operand  # The current task's handle
    result: Optional[Operand]  # The result value (None for void)


@dataclass
class AllocAsyncFrame(Op):
    """
    Allocate the heap frame backing an async state machine.

    Lowers to a single `rt_obj_alloc(payload_size, align, type_id)` call so
    the layout (including the 32-byte ARC header) matches what generated
    `_resume` functions expect. The state field is zero-initialised by
    rt_obj_alloc; all other locals start out as zero-bit-pattern.
    """
    result: LocalId    # Where the frame pointer lands (ptr-typed local)
    frame_type: TypeId  # The frame struct type (MirStruct.type_id)


@dataclass
class SchedulerRun(Op):
    """
    Drain the task queue until either it's empty or `until_handle` (if given)
    has been marked completed. Lowers to either `rt_scheduler_run()` (no
    handle) or `rt_task_join(handle)` (specific handle). Used by the entry
    function of an async program to drive the state machine to completion.
    """
    until_handle: Optional[Operand] = None
    destroy_after: bool = False


@dataclass
class TaskGetResult(Op):
    """
    Load the `result` field of a TaskHandle (`void* result;`) and reinterpret
    it as the given result type. Used after `SchedulerRun` to harvest the
    final value of an async state machine. The handle must be in the
    completed state.
    """
    result: LocalId
    task_handle: Operand
    result_type: TypeId


# =============================================================================
# Terminators (instructions that end a basic block)
# =============================================================================

@dataclass
class Terminator(ABC):
    """Base class for block terminators."""
    pass


@dataclass
class Branch(Terminator):
    """Unconditional branch: br target"""
    target: BlockId


@dataclass
class CondBranch(Terminator):
    """Conditional branch: if cond then true_target else false_target"""
    condition: Operand
    true_target: BlockId
    false_target: BlockId


@dataclass
class SwitchInt(Terminator):
    """Integer switch: switch val { cases } default"""
    value: Operand
    cases: List[Tuple[int, BlockId]]  # (value, target) pairs
    default: BlockId


@dataclass
class Return(Terminator):
    """Return from function: return value"""
    value: Optional[Operand]


@dataclass
class Unreachable(Terminator):
    """Unreachable code marker (e.g., after exhaustive match)."""
    pass


# =============================================================================
# Basic Block
# =============================================================================

@dataclass
class Block:
    """
    A basic block in the CFG.

    A basic block contains a sequence of operations followed by exactly
    one terminator. Operations never change control flow; terminators always do.
    """
    id: BlockId
    ops: List[Op] = field(default_factory=list)
    terminator: Optional[Terminator] = None

    def is_terminated(self) -> bool:
        """Check if this block has a terminator."""
        return self.terminator is not None


# =============================================================================
# MIR Function
# =============================================================================

@dataclass
class MirFunction:
    """
    A function in MIR form.

    The function body is represented as a CFG of basic blocks.
    Arguments and return value are represented as locals.
    """
    name: str
    symbol_id: Optional[SymbolId]
    args: List[Local]
    locals: List[Local]  # All locals including args
    ret_type: TypeId
    blocks: Dict[BlockId, Block]
    entry_block: BlockId
    is_async: bool = False
    is_method: bool = False

    def get_block(self, block_id: BlockId) -> Optional[Block]:
        """Get a block by ID."""
        return self.blocks.get(block_id)

    def all_locals(self) -> List[Local]:
        """Get all locals (args + local vars)."""
        return self.locals


# =============================================================================
# MIR Type Definitions (for structs, enums, externs)
# =============================================================================

@dataclass
class MirField:
    """A struct field in MIR."""
    name: str
    type_id: TypeId
    is_mutable: bool


@dataclass
class MirStruct:
    """A struct definition in MIR.

    Deinit hooks (``__release__``) and GC trace hooks (``__gc_trace__``)
    are not stored on this struct — they're plain methods in
    ``MirProgram.functions`` discovered by codegen via name convention
    (``<struct.name>___release__`` / ``<struct.name>___gc_trace__``)
    when emitting the type descriptor table.
    """
    name: str
    symbol_id: Optional[SymbolId]
    fields: List[MirField]
    type_id: TypeId


@dataclass
class MirEnumCase:
    """An enum case in MIR."""
    name: str
    tag: int
    payload_types: List[Tuple[Optional[str], TypeId]]  # (label, type) pairs


@dataclass
class MirEnum:
    """An enum definition in MIR."""
    name: str
    symbol_id: Optional[SymbolId]
    cases: List[MirEnumCase]
    type_id: TypeId


@dataclass
class MirExternFunc:
    """An external function declaration in MIR."""
    name: str
    symbol_id: Optional[SymbolId]
    abi: str
    params: List[Tuple[str, TypeId]]  # (name, type) pairs
    ret_type: TypeId


# =============================================================================
# MIR Program
# =============================================================================

@dataclass
class MirProgram:
    """
    A complete MIR program.

    Contains all monomorphized functions, type definitions, and externs.
    """
    functions: List[MirFunction]
    structs: List[MirStruct]
    enums: List[MirEnum]
    externs: List[MirExternFunc]


# =============================================================================
# Build Result
# =============================================================================

@dataclass
class MirBuildResult:
    """Result of MIR building."""
    program: MirProgram
    type_table: TypeTable
    symbol_table: SymbolTable
    frame_structs: Dict[str, object] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        """Check if there were any errors during building."""
        return len(self.errors) > 0


# =============================================================================
# Validation
# =============================================================================

def validate_function(func: MirFunction) -> List[str]:
    """
    Validate a MIR function for structural correctness.

    Checks:
    - Entry block exists
    - All blocks have terminators
    - All branch targets exist
    """
    errors: List[str] = []

    # Check entry block exists
    if func.entry_block not in func.blocks:
        errors.append(f"Function '{func.name}': entry block {func.entry_block.id} does not exist")

    # Check all blocks have terminators and valid targets
    for block_id, block in func.blocks.items():
        if block.terminator is None:
            errors.append(f"Function '{func.name}': block {block_id.id} has no terminator")
            continue

        # Check branch targets exist
        targets = get_terminator_targets(block.terminator)
        for target in targets:
            if target not in func.blocks:
                errors.append(
                    f"Function '{func.name}': block {block_id.id} branches to "
                    f"non-existent block {target.id}"
                )

    return errors


def get_terminator_targets(term: Terminator) -> List[BlockId]:
    """Get all possible branch targets from a terminator."""
    if isinstance(term, Branch):
        return [term.target]
    elif isinstance(term, CondBranch):
        return [term.true_target, term.false_target]
    elif isinstance(term, SwitchInt):
        targets = [case[1] for case in term.cases]
        targets.append(term.default)
        return targets
    elif isinstance(term, (Return, Unreachable)):
        return []
    else:
        return []


def validate_program(program: MirProgram) -> List[str]:
    """Validate an entire MIR program."""
    errors: List[str] = []
    for func in program.functions:
        errors.extend(validate_function(func))
    return errors


# =============================================================================
# Pretty Printing (for debugging)
# =============================================================================

def format_operand(op: Operand, type_table: TypeTable) -> str:
    """Format an operand for display."""
    if isinstance(op, CopyOperand):
        return format_place(op.place)
    elif isinstance(op, MoveOperand):
        return f"move {format_place(op.place)}"
    else:  # ConstantOperand
        if op.kind == ConstantKind.NIL:
            return "nil"
        elif op.kind == ConstantKind.UNIT:
            return "()"
        elif op.kind == ConstantKind.STRING:
            return f'"{op.value}"'
        else:
            return str(op.value)


def format_place(place: Place) -> str:
    """Format a place for display."""
    result = f"_{place.base.id}"
    for proj in place.projections:
        if isinstance(proj, FieldProjection):
            result += f".{proj.field_name}"
        elif isinstance(proj, IndexProjection):
            result += f"[...]"
        elif isinstance(proj, DerefProjection):
            result = f"(*{result})"
    return result


def format_local(local: Local, type_table: TypeTable) -> str:
    """Format a local variable declaration."""
    binding = "var" if local.is_mutable else "let"
    arg = " (arg)" if local.is_arg else ""
    type_str = type_table.format_type(local.type_id)
    return f"_{local.id.id}: {type_str}{arg}  // {binding} {local.name}"


def format_op(op: Op, type_table: TypeTable) -> str:
    """Format an operation for display."""
    if isinstance(op, BinOp):
        left = format_operand(op.left, type_table)
        right = format_operand(op.right, type_table)
        return f"_{op.result.id} = {left} {op.op.name.lower()} {right}"
    elif isinstance(op, CmpOp):
        left = format_operand(op.left, type_table)
        right = format_operand(op.right, type_table)
        return f"_{op.result.id} = {left} {op.op.name.lower()} {right}"
    elif isinstance(op, UnaryOp):
        operand = format_operand(op.operand, type_table)
        return f"_{op.result.id} = {op.op.name.lower()} {operand}"
    elif isinstance(op, CastOp):
        operand = format_operand(op.operand, type_table)
        target = type_table.format_type(op.target_type)
        return f"_{op.result.id} = {operand} as {target}"
    elif isinstance(op, MakeStruct):
        fields = ", ".join(f"{n}: {format_operand(v, type_table)}" for n, v in op.fields)
        type_str = type_table.format_type(op.struct_type)
        return f"_{op.result.id} = {type_str} {{ {fields} }}"
    elif isinstance(op, MakeEnum):
        payloads = ", ".join(format_operand(p, type_table) for p in op.payload)
        type_str = type_table.format_type(op.enum_type)
        return f"_{op.result.id} = {type_str}.{op.case_name}({payloads})"
    elif isinstance(op, MakeSome):
        val = format_operand(op.value, type_table)
        return f"_{op.result.id} = Some({val})"
    elif isinstance(op, MakeNone):
        return f"_{op.result.id} = None"
    elif isinstance(op, ExtractField):
        agg = format_operand(op.aggregate, type_table)
        return f"_{op.result.id} = {agg}.{op.field_name}"
    elif isinstance(op, ExtractClosureCapture):
        closure = format_operand(op.closure, type_table)
        return f"_{op.result.id} = {closure}.capture[{op.capture_index}]"
    elif isinstance(op, ExtractEnumPayload):
        enum = format_operand(op.enum_val, type_table)
        return f"_{op.result.id} = ({enum} as .{op.case_name}).{op.payload_index}"
    elif isinstance(op, GetTag):
        enum = format_operand(op.enum_val, type_table)
        return f"_{op.result.id} = tag({enum})"
    elif isinstance(op, Assign):
        place = format_place(op.place)
        val = format_operand(op.value, type_table)
        return f"{place} = {val}"
    elif isinstance(op, Store):
        place = format_place(op.place)
        val = format_operand(op.value, type_table)
        return f"store {place} = {val}"
    elif isinstance(op, Load):
        place = format_place(op.place)
        return f"_{op.result.id} = load {place}"
    elif isinstance(op, Retain):
        val = format_operand(op.operand, type_table)
        return f"retain {val}"
    elif isinstance(op, Release):
        val = format_operand(op.operand, type_table)
        return f"release {val}"
    elif isinstance(op, CallStatic):
        args = ", ".join(format_operand(a, type_table) for a in op.args)
        if op.result:
            return f"_{op.result.id} = call {op.func_name}({args})"
        else:
            return f"call {op.func_name}({args})"
    elif isinstance(op, MakeClosure):
        captures = ", ".join(format_operand(c, type_table) for c in op.captures)
        return f"_{op.result.id} = make_closure {op.func_name}[{captures}]"
    elif isinstance(op, CallClosure):
        closure = format_operand(op.closure, type_table)
        args = ", ".join(format_operand(a, type_table) for a in op.args)
        if op.result:
            return f"_{op.result.id} = call_closure {closure}({args})"
        else:
            return f"call_closure {closure}({args})"
    elif isinstance(op, BoxExistential):
        val = format_operand(op.value, type_table)
        proto = type_table.format_type(op.protocol_type)
        return f"_{op.result.id} = box_existential {val} as any {proto}"
    elif isinstance(op, ExistentialCheckType):
        val = format_operand(op.existential, type_table)
        ct = type_table.format_type(op.concrete_type)
        return f"_{op.result.id} = existential_is {val} {ct}"
    elif isinstance(op, ExistentialUnbox):
        val = format_operand(op.existential, type_table)
        ct = type_table.format_type(op.concrete_type)
        return f"_{op.result.id} = existential_unbox {val} as {ct}"
    elif isinstance(op, CallVTable):
        recv = format_operand(op.receiver, type_table)
        args = ", ".join(format_operand(a, type_table) for a in op.args)
        if op.result:
            return f"_{op.result.id} = call_vtable {recv}.{op.method_name}({args})"
        else:
            return f"call_vtable {recv}.{op.method_name}({args})"
    elif isinstance(op, CallWitness):
        args = ", ".join(format_operand(a, type_table) for a in op.args)
        wit = type_table.format_type(op.witness_type)
        if op.result:
            return f"_{op.result.id} = call_witness {wit}::{op.method_name}({args})"
        else:
            return f"call_witness {wit}::{op.method_name}({args})"
    # Async operations
    elif isinstance(op, Suspend):
        if op.result:
            return f"_{op.result.id} = suspend state={op.state_id}"
        else:
            return f"suspend state={op.state_id}"
    elif isinstance(op, TaskSpawn):
        args = ", ".join(format_operand(a, type_table) for a in op.args)
        return f"_{op.result.id} = task_spawn {op.async_func_name}({args})"
    elif isinstance(op, TaskJoin):
        handle = format_operand(op.task_handle, type_table)
        if op.result:
            return f"_{op.result.id} = task_join {handle}"
        else:
            return f"task_join {handle}"
    elif isinstance(op, TaskYield):
        return "task_yield"
    elif isinstance(op, TaskComplete):
        handle = format_operand(op.task_handle, type_table)
        if op.result:
            val = format_operand(op.result, type_table)
            return f"task_complete {handle}, {val}"
        else:
            return f"task_complete {handle}"
    elif isinstance(op, AllocAsyncFrame):
        type_str = type_table.format_type(op.frame_type)
        return f"_{op.result.id} = alloc_async_frame {type_str}"
    elif isinstance(op, SchedulerRun):
        if op.until_handle is not None:
            handle = format_operand(op.until_handle, type_table)
            return f"scheduler_run until={handle}"
        return "scheduler_run"
    elif isinstance(op, TaskGetResult):
        handle = format_operand(op.task_handle, type_table)
        return f"_{op.result.id} = task_get_result {handle}"
    elif isinstance(op, AllocObj):
        type_str = type_table.format_type(op.result_type)
        return f"_{op.result.id} = alloc_obj {type_str}"
    elif isinstance(op, Clone):
        val = format_operand(op.value, type_table)
        return f"_{op.result.id} = clone {val}"
    elif isinstance(op, GCCheck):
        return "gc_check"
    else:
        return f"<unknown op: {type(op).__name__}>"


def format_terminator(term: Terminator, type_table: TypeTable) -> str:
    """Format a terminator for display."""
    if isinstance(term, Branch):
        return f"br bb{term.target.id}"
    elif isinstance(term, CondBranch):
        cond = format_operand(term.condition, type_table)
        return f"if {cond} then bb{term.true_target.id} else bb{term.false_target.id}"
    elif isinstance(term, SwitchInt):
        val = format_operand(term.value, type_table)
        cases = ", ".join(f"{v} => bb{t.id}" for v, t in term.cases)
        return f"switch {val} {{ {cases}, _ => bb{term.default.id} }}"
    elif isinstance(term, Return):
        if term.value:
            val = format_operand(term.value, type_table)
            return f"return {val}"
        else:
            return "return"
    elif isinstance(term, Unreachable):
        return "unreachable"
    else:
        return f"<unknown terminator: {type(term).__name__}>"


def format_block(block: Block, type_table: TypeTable) -> str:
    """Format a basic block for display."""
    lines = [f"bb{block.id.id}:"]
    for op in block.ops:
        lines.append(f"    {format_op(op, type_table)}")
    if block.terminator:
        lines.append(f"    {format_terminator(block.terminator, type_table)}")
    else:
        lines.append("    <no terminator>")
    return "\n".join(lines)


def format_function(func: MirFunction, type_table: TypeTable) -> str:
    """Format a MIR function for display."""
    lines = []

    # Function signature
    ret_type = type_table.format_type(func.ret_type)
    args_str = ", ".join(f"{a.name}: {type_table.format_type(a.type_id)}" for a in func.args)
    lines.append(f"def {func.name}({args_str}) -> {ret_type} {{")

    # Locals
    if func.locals:
        lines.append("  // locals:")
        for local in func.locals:
            lines.append(f"  //   {format_local(local, type_table)}")
        lines.append("")

    # Blocks (entry first, then others in id order)
    block_ids = sorted(func.blocks.keys(), key=lambda b: (b != func.entry_block, b.id))
    for block_id in block_ids:
        block = func.blocks[block_id]
        for line in format_block(block, type_table).split("\n"):
            lines.append(f"  {line}")
        lines.append("")

    lines.append("}")
    return "\n".join(lines)


def format_program(program: MirProgram, type_table: TypeTable) -> str:
    """Format an entire MIR program for display."""
    lines = []

    # Structs
    for struct in program.structs:
        fields = ", ".join(f"{f.name}: {type_table.format_type(f.type_id)}" for f in struct.fields)
        lines.append(f"struct {struct.name} {{ {fields} }}")

    if program.structs:
        lines.append("")

    # Enums
    for enum in program.enums:
        lines.append(f"enum {enum.name} {{")
        for case in enum.cases:
            if case.payload_types:
                payload = ", ".join(
                    f"{l + ': ' if l else ''}{type_table.format_type(t)}"
                    for l, t in case.payload_types
                )
                lines.append(f"  case {case.name}({payload})  // tag = {case.tag}")
            else:
                lines.append(f"  case {case.name}  // tag = {case.tag}")
        lines.append("}")

    if program.enums:
        lines.append("")

    # Externs
    for ext in program.externs:
        params = ", ".join(f"{n}: {type_table.format_type(t)}" for n, t in ext.params)
        ret = type_table.format_type(ext.ret_type)
        lines.append(f"extern \"{ext.abi}\" def {ext.name}({params}) -> {ret}")

    if program.externs:
        lines.append("")

    # Functions
    for func in program.functions:
        lines.append(format_function(func, type_table))
        lines.append("")

    return "\n".join(lines)
