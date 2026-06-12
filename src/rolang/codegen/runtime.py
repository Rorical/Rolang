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
        self.i32 = ir.IntType(32)
        self.i64 = ir.IntType(64)
        self.void = ir.VoidType()
        self.ptr = ir.PointerType(self.i8)

        # Declare all runtime functions
        self._declare_memory_functions()
        self._declare_panic_functions()
        self._declare_obj_functions()        # typed object system
        self._declare_collection_functions()
        self._declare_string_functions()
        self._declare_inline_frem_f64()

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

    def _declare_inline_frem_f64(self) -> None:
        """Emit an internal alwaysinline exact-fmod fast path for f64 `%`.

        A plain `frem` lowers to a libm `fmod` call on AArch64/x86-64. The
        fast path computes r = fma(-trunc(a/b), b, a): when trunc(a/b) is the
        true integral quotient, a - t*b is the exactly-representable fmod
        value and the fused multiply-add returns it exactly. The guards
        reject every case where that does not hold — |a/b| >= 2^52 (trunc(q)
        may differ from the true quotient by more than one), zero results
        (libm fmod preserves the sign of `a`), wrong-sign or out-of-range
        results (off-by-one rounded quotient), and NaN/inf operands — and
        fall back to libm, so the operator stays bit-exact IEEE fmod on
        every input.
        """
        f64 = ir.DoubleType()
        fnty = ir.FunctionType(f64, [f64, f64])

        def get_fn(name: str, ty: ir.FunctionType) -> ir.Function:
            fn = self.module.globals.get(name)
            if fn is None:
                fn = ir.Function(self.module, ty, name=name)
            return fn

        fmod_lib = get_fn("fmod", fnty)
        fabs = get_fn("llvm.fabs.f64", ir.FunctionType(f64, [f64]))
        ftrunc = get_fn("llvm.trunc.f64", ir.FunctionType(f64, [f64]))
        fma = get_fn("llvm.fma.f64", ir.FunctionType(f64, [f64, f64, f64]))

        func = ir.Function(self.module, fnty, name="rt_frem_f64")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")

        entry = func.append_basic_block(name="entry")
        fast = func.append_basic_block(name="fast")
        slow = func.append_basic_block(name="slow")
        join = func.append_basic_block(name="join")

        builder = ir.IRBuilder(entry)
        a, b = func.args
        q = builder.fdiv(a, b, name="q")
        qabs = builder.call(fabs, [q], name="qabs")
        # Ordered < is false when q is NaN (NaN/inf operands, b == 0),
        # routing those to libm too.
        small = builder.fcmp_ordered(
            "<", qabs, ir.Constant(f64, 2.0 ** 52), name="q.small"
        )
        builder.cbranch(small, fast, slow)

        builder.position_at_end(fast)
        t = builder.call(ftrunc, [q], name="t")
        nt = builder.fneg(t, name="nt")
        r = builder.call(fma, [nt, b, a], name="r")
        rabs = builder.call(fabs, [r], name="rabs")
        babs = builder.call(fabs, [b], name="babs")
        in_range = builder.fcmp_ordered("<", rabs, babs, name="r.inrange")
        nonzero = builder.fcmp_ordered(
            "!=", r, ir.Constant(f64, 0.0), name="r.nonzero"
        )
        r_bits = builder.bitcast(r, self.i64, name="r.bits")
        a_bits = builder.bitcast(a, self.i64, name="a.bits")
        sign_xor = builder.xor(r_bits, a_bits, name="sign.xor")
        same_sign = builder.icmp_signed(
            ">=", sign_xor, ir.Constant(self.i64, 0), name="r.samesign"
        )
        ok = builder.and_(
            builder.and_(in_range, nonzero), same_sign, name="r.ok"
        )
        fast_end = builder.block
        builder.cbranch(ok, join, slow)

        builder.position_at_end(slow)
        lr = builder.call(fmod_lib, [a, b], name="r.libm")
        slow_end = builder.block
        builder.branch(join)

        builder.position_at_end(join)
        phi = builder.phi(f64, name="fmod")
        phi.add_incoming(r, fast_end)
        phi.add_incoming(lr, slow_end)
        builder.ret(phi)

        self.rt_frem_f64 = func

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

        # void* rt_obj_alloc_noinit(...) — same, but skips the payload
        # zero-fill. Only for construction sites that store every live field
        # immediately (MakeStruct / MakeEnum); see rolang_rt.c.
        self.rt_obj_alloc_noinit = ir.Function(
            self.module, obj_alloc_type, name="rt_obj_alloc_noinit"
        )

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

        # Inline pool-pop allocation fast path (needs rt_gc_collect above).
        self._declare_inline_obj_alloc_fast()

    # Pool size classes (TOTAL bytes: 32B header + payload). MUST match
    # pool_bin_sizes in runtime/rolang_rt.c — the inline fast path pops from
    # the same free lists the C slow path pushes to, so a drifted table would
    # mix size classes and corrupt the heap.
    POOL_BIN_SIZES = (48, 64, 96, 128, 192, 256)
    OBJ_HEADER_SIZE = 32

    def _declare_inline_obj_alloc_fast(self) -> None:
        """Inline pool-allocation fast path for the no-init alloc.

        rt_obj_alloc_fast(payload_size, align, type_id, bin) — `bin` is a
        compile-time constant the call site derives from POOL_BIN_SIZES.
        Fast path (free list non-empty): pop node, write header (rc=1,
        type_id), link into gc_object_list, bump the allocation counter, and
        poll the cycle-GC threshold — exactly what _obj_alloc_impl +
        gc_list_add do in rolang_rt.c for a pooled, non-zeroing allocation.
        The collector skips the list head (= this not-yet-stored object).
        Falls back to rt_obj_alloc_noinit when the free list is empty.

        Links against the runtime's exported globals: pool_free_lists,
        gc_object_list, gc_alloc_counter, gc_last_collect_count, gc_next_gap,
        gc_running. Header offsets: rc=0, type_id=8, prev=16, next=24
        (guarded C-side by _Static_asserts on ObjHeader).
        """
        # External globals from rolang_rt.c
        pool_lists_ty = ir.ArrayType(self.ptr, len(self.POOL_BIN_SIZES))
        self.g_pool_free_lists = ir.GlobalVariable(
            self.module, pool_lists_ty, name="pool_free_lists")
        self.g_gc_object_list = ir.GlobalVariable(
            self.module, self.ptr, name="gc_object_list")
        self.g_gc_alloc_counter = ir.GlobalVariable(
            self.module, self.i64, name="gc_alloc_counter")
        self.g_gc_trigger_at = ir.GlobalVariable(
            self.module, self.i64, name="gc_trigger_at")
        self.g_gc_running = ir.GlobalVariable(
            self.module, self.i32, name="gc_running")

        fnty = ir.FunctionType(self.ptr, [self.i64, self.i64, self.i64, self.i64])
        func = ir.Function(self.module, fnty, name="rt_obj_alloc_fast")
        func.linkage = "internal"
        func.attributes.add("alwaysinline")
        self.rt_obj_alloc_fast = func

        payload_size, align, type_id, bin_idx = func.args

        entry = func.append_basic_block(name="entry")
        fast = func.append_basic_block(name="fast")
        set_head_prev = func.append_basic_block(name="set_head_prev")
        after_link = func.append_basic_block(name="after_link")
        gc_poll = func.append_basic_block(name="gc_poll")
        gc_run = func.append_basic_block(name="gc_run")
        done = func.append_basic_block(name="done")
        slow = func.append_basic_block(name="slow")

        b = ir.IRBuilder(entry)
        slot = b.gep(self.g_pool_free_lists,
                     [ir.Constant(self.i32, 0), b.trunc(bin_idx, self.i32)],
                     name="pool.slot")
        node = b.load(slot, name="pool.node")
        is_empty = b.icmp_signed(
            "==", b.ptrtoint(node, self.i64), ir.Constant(self.i64, 0))
        b.cbranch(is_empty, slow, fast)

        # --- fast: pop free list, init header, link into GC list ---
        b.position_at_end(fast)
        next_slot = b.bitcast(node, ir.PointerType(self.ptr), name="pool.next.slot")
        nxt = b.load(next_slot, name="pool.next")
        b.store(nxt, slot)
        # header: rc=1 (offset 0; overwrites the free-list link), type_id at 8
        rc_ptr = b.bitcast(node, ir.PointerType(self.i64), name="hdr.rc")
        b.store(ir.Constant(self.i64, 1), rc_ptr)
        tid_ptr = b.bitcast(
            b.gep(node, [ir.Constant(self.i64, 8)]), ir.PointerType(self.i64),
            name="hdr.tid")
        b.store(type_id, tid_ptr)
        # gc_list_add: prev=NULL, next=head, head->prev=node, head=node
        prev_ptr = b.bitcast(
            b.gep(node, [ir.Constant(self.i64, 16)]), ir.PointerType(self.ptr),
            name="hdr.prev")
        b.store(ir.Constant(self.ptr, None), prev_ptr)
        head = b.load(self.g_gc_object_list, name="gc.head")
        hdr_next_ptr = b.bitcast(
            b.gep(node, [ir.Constant(self.i64, 24)]), ir.PointerType(self.ptr),
            name="hdr.next")
        b.store(head, hdr_next_ptr)
        head_null = b.icmp_signed(
            "==", b.ptrtoint(head, self.i64), ir.Constant(self.i64, 0))
        b.cbranch(head_null, after_link, set_head_prev)

        b.position_at_end(set_head_prev)
        head_prev_ptr = b.bitcast(
            b.gep(head, [ir.Constant(self.i64, 16)]), ir.PointerType(self.ptr),
            name="gc.head.prev")
        b.store(node, head_prev_ptr)
        b.branch(after_link)

        b.position_at_end(after_link)
        b.store(node, self.g_gc_object_list)
        cnt = b.load(self.g_gc_alloc_counter, name="gc.cnt")
        cnt1 = b.add(cnt, ir.Constant(self.i64, 1), name="gc.cnt1")
        b.store(cnt1, self.g_gc_alloc_counter)
        # Post-link GC poll. The just-linked `node` is the list head; the
        # collector skips the head so its stale (noinit) payload is never
        # walked — mirrors _obj_alloc_impl in rolang_rt.c.
        trigger_at = b.load(self.g_gc_trigger_at, name="gc.trigger_at")
        due = b.icmp_signed(">=", cnt1, trigger_at, name="gc.due")
        b.cbranch(due, gc_poll, done)

        # --- threshold crossed: skip while a collection is already running ---
        b.position_at_end(gc_poll)
        running = b.load(self.g_gc_running, name="gc.running")
        is_running = b.icmp_signed("!=", running, ir.Constant(self.i32, 0))
        b.cbranch(is_running, done, gc_run)

        b.position_at_end(gc_run)
        b.call(self.rt_gc_collect, [])
        b.branch(done)

        b.position_at_end(done)
        b.ret(node)

        # --- slow: pool empty (or first use) — full C path. Its own GC poll
        # re-checks a freshly reset clock, so no double collection occurs. ---
        b.position_at_end(slow)
        result = b.call(self.rt_obj_alloc_noinit, [payload_size, align, type_id],
                        name="obj_alloc.slow")
        b.ret(result)

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
        zero_init: bool = True,
    ) -> ir.Value:
        """Emit a call to rt_obj_alloc (or rt_obj_alloc_noinit).

        Pass zero_init=False ONLY when the caller stores every live payload
        field before the next allocation/release (MakeStruct, MakeEnum, and
        the string-literal emission, which fills all three String fields).

        No-init allocations of a compile-time-known pooled size go through
        the inline rt_obj_alloc_fast pool pop instead of a C call.
        """
        if not zero_init and isinstance(payload_size, ir.Constant):
            total = int(payload_size.constant) + self.OBJ_HEADER_SIZE
            if total <= self.POOL_BIN_SIZES[-1]:
                bin_idx = next(i for i, s in enumerate(self.POOL_BIN_SIZES)
                               if total <= s)
                return builder.call(
                    self.rt_obj_alloc_fast,
                    [payload_size, align, type_id,
                     ir.Constant(self.i64, bin_idx)],
                    name="obj_alloc",
                )
        fn = self.rt_obj_alloc if zero_init else self.rt_obj_alloc_noinit
        return builder.call(fn, [payload_size, align, type_id], name="obj_alloc")

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
