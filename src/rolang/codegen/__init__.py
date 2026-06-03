"""
LLVM Code Generation for the Rolang compiler.

This module transforms MIR (with ARC operations) into LLVM IR and compiles
to object files.

Pipeline:
    ArcInsertionResult (MIR + ARC ops)
            ↓
       TypeLayoutCache (compute LLVM types/layouts)
            ↓
       LLVMCodegen (emit LLVM IR)
            ↓
       ObjectEmitter (compile to .o)
            ↓
       Link with runtime → executable
"""

from __future__ import annotations

from typing import Optional, Dict, Any

from llvmlite import ir

from ..arc_insertion import ArcInsertionResult
from ..conformance import ConformanceChecker, WitnessEntry
from ..mir import BoxExistential, ExistentialCheckType, ExistentialUnbox, MirProgram
from ..types import (
    FuncRequirement,
    ProtocolTypeData,
    StructTypeData,
    TypeId,
    TypeKind,
    TypeTable,
)
from ..symbols import SymbolTable
from .types import TypeLayoutCache
from .runtime import RuntimeABI
from .function import FunctionCodegen
from .object_file import compile_module_to_object
from .async_codegen import AsyncCodegen


class CodegenResult:
    """Result of LLVM code generation."""

    def __init__(
        self,
        module: ir.Module,
        errors: list[str],
    ) -> None:
        self.module = module
        self.errors = errors

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def __str__(self) -> str:
        return str(self.module)


def compile_to_llvm(
    arc_result: ArcInsertionResult,
    module_name: str = "rolang_module",
    target_triple: Optional[str] = None,
    frame_structs: Optional[Dict[str, Any]] = None,
) -> CodegenResult:
    """
    Compile MIR (with ARC) to LLVM IR.

    Args:
        arc_result: Result from ARC insertion pass
        module_name: Name for the LLVM module
        target_triple: Target triple (e.g., "x86_64-unknown-linux-gnu")
                       If None, uses host target.
        frame_structs: Async frame descriptors from async lowering (may be empty)

    Returns:
        CodegenResult containing the LLVM module
    """
    errors: list[str] = []

    # Create LLVM module with its own context so identified-struct types do
    # not collide across compilations sharing this process (llvmlite uses a
    # global default context otherwise).
    module = ir.Module(name=module_name, context=ir.Context())
    if target_triple:
        module.triple = target_triple

    # Create type layout cache
    type_cache = TypeLayoutCache(module, arc_result.type_table, arc_result.symbol_table)

    # Declare structs and enums
    for struct in arc_result.program.structs:
        type_cache.get_struct_type(struct)

    for enum in arc_result.program.enums:
        type_cache.get_enum_type(enum)

    # Declare runtime ABI
    runtime = RuntimeABI(module)

    # Create async codegen (always instantiated; handles frame types and async runtime)
    async_codegen = AsyncCodegen(module, type_cache, arc_result.type_table)

    # Declare extern functions
    for extern in arc_result.program.externs:
        _declare_extern_func(module, type_cache, extern)

    # Resolve LLVM-symbol-name collisions before forward declaration.
    # Under unified multi-module compilation, two distinct modules can
    # legitimately declare different functions with the same source-level
    # name (e.g. an `internal` helper in lib.rl and a `def helper` in
    # main.rl). They have unique SymbolIds but share a `name`, which
    # makes llvmlite refuse to register the second LLVM symbol. Disambig-
    # uate by appending the symbol id, and rewrite every CallStatic op
    # that targets one of the renamed functions to use the new name.
    _uniquify_function_names(arc_result.program)

    # Rename user `main` to `__rolang_user_main` so that the runtime can
    # supply the real C `int main(int argc, char** argv)` wrapper. The
    # wrapper installs argv into a global readable via `rt_args_get`/
    # `rt_args_count` externs, then dispatches to the user entry point.
    # This is what enables `import "process.rl"` -> `argv()`.
    _rename_user_main(arc_result.program, arc_result.type_table)

    # Forward-declare all functions first
    func_map: dict[str, ir.Function] = {}
    for mir_func in arc_result.program.functions:
        llvm_func = _declare_function(module, type_cache, mir_func)
        func_map[mir_func.name] = llvm_func

    witness_tables = _emit_witness_tables(
        module,
        type_cache,
        arc_result.type_table,
        arc_result.symbol_table,
        arc_result.program,
        func_map,
        errors,
    )

    # Generate function bodies
    for mir_func in arc_result.program.functions:
        llvm_func = func_map[mir_func.name]
        func_codegen = FunctionCodegen(
            mir_func=mir_func,
            llvm_func=llvm_func,
            type_cache=type_cache,
            runtime=runtime,
            type_table=arc_result.type_table,
            func_map=func_map,
            witness_tables=witness_tables,
            async_codegen=async_codegen,
            mir_structs=arc_result.program.structs,
        )
        func_errors = func_codegen.generate()
        errors.extend(func_errors)

    # Emit type descriptor table for GC tracing (+ deinit hooks)
    _emit_type_descriptor_table(
        module,
        type_cache,
        arc_result.type_table,
        arc_result.program,
        func_map,
        errors,
    )

    return CodegenResult(module=module, errors=errors)


