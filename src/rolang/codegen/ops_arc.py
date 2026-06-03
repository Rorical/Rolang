"""ARC (Automatic Reference Counting) LLVM codegen operations."""

from __future__ import annotations

from llvmlite import ir

from ..mir import (
    Retain, Release,
)


class OpsArcMixin:
    """Mixin for ARC operations: retain, release."""

    def _emit_retain(self, op: Retain) -> ir.Value:
        """Emit retain (increment refcount)."""
        ptr = self.emit_operand(op.operand)
        self.runtime.emit_obj_retain(self.builder, ptr)
        return ptr

    def _emit_release(self, op: Release) -> ir.Value:
        """Emit release (decrement refcount)."""
        ptr = self.emit_operand(op.operand)
        self.runtime.emit_obj_release(self.builder, ptr)
        return ptr
