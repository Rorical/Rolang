"""MIR-level optimizations, run after async lowering and before ARC insertion.

Two passes, in order:

1. **Inlining** of small straight-line functions (single basic block ending in
   ``Return``). This is not primarily a call-overhead optimization — LLVM
   already inlines at -O2+. It exists so the *scalar replacement* pass below
   can see through calls like ``Vec3::cross``: the heap allocation for a
   returned struct lives in the callee, and only after MIR-level inlining does
   the allocation, all its uses, and its death become visible in one function.

2. **Scalar replacement of aggregates (SROA)** for non-escaping structs whose
   fields are all scalars (int/float/bool). Every qualifying struct local is
   replaced by one scalar local per field: the heap allocation, its ARC
   traffic (the pass runs before ARC insertion, so retain/release for the
   local are simply never created), and the GC registration all disappear,
   and LLVM's mem2reg promotes the scalars to registers.

   Safety model — Rolang structs are reference types, so scalarization is
   only legal when reference identity and aliasing are unobservable:

   * locals are connected into alias groups by plain ``a = b`` assignments
     (union-find);
   * a group is disqualified if any member escapes — passed to a call,
     returned, stored into another object/collection, compared, captured,
     cast (address-of), or touched by any op this pass does not explicitly
     model (detected generically, so new op kinds fail safe);
   * field *writes* are only allowed when the group has a single member —
     with aliases, a write through one name must be visible through the
     other, which per-alias scalar copies would break. Reads are always
     fine: a fieldwise copy at the assignment point is indistinguishable
     from sharing when nobody writes afterwards.
"""

from __future__ import annotations

import copy
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Dict, List, Optional, Set

from .mir import (
    Assign, Block, CallStatic, CastOp, ConstantKind, ConstantOperand,
    CopyOperand, ExtractField, FieldProjection, IndexProjection, Load, Local,
    LocalId, MakeStruct, MirFunction, MirProgram, MirStruct, MoveOperand,
    Operand, Place, Return, Store, Suspend,
)
from .types import TypeId, TypeKind, TypeTable

INLINE_MAX_OPS = 48
INLINE_ROUNDS = 3


def optimize_mir(program: MirProgram, type_table: TypeTable) -> None:
    """Run MIR-level optimizations in place."""
    struct_fields = _all_scalar_struct_map(program, type_table)
    _inline_small_functions(program, type_table, struct_fields)

    if struct_fields:
        for fn in program.functions:
            if not fn.is_async:
                _scalarize_structs(fn, struct_fields, type_table)


# =============================================================================
# Generic LocalId remapping (used by the inliner)
# =============================================================================