WitnessTableMap = dict[tuple[TypeId, TypeId], ir.GlobalVariable]


def _emit_witness_tables(
    module: ir.Module,
    type_cache: TypeLayoutCache,
    type_table: TypeTable,
    symbol_table: SymbolTable,
    program: MirProgram,
    func_map: dict[str, ir.Function],
    errors: list[str],
) -> WitnessTableMap:
    """Emit witness table globals for existential boxes used by the program."""
    conformance_checker = ConformanceChecker(type_table, symbol_table)
    _register_declared_extensions(conformance_checker, type_table, symbol_table)
    pairs = _collect_existential_boxes(program)
    tables: WitnessTableMap = {}

    for concrete_type, protocol_type in sorted(pairs, key=lambda p: (p[0].id, p[1].id)):
        conformance = conformance_checker.check_conformance(concrete_type, protocol_type)
        if not conformance.conforms:
            concrete_name = type_table.format_type(concrete_type)
            protocol_name = type_table.format_type(protocol_type)
            errors.append(f"{concrete_name} does not conform to {protocol_name}")
            continue

        protocol_info = type_table.get_type(protocol_type)
        if protocol_info is None or not isinstance(protocol_info.data, ProtocolTypeData):
            continue

        entries: list[ir.Constant] = []
        for requirement in protocol_info.data.func_requirements:
            witness = next(
                (
                    entry
                    for entry in conformance.witnesses
                    if entry.is_method and entry.requirement_name == requirement.name
                ),
                None,
            )
            if witness is None:
                # Keep a null slot so every slot index matches its position
                # in func_requirements — _existential_method_index uses the
                # positional index as a GEP offset into this table.
                entries.append(ir.Constant(type_cache.ptr, None))
                continue

            thunk = _emit_witness_thunk(
                module,
                type_cache,
                type_table,
                symbol_table,
                concrete_type,
                protocol_type,
                requirement,
                witness,
                func_map,
                errors,
            )
            if thunk is not None:
                entries.append(thunk.bitcast(type_cache.ptr))
            else:
                # Thunk generation failed; keep the slot with a null pointer
                # so indices remain consistent.
                entries.append(ir.Constant(type_cache.ptr, None))

        array_type = ir.ArrayType(type_cache.ptr, len(entries))
        global_name = _witness_table_name(type_table, symbol_table, concrete_type, protocol_type)
        table = ir.GlobalVariable(module, array_type, name=global_name)
        table.global_constant = True
        table.initializer = ir.Constant(array_type, entries)
        tables[(concrete_type, protocol_type)] = table

    return tables


def _register_declared_extensions(
    conformance_checker: ConformanceChecker,
    type_table: TypeTable,
    symbol_table: SymbolTable,
) -> None:
    """Re-register extension-based protocol conformances with a fresh checker.

    The checker pass owns the canonical registry but constructs it on the
    TypeChecker instance, so codegen has to repopulate from the symbol table.
    Walks every EXTENSION-kind symbol and replays its `extension X: P, Q`
    conformances.
    """
    from .. import ast as _ast
    from ..symbols import SymbolKind
    for symbol in symbol_table.symbols.values():
        if symbol.kind != SymbolKind.EXTENSION:
            continue
        decl = symbol.decl_node
        if not isinstance(decl, _ast.ExtensionDecl):
            continue
        if not decl.conformances or decl.extended_type is None:
            continue
        extended_type = _resolve_named_type(type_table, symbol_table, decl.extended_type)
        if extended_type is None:
            continue
        for conformance_node in decl.conformances:
            protocol_type = _resolve_named_type(type_table, symbol_table, conformance_node)
            if protocol_type is None:
                continue
            conformance_checker.register_extension(
                extended_type, protocol_type, symbol.id,
            )


