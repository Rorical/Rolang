"""Rolang toolchain — project and package management."""

from .manifest import (
    Manifest,
    PackageMeta,
    WorkspaceConfig,
    BinTarget,
    LibTarget,
    TestTarget,
    BuildConfig,
    PathDependency,
    GitDependency,
    RegistryDependency,
    Dependency,
    find_manifest_root,
    MANIFEST_FILENAME,
)
from .lockfile import LockFile, LockedPackage, LOCK_FILENAME
from .errors import (
    ToolchainError,
    ManifestError,
    DependencyError,
    BuildError,
    WorkspaceError,
)
from .workspace import Workspace
from .build import build_project, BuildResult
from .deps import install_deps, resolve_dep, ResolvedDep, build_include_paths

__all__ = [
    # Manifest
    "Manifest",
    "PackageMeta",
    "WorkspaceConfig",
    "BinTarget",
    "LibTarget",
    "TestTarget",
    "BuildConfig",
    "PathDependency",
    "GitDependency",
    "RegistryDependency",
    "Dependency",
    "find_manifest_root",
    "MANIFEST_FILENAME",
    # Lock file
    "LockFile",
    "LockedPackage",
    "LOCK_FILENAME",
    # Errors
    "ToolchainError",
    "ManifestError",
    "DependencyError",
    "BuildError",
    "WorkspaceError",
    # Workspace
    "Workspace",
    # Build
    "build_project",
    "BuildResult",
    # Deps
    "install_deps",
    "resolve_dep",
    "ResolvedDep",
    "build_include_paths",
]
