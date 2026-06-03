"""Async task operations LLVM codegen."""

from __future__ import annotations

from typing import Optional

from llvmlite import ir

from ..mir import (
    TaskJoin, TaskSpawn, TaskYield, TaskComplete, Suspend,
    AllocAsyncFrame, SchedulerRun, TaskGetResult, operand_type,
)


class OpsAsyncMixin:
    """Mixin for async operations: task spawn, join, yield, complete, suspend.

    All async frames are heap-allocated via `rt_obj_alloc` so the layout
    (32-byte ARC header + payload) is identical no matter whether the frame
    was created by the entry function or by a `rt_task_spawn` call site.
    Resume functions then GEP `+32` past the header to reach the payload.
    """

    def _emit_task_join(self, op: TaskJoin) -> Optional[ir.Value]:
        """Emit task join (await)."""
        task_handle = self.emit_operand(op.task_handle)

        if self.async_codegen is not None:
            result = self.async_codegen.emit_task_join(self.builder, task_handle)
        else:
            join_func = None
            for f in self.module.functions:
                if f.name == "rt_task_join":
                    join_func = f
                    break
            if join_func:
                result = self.builder.call(join_func, [task_handle], name="join_result")
            else:
                result = None

        if op.result is not None and result is not None:
            self._store_local(op.result, result)
            return result
        elif op.result is not None:
            llvm_type = self.type_cache.get_llvm_type(op.result_type)
            result = ir.Constant(llvm_type, ir.Undefined)
            self._store_local(op.result, result)
            return result
        return result

    def _emit_task_spawn(self, op: TaskSpawn) -> Optional[ir.Value]:
        """
        Emit task spawn: `result = rt_task_spawn(resume_fn, frame)`.

        After spawn we also store the resulting TaskHandle into the child's
        frame at the `_handle` field. The resume function reads that field
        in its completion block to call `rt_task_complete(self_handle, result)`
        — without this back-link the child would silently complete against
        a null handle and waiters would deadlock.
        """
        resume_name = op.async_func_name + "_resume" if hasattr(op, 'async_func_name') else ""

        resume_func = self.func_map.get(resume_name)
        if resume_func is None:
            for f in self.module.functions:
                if f.name == resume_name:
                    resume_func = f
                    break

        ptr_t = ir.IntType(8).as_pointer()
        if hasattr(op, 'frame') and op.frame is not None:
            frame_ptr = self.emit_operand(op.frame)
        else:
            frame_ptr = self._allocate_async_frame(op.async_func_name)
            if frame_ptr is None:
                frame_ptr = ir.Constant(ptr_t, None)

        if self.async_codegen is not None and resume_func is not None:
            resume_ptr = self.builder.bitcast(resume_func, ptr_t, name="resume_ptr")
            frame_i8 = self.builder.bitcast(frame_ptr, ptr_t, name="frame_i8")
            handle = self.async_codegen.emit_task_spawn(self.builder, resume_ptr, frame_i8)

            # Wire the back-link: child_frame._handle = handle.
            self._store_handle_into_frame(op.async_func_name, frame_ptr, handle)

            if op.result is not None:
                result_llvm_type = self.type_cache.get_llvm_type(op.result_type)
                if isinstance(result_llvm_type, ir.PointerType) or result_llvm_type == ptr_t:
                    self._store_local(op.result, handle)
            return handle

        if op.result is not None:
            llvm_type = self.type_cache.get_llvm_type(op.result_type)
            result = ir.Constant(llvm_type, ir.Undefined)
            self._store_local(op.result, result)
            return result
        return None

    def _store_handle_into_frame(
        self,
        async_func_name: str,
        frame_ptr: ir.Value,
        handle: ir.Value,
    ) -> None:
        """Store `handle` into `frame.{_handle}` so the child resume function
        can complete against its own handle. `frame_ptr` is the header
        pointer; the payload is at +32 bytes."""
        frame_struct = None
        for s in self.mir_structs:
            if getattr(s, 'name', '') == f"{async_func_name}_Frame":
                frame_struct = s
                break
        if frame_struct is None:
            return

        handle_idx = None
        for idx, f in enumerate(frame_struct.fields):
            if f.name == "$handle":
                handle_idx = idx
                break
        if handle_idx is None:
            return

        # GEP from header ptr -> +32 -> payload struct ptr -> field.
        inner = self.type_cache.get_inner_struct_type(frame_struct.type_id)
        if inner is None:
            inner = self.type_cache.get_struct_type(frame_struct)
        i32 = self.type_cache.i32
        i64 = self.type_cache.i64
        ptr_t = ir.IntType(8).as_pointer()

        # Normalise frame_ptr to i8* before doing the byte GEP.
        if frame_ptr.type != ptr_t:
            frame_i8 = self.builder.bitcast(frame_ptr, ptr_t, name="frame.hdr")
        else:
            frame_i8 = frame_ptr
        payload_i8 = self.builder.gep(
            frame_i8, [ir.Constant(i64, self.type_cache.OBJ_HEADER_SIZE)], name="frame.payload"
        )
        payload_typed = self.builder.bitcast(
            payload_i8, ir.PointerType(inner), name="frame.payload.typed"
        )
        handle_field = self.builder.gep(
            payload_typed,
            [ir.Constant(i32, 0), ir.Constant(i32, handle_idx)],
            name="frame._handle.ptr",
        )
        self.builder.store(handle, handle_field)

    def _async_frame_payload_size(self, frame_struct) -> int:
        """
        Compute the byte size of an async frame's payload using the same
        padded MIR-struct layout that type descriptors use for GC tracing.
        """
        return self.type_cache._get_type_size(frame_struct.type_id)

    def _allocate_async_frame(self, func_name: str) -> Optional[ir.Value]:
        """
        Heap-allocate the async frame for `func_name` via `rt_obj_alloc`.

        Always returns a pointer to the *typed-object header* (i.e. the
        raw `rt_obj_alloc` return value), NOT the payload — `_resume`
        functions take the header pointer and GEP `+32` themselves. This
        matches how the rest of the codebase (MakeStruct, MakeTuple, …)
        passes object pointers around.
        """
        frame_name = f"{func_name}_Frame"
        frame_struct = None
        for s in self.mir_structs:
            if getattr(s, 'name', '') == frame_name:
                frame_struct = s
                break

        if frame_struct is None:
            return None

        payload_size = self._async_frame_payload_size(frame_struct)
        type_id = self.type_cache.get_or_assign_descriptor_id(frame_struct.type_id)

        raw_ptr = self.runtime.emit_obj_alloc(
            self.builder,
            ir.Constant(self.type_cache.i64, payload_size),
            ir.Constant(self.type_cache.i64, 8),
            ir.Constant(self.type_cache.i64, type_id),
        )

        # Zero-initialise the state field through the payload pointer.
        # rt_obj_alloc zeroes the payload, but we keep this explicit so the
        # invariant "state field is 0 immediately after allocation" is
        # visible in the IR.
        payload_byte_ptr = self.builder.gep(
            raw_ptr,
            [ir.Constant(self.type_cache.i64, self.type_cache.OBJ_HEADER_SIZE)],
            name="frame.payload.ptr",
        )
        frame_inner_type = self.type_cache.get_inner_struct_type(frame_struct.type_id)
        if frame_inner_type is None:
            frame_inner_type = self.type_cache.get_struct_type(frame_struct)
        frame_typed = self.builder.bitcast(
            payload_byte_ptr,
            ir.PointerType(frame_inner_type),
            name="frame.typed",
        )
        i32 = self.type_cache.i32
        state_ptr = self.builder.gep(
            frame_typed,
            [ir.Constant(i32, 0), ir.Constant(i32, 0)],
            name="frame.state.ptr",
        )
        self.builder.store(ir.Constant(i32, 0), state_ptr)

        # Return the header pointer; callers can pass it straight to
        # rt_task_spawn and to _resume functions, which both expect the
        # header pointer (and GEP +32 internally).
        return raw_ptr

    def _emit_alloc_async_frame(self, op: AllocAsyncFrame) -> Optional[ir.Value]:
        """
        Lower an `AllocAsyncFrame` MIR op: allocate the frame on the heap
        through `rt_obj_alloc` and store the resulting pointer (header
        pointer) into the local indicated by `op.result`.
        """
        # The MIR-level result type is the frame struct's TypeId. Look up
        # which mir_struct that corresponds to by id.
        frame_struct = None
        for s in self.mir_structs:
            if getattr(s, "type_id", None) == op.frame_type:
                frame_struct = s
                break

        if frame_struct is None:
            # Should never happen if async_lowering wired things up correctly.
            # Be loud: store an undef pointer so the IR validator catches it.
            ptr_t = ir.IntType(8).as_pointer()
            self._store_local(op.result, ir.Constant(ptr_t, ir.Undefined))
            return None

        # Strip "{Name}_Frame" -> "{Name}" so _allocate_async_frame can look
        # up the same struct by name.
        name = frame_struct.name
        if name.endswith("_Frame"):
            owner = name[: -len("_Frame")]
        else:
            owner = name
        frame_ptr = self._allocate_async_frame(owner)
        if frame_ptr is None:
            ptr_t = ir.IntType(8).as_pointer()
            frame_ptr = ir.Constant(ptr_t, ir.Undefined)
        self._store_local(op.result, frame_ptr)
        return frame_ptr

    def _emit_scheduler_run(self, op: SchedulerRun) -> None:
        """Drain the task queue until empty (or until a specific handle
        completes if one was provided). Used by async program entry points."""
        if op.until_handle is not None:
            handle = self.emit_operand(op.until_handle)
            if self.async_codegen is not None:
                self.async_codegen.emit_task_join(self.builder, handle)
                if op.destroy_after:
                    self.async_codegen.emit_task_destroy(self.builder, handle)
            else:
                join_func = None
                for f in self.module.functions:
                    if f.name == "rt_task_join":
                        join_func = f
                        break
                if join_func is not None:
                    self.builder.call(join_func, [handle])
        else:
            run_func = None
            for f in self.module.functions:
                if f.name == "rt_scheduler_run":
                    run_func = f
                    break
            if run_func is not None:
                self.builder.call(run_func, [])

    def _emit_task_get_result(self, op: TaskGetResult) -> Optional[ir.Value]:
        """
        Join the task, take ownership of its result, then destroy the handle.

        Heap/reference results are stored directly in the handle with an extra
        retain taken by TaskComplete; taking the result transfers that owned
        reference to the destination local. Scalar results are stored in a
        runtime heap box, loaded here, and the box is freed immediately.

        """
        handle_i8 = self.emit_operand(op.task_handle)

        # Drive the scheduler until `handle` completes; safe to call even
        # if `handle` is already done.
        if self.async_codegen is not None:
            self.async_codegen.emit_task_join(self.builder, handle_i8)
        else:
            join_func = None
            for f in self.module.functions:
                if f.name == "rt_task_join":
                    join_func = f
                    break
            if join_func is not None:
                self.builder.call(join_func, [handle_i8])

        if self.async_codegen is not None:
            result_void_ptr = self.async_codegen.emit_task_take_result(self.builder, handle_i8)
        else:
            result_void_ptr = self.builder.call(
                next(f for f in self.module.functions if f.name == "rt_task_take_result"),
                [handle_i8],
                name="task.result",
            )

        result_llvm = self.type_cache.get_llvm_type(op.result_type)
        if isinstance(result_llvm, ir.VoidType):
            if self.async_codegen is not None:
                self.async_codegen.emit_task_destroy(self.builder, handle_i8)
            return None

        if self._type_needs_task_arc(op.result_type):
            if isinstance(result_llvm, ir.PointerType) and result_void_ptr.type != result_llvm:
                value = self.builder.bitcast(result_void_ptr, result_llvm, name="result.ref")
            else:
                value = result_void_ptr
        else:
            typed_ptr = self.builder.bitcast(
                result_void_ptr, ir.PointerType(result_llvm), name="result.box",
            )
            value = self.builder.load(typed_ptr, name="result.val")
            self.runtime.emit_free(self.builder, result_void_ptr)

        self._store_local(op.result, value)
        if self.async_codegen is not None:
            self.async_codegen.emit_task_destroy(self.builder, handle_i8)
        return value

    def _emit_task_yield(self, op: TaskYield) -> Optional[ir.Value]:
        """Emit task yield: rt_task_yield()."""
        if self.async_codegen is not None:
            self.async_codegen.emit_task_yield(self.builder)
        else:
            yield_func = None
            for f in self.module.functions:
                if f.name == "rt_task_yield":
                    yield_func = f
                    break
            if yield_func:
                self.builder.call(yield_func, [])
        return None

    def _emit_task_complete(self, op: TaskComplete) -> Optional[ir.Value]:
        """
        Emit task completion: `rt_task_complete(handle, result_ptr)`.

        The TaskHandle owns whatever is stored into its result slot:

        * No result (Void return)             -> NULL, no cleanup.
        * Heap/reference result               -> retain and store the object
          pointer directly; TaskGetResult transfers that owned reference.
        * Scalar result                       -> allocate a runtime box and
          store the scalar there; TaskGetResult frees the box after loading.
        """
        i8ptr = ir.IntType(8).as_pointer()
        handle = self.emit_operand(op.task_handle)

        if op.result is not None:
            result_val = self.emit_operand(op.result)
            result_type = operand_type(op.result)
            if result_type is not None and self._type_needs_task_arc(result_type):
                self.runtime.emit_obj_retain(self.builder, result_val)
                result_ptr = result_val
                result_kind = 2
            else:
                size = max(1, self.type_cache._get_type_size(result_type)) if result_type is not None else 8
                align = self.type_cache.get_type_alignment(result_type) if result_type is not None else 8
                result_ptr = self.runtime.emit_alloc(
                    self.builder,
                    ir.Constant(self.type_cache.i64, size),
                    ir.Constant(self.type_cache.i64, align),
                )
                typed_ptr = self.builder.bitcast(result_ptr, ir.PointerType(result_val.type), name="task_result.box")
                self.builder.store(result_val, typed_ptr)
                result_kind = 1
        else:
            result_ptr = ir.Constant(i8ptr, None)
            result_kind = 0

        if self.async_codegen is not None:
            self.async_codegen.emit_task_complete_owned(self.builder, handle, result_ptr, result_kind)
        else:
            complete_func = None
            for f in self.module.functions:
                if f.name == "rt_task_complete_owned":
                    complete_func = f
                    break
            if complete_func:
                self.builder.call(complete_func, [handle, result_ptr, ir.Constant(self.type_cache.i32, result_kind)])
        return None

    def _type_needs_task_arc(self, type_id) -> bool:
        """Return True if an async result is represented as a managed object ref."""
        if type_id is None:
            return False
        if self.type_table.is_heap_type(type_id):
            return True
        inner = self.type_table.get_optional_inner(type_id)
        return inner is not None and self.type_table.is_heap_type(inner)

    def _emit_suspend(self, op: Suspend) -> Optional[ir.Value]:
        """Emit suspend point: signal scheduler to yield and store state."""
        if self.async_codegen is not None:
            self.async_codegen.emit_task_yield(self.builder)
        else:
            yield_func = None
            for f in self.module.functions:
                if f.name == "rt_task_yield":
                    yield_func = f
                    break
            if yield_func:
                self.builder.call(yield_func, [])

        if op.result is not None:
            llvm_type = self.type_cache.get_llvm_type(op.result_type)
            result = ir.Constant(llvm_type, ir.Undefined)
            self._store_local(op.result, result)
            return result
        return None