def _resolve_named_type(
    type_table: TypeTable,
    symbol_table: SymbolTable,
    named: "ast.NamedType",
) -> Optional[TypeId]:
    """Resolve a NamedType to a TypeId by symbol lookup."""
    sid = symbol_table.get_type_symbol(named.name)
    if sid is not None:
        sym = symbol_table.get_symbol(sid)
        if sym is not None:
            from ..symbols import SymbolKind
            if sym.kind == SymbolKind.STRUCT:
                return type_table.make_struct(sym.id, ())
            if sym.kind == SymbolKind.ENUM:
                return type_table.make_enum(sym.id, ())
            if sym.kind == SymbolKind.PROTOCOL:
                return type_table.get_protocol_type(sym.id)
    return type_table.get_builtin(named.name)


def _llvm_signature_matches(fn: ir.Function, expected: ir.FunctionType) -> bool:
    """Compare an LLVM function's signature to ``expected``.

    A Rolang ``self: SomeStruct`` parameter lowers to a typed pointer
    (e.g. ``%"Vec_String"*``) while the runtime ABI for dunder hooks
    expects a generic ``i8*`` / ``ptr``. Both are semantically pointers,
    so we treat *any* pointer LLVM type as compatible with the expected
    pointer slot. Non-pointer slots still compare by string form so a
    user that wrote ``__release__(extra: i32)`` is still rejected.
    """
    func_type = fn.function_type
    if str(func_type.return_type) != str(expected.return_type):
        return False
    if len(func_type.args) != len(expected.args):
        return False
    for actual, want in zip(func_type.args, expected.args):
        if isinstance(want, ir.PointerType):
            if not isinstance(actual, ir.PointerType):
                return False
        else:
            if str(actual) != str(want):
                return False
    return True


def _find_dunder_method(
    func_map: dict[str, ir.Function],
    struct_name: str,
    dunder: str,
    expected: ir.FunctionType,
    struct_label: str,
    errors: list[str],
) -> Optional[ir.Function]:
    """Look up ``<struct_name>_<dunder>`` and validate its signature.

    The compiler convention is that any struct method named ``__release__``
    or ``__gc_trace__`` is picked up as the destructor / GC trace hook
    respectively. Method mangling makes the LLVM symbol
    ``<struct_name>_<dunder>`` (after monomorphization, ``<struct_name>``
    is e.g. ``Vec_String``). If the method exists but the signature is
    wrong, we record a compile error so wrong-shape hooks can't reach
    the runtime and crash the GC / deinit path.
    """
    sym = f"{struct_name}_{dunder}"
    fn = func_map.get(sym)
    if fn is None:
        return None
    if not _llvm_signature_matches(fn, expected):
        errors.append(
            f"{dunder} on struct '{struct_label}' has signature "
            f"{fn.function_type}, expected {expected}"
        )
        return None
    return fn


def _collect_existential_boxes(program: MirProgram) -> set[tuple[TypeId, TypeId]]:
    """Collect every ``(concrete, protocol)`` pair the program needs a
    witness table for. Includes both up-cast sites (``BoxExistential``)
    and down-cast sites (``ExistentialCheckType`` / ``ExistentialUnbox``)
    — without the latter, a program that only downcasts (e.g. round-trips
    an existential it received from another module) would have no
    witness table to compare against and the cast would always fail.
    """
    pairs: set[tuple[TypeId, TypeId]] = set()
    for func in program.functions:
        for block in func.blocks.values():
            for op in block.ops:
                if isinstance(op, BoxExistential):
                    pairs.add((op.concrete_type, op.protocol_type))
                elif isinstance(op, (ExistentialCheckType, ExistentialUnbox)):
                    pairs.add((op.concrete_type, op.protocol_type))
    return pairs


