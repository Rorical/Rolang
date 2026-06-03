"""
LLVM Runtime ABI (Application Binary Interface) for RoLang.

Declares runtime functions (rt_*) and provides helpers to emit calls.
"""

from __future__ import annotations

from llvmlite import ir

from .types import OBJ_HEADER_SIZE


class RuntimeABI:
    """
    Declares and provides access to runtime functions.

    All runtime functions are prefixed with 'rt_' to avoid name collisions.
    """

    def __init__(self, module: ir.Module) -> None:
        self.module = module

        # Common types
        self.i8 = ir.IntType(8)
        self.i64 = ir.IntType(64)
        self.void = ir.VoidType()
        self.ptr = ir.PointerType(self.i8)

        # Declare all runtime functions
        self._declare_memory_functions()
        self._declare_panic_functions()
        self._declare_obj_functions()        # typed object system
        self._declare_collection_functions()
        self._declare_string_functions()

    def _declare_string_functions(self) -> None:
        """Declare runtime helpers used by compiler-emitted string literals."""
        string_from_rodata_type = ir.FunctionType(self.ptr, [self.ptr, self.i64])
        self.rt_string_from_rodata = ir.Function(
            self.module, string_from_rodata_type, name="rt_string_from_rodata"
        )

        # Emit inlinable LLVM IR definitions for hot C runtime functions.
        self._declare_inline_char_at()
        self._declare_inline_char_classify()

    def _declare_inline_char_at(self) -> None:
        """Emit an internal alwaysinline definition of rt_string_char_at."""
        char_at_type = ir.FunctionType(self.i32, [self.ptr, self.i32])
        func = ir.Function(self.module, char_at_type, name="rt_string_char_at")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")

        entry = func.append_basic_block(name="entry")
        builder = ir.IRBuilder(entry)
        s = func.args[0]
        index = func.args[1]

        is_null = builder.icmp_signed("==", builder.ptrtoint(s, self.i64), ir.Constant(self.i64, 0))
        fail = func.append_basic_block(name="fail")
        ok = func.append_basic_block(name="ok")
        builder.cbranch(is_null, fail, ok)

        builder.position_at_end(ok)
        payload = builder.gep(s, [ir.Constant(self.i64, OBJ_HEADER_SIZE)], name="payload")
        data_ptr = builder.load(
            builder.bitcast(payload, ir.PointerType(self.ptr)), name="data"
        )
        len_val = builder.load(
            builder.bitcast(
                builder.gep(payload, [ir.Constant(self.i64, 8)]),
                ir.PointerType(self.i64),
            ),
            name="len",
        )
        idx64 = builder.sext(index, self.i64, name="idx64")
        bounds_ok = builder.icmp_unsigned("<", idx64, len_val, name="bounds_ok")
        read_block = func.append_basic_block(name="read")
        builder.cbranch(bounds_ok, read_block, fail)

        builder.position_at_end(read_block)
        ch_ptr = builder.gep(data_ptr, [idx64], name="ch_ptr")
        ch = builder.load(ch_ptr, name="ch")
        result = builder.zext(ch, self.i32, name="result")
        builder.ret(result)

        builder.position_at_end(fail)
        builder.ret(ir.Constant(self.i32, -1))

    def _declare_inline_char_classify(self) -> None:
        """Emit internal alwaysinline definitions for char classification."""
        for name, check_expr in [
            ("rt_char_is_digit", "c >= 48 && c <= 57"),
            ("rt_char_is_space", "c == 32 || c == 9 || c == 10 || c == 13"),
            ("rt_char_is_alpha", "(c >= 65 && c <= 90) || (c >= 97 && c <= 122)"),
            ("rt_char_is_alnum", "(c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122)"),
        ]:
            cls_type = ir.FunctionType(self.i32, [self.i32])
            func = ir.Function(self.module, cls_type, name=name)
            func.linkage = "internal"
            func.attributes.add("alwaysinline")

            entry = func.append_basic_block(name="entry")
            builder = ir.IRBuilder(entry)
            c = func.args[0]

            # Build the condition expression
            if name == "rt_char_is_digit":
                ge_48 = builder.icmp_signed(">=", c, ir.Constant(self.i32, 48))
                le_57 = builder.icmp_signed("<=", c, ir.Constant(self.i32, 57))
                cond = builder.and_(ge_48, le_57)
            elif name == "rt_char_is_space":
                eq_32 = builder.icmp_signed("==", c, ir.Constant(self.i32, 32))
                eq_9 = builder.icmp_signed("==", c, ir.Constant(self.i32, 9))
                eq_10 = builder.icmp_signed("==", c, ir.Constant(self.i32, 10))
                eq_13 = builder.icmp_signed("==", c, ir.Constant(self.i32, 13))
                or_1 = builder.or_(eq_32, eq_9)
                or_2 = builder.or_(or_1, eq_10)
                cond = builder.or_(or_2, eq_13)
            elif name == "rt_char_is_alpha":
                ge_65 = builder.icmp_signed(">=", c, ir.Constant(self.i32, 65))
                le_90 = builder.icmp_signed("<=", c, ir.Constant(self.i32, 90))
                upper = builder.and_(ge_65, le_90)
                ge_97 = builder.icmp_signed(">=", c, ir.Constant(self.i32, 97))
                le_122 = builder.icmp_signed("<=", c, ir.Constant(self.i32, 122))
                lower = builder.and_(ge_97, le_122)
                cond = builder.or_(upper, lower)
            elif name == "rt_char_is_alnum":
                ge_48 = builder.icmp_signed(">=", c, ir.Constant(self.i32, 48))
                le_57 = builder.icmp_signed("<=", c, ir.Constant(self.i32, 57))
                dig = builder.and_(ge_48, le_57)
                ge_65 = builder.icmp_signed(">=", c, ir.Constant(self.i32, 65))
                le_90 = builder.icmp_signed("<=", c, ir.Constant(self.i32, 90))
                upper = builder.and_(ge_65, le_90)
                ge_97 = builder.icmp_signed(">=", c, ir.Constant(self.i32, 97))
                le_122 = builder.icmp_signed("<=", c, ir.Constant(self.i32, 122))
                lower = builder.and_(ge_97, le_122)
                or_1 = builder.or_(dig, upper)
                cond = builder.or_(or_1, lower)
            else:
                cond = ir.Constant(self.i32, 0)

            result = builder.zext(cond, self.i32, name="result")
            builder.ret(result)

    def _declare_panic_functions(self) -> None:
        """Declare runtime panic / trap helpers used by codegen-emitted checks."""
        # void rt_panic_divide_by_zero(void) — noreturn
        div_zero_type = ir.FunctionType(self.void, [])
        self.rt_panic_divide_by_zero = ir.Function(
            self.module, div_zero_type, name="rt_panic_divide_by_zero"
        )
        self.rt_panic_divide_by_zero.attributes.add("noreturn")

        # void rt_panic_remainder_by_zero(void) — noreturn
        rem_zero_type = ir.FunctionType(self.void, [])
        self.rt_panic_remainder_by_zero = ir.Function(
            self.module, rem_zero_type, name="rt_panic_remainder_by_zero"
        )
        self.rt_panic_remainder_by_zero.attributes.add("noreturn")

        # void rt_panic(const char* ctx) — noreturn
        panic_type = ir.FunctionType(self.void, [self.ptr])
        self.rt_panic = ir.Function(self.module, panic_type, name="rt_panic")
        self.rt_panic.attributes.add("noreturn")

        # void rt_panic_invalid_cast(void) — noreturn
        # Emitted at the mismatch branch of `expr as! T`.
        invalid_cast_type = ir.FunctionType(self.void, [])
        self.rt_panic_invalid_cast = ir.Function(
            self.module, invalid_cast_type, name="rt_panic_invalid_cast",
        )
        self.rt_panic_invalid_cast.attributes.add("noreturn")

        # NOTE: ``rt_gvec_gc_trace`` and ``rt_dict_gc_trace`` are *not*
        # declared here. They're referenced from user code via
        # ``static def __gc_trace__(payload, cb, ctx)`` on
        # ``std/vec.rl``'s ``Vec<T>`` (and the analogue on Dict). The
        # corresponding ``extern "C" def`` in those stdlib files emits
        # the LLVM declaration with the proper signature. Keeping the
        # mechanism uniform makes user-defined containers work through
        # exactly the same path as the stdlib — no special-casing.

    def _declare_memory_functions(self) -> None:
        """Declare memory allocation functions."""
        # void* rt_alloc(int64_t size, int64_t align)
        alloc_type = ir.FunctionType(self.ptr, [self.i64, self.i64])
        self.rt_alloc = ir.Function(self.module, alloc_type, name="rt_alloc")

        # void rt_free(void* ptr)
        free_type = ir.FunctionType(self.void, [self.ptr])
        self.rt_free = ir.Function(self.module, free_type, name="rt_free")

    def _declare_obj_functions(self) -> None:
        """Declare typed-object runtime functions (heap objects with GC headers)."""
        # void* rt_obj_alloc(int64_t payload_size, int64_t align, uint64_t type_id)
        obj_alloc_type = ir.FunctionType(self.ptr, [self.i64, self.i64, self.i64])
        self.rt_obj_alloc = ir.Function(self.module, obj_alloc_type, name="rt_obj_alloc")

        # Inlinable retain/release (replaces the extern C calls). The C
        # functions of the same name still exist for the runtime's own
        # internal use; `internal` linkage keeps these module-private.
        self._declare_inline_obj_retain()
        self._declare_inline_obj_release()

        # void* rt_obj_clone(void* ptr)
        obj_clone_type = ir.FunctionType(self.ptr, [self.ptr])
        self.rt_obj_clone = ir.Function(self.module, obj_clone_type, name="rt_obj_clone")

        # void rt_gc_collect(void)
        gc_collect_type = ir.FunctionType(self.void, [])
        self.rt_gc_collect = ir.Function(self.module, gc_collect_type, name="rt_gc_collect")

        # int64_t rt_obj_alloc_count(void)
        alloc_count_type = ir.FunctionType(self.i64, [])
        self.rt_obj_alloc_count = ir.Function(self.module, alloc_count_type, name="rt_obj_alloc_count")

    def _declare_inline_obj_retain(self) -> None:
        """rc is the first header field (offset 0). retain = null-check + rc++.

        Plain (non-atomic) load/store: the runtime is single-threaded. The C
        rt_obj_retain (still defined for the runtime's own use) reads rc at the
        same offset; Task 2's _Static_assert(offsetof(ObjHeader, rc) == 0)
        guards the two against drift.
        """
        fnty = ir.FunctionType(self.void, [self.ptr])
        func = ir.Function(self.module, fnty, name="rt_obj_retain")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")
        self.rt_obj_retain = func

        entry = func.append_basic_block(name="entry")
        b = ir.IRBuilder(entry)
        ptr = func.args[0]
        is_null = b.icmp_signed("==", b.ptrtoint(ptr, self.i64), ir.Constant(self.i64, 0))
        do = func.append_basic_block(name="do")
        done = func.append_basic_block(name="done")
        b.cbranch(is_null, done, do)

        b.position_at_end(do)
        rc_ptr = b.bitcast(ptr, ir.PointerType(self.i64), name="rc_ptr")
        rc = b.load(rc_ptr, name="rc")
        b.store(b.add(rc, ir.Constant(self.i64, 1), name="rc_inc"), rc_ptr)
        b.branch(done)

        b.position_at_end(done)
        b.ret_void()

    def _declare_inline_obj_release(self) -> None:
        """release fast path: null-check + rc--; on prev==1 call rt_obj_release_slow.

        The teardown (deinit/resurrection/field-release/GC-unlink/free) lives in
        the C rt_obj_release_slow (Task 2); the fast path never duplicates it.
        """
        # extern C slow path (the single shared teardown implementation)
        slow_ty = ir.FunctionType(self.void, [self.ptr])
        self.rt_obj_release_slow = ir.Function(
            self.module, slow_ty, name="rt_obj_release_slow"
        )

        fnty = ir.FunctionType(self.void, [self.ptr])
        func = ir.Function(self.module, fnty, name="rt_obj_release")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")
        self.rt_obj_release = func

        entry = func.append_basic_block(name="entry")
        b = ir.IRBuilder(entry)
        ptr = func.args[0]
        is_null = b.icmp_signed("==", b.ptrtoint(ptr, self.i64), ir.Constant(self.i64, 0))
        do = func.append_basic_block(name="do")
        done = func.append_basic_block(name="done")
        b.cbranch(is_null, done, do)

        b.position_at_end(do)
        rc_ptr = b.bitcast(ptr, ir.PointerType(self.i64), name="rc_ptr")
        rc = b.load(rc_ptr, name="rc")
        b.store(b.sub(rc, ir.Constant(self.i64, 1), name="rc_dec"), rc_ptr)
        was_one = b.icmp_signed("==", rc, ir.Constant(self.i64, 1), name="was_one")
        slow = func.append_basic_block(name="slow")
        b.cbranch(was_one, slow, done)

        b.position_at_end(slow)
        b.call(self.rt_obj_release_slow, [ptr])
        b.branch(done)

        b.position_at_end(done)
        b.ret_void()

    def _declare_collection_functions(self) -> None:
        """Pre-declare runtime helpers that the codegen may emit directly.

        The legacy `rt_array_*` family was removed when `[T]` was folded
        into `Vec<T>`. The `rt_dict_*` family is still alive because
        `std/dict.rl`'s `Dict<K, V>` uses it as its backing store; the
        std extern declaration adds a `Function` to the LLVM module on
        first reference, so we don't pre-declare it here.
        """
        self.i32 = ir.IntType(32)

    # Helper methods to emit runtime calls

    def emit_alloc(
        self,
        builder: ir.IRBuilder,
        size: ir.Value,
        align: ir.Value,
    ) -> ir.Value:
        """Emit a call to rt_alloc."""
        return builder.call(self.rt_alloc, [size, align], name="alloc")

    def emit_free(self, builder: ir.IRBuilder, ptr: ir.Value) -> None:
        """Emit a call to rt_free."""
        builder.call(self.rt_free, [ptr])

    # --- typed object emission helpers ---

    def emit_obj_alloc(
        self,
        builder: ir.IRBuilder,
        payload_size: ir.Value,
        align: ir.Value,
        type_id: ir.Value,
    ) -> ir.Value:
        """Emit a call to rt_obj_alloc."""
        return builder.call(self.rt_obj_alloc, [payload_size, align, type_id], name="obj_alloc")

    def emit_obj_retain(self, builder: ir.IRBuilder, ptr: ir.Value) -> None:
        """Emit a call to rt_obj_retain. Accepts any pointer type, bitcasts to i8*."""
        if isinstance(ptr.type, ir.PointerType) and ptr.type != self.ptr:
            ptr = builder.bitcast(ptr, self.ptr, name="retain.i8")
        builder.call(self.rt_obj_retain, [ptr])

    def emit_obj_release(self, builder: ir.IRBuilder, ptr: ir.Value) -> None:
        """Emit a call to rt_obj_release. Accepts any pointer type, bitcasts to i8*."""
        if isinstance(ptr.type, ir.PointerType) and ptr.type != self.ptr:
            ptr = builder.bitcast(ptr, self.ptr, name="release.i8")
        builder.call(self.rt_obj_release, [ptr])

    def emit_obj_clone(self, builder: ir.IRBuilder, ptr: ir.Value) -> ir.Value:
        """Emit a call to rt_obj_clone. Accepts any pointer type, bitcasts to i8*."""
        if isinstance(ptr.type, ir.PointerType) and ptr.type != self.ptr:
            ptr = builder.bitcast(ptr, self.ptr, name="clone.i8")
        return builder.call(self.rt_obj_clone, [ptr], name="clone")

    def emit_gc_collect(self, builder: ir.IRBuilder) -> None:
        """Emit a call to rt_gc_collect."""
        builder.call(self.rt_gc_collect, [])

    def emit_obj_alloc_count(self, builder: ir.IRBuilder) -> ir.Value:
        """Emit a call to rt_obj_alloc_count."""
        return builder.call(self.rt_obj_alloc_count, [], name="alloc_count")

    def emit_string_from_rodata(
        self,
        builder: ir.IRBuilder,
        data: ir.Value,
        length: ir.Value,
    ) -> ir.Value:
        """Emit a call to rt_string_from_rodata."""
        return builder.call(self.rt_string_from_rodata, [data, length], name="string.handle")

    # Async runtime helpers are provided by AsyncCodegen.
