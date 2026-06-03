"""Build orchestration: reads rolang.toml and drives CompilationDriver."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from ..diagnostics import create_formatter
from ..driver import CompilationDriver, CompileOptions, EmitKind, OptLevel
from .deps import build_include_paths, install_deps
from .errors import BuildError
from .lockfile import LockFile
from .manifest import BinTarget, LibTarget, Manifest, TestTarget


_OPT_MAP: dict[int, OptLevel] = {
    0: OptLevel.O0,
    1: OptLevel.O1,
    2: OptLevel.O2,
    3: OptLevel.O3,
}

Target = Union[BinTarget, LibTarget, TestTarget]


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class BuildResult:
    success: bool
    outputs: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── Public API ────────────────────────────────────────────────────────────────


def build_project(
    manifest: Manifest,
    *,
    release: bool = False,
    check_only: bool = False,
    verbose: bool = False,
    targets: Optional[list[str]] = None,
) -> BuildResult:
    """
    Build all (or the specified) targets in *manifest*.

    Parameters
    ----------
    manifest    Loaded Manifest object.
    release     Use opt-level 2 when the manifest specifies 0.
    check_only  Only type-check; do not produce any output files.
    verbose     Forward verbose flag to the compiler.
    targets     If given, only build the named targets (by target name).
    """
    root = manifest.root
    lock = LockFile.load(root)

    # Install / resolve dependencies
    include_paths: list[Path] = []
    if manifest.dependencies or (manifest.dev_dependencies and not check_only):
        if verbose:
            print("Resolving dependencies...")
        install_deps(manifest, lock, verbose=verbose)
        lock.save(root)
        include_paths = build_include_paths(root)
    else:
        include_paths = build_include_paths(root)

    opt_level = manifest.build.opt_level
    if release and opt_level == 0:
        opt_level = 2

    out_dir = root / manifest.build.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Select targets to build
    build_targets: list[Target]
    if targets:
        by_name: dict[str, Target] = {t.name: t for t in manifest.all_targets()}
        missing = [n for n in targets if n not in by_name]
        if missing:
            return BuildResult(
                success=False,
                errors=[f"Unknown target(s): {', '.join(missing)}"],
            )
        build_targets = [by_name[n] for n in targets]
    else:
        build_targets = list(manifest.effective_bins())
        if not build_targets:
            if lib := manifest.effective_lib():
                build_targets = [lib]

    if not build_targets:
        return BuildResult(
            success=False,
            errors=["No build targets found — add a [[bin]] or [lib] to rolang.toml"],
        )

    outputs: list[Path] = []
    errors: list[str] = []

    for target in build_targets:
        ok, out, errs = _build_target(
            root=root,
            target=target,
            out_dir=out_dir,
            opt_level=opt_level,
            include_paths=include_paths,
            check_only=check_only,
            verbose=verbose,
            target_triple=manifest.build.target,
        )
        if ok:
            if out is not None:
                outputs.append(out)
        else:
            errors.extend(errs)

    return BuildResult(success=not errors, outputs=outputs, errors=errors)


# ── Internal ──────────────────────────────────────────────────────────────────


def _build_target(
    root: Path,
    target: Target,
    out_dir: Path,
    opt_level: int,
    include_paths: list[Path],
    check_only: bool,
    verbose: bool,
    target_triple: Optional[str],
) -> tuple[bool, Optional[Path], list[str]]:
    """
    Compile a single target.

    Returns (success, output_path_or_None, error_strings).
    """
    src_path = root / target.path
    if not src_path.exists():
        return False, None, [f"Source file not found: {src_path}"]

    if check_only:
        emit = EmitKind.MIR
        output_path: Optional[Path] = None
    elif isinstance(target, LibTarget):
        emit = EmitKind.OBJECT
        output_path = out_dir / f"lib{target.name}.o"
    else:
        # BinTarget or TestTarget → full executable
        emit = EmitKind.EXECUTABLE
        output_path = out_dir / target.name

    opts = CompileOptions(
        emit=emit,
        opt_level=_OPT_MAP.get(opt_level, OptLevel.O0),
        output_path=output_path,
        target_triple=target_triple,
        include_paths=include_paths,
        verbose=verbose,
        use_color=True,
    )

    driver = CompilationDriver(opts)
    result = driver.compile_file(src_path)

    # Emit diagnostics to stderr (mirrors what rolangc does)
    if result.diagnostics:
        formatter = create_formatter()
        result.diagnostics.emit_all(formatter)

    if result.success:
        return True, result.output_path, []

    summary = (
        result.diagnostics.summary()
        if result.diagnostics
        else "compilation failed"
    )
    return False, None, [f"{target.name}: {summary}"]