def _emit_witness_thunk(
    module: ir.Module,
    type_cache: TypeLayoutCache,
    type_table: TypeTable,
    symbol_table: SymbolTable,
    concrete_type: TypeId,
    protocol_type: TypeId,
    requirement: FuncRequirement,
    witness: WitnessEntry,
    func_map: dict[str, ir.Function],
    errors: list[str],
) -> Optional[ir.Function]:
    concrete_name = _type_mangle_name(type_table, symbol_table, concrete_type)
    protocol_name = _type_mangle_name(type_table, symbol_table, protocol_type)
    impl_name = f"{concrete_name}_{witness.implementation_name}"
    impl = func_map.get(impl_name)
    if impl is None:
        errors.append(f"Missing witness implementation function: {impl_name}")
        return None

    ret_type = type_cache.get_llvm_type(requirement.return_type)
    param_types = [type_cache.ptr] + [type_cache.get_llvm_type(p) for p in requirement.params]
    thunk_type = ir.FunctionType(ret_type, param_types)
    thunk_name = f"__witness_thunk_{concrete_name}_{protocol_name}_{requirement.name}"
    thunk = ir.Function(module, thunk_type, name=thunk_name)
    thunk.args[0].name = "value"
    for i, arg in enumerate(thunk.args[1:]):
        arg.name = f"arg{i}"

    builder = ir.IRBuilder(thunk.append_basic_block("entry"))
    concrete_llvm = type_cache.get_llvm_type(concrete_type)
    if type_table.is_heap_type(concrete_type):
        if thunk.args[0].type == concrete_llvm:
            receiver = thunk.args[0]
        else:
            receiver = builder.bitcast(thunk.args[0], concrete_llvm, name="self")
    else:
        value_payload = builder.gep(
            thunk.args[0],
            [ir.Constant(type_cache.i64, type_cache.OBJ_HEADER_SIZE)],
            name="value.payload",
        )
        value_ptr = builder.bitcast(value_payload, concrete_llvm.as_pointer(), name="typed_value")
        receiver = builder.load(value_ptr, name="self")
    call_args = [receiver] + list(thunk.args[1:])

    if isinstance(ret_type, ir.VoidType):
        builder.call(impl, call_args)
        builder.ret_void()
    else:
        result = builder.call(impl, call_args, name="result")
        builder.ret(result)

    return thunk


def _witness_table_name(
    type_table: TypeTable,
    symbol_table: SymbolTable,
    concrete_type: TypeId,
    protocol_type: TypeId,
) -> str:
    concrete_name = _type_mangle_name(type_table, symbol_table, concrete_type)
    protocol_name = _type_mangle_name(type_table, symbol_table, protocol_type)
    return f"__witness_{concrete_name}_{protocol_name}"


def _type_mangle_name(
    type_table: TypeTable,
    symbol_table: SymbolTable,
    type_id: TypeId,
) -> str:
    info = type_table.get_type(type_id)
    if info is not None and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
        symbol = symbol_table.get_symbol(info.data.symbol_id)
        if symbol is not None:
            return _sanitize_name(symbol.name)

    if info is not None and info.kind == TypeKind.PROTOCOL and isinstance(info.data, ProtocolTypeData):
        symbol = symbol_table.get_symbol(info.data.symbol_id)
        if symbol is not None:
            return _sanitize_name(symbol.name)

    return _sanitize_name(type_table.format_type(type_id))


def _sanitize_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)


def compile_to_object(
    arc_result: ArcInsertionResult,
    output_path: str,
    module_name: str = "rolang_module",
    target_triple: Optional[str] = None,
    opt_level: int = 0,
    frame_structs: Optional[Dict[str, Any]] = None,
) -> list[str]:
    """
    Compile MIR to an object file.

    Args:
        arc_result: Result from ARC insertion pass
        output_path: Path for the output .o file
        module_name: Name for the LLVM module
        target_triple: Target triple (if None, uses host)
        opt_level: Optimization level (0-3)
        frame_structs: Async frame descriptors from async lowering

    Returns:
        List of error messages (empty if successful)
    """
    # Generate LLVM IR
    result = compile_to_llvm(arc_result, module_name, target_triple, frame_structs=frame_structs)
    if result.has_errors():
        return result.errors

    # Compile to object file
    return compile_module_to_object(result.module, output_path, opt_level)


