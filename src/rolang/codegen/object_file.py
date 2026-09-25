"""
ObjectEmitter - Compile LLVM IR module to object file.

Uses llvmlite.binding to:
- Initialize LLVM target machinery
- Create target machine
- Compile module to object code
"""

from __future__ import annotations

from typing import List, Optional

from llvmlite import ir
from llvmlite import binding as llvm


# Track if LLVM has been initialized
_llvm_initialized = False


def _init_llvm() -> None:
    """Initialize LLVM target machinery (once).

    Must be called before using Target.from_triple() or creating target machines.
    """
    global _llvm_initialized
    if _llvm_initialized:
        return

    # Initialize the native target for code generation
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()

    _llvm_initialized = True


def get_host_triple() -> str:
    """Get the host target triple."""
    # Don't need to initialize - llvmlite handles this automatically
    return llvm.get_default_triple()


def _prepare_module(
    module: ir.Module,
    opt_level: int,
    target_triple: Optional[str],
):
    """Use one target, verifier, and optimization pipeline for every backend output."""
    _init_llvm()
    triple = target_triple or get_host_triple()
    target = llvm.Target.from_triple(triple)
    target_machine = target.create_target_machine(opt=opt_level, reloc="pic")
    module.triple = triple
    module.data_layout = str(target_machine.target_data)
    llvm_module = llvm.parse_assembly(str(module))
    llvm_module.verify()
    if opt_level > 0:
        try:
            with llvm.create_pipeline_tuning_options(speed_level=opt_level) as tuning:
                with llvm.create_pass_builder(target_machine, tuning) as builder:
                    with builder.getModulePassManager() as passes:
                        passes.run(llvm_module, builder)
            llvm_module.verify()
        except Exception as error:
            raise RuntimeError(
                f"LLVM optimization failed at opt_level={opt_level}: {error}"
            ) from error
    return llvm_module, target_machine


def compile_module_to_object(
    module: ir.Module,
    output_path: str,
    opt_level: int = 0,
    target_triple: Optional[str] = None,
) -> List[str]:
    """Verify, optimize, and write native object code; return any errors."""
    try:
        llvm_module, target_machine = _prepare_module(module, opt_level, target_triple)
        with llvm_module, target_machine:
            obj_code = target_machine.emit_object(llvm_module)
        with open(output_path, "wb") as output:
            output.write(obj_code)
        return []
    except Exception as error:
        return [f"Failed to emit object file: {error}"]


def compile_module_to_assembly(
    module: ir.Module,
    target_triple: Optional[str] = None,
    opt_level: int = 0,
) -> tuple[Optional[str], List[str]]:
    """Emit assembly with the same target and optimization settings as objects."""
    try:
        llvm_module, target_machine = _prepare_module(module, opt_level, target_triple)
        with llvm_module, target_machine:
            return target_machine.emit_assembly(llvm_module), []
    except Exception as error:
        return None, [f"Failed to emit assembly: {error}"]


def optimize_module_to_ir(
    module: ir.Module,
    opt_level: int = 0,
    target_triple: Optional[str] = None,
) -> tuple[Optional[str], List[str]]:
    """Emit the verified LLVM IR passed to native code generation."""
    try:
        llvm_module, target_machine = _prepare_module(module, opt_level, target_triple)
        with llvm_module, target_machine:
            return str(llvm_module), []
    except Exception as error:
        return None, [f"Failed to emit optimized LLVM IR: {error}"]


def get_llvm_ir(module: ir.Module) -> str:
    """Get the LLVM IR text from a module."""
    return str(module)


def verify_module(module: ir.Module) -> List[str]:
    """
    Verify an LLVM module for correctness.

    Returns list of error messages (empty if valid).
    """
    errors: List[str] = []

    try:
        # Don't call _init_llvm() as initialization is automatic
        llvm_ir = str(module)

        try:
            llvm_module = llvm.parse_assembly(llvm_ir)
            llvm_module.verify()
        except Exception as e:
            # Filter out deprecation warnings
            err_str = str(e)
            if "deprecated" not in err_str.lower():
                errors.append(err_str)

    except Exception as e:
        err_str = str(e)
        if "deprecated" not in err_str.lower():
            errors.append(f"Verification error: {e}")

    return errors
