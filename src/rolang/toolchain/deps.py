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
    checksum: Optional[str] = None
    archive: Optional[str] = None
    dependencies: tuple[str, ...] = ()


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
        candidate = next(_registry_candidates(name, dep, project_root, lockfile), None)
        if candidate is None:
            raise DependencyError(f'No matching release for {name}: {dep.version}')
        return candidate
    raise DependencyError(f"Unknown dependency type for '{name}': {type(dep)!r}")


def _resolve_path(
    name: str,
    dep: PathDependency,
    project_root: Path,
) -> ResolvedDep:
    raw = Path(dep.path)
    resolved = (raw if raw.is_absolute() else project_root / raw).resolve()
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

    resolved = _resolve_graph(to_install, project_root, lockfile, verbose)
    # Keep installation/lock mutation after successful resolution of the graph.
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

    # Remove only generated symlinks for dependencies removed from the graph.
    for previous in lockfile.packages:
        if previous.name not in resolved:
            from .registry import validate_name
            validate_name(previous.name)
            for obsolete in (dd / previous.name, dd / f'{previous.name}.rl'):
                if obsolete.is_symlink():
                    obsolete.unlink()
    # Production installs retain dev pins for later test builds without
    # installing those packages into the production import path.
    keep_dev = set(manifest.dev_dependencies) if not dev else set()
    queue = list(keep_dev)
    while queue:
        pinned = lockfile.find(queue.pop())
        if pinned:
            for child in pinned.dependencies:
                if child not in keep_dev:
                    keep_dev.add(child)
                    queue.append(child)
    retained = [p for p in lockfile.packages if p.name in keep_dev and p.name not in resolved]
    lockfile.packages = [LockedPackage(
        name=rdep.name, version=rdep.version, source=rdep.source,
        checksum=rdep.checksum, archive=rdep.archive,
        dependencies=list(rdep.dependencies),
    ) for rdep in resolved.values()] + retained
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


class _Conflict(DependencyError):
    """An unsatisfied graph branch; allows trying another release."""


def _registry_candidates(name, dep, root, lockfile):
    from .registry import registry_url, releases, matches, fetch_package
    base = registry_url(dep.registry, root)
    source = 'registry:' + base
    locked = lockfile.find(name)
    tried = set()
    if locked and locked.source == source and matches(locked.version, dep.version) and locked.checksum and locked.archive:
        release = {'version': locked.version, 'sha256': locked.checksum, 'archive': locked.archive}
        path = fetch_package(name, release, base, cache_dir())
        tried.add(locked.version)
        yield ResolvedDep(name, locked.version, source, path, locked.checksum, locked.archive)
    for release in releases(name, base):
        if release['version'] in tried or release.get('yanked') or not matches(release['version'], dep.version):
            continue
        path = fetch_package(name, release, base, cache_dir())
        yield ResolvedDep(name, release['version'], source, path, release['sha256'], release['archive'])


def _resolve_graph(dependencies, root, lockfile, verbose):
    from .registry import validate_name, matches, registry_url
    attempts = 0

    def visit(pending, selected):
        nonlocal attempts
        attempts += 1
        if attempts > 10000 or len(selected) > 200:
            raise DependencyError('Dependency resolution limit exceeded')
        if not pending:
            return selected
        name, dep, parent, ancestors = pending[0]
        validate_name(name)
        if name in ancestors:
            raise _Conflict('Dependency cycle: ' + ' -> '.join((*ancestors, name)))
        existing = selected.get(name)
        if existing:
            if isinstance(dep, RegistryDependency):
                compatible = (existing.source == 'registry:' + registry_url(dep.registry, parent)
                              and matches(existing.version, dep.version))
            elif isinstance(dep, PathDependency):
                compatible = existing.local_path == (parent / dep.path).resolve()
            else:
                compatible = existing.source == _source_str(dep)
            if not compatible:
                raise _Conflict(f'Conflicting dependency requirements for {name}: selected {existing.version} from {existing.source}')
            return visit(pending[1:], selected)
        candidates = (_registry_candidates(name, dep, parent, lockfile)
                      if isinstance(dep, RegistryDependency)
                      else [resolve_dep(name, dep, parent, lockfile)])
        failure = _Conflict(f'No matching release for {name}: {dep}')
        for candidate in candidates:
            if verbose:
                print(f'  Resolving {name} {candidate.version}...', flush=True)
            child_manifest = Manifest.load(candidate.local_path)
            children = dict(child_manifest.dependencies)
            if isinstance(dep, RegistryDependency):
                base = registry_url(dep.registry, parent)
                for child_name, child in children.items():
                    if not isinstance(child, RegistryDependency):
                        raise DependencyError(f'Registry package {name} must use registry dependencies ({child_name})')
                    if child.registry is None:
                        children[child_name] = RegistryDependency(child.version, base)
            candidate.dependencies = tuple(sorted(children))
            following = [(n, d, candidate.local_path, (*ancestors, name)) for n, d in children.items()]
            try:
                return visit(following + pending[1:], {**selected, name: candidate})
            except _Conflict as exc:
                failure = exc
        raise failure

    try:
        return visit([(n, d, root, ()) for n, d in dependencies.items()], {})
    except RecursionError as exc:
        raise DependencyError('Dependency graph is too deep') from exc