def _uniquify_function_names(program) -> None:
    """Rename duplicate MIR function names so each gets a unique LLVM symbol.

    Under unified multi-module compilation the MIR program can legitimately
    contain several functions sharing a source-level name across modules
    (e.g. an `internal` helper in lib.rl and a `def helper` in main.rl).
    They carry distinct SymbolIds but a shared `name`, which makes llvmlite
    reject the second LLVM symbol. We disambiguate by appending the SymbolId
    to all but one occurrence and rewrite every CallStatic op that resolved
    to a renamed function.

    Tie-break rule:
      * For `main`: the LAST occurrence keeps the bare name (the merge step
        appends the entry module last in topological order, so this is the
        program entry the linker should see).
      * For any other name: the FIRST occurrence keeps the bare name.
    """
    from ..mir import CallStatic

    # First pass: count name occurrences.
    name_counts: dict[str, int] = {}
    for func in program.functions:
        name_counts[func.name] = name_counts.get(func.name, 0) + 1

    if not any(c > 1 for c in name_counts.values()):
        return  # No collisions, nothing to do.

    # Determine which occurrence of each duplicated name keeps the bare
    # form. For `main`, that's the last occurrence; for everything else,
    # the first.
    keep_index: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    for idx, func in enumerate(program.functions):
        last_seen[func.name] = idx
        keep_index.setdefault(func.name, idx)
    if "main" in name_counts and name_counts["main"] > 1:
        keep_index["main"] = last_seen["main"]

    # Apply the renaming plan, keyed by SymbolId for the rewrite step.
    sym_to_new_name: dict = {}
    seen_names: set[str] = set()
    for idx, func in enumerate(program.functions):
        original = func.name
        if name_counts[original] == 1:
            seen_names.add(original)
            if func.symbol_id is not None:
                sym_to_new_name[func.symbol_id] = original
            continue
        if idx == keep_index[original]:
            seen_names.add(original)
            if func.symbol_id is not None:
                sym_to_new_name[func.symbol_id] = original
            continue
        # Disambiguate via symbol id (or fallback counter).
        sid = func.symbol_id
        if sid is not None:
            new_name = f"{original}.{sid.id}"
        else:
            counter = 1
            while f"{original}.{counter}" in seen_names:
                counter += 1
            new_name = f"{original}.{counter}"
        func.name = new_name
        seen_names.add(new_name)
        if sid is not None:
            sym_to_new_name[sid] = new_name

    if not sym_to_new_name:
        return

    # Rewrite every CallStatic that carries a symbol id to use the new name.
    for func in program.functions:
        for block in func.blocks.values():
            for op in block.ops:
                if isinstance(op, CallStatic) and op.func_symbol is not None:
                    new_name = sym_to_new_name.get(op.func_symbol)
                    if new_name is not None and op.func_name != new_name:
                        op.func_name = new_name


# The runtime supplies a real C `int main(int argc, char** argv)` that
# captures argv into globals (so `argv()` / `argc()` from process.rl work)
# and then calls into this internal name. Keep in sync with rolang_rt.c.
USER_MAIN_INTERNAL_NAME = "__rolang_user_main"


def _rename_user_main(program, type_table=None) -> None:
    """Rename the user's `main` MIR function to USER_MAIN_INTERNAL_NAME.

    The runtime library defines the actual C `main(int argc, char** argv)`
    entry point, which stashes argv/argc into globals (readable from
    Rolang via `rt_args_count` / `rt_args_get`) and then calls into the
    renamed user function. Keeping the user entry point under an internal
    name avoids a duplicate-symbol link error against the runtime's
    `main`, and means user code never has to spell `(argc, argv)` in its
    Rolang `main` signature.

    Any CallStatic targeting the old `main` symbol is rewritten too;
    user code calling `main()` recursively is unusual but legal.
    """
    from ..mir import CallStatic

    from ..mir import Return, ConstantOperand, ConstantKind

    target_symbol = None
    renamed = False
    for func in program.functions:
        if func.name == "main":
            func.name = USER_MAIN_INTERNAL_NAME
            renamed = True
            if func.symbol_id is not None:
                target_symbol = func.symbol_id
            # The C runtime entry point reads the user main's return value as
            # an int32_t process exit code (`extern int32_t __rolang_user_main`).
            # A `Void` main would otherwise be emitted as an LLVM `void` function
            # and the wrapper would read a garbage register as the exit code.
            # Normalise a Void main to return `i32 0` so the ABI matches.
            if type_table is not None and func.ret_type == type_table.void_type:
                i32_type = type_table.get_builtin("i32")
                if i32_type is not None:
                    func.ret_type = i32_type
                    zero = ConstantOperand(ConstantKind.INT, 0, i32_type)
                    for block in func.blocks.values():
                        if isinstance(block.terminator, Return) and block.terminator.value is None:
                            block.terminator = Return(value=zero)
            break  # MIR has a single `main` after _uniquify_function_names.

    if not renamed:
        return

    for func in program.functions:
        for block in func.blocks.values():
            for op in block.ops:
                if not isinstance(op, CallStatic):
                    continue
                # Rewrite by symbol id when available (robust against
                # any future passes that mess with names); also catch
                # any stale-name references defensively.
                if target_symbol is not None and op.func_symbol == target_symbol:
                    op.func_name = USER_MAIN_INTERNAL_NAME
                elif op.func_name == "main":
                    op.func_name = USER_MAIN_INTERNAL_NAME