def _remap_locals(obj, mapping: Dict[LocalId, LocalId]):
    """Recursively replace LocalId references inside a (deep-copied) MIR
    node. Mutates mutable dataclasses in place; returns the (possibly
    replaced) object so callers can rebind immutable leaves."""
    if isinstance(obj, LocalId):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            obj[i] = _remap_locals(item, mapping)
        return obj
    if isinstance(obj, tuple):
        return tuple(_remap_locals(item, mapping) for item in obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        if obj.__dataclass_params__.frozen:
            # Frozen leaves (BlockId, TypeId, SymbolId...). LocalId was
            # handled above; nothing else frozen can contain a LocalId.
            return obj
        for f in dataclass_fields(obj):
            setattr(obj, f.name, _remap_locals(getattr(obj, f.name), mapping))
        return obj
    return obj


# =============================================================================
# Pass 1: inline small straight-line functions
# =============================================================================

def _inline_candidates(
    program: MirProgram, type_table: TypeTable,
    struct_fields: Dict[TypeId, MirStruct],
) -> Dict[str, MirFunction]:
    def scalarish(type_id: TypeId) -> bool:
        return (
            type_table.is_integer(type_id)
            or type_table.is_float(type_id)
            or type_table.is_bool(type_id)
            or type_id in struct_fields
            or type_table.format_type(type_id) == "Void"
        )

    out: Dict[str, MirFunction] = {}
    for fn in program.functions:
        if fn.is_async:
            continue
        if len(fn.blocks) != 1:
            continue
        block = fn.blocks.get(fn.entry_block)
        if block is None or not isinstance(block.terminator, Return):
            continue
        if len(block.ops) > INLINE_MAX_OPS:
            continue
        # Inlining exists to feed the SROA pass: only inline functions whose
        # every type (args, return, locals) is a plain scalar or an
        # all-scalar struct. Functions touching ARC-managed types (String,
        # Vec, Dict, ref structs) gain nothing from MIR inlining — LLVM
        # already inlines the calls — and interact with ARC liveness in
        # subtle ways (e.g. the address-of-local idiom `local as RawPtr` is
        # only safe on borrowed ARGUMENTS, which the caller keeps alive
        # across the call; inlined, ARC would free the local at the
        # address-taking cast, before the call that reads through it).
        if not scalarish(fn.ret_type):
            continue
        if not all(scalarish(loc.type_id) for loc in fn.locals):
            continue
        skip = False
        for op in block.ops:
            # Suspend is state-machine plumbing.
            if isinstance(op, Suspend):
                skip = True
                break
            # A self-call would inline forever.
            if isinstance(op, CallStatic) and op.func_name == fn.name:
                skip = True
                break
            # Belt-and-braces: the RawPtr address-of idiom (see above).
            # The scalar-only local filter already excludes RawPtr locals.
            if (isinstance(op, CastOp)
                    and isinstance(op.operand, (CopyOperand, MoveOperand))
                    and not op.operand.place.projections
                    and type_table.format_type(op.target_type) == "RawPtr"):
                skip = True
                break
        if not skip:
            out[fn.name] = fn
    return out


def _inline_into(fn: MirFunction, candidates: Dict[str, MirFunction]) -> bool:
    changed = False
    next_id = max((loc.id.id for loc in fn.locals), default=-1) + 1

    for block in fn.blocks.values():
        if not any(
            isinstance(op, CallStatic) and op.func_name in candidates
            for op in block.ops
        ):
            continue
        new_ops: List = []
        for op in block.ops:
            callee = (
                candidates.get(op.func_name)
                if isinstance(op, CallStatic) else None
            )
            if (callee is None or callee is fn
                    or len(op.args) != len(callee.args)):
                new_ops.append(op)
                continue

            # Fresh caller locals for every callee local (args included).
            mapping: Dict[LocalId, LocalId] = {}
            for loc in callee.locals:
                new_local = Local(
                    id=LocalId(next_id),
                    symbol_id=None,
                    name=f"__inl_{callee.name}_{loc.name}",
                    type_id=loc.type_id,
                    is_mutable=True,
                    is_arg=False,
                )
                next_id += 1
                fn.locals.append(new_local)
                mapping[loc.id] = new_local.id

            # Bind arguments.
            for arg_local, arg_operand in zip(callee.args, op.args):
                new_ops.append(Assign(
                    place=Place(
                        base=mapping[arg_local.id],
                        projections=[],
                        type_id=arg_local.type_id,
                    ),
                    value=copy.deepcopy(arg_operand),
                ))

            # Splice the (remapped) body.
            callee_block = callee.blocks[callee.entry_block]
            for body_op in callee_block.ops:
                new_ops.append(
                    _remap_locals(copy.deepcopy(body_op), mapping)
                )

            # Bind the return value.
            term = callee_block.terminator
            if op.result is not None and term.value is not None:
                ret_val = _remap_locals(copy.deepcopy(term.value), mapping)
                new_ops.append(Assign(
                    place=Place(
                        base=op.result,
                        projections=[],
                        type_id=op.result_type,
                    ),
                    value=ret_val,
                ))
            changed = True
        block.ops = new_ops
    return changed


def _inline_small_functions(
    program: MirProgram, type_table: TypeTable,
    struct_fields: Dict[TypeId, MirStruct],
) -> None:
    for _ in range(INLINE_ROUNDS):
        candidates = _inline_candidates(program, type_table, struct_fields)
        if not candidates:
            return
        changed = False
        for fn in program.functions:
            if fn.is_async:
                continue
            if _inline_into(fn, candidates):
                changed = True
        if not changed:
            return


# =============================================================================
# Pass 2: scalar replacement of non-escaping all-scalar structs
# =============================================================================

def _all_scalar_struct_map(
    program: MirProgram, type_table: TypeTable
) -> Dict[TypeId, MirStruct]:
    """Map struct TypeId -> MirStruct for structs whose every field is a
    plain scalar (int/float/bool). RawPtr is deliberately excluded: the
    address-of cast idiom reads a local's storage slot directly."""
    out: Dict[TypeId, MirStruct] = {}
    for st in program.structs:
        info = type_table.get_type(st.type_id)
        if info is None or info.kind != TypeKind.STRUCT:
            continue
        if not st.fields:
            continue
        if all(
            type_table.is_integer(f.type_id)
            or type_table.is_float(f.type_id)
            or type_table.is_bool(f.type_id)
            for f in st.fields
        ):
            out[st.type_id] = st
    return out


class _Sroa:
    """Per-function scalar-replacement analysis and rewrite."""

    BOT = LocalId(-1)  # disqualification sink for the union-find

    def __init__(self, fn: MirFunction,
                 struct_fields: Dict[TypeId, MirStruct],
                 type_table: TypeTable) -> None:
        self.fn = fn
        self.struct_fields = struct_fields
        self.type_table = type_table
        # Candidate struct locals (args are caller-owned heap pointers).
        self.cands: Dict[LocalId, Local] = {
            loc.id: loc
            for loc in fn.locals
            if not loc.is_arg and loc.type_id in struct_fields
        }
        self.parent: Dict[LocalId, LocalId] = {
            lid: lid for lid in self.cands
        }
        self.parent[self.BOT] = self.BOT
        self.field_written: Set[LocalId] = set()

    # ---- union-find ----

    def _find(self, x: LocalId) -> LocalId:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def _union(self, a: LocalId, b: LocalId) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            # Keep BOT as its own root so "disqualified" is sticky.
            if ra == self.BOT:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb

    def _disqualify(self, lid: LocalId) -> None:
        self._union(lid, self.BOT)

    def _scalar_zero(self, type_id: TypeId) -> ConstantOperand:
        """Zero constant matching MakeStruct's zero-init of omitted fields.
        The pass only admits int/float/bool fields."""
        if self.type_table.is_float(type_id):
            return ConstantOperand(
                kind=ConstantKind.FLOAT, value=0.0, type_id=type_id,
            )
        if self.type_table.is_bool(type_id):
            return ConstantOperand(
                kind=ConstantKind.BOOL, value=False, type_id=type_id,
            )
        return ConstantOperand(kind=ConstantKind.INT, value=0, type_id=type_id)

    # ---- analysis ----

    @staticmethod
    def _bare_struct_operand(v) -> Optional[LocalId]:
        """LocalId if v is Copy/Move of a bare (projection-free) place."""
        if isinstance(v, (CopyOperand, MoveOperand)) and not v.place.projections:
            return v.place.base
        return None

    def _walk_disqualifying(self, obj) -> None:
        """Generic scan: any candidate reference found here disqualifies it,
        EXCEPT the rewritable read form Copy/Move(local.field). This is the
        fail-safe for every op kind the pass does not explicitly model."""
        if isinstance(obj, (CopyOperand, MoveOperand)):
            place = obj.place
            base_is_cand = place.base in self.cands
            if base_is_cand:
                if not (len(place.projections) == 1
                        and isinstance(place.projections[0], FieldProjection)):
                    self._disqualify(place.base)
            for proj in place.projections:
                if isinstance(proj, IndexProjection):
                    self._walk_disqualifying(proj.index)
            return
        if isinstance(obj, Place):
            if obj.base in self.cands:
                self._disqualify(obj.base)
            for proj in obj.projections:
                if isinstance(proj, IndexProjection):
                    self._walk_disqualifying(proj.index)
            return
        if isinstance(obj, LocalId):
            if obj in self.cands:
                self._disqualify(obj)
            return
        if isinstance(obj, list):
            for item in obj:
                self._walk_disqualifying(item)
            return
        if isinstance(obj, tuple):
            for item in obj:
                self._walk_disqualifying(item)
            return
        if is_dataclass(obj) and not isinstance(obj, type):
            if obj.__dataclass_params__.frozen:
                return
            for f in dataclass_fields(obj):
                self._walk_disqualifying(getattr(obj, f.name))

    def _analyze_assign_like(self, place: Place, value) -> None:
        if place.base in self.cands:
            if not place.projections:
                src = self._bare_struct_operand(value)
                if src is not None and src in self.cands:
                    self._union(place.base, src)  # alias edge
                else:
                    # Receives something we cannot scalarize (call result,
                    # field load of struct type, arg alias, ...).
                    self._disqualify(place.base)
                    self._walk_disqualifying(value)
                return
            if (len(place.projections) == 1
                    and isinstance(place.projections[0], FieldProjection)):
                self.field_written.add(place.base)
                self._walk_disqualifying(value)
                return
            self._disqualify(place.base)
            self._walk_disqualifying(value)
            for proj in place.projections:
                if isinstance(proj, IndexProjection):
                    self._walk_disqualifying(proj.index)
            return
        # Target is not a candidate: storing a bare candidate INTO it would
        # publish the reference — the generic walk disqualifies that.
        self._walk_disqualifying(value)
        for proj in place.projections:
            if isinstance(proj, IndexProjection):
                self._walk_disqualifying(proj.index)

    def _analyze_op(self, op) -> None:
        if isinstance(op, ExtractField):
            agg = self._bare_struct_operand(op.aggregate)
            if agg is not None and agg in self.cands:
                return  # plain field read of a candidate: always fine
            self._walk_disqualifying(op.aggregate)
            return
        if isinstance(op, MakeStruct):
            if op.result not in self.cands:
                self._walk_disqualifying(op)
                return
            # Definition of a candidate; operands are scalars for an
            # all-scalar struct, but walk them anyway (fail safe).
            for _, v in op.fields:
                self._walk_disqualifying(v)
            return
        if isinstance(op, (Assign, Store)):
            self._analyze_assign_like(op.place, op.value)
            return
        if isinstance(op, Load):
            if op.place.base in self.cands:
                projs = op.place.projections
                if (len(projs) == 1
                        and isinstance(projs[0], FieldProjection)):
                    return  # field read
                if not projs and op.result in self.cands:
                    self._union(op.result, op.place.base)  # alias edge
                    return
                self._disqualify(op.place.base)
                return
            self._walk_disqualifying(op)
            return
        self._walk_disqualifying(op)

    def analyze(self) -> Set[LocalId]:
        """Returns the set of locals safe to scalarize."""
        if not self.cands:
            return set()
        for block in self.fn.blocks.values():
            for op in block.ops:
                self._analyze_op(op)
            if block.terminator is not None:
                self._walk_disqualifying(block.terminator)

        # Group field-writes: only singleton groups may be written through.
        groups: Dict[LocalId, List[LocalId]] = {}
        for lid in self.cands:
            groups.setdefault(self._find(lid), []).append(lid)
        qualified: Set[LocalId] = set()
        bot_root = self._find(self.BOT)
        for root, members in groups.items():
            if root == bot_root:
                continue
            if len(members) > 1 and any(
                m in self.field_written for m in members
            ):
                continue
            qualified.update(members)
        return qualified

    # ---- rewrite ----

    def rewrite(self, qualified: Set[LocalId]) -> None:
        fn = self.fn
        next_id = max((loc.id.id for loc in fn.locals), default=-1) + 1
        # local -> {field_name: (LocalId, TypeId)}
        scalar_map: Dict[LocalId, Dict[str, tuple]] = {}
        for lid in qualified:
            loc = self.cands[lid]
            st = self.struct_fields[loc.type_id]
            per_field: Dict[str, tuple] = {}
            for f in st.fields:
                new_local = Local(
                    id=LocalId(next_id),
                    symbol_id=None,
                    name=f"{loc.name}__{f.name}",
                    type_id=f.type_id,
                    is_mutable=True,
                    is_arg=False,
                )
                next_id += 1
                fn.locals.append(new_local)
                per_field[f.name] = (new_local.id, f.type_id)
            scalar_map[lid] = per_field

        def scalar_read(lid: LocalId, fname: str) -> CopyOperand:
            sid, stype = scalar_map[lid][fname]
            return CopyOperand(Place(base=sid, projections=[], type_id=stype))

        def scalar_assign(lid: LocalId, fname: str, value) -> Assign:
            sid, stype = scalar_map[lid][fname]
            return Assign(
                place=Place(base=sid, projections=[], type_id=stype),
                value=value,
            )

        def fix_operand(v):
            """Rewrite Copy/Move(local.field) reads of scalarized locals."""
            if isinstance(v, (CopyOperand, MoveOperand)):
                place = v.place
                if (place.base in scalar_map
                        and len(place.projections) == 1
                        and isinstance(place.projections[0], FieldProjection)):
                    fname = place.projections[0].field_name
                    sid, stype = scalar_map[place.base][fname]
                    place.base = sid
                    place.projections = []
                    place.type_id = stype
                for proj in place.projections:
                    if isinstance(proj, IndexProjection):
                        fix_operand(proj.index)
            return v

        def fix_all_operands(obj):
            if isinstance(obj, (CopyOperand, MoveOperand)):
                fix_operand(obj)
                return
            if isinstance(obj, Place):
                for proj in obj.projections:
                    if isinstance(proj, IndexProjection):
                        fix_all_operands(proj.index)
                return
            if isinstance(obj, (list, tuple)):
                for item in obj:
                    fix_all_operands(item)
                return
            if is_dataclass(obj) and not isinstance(obj, type):
                if obj.__dataclass_params__.frozen:
                    return
                for f in dataclass_fields(obj):
                    fix_all_operands(getattr(obj, f.name))

        for block in fn.blocks.values():
            new_ops: List = []
            for op in block.ops:
                if (isinstance(op, MakeStruct)
                        and op.result in scalar_map):
                    st = self.struct_fields[
                        self.cands[op.result].type_id
                    ]
                    provided = set()
                    for fname, v in op.fields:
                        fix_all_operands(v)
                        new_ops.append(scalar_assign(op.result, fname, v))
                        provided.add(fname)
                    for f in st.fields:
                        if f.name not in provided:
                            _, stype = scalar_map[op.result][f.name]
                            new_ops.append(scalar_assign(
                                op.result, f.name,
                                self._scalar_zero(stype),
                            ))
                    continue
                if isinstance(op, ExtractField):
                    agg = self._bare_struct_operand(op.aggregate)
                    if agg is not None and agg in scalar_map:
                        new_ops.append(Assign(
                            place=Place(
                                base=op.result,
                                projections=[],
                                type_id=op.result_type,
                            ),
                            value=scalar_read(agg, op.field_name),
                        ))
                        continue
                if isinstance(op, (Assign, Store)):
                    place = op.place
                    if place.base in scalar_map:
                        if not place.projections:
                            src = self._bare_struct_operand(op.value)
                            # Analysis guarantees src is in the same group.
                            st = self.struct_fields[
                                self.cands[place.base].type_id
                            ]
                            for f in st.fields:
                                new_ops.append(scalar_assign(
                                    place.base, f.name,
                                    scalar_read(src, f.name),
                                ))
                            continue
                        if (len(place.projections) == 1
                                and isinstance(place.projections[0],
                                               FieldProjection)):
                            fname = place.projections[0].field_name
                            fix_all_operands(op.value)
                            new_ops.append(scalar_assign(
                                place.base, fname, op.value,
                            ))
                            continue
                if isinstance(op, Load) and op.place.base in scalar_map:
                    projs = op.place.projections
                    if (len(projs) == 1
                            and isinstance(projs[0], FieldProjection)):
                        new_ops.append(Assign(
                            place=Place(
                                base=op.result,
                                projections=[],
                                type_id=projs[0].result_type,
                            ),
                            value=scalar_read(
                                op.place.base, projs[0].field_name,
                            ),
                        ))
                        continue
                    if not projs and op.result in scalar_map:
                        st = self.struct_fields[
                            self.cands[op.result].type_id
                        ]
                        for f in st.fields:
                            new_ops.append(scalar_assign(
                                op.result, f.name,
                                scalar_read(op.place.base, f.name),
                            ))
                        continue
                fix_all_operands(op)
                new_ops.append(op)
            block.ops = new_ops
            if block.terminator is not None:
                fix_all_operands(block.terminator)


def _scalarize_structs(
    fn: MirFunction, struct_fields: Dict[TypeId, MirStruct],
    type_table: TypeTable,
) -> None:
    sroa = _Sroa(fn, struct_fields, type_table)
    qualified = sroa.analyze()
    if qualified:
        sroa.rewrite(qualified)
