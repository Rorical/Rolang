"""
Async Code Generation for the Rolang compiler.

This module generates LLVM IR for async runtime calls:
- Task spawning and joining
- Task completion and yielding
"""

from __future__ import annotations

from typing import Optional

from llvmlite import ir

from ..types import TypeTable
from .types import TypeLayoutCache


class AsyncCodegen:
    """
    Generates LLVM IR for async runtime calls.

    Handles:
    - Async runtime function declarations
    - Task spawn, join, complete, yield
    """

    def __init__(
        self,
        module: ir.Module,
        type_cache: TypeLayoutCache,
        type_table: TypeTable,
    ) -> None:
        self.module = module
        self.type_cache = type_cache
        self.type_table = type_table

        # Common types
        self.i8 = ir.IntType(8)
        self.i32 = ir.IntType(32)
        self.i64 = ir.IntType(64)
        self.void = ir.VoidType()
        self.ptr = ir.PointerType(self.i8)

        # TaskHandle LLVM type — kept in sync with the C struct in
        # rolang_rt.c:1327-1332. Used to GEP into `result` from
        # TaskGetResult lowering.
        #     { void* frame; void(*resume_fn)(void*); int32_t completed;
        #       int32_t result_kind; void* result; }
        self.task_handle_type = ir.LiteralStructType([
            self.ptr,       # frame
            self.ptr,       # resume_fn
            self.i32,       # completed
            self.i32,       # result_kind
            self.ptr,       # result
        ])

        self._declare_async_runtime()

    def _declare_async_runtime(self) -> None:
        """Declare or reuse async runtime functions."""
        def _get_or_declare(name, ret_type, param_types):
            for f in self.module.functions:
                if f.name == name:
                    return f
            func_type = ir.FunctionType(ret_type, param_types)
            return ir.Function(self.module, func_type, name=name)

        self.rt_task_spawn = _get_or_declare("rt_task_spawn", self.ptr, [self.ptr, self.ptr])
        self.rt_task_join = _get_or_declare("rt_task_join", self.ptr, [self.ptr])
        self.rt_task_complete = _get_or_declare("rt_task_complete", self.void, [self.ptr, self.ptr])
        self.rt_task_complete_owned = _get_or_declare("rt_task_complete_owned", self.void, [self.ptr, self.ptr, self.i32])
        self.rt_task_take_result = _get_or_declare("rt_task_take_result", self.ptr, [self.ptr])
        self.rt_task_destroy = _get_or_declare("rt_task_destroy", self.void, [self.ptr])
        self.rt_task_yield = _get_or_declare("rt_task_yield", self.void, [])
        self.rt_scheduler_run = _get_or_declare("rt_scheduler_run", self.void, [])
        self.rt_frame_alloc = _get_or_declare("rt_frame_alloc", self.ptr, [self.i64])
        self.rt_frame_free = _get_or_declare("rt_frame_free", self.void, [self.ptr])

    def emit_task_spawn(
        self,
        builder: ir.IRBuilder,
        resume_fn: ir.Function,
        frame_ptr: ir.Value,
    ) -> ir.Value:
        """Emit code to spawn an async task. Returns pointer to TaskHandle."""
        resume_ptr = builder.bitcast(resume_fn, self.ptr)
        return builder.call(self.rt_task_spawn, [resume_ptr, frame_ptr], name="task")

    def emit_task_join(
        self,
        builder: ir.IRBuilder,
        task_handle: ir.Value,
    ) -> ir.Value:
        """Emit code to join (await) a task. Returns result as void*."""
        return builder.call(self.rt_task_join, [task_handle], name="result")

    def emit_task_complete(
        self,
        builder: ir.IRBuilder,
        task_handle: ir.Value,
        result: Optional[ir.Value] = None,
    ) -> None:
        """Emit code to mark a task as complete."""
        result_ptr = result if result else ir.Constant(self.ptr, None)
        if not isinstance(result_ptr.type, ir.PointerType):
            result_ptr = builder.inttoptr(result_ptr, self.ptr)
        builder.call(self.rt_task_complete, [task_handle, result_ptr])

    def emit_task_complete_owned(
        self,
        builder: ir.IRBuilder,
        task_handle: ir.Value,
        result: ir.Value,
        result_kind: int,
    ) -> None:
        """Complete a task with a result owned by the TaskHandle."""
        if result.type != self.ptr:
            result = builder.bitcast(result, self.ptr)
        builder.call(
            self.rt_task_complete_owned,
            [task_handle, result, ir.Constant(self.i32, result_kind)],
        )

    def emit_task_take_result(self, builder: ir.IRBuilder, task_handle: ir.Value) -> ir.Value:
        """Take ownership of a completed task result and clear the handle slot."""
        return builder.call(self.rt_task_take_result, [task_handle], name="task.result")

    def emit_task_destroy(self, builder: ir.IRBuilder, task_handle: ir.Value) -> None:
        """Destroy a completed TaskHandle and release its frame/result owners."""
        builder.call(self.rt_task_destroy, [task_handle])

    def emit_task_yield(self, builder: ir.IRBuilder) -> None:
        """Emit code to yield control to the scheduler."""
        builder.call(self.rt_task_yield, [])