def _declare_function(
    module: ir.Module,
    type_cache: TypeLayoutCache,
    mir_func,
) -> ir.Function:
    """Declare an LLVM function from MIR function signature."""
    from ..mir import MirFunction

    func: MirFunction = mir_func

    # Get return type
    ret_type = type_cache.get_llvm_type(func.ret_type)

    # Get parameter types.
    # Note: the async-resume frame parameter is just a regular heap pointer
    # under Rolang v2 — every struct ref is already a single-level pointer
    # to a `rt_obj_alloc`-managed object. The historic "pointer-to-pointer"
    # special case was a v0.1 leftover; both entry (now `rt_obj_alloc` +
    # `rt_task_spawn`) and the runtime's `task->frame` field pass a plain
    # heap pointer.
    param_types = []
    for arg in func.args:
        llvm_type = type_cache.get_llvm_type(arg.type_id)
        param_types.append(llvm_type)

    # Create function type
    func_type = ir.FunctionType(ret_type, param_types)

    # Create function
    llvm_func = ir.Function(module, func_type, name=func.name)

    # Name parameters
    for i, arg in enumerate(func.args):
        llvm_func.args[i].name = arg.name

    return llvm_func


def _declare_extern_func(
    module: ir.Module,
    type_cache: TypeLayoutCache,
    extern,
) -> ir.Function:
    """Declare an external function, reusing existing declaration if present."""
    from ..mir import MirExternFunc

    ext: MirExternFunc = extern

    # Reuse existing declaration if already declared (e.g. by RuntimeABI)
    for f in module.functions:
        if f.name == ext.name:
            return f

    # Get return type
    ret_type = type_cache.get_llvm_type(ext.ret_type)

    # Get parameter types
    param_types = [type_cache.get_llvm_type(t) for _, t in ext.params]

    # Create function type
    func_type = ir.FunctionType(ret_type, param_types)

    # Create function (external linkage by default)
    llvm_func = ir.Function(module, func_type, name=ext.name)

    # Name parameters
    for i, (name, _) in enumerate(ext.params):
        llvm_func.args[i].name = name

    return llvm_func


