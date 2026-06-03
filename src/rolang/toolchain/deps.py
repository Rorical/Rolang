"""Dependency resolution, fetching, and installation."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import DependencyError
from .lockfile import LockFile, LockedPackage
from .manifest import (
    Dependency,
    GitDependency,
    Manifest,
    PathDependency,
    RegistryDependency,
)


# ── Cache / installation directories ─────────────────────────────────────────


def cache_dir() -> Path:
    """Return the global Rolang package cache, creating it if needed."""
    base = Path(
        os.environ.get("ROLANG_CACHE_DIR", str(Path.home() / ".rolang" / "cache"))
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def deps_dir(project_root: Path) -> Path:
    """Return the project-local installed-deps directory (.rolang/deps)."""
    d = project_root / ".rolang" / "deps"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Resolved dependency ───────────────────────────────────────────────────────


@dataclass
class ResolvedDep:
    """A dependency pinned to a concrete filesystem path."""
    name: str
    version: str
    source: str       # same format as LockedPackage.source
    local_path: Path  # root of the resolved package (contains rolang.toml)


# ── Per-kind resolution ───────────────────────────────────────────────────────


def resolve_dep(
    name: str,
    dep: Dependency,
    project_root: Path,
    lockfile: LockFile,
) -> ResolvedDep:
    """Resolve a single dependency to a concrete filesystem path."""
    if isinstance(dep, PathDependency):
        return _resolve_path(name, dep, project_root)
    if isinstance(dep, GitDependency):
        return _resolve_git(name, dep, project_root, lockfile)
    if isinstance(dep, RegistryDependency):
        raise DependencyError(
            f"Registry dependencies are not yet supported: "
            f"'{name} = \"{dep.version}\"'. "
            "Use --path or --git instead."
        )
    raise DependencyError(f"Unknown dependency type for '{name}': {type(dep)!r}")


def _resolve_path(
    name: str,
    dep: PathDependency,
    project_root: Path,
) -> ResolvedDep:
    raw = Path(dep.path)
    resolved = raw if raw.is_absolute() else (project_root / raw).resolve()
    if not resolved.exists():
        raise DependencyError(
            f"Path dependency '{name}' not found: {resolved}"
        )
    if not (resolved / "rolang.toml").exists():
        raise DependencyError(
            f"Path dependency '{name}' at {resolved} has no rolang.toml"
        )
    dep_manifest = Manifest.load(resolved)
    version = dep_manifest.package.version if dep_manifest.package else "0.0.0"
    return ResolvedDep(
        name=name,
        version=version,
        source=f"path:{dep.path}",
        local_path=resolved,
    )


def _git_cache_key(dep: GitDependency) -> str:
    ref = dep.rev or dep.tag or dep.branch or "HEAD"
    digest = hashlib.sha256(f"{dep.git}#{ref}".encode()).hexdigest()[:12]
    return digest


def _source_str(dep: GitDependency) -> str:
    base = f"git:{dep.git}"
    if dep.tag:
        return base + f"?tag={dep.tag}"
    if dep.branch:
        return base + f"?branch={dep.branch}"
    if dep.rev:
        return base + f"?rev={dep.rev}"
    return base


def _resolve_git(
    name: str,
    dep: GitDependency,
    project_root: Path,
    lockfile: LockFile,
) -> ResolvedDep:
    dest = cache_dir() / "git" / _git_cache_key(dep)

    if not dest.exists():
        _git_clone(name, dep, dest)
    elif dep.branch and not dep.rev and not dep.tag:
        # Branch reference — try to pull latest (best-effort, ignore failures)
        subprocess.run(
            ["git", "-C", str(dest), "pull", "--ff-only"],
            capture_output=True,
        )

    if not (dest / "rolang.toml").exists():
        raise DependencyError(
            f"Git dependency '{name}' ({dep.git}) has no rolang.toml at its root"
        )
    dep_manifest = Manifest.load(dest)
    version = dep_manifest.package.version if dep_manifest.package else "0.0.0"
    return ResolvedDep(
        name=name,
        version=version,
        source=_source_str(dep),
        local_path=dest,
    )


def _git_clone(name: str, dep: GitDependency, dest: Path) -> None:
    cmd = ["git", "clone", "--depth=1"]
    if dep.tag:
        cmd += ["--branch", dep.tag]
    elif dep.branch:
        cmd += ["--branch", dep.branch]
    cmd += [dep.git, str(dest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise DependencyError(
            f"Failed to clone git dependency '{name}' from {dep.git!r}:\n"
            + result.stderr.strip()
        )
    if dep.rev:
        result = subprocess.run(
            ["git", "-C", str(dest), "checkout", dep.rev],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise DependencyError(
                f"Failed to checkout rev '{dep.rev}' for '{name}':\n"
                + result.stderr.strip()
            )


# ── Installation ──────────────────────────────────────────────────────────────


def install_deps(
    manifest: Manifest,
    lockfile: LockFile,
    *,
    dev: bool = False,
    verbose: bool = False,
) -> dict[str, ResolvedDep]:
    """
    Resolve and install all dependencies declared in *manifest*.

    Populates .rolang/deps/<name>/ symlinks for each dep and updates *lockfile*.
    Returns a mapping of dep-name → ResolvedDep.
    """
    project_root = manifest.root
    to_install = dict(manifest.dependencies)
    if dev:
        to_install.update(manifest.dev_dependencies)

    resolved: dict[str, ResolvedDep] = {}
    for name, dep in to_install.items():
        if verbose:
            print(f"  Resolving {name}...", flush=True)
        rdep = resolve_dep(name, dep, project_root, lockfile)
        resolved[name] = rdep
        lockfile.upsert(
            LockedPackage(
                name=rdep.name,
                version=rdep.version,
                source=rdep.source,
            )
        )

    # Symlink each dep into .rolang/deps/<name>/ and create a .rl entry shim.
    #
    # After installation the following import styles all work:
    #
    #   import "mylib.rl"          -- file-path, resolves via include root
    #   import mylib               -- dotted, mylib -> mylib.rl
    #   import mylib.utils         -- dotted, mylib/utils.rl inside the package
    #   import "mylib/src/lib.rl"  -- explicit path inside the package tree
    #
    dd = deps_dir(project_root)
    for name, rdep in resolved.items():
        # Directory symlink: .rolang/deps/<name>/ -> package root
        link = dd / name
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            shutil.rmtree(link)
        link.symlink_to(rdep.local_path, target_is_directory=True)

        # Entry-point shim: .rolang/deps/<name>.rl -> <name>/<lib_path>
        # Enables  import mylib  and  import "mylib.rl"
        shim = dd / f"{name}.rl"
        if shim.is_symlink() or shim.exists():
            shim.unlink()
        try:
            dep_manifest = Manifest.load(rdep.local_path)
            lib_target = dep_manifest.effective_lib()
            if lib_target:
                # Relative symlink so it survives directory moves
                shim.symlink_to(Path(name) / lib_target.path)
                if verbose:
                    print(
                        f"  Installed {name} {rdep.version} -> {rdep.local_path}\n"
                        f"    import {name!r}  or  import \"{name}.rl\"  -> {lib_target.path}"
                    )
            else:
                if verbose:
                    print(f"  Installed {name} {rdep.version} -> {rdep.local_path}")
        except Exception:
            # Non-fatal: path imports still work even without the shim
            if verbose:
                print(f"  Installed {name} {rdep.version} -> {rdep.local_path}")

    return resolved


def build_include_paths(project_root: Path) -> list[Path]:
    """
    Return the list of -I include paths for the project's installed deps.

    After installation, all of these work from any source file:

      import mylib                  -- dotted: mylib -> mylib.rl (shim)
      import "mylib.rl"             -- file path: resolved via include root
      import mylib.utils            -- dotted: mylib/utils.rl inside the package
      import "mylib/src/lib.rl"     -- explicit path inside the package tree
    """
    dd = project_root / ".rolang" / "deps"
    if not dd.exists():
        return []
    return [dd]
