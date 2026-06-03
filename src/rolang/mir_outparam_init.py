"""MIR pass: elide the phantom default-init of pure out-param locals.

Background
----------
A heap-typed local declared without an initializer (``var out: T;``) is
default-initialized by ``mir_builder._emit_default_init`` to a *fresh zero heap
object* (an ``AllocObj``), so that reading an uninitialized local is always
well-defined (see the rationale there). That matters for two genuine patterns:

    var n: Node; n.v = 7;        // declare-then-mutate: fields need storage
    var s: String; s.len();      // read-the-default: observes an empty string

But the *dominant* use of the form is the out-param idiom in every container
accessor in the stdlib:

    pub def get(index: i32) -> T {
        var out: T;                                              // phantom AllocObj
        unsafe { rt_gvec_get(self.handle, index, out as RawPtr); }   // overwrites out
        return out;
    }

Here ``out``'s *address* is handed to the runtime, which ``memcpy``-overwrites
the slot without releasing the phantom — so the phantom object leaks on **every**
call (and bloats the GC list; this was ~82% of ``word_freq``'s runtime). The
default value is never observed: the call fully initializes the slot before any
read.

This pass replaces the phantom ``AllocObj`` with a ``NIL`` (null-pointer)
initialization for exactly the locals used purely as out-params: a heap local
whose default-init is immediately followed — in the same block, before any read
or field access of the local — by taking its address (``L as RawPtr``) and
passing that address to a call. NULL is safe because:

  * the call initializes the slot before it is read (the default is dead), and
  * if the runtime ever leaves the slot untouched, a NULL slot releases as a
    no-op — never a leak, never a phantom ``__release__``.

Locals that are read or field-mutated as genuine defaults do not match (their
first post-init reference is not the address-into-call), so they keep the phantom
object and their existing semantics. The transform is intentionally conservative:
anything it cannot prove is a pure straight-line out-param is left untouched.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Optional

from .mir import (
    AllocObj,
    Assign,
    CastOp,
    ConstantKind,
    ConstantOperand,
    CopyOperand,
    LocalId,
    MirProgram,
    MoveOperand,
    Op,
    Place,
)
from .types import PrimitiveTypeData, TypeId, TypeKind, TypeTable
from .types import PrimitiveType


# Call ops carry their arguments in an ``args: List[Operand]`` field. We detect
# them structurally (any op with an ``args`` list) so this stays correct as new
# call variants are added.
def _is_call_op(op: Op) -> bool:
    return type(op).__name__.startswith("Call") and hasattr(op, "args")


def _is_raw_ptr(type_table: TypeTable, type_id: TypeId) -> bool:
    info = type_table.get_type(type_id)
    if info is None or info.kind != TypeKind.PRIMITIVE:
        return False
    data = info.data
    return isinstance(data, PrimitiveTypeData) and data.primitive == PrimitiveType.RAW_PTR


def _iter_operands(op: Op) -> Iterable[object]:
    """Yield every Operand referenced by ``op`` (recursing into lists/tuples)."""
    for fld in dataclasses.fields(op):
        value = getattr(op, fld.name)
        if isinstance(value, (CopyOperand, MoveOperand, ConstantOperand)):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (CopyOperand, MoveOperand, ConstantOperand)):
                    yield item
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, (CopyOperand, MoveOperand, ConstantOperand)):
                            yield sub


def _iter_place_fields(op: Op) -> Iterable[Place]:
    """Yield Places that appear *directly* as op fields (write targets:
    Assign/Store ``place``, Load ``place``)."""
    for fld in dataclasses.fields(op):
        value = getattr(op, fld.name)
        if isinstance(value, Place):
            yield value


def _references_local(op: Op, local: LocalId) -> bool:
    """True if ``op`` reads, writes, or projects ``local`` in any way."""
    for operand in _iter_operands(op):
        if isinstance(operand, (CopyOperand, MoveOperand)) and operand.place.base == local:
            return True
    for place in _iter_place_fields(op):
        if place.base == local:
            return True
    return False


def _is_address_take(op: Op, local: LocalId, type_table: TypeTable) -> Optional[LocalId]:
    """If ``op`` is exactly ``R = <local> as RawPtr`` (address-of the whole
    local, not a projection), return R; else None."""
    if not isinstance(op, CastOp):
        return None
    if not _is_raw_ptr(type_table, op.target_type):
        return None
    operand = op.operand
    if not isinstance(operand, (CopyOperand, MoveOperand)):
        return None
    if operand.place.base != local or operand.place.projections:
        return None
    return op.result


def _call_uses(op: Op, ptr_local: LocalId) -> bool:
    """True if ``op`` is a call passing ``ptr_local`` as one of its args."""
    if not _is_call_op(op):
        return False
    for arg in op.args:  # type: ignore[attr-defined]
        if isinstance(arg, (CopyOperand, MoveOperand)) and arg.place.base == ptr_local:
            return True
    return False


def _is_pure_outparam(ops: list, start: int, local: LocalId, type_table: TypeTable) -> bool:
    """Straight-line check (within one block): the first reference to ``local``
    at/after ``start`` is taking its address (``local as RawPtr``), and that
    address then flows into a call — with ``local`` never read in between."""
    cast_result: Optional[LocalId] = None
    for op in ops[start:]:
        if cast_result is None:
            if not _references_local(op, local):
                continue
            # First reference to the local MUST be the pure address-take.
            cast_result = _is_address_take(op, local, type_table)
            if cast_result is None:
                return False
        else:
            # The local must not be observed before the out-param call writes it.
            if _references_local(op, local):
                return False
            if _call_uses(op, cast_result):
                return True
            # The address is consumed by something other than a call first —
            # too unusual to reason about; keep the phantom.
            if _references_local(op, cast_result):
                return False
    return False


def _elide_in_function(func, type_table: TypeTable) -> int:
    elided = 0
    for block in func.blocks.values():
        ops = block.ops
        i = 0
        while i + 1 < len(ops):
            op = ops[i]
            nxt = ops[i + 1]
            # phantom default-init pattern:  tmp = alloc_obj T ; L = copy tmp
            if (
                isinstance(op, AllocObj)
                and isinstance(nxt, Assign)
                and not nxt.place.projections
                and isinstance(nxt.value, (CopyOperand, MoveOperand))
                and not nxt.value.place.projections
                and nxt.value.place.base == op.result
                and type_table.is_heap_type(nxt.place.type_id)
            ):
                local = nxt.place.base
                tmp = op.result
                # ``tmp`` must be a throwaway used only by this Assign.
                if _tmp_used_only_by_init(func, tmp, op, nxt) and _is_pure_outparam(
                    ops, i + 2, local, type_table
                ):
                    ops[i] = Assign(
                        place=nxt.place,
                        value=ConstantOperand(ConstantKind.NIL, None, nxt.place.type_id),
                    )
                    del ops[i + 1]
                    elided += 1
            i += 1
    return elided


def _tmp_used_only_by_init(func, tmp: LocalId, alloc_op: Op, init_assign: Op) -> bool:
    """True if the AllocObj result ``tmp`` is referenced nowhere except the
    immediately-following default-init Assign (so dropping the AllocObj is safe)."""
    for block in func.blocks.values():
        for op in block.ops:
            if op is alloc_op or op is init_assign:
                continue
            if _references_local(op, tmp):
                return False
    return True


def elide_outparam_default_init(program: MirProgram, type_table: TypeTable) -> int:
    """Rewrite phantom out-param default-inits to NULL across the whole program.
    Returns the number of default-inits elided (for diagnostics/telemetry)."""
    total = 0
    for func in program.functions:
        total += _elide_in_function(func, type_table)
    return total