def _emit_type_descriptor_table(
    module: ir.Module,
    type_cache: TypeLayoutCache,
    type_table: TypeTable,
    program: MirProgram,
    func_map: dict[str, ir.Function],
    errors: list[str],
) -> None:
    """Emit the type descriptor table for the GC.

    Emits:
      - RT_TYPE_DESCRIPTORS: array of {i64, i64, i32, i32, ptr, ptr, i32}
        (type_id, payload_size, field_count, fields_start, deinit_fn,
        trace_fn, acyclic). ``acyclic`` is 1 when instances of this type can
        never be part of a reference cycle, letting the cycle collector skip
        them. ``trace_fn`` is non-null for any struct that
        registered a ``@gc_trace("rt_xyz")`` hook (the stdlib's
        ``Vec<T>`` and ``Dict<K, V>`` use this same path — no special
        casing in the compiler) and points at a runtime helper that
        walks the buffer they hold via a ``RawPtr``.
      - RT_TYPE_FIELD_DESCRIPTORS: array of {i32, i64}
        (offset, field_type_descriptor_id)
      - RT_TYPE_DESCRIPTOR_COUNT: i32
      - RT_TYPE_FIELD_DESCRIPTOR_COUNT: i32

    Field descriptors enable the runtime to walk interior pointer fields
    of heap objects (for retain/release recursion and GC cycle detection).

    The deinit_fn slot, when non-null, points to a generated
    function. rt_obj_release calls it on the final reference-count
    decrement, just before releasing the object's pointer fields.
    See `MirStruct.deinit_func_name`.

    Note: the deinit function receives the ObjHeader pointer (not the
    payload pointer) — the codegen internally compensates by advancing
    past the 32-byte header when accessing `self` fields.
    """
    # Compute field descriptors first; may assign new descriptor IDs for field types
    field_desc_map = type_cache.compute_field_descriptors()

    descriptor_count = type_cache.get_descriptor_count()
    if descriptor_count == 0:
        return

    i64 = ir.IntType(64)
    i32 = ir.IntType(32)
    ptr_t = ir.IntType(8).as_pointer()

    # Build reverse map: descriptor_id -> type_id
    desc_to_type: dict[int, type] = {}
    for tid, did in type_cache._descriptor_ids.items():
        desc_to_type[did] = tid

    # Map type_id -> deinit / GC trace LLVM function by scanning each
    # struct's mangled method symbols for the two Python-style dunder
    # names recognised by the compiler:
    #
    #   * ``def __release__() -> Void`` — destructor (instance method).
    #     LLVM signature ``void(ptr)`` (just self).
    #   * ``static def __gc_trace__(payload, cb, ctx) -> Void`` — GC
    #     cycle-collector trace hook for containers whose managed
    #     pointers live inside an opaque buffer reached via ``RawPtr``.
    #     LLVM signature ``void(ptr, ptr, ptr)`` (no self because it's
    #     static).
    #
    # Neither hook is special-cased anywhere upstream — they're plain
    # methods discovered here by name. The stdlib ``Vec<T>`` and
    # ``Dict<K, V>`` use this same mechanism as user containers.
    type_to_deinit: dict[type, ir.Function] = {}
    type_to_trace: dict[type, ir.Function] = {}

    release_sig = ir.FunctionType(ir.VoidType(), [ptr_t])
    trace_sig = ir.FunctionType(ir.VoidType(), [ptr_t, ptr_t, ptr_t])

    for mir_struct in program.structs:
        release_fn = _find_dunder_method(
            func_map, mir_struct.name, "__release__",
            release_sig, mir_struct.name, errors,
        )
        if release_fn is not None:
            type_to_deinit[mir_struct.type_id] = release_fn

        trace_fn = _find_dunder_method(
            func_map, mir_struct.name, "__gc_trace__",
            trace_sig, mir_struct.name, errors,
        )
        if trace_fn is not None:
            type_to_trace[mir_struct.type_id] = trace_fn

    # === RT_TYPE_FIELD_DESCRIPTORS ===
    # {i32 offset, i64 field_type_id, i32 case_tag, i32 _pad}
    fd_type = ir.LiteralStructType([i32, i64, i32, i32])
    flat_field_descriptors: list[ir.Constant] = []

    for desc_id in range(descriptor_count):
        fds_for_type = field_desc_map.get(desc_id, [])
        for offset, field_desc_id, case_tag in fds_for_type:
            flat_field_descriptors.append(ir.Constant(fd_type, [
                ir.Constant(i32, offset),
                ir.Constant(i64, field_desc_id),
                ir.Constant(i32, case_tag),
                ir.Constant(i32, 0),
            ]))

    total_field_descriptors = len(flat_field_descriptors)
    if total_field_descriptors > 0:
        fd_array_type = ir.ArrayType(fd_type, total_field_descriptors)
        fd_global = ir.GlobalVariable(
            module, fd_array_type,
            name="RT_TYPE_FIELD_DESCRIPTORS"
        )
        fd_global.global_constant = True
        fd_global.initializer = ir.Constant(fd_array_type, flat_field_descriptors)

    # Emit field descriptor count
    fd_count_global = ir.GlobalVariable(
        module, i32,
        name="RT_TYPE_FIELD_DESCRIPTOR_COUNT"
    )
    fd_count_global.global_constant = True
    fd_count_global.initializer = ir.Constant(i32, total_field_descriptors)

    # === Per-type field-release fast paths ===
    # For each type with heap pointer fields, generate a specialized
    #   void __rolang_release_fields_<id>(i8* payload)
    # that ARC-releases each field with constant offsets — a fast,
    # branch-predictable replacement for the runtime's generic
    # obj_release_fields descriptor walk on the hot teardown path. Built from
    # the SAME (offset, case_tag) field data, so it is exactly equivalent:
    # per-field null checks, and enum-tag filtering (tag read at payload[0],
    # matching obj_release_fields) for case-specific fields. The descriptor
    # table still carries the field descriptors (GC trace, clone, fallback);
    # this only accelerates release.
    rt_release_fn = module.globals.get("rt_obj_release")
    if rt_release_fn is None:
        rt_release_fn = ir.Function(
            module, ir.FunctionType(ir.VoidType(), [ptr_t]), name="rt_obj_release"
        )
    release_fields_sig = ir.FunctionType(ir.VoidType(), [ptr_t])
    type_to_release_fields: dict[int, ir.Function] = {}

    for desc_id in range(descriptor_count):
        fds_for_type = field_desc_map.get(desc_id, [])
        if not fds_for_type:
            continue
        fn = ir.Function(module, release_fields_sig,
                         name=f"__rolang_release_fields_{desc_id}")
        fn.linkage = "internal"
        payload = fn.args[0]
        payload.name = "payload"
        b = ir.IRBuilder(fn.append_basic_block("entry"))

        tag_val = None
        if any(ct >= 0 for (_o, _f, ct) in fds_for_type):
            tag_val = b.load(b.bitcast(payload, ir.PointerType(i32)), name="enum.tag")

        def _release_field(off: int) -> None:
            faddr = b.gep(payload, [ir.Constant(i64, off)], name="f.addr")
            val = b.load(b.bitcast(faddr, ir.PointerType(ptr_t)), name="f.val")
            with b.if_then(b.icmp_unsigned("!=", val, ir.Constant(ptr_t, None))):
                b.call(rt_release_fn, [val])

        for offset, _field_desc_id, case_tag in fds_for_type:
            if case_tag >= 0:
                with b.if_then(b.icmp_unsigned("==", tag_val,
                                               ir.Constant(i32, case_tag))):
                    _release_field(offset)
            else:
                _release_field(offset)
        b.ret_void()
        type_to_release_fields[desc_id] = fn

    # === RT_TYPE_DESCRIPTORS ===
    # {i64 type_id, i64 payload_size, i32 field_count, i32 fields_start,
    #  ptr  deinit_fn, ptr trace_fn, i32 acyclic, ptr release_fields_fn}
    # MUST stay in sync with the C-side struct definition in rolang_rt.c.
    # Both the i32 acyclic flag and the trailing release_fields_fn pointer are
    # append-only so existing field offsets do not shift.
    desc_type = ir.LiteralStructType([i64, i64, i32, i32, ptr_t, ptr_t, i32, ptr_t])
    descriptors: list[ir.Constant] = []
    field_desc_offset = 0

    # Descriptor ids whose type can never be part of a reference cycle. These
    # are excluded from the cycle collector's candidate set at runtime.
    acyclic_ids = type_cache.acyclic_descriptor_ids(type_table, type_to_trace)

    null_ptr = ir.Constant(ptr_t, None)

    for desc_id in range(descriptor_count):
        type_id = desc_to_type.get(desc_id)
        payload_size = 0
        if type_id is not None:
            info = type_table.get_type(type_id)
            if info is not None and info.kind == TypeKind.CLOSURE:
                payload_size = type_cache.get_closure_payload_size(type_id)
            elif info is not None and info.kind == TypeKind.EXISTENTIAL:
                payload_size = type_cache.get_existential_payload_size()
            else:
                payload_size = type_cache._get_type_size(type_id)
        fds_for_type = field_desc_map.get(desc_id, [])
        field_count = len(fds_for_type)

        deinit_const: ir.Constant = null_ptr
        deinit_fn = type_to_deinit.get(type_id) if type_id is not None else None
        if deinit_fn is not None:
            deinit_const = deinit_fn.bitcast(ptr_t)

        trace_const: ir.Constant = null_ptr
        trace_fn = type_to_trace.get(type_id) if type_id is not None else None
        if trace_fn is not None:
            trace_const = trace_fn.bitcast(ptr_t)

        release_fields_const: ir.Constant = null_ptr
        release_fields_fn = type_to_release_fields.get(desc_id)
        if release_fields_fn is not None:
            release_fields_const = release_fields_fn.bitcast(ptr_t)

        descriptors.append(ir.Constant(desc_type, [
            ir.Constant(i64, desc_id),
            ir.Constant(i64, payload_size),
            ir.Constant(i32, field_count),
            ir.Constant(i32, field_desc_offset),
            deinit_const,
            trace_const,
            ir.Constant(i32, 1 if desc_id in acyclic_ids else 0),
            release_fields_const,
        ]))
        field_desc_offset += field_count

    desc_array_type = ir.ArrayType(desc_type, descriptor_count)
    desc_global = ir.GlobalVariable(
        module, desc_array_type,
        name="RT_TYPE_DESCRIPTORS"
    )
    desc_global.global_constant = True
    desc_global.initializer = ir.Constant(desc_array_type, descriptors)

    # Emit descriptor count
    count_global = ir.GlobalVariable(
        module, i32,
        name="RT_TYPE_DESCRIPTOR_COUNT"
    )
    count_global.global_constant = True
    count_global.initializer = ir.Constant(i32, descriptor_count)


# Public exports
__all__ = [
    "compile_to_llvm",
    "compile_to_object",
    "CodegenResult",
    "TypeLayoutCache",
    "RuntimeABI",
    "FunctionCodegen",
]
