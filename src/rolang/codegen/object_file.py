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


def compile_module_to_object(
    module: ir.Module,
    output_path: str,
    opt_level: int = 0,
    target_triple: Optional[str] = None,
) -> List[str]:
    """
    Compile an LLVM IR module to an object file.

    Args:
        module: The LLVM IR module to compile
        output_path: Path for the output .o file
        opt_level: Optimization level (0-3)
        target_triple: Target triple (if None, uses host)

    Returns:
        List of error messages (empty if successful)
    """
    errors: List[str] = []

    try:
        _init_llvm()

        # Get target triple early so we can stamp it on the module before
        # serializing, ensuring the parsed IR already carries the right info.
        triple = target_triple or get_host_triple()

        try:
            target = llvm.Target.from_triple(triple)
        except Exception as e:
            errors.append(f"Failed to get target for triple '{triple}': {e}")
            return errors

        # Create target machine before serializing so we can obtain the real
        # data layout string and stamp it on the ir.Module.
        try:
            target_machine = target.create_target_machine(
                opt=opt_level,
                reloc='pic',  # Position-independent code
            )
        except Exception as e:
            errors.append(f"Failed to create target machine: {e}")
            return errors

        # Stamp the module with the target's data layout + triple so LLVM
        # optimizes against real alignment/pointer/struct-packing info.
        module.triple = triple
        module.data_layout = str(target_machine.target_data)

        # Get LLVM assembly string (now includes correct data layout + triple)
        llvm_ir = str(module)

        # Parse the LLVM IR
        try:
            llvm_module = llvm.parse_assembly(llvm_ir)
        except Exception as e:
            errors.append(f"Failed to parse LLVM IR: {e}")
            return errors

        # Verify the module
        try:
            llvm_module.verify()
        except Exception as e:
            errors.append(f"LLVM module verification failed: {e}")
            return errors

        # Optionally run optimization passes using the new LLVM pass manager.
        # add_analysis_passes is called implicitly by the PassBuilder when a
        # target machine is provided, giving passes target-specific cost models.
        if opt_level > 0:
            try:
                pto = llvm.create_pipeline_tuning_options(speed_level=opt_level)
                pb = llvm.create_pass_builder(target_machine, pto)
                pm = pb.getModulePassManager()
                pm.run(llvm_module, pb)
            except Exception as e:
                # A broken optimizer must be loud — never silently ship -O0 code
                # under an -O2/-O3 flag.
                errors.append(f"LLVM optimization failed at opt_level={opt_level}: {e}")
                return errors

        # Emit object code
        try:
            obj_code = target_machine.emit_object(llvm_module)
        except Exception as e:
            errors.append(f"Failed to emit object code: {e}")
            return errors

        # Write to file
        try:
            with open(output_path, 'wb') as f:
                f.write(obj_code)
        except Exception as e:
            errors.append(f"Failed to write object file: {e}")
            return errors

    except Exception as e:
        errors.append(f"Unexpected error during compilation: {e}")

    return errors


def compile_module_to_assembly(
    module: ir.Module,
    target_triple: Optional[str] = None,
) -> tuple[Optional[str], List[str]]:
    """
    Compile an LLVM IR module to assembly text.

    Args:
        module: The LLVM IR module to compile
        target_triple: Target triple (if None, uses host)

    Returns:
        Tuple of (assembly_string, errors)
    """
    errors: List[str] = []

    try:
        _init_llvm()

        # Get LLVM assembly string
        llvm_ir = str(module)

        # Parse the LLVM IR
        try:
            llvm_module = llvm.parse_assembly(llvm_ir)
        except Exception as e:
            errors.append(f"Failed to parse LLVM IR: {e}")
            return None, errors

        # Verify the module
        try:
            llvm_module.verify()
        except Exception as e:
            errors.append(f"LLVM module verification failed: {e}")
            return None, errors

        # Get target
        triple = target_triple or get_host_triple()

        try:
            target = llvm.Target.from_triple(triple)
        except Exception as e:
            errors.append(f"Failed to get target for triple '{triple}': {e}")
            return None, errors

        # Create target machine
        try:
            target_machine = target.create_target_machine()
        except Exception as e:
            errors.append(f"Failed to create target machine: {e}")
            return None, errors

        # Emit assembly
        try:
            asm = target_machine.emit_assembly(llvm_module)
            return asm, errors
        except Exception as e:
            errors.append(f"Failed to emit assembly: {e}")
            return None, errors

    except Exception as e:
        errors.append(f"Unexpected error: {e}")
        return None, errors


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
