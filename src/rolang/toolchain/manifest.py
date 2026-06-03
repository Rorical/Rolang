"""Rolang project manifest (rolang.toml) parsing and serialization."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union, Any

MANIFEST_FILENAME = "rolang.toml"


# ── Dependency specs ──────────────────────────────────────────────────────────


@dataclass
class PathDependency:
    """A dependency resolved from a local filesystem path."""
    path: str


@dataclass
class GitDependency:
    """A dependency fetched from a Git repository."""
    git: str
    tag: Optional[str] = None
    branch: Optional[str] = None
    rev: Optional[str] = None


@dataclass
class RegistryDependency:
    """A dependency resolved from a package registry (version constraint)."""
    version: str
    registry: Optional[str] = None


Dependency = Union[PathDependency, GitDependency, RegistryDependency]


def _parse_dependency(name: str, value: Any) -> Dependency:
    if isinstance(value, str):
        return RegistryDependency(version=value)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid dependency spec for '{name}': {value!r}")
    if "path" in value:
        return PathDependency(path=value["path"])
    if "git" in value:
        return GitDependency(
            git=value["git"],
            tag=value.get("tag"),
            branch=value.get("branch"),
            rev=value.get("rev"),
        )
    if "version" in value:
        return RegistryDependency(
            version=value["version"],
            registry=value.get("registry"),
        )
    raise ValueError(f"Cannot parse dependency '{name}': {value!r}")


# ── Build targets ─────────────────────────────────────────────────────────────


@dataclass
class BinTarget:
    """A binary (executable) build target."""
    name: str
    path: str = "src/main.rl"


@dataclass
class LibTarget:
    """A library build target."""
    name: str
    path: str = "src/lib.rl"


@dataclass
class TestTarget:
    """A test build target (compiled to an executable and run)."""
    name: str
    path: str


# ── Configuration sections ────────────────────────────────────────────────────


@dataclass
class BuildConfig:
    opt_level: int = 0
    target: Optional[str] = None
    output_dir: str = "build"


@dataclass
class PackageMeta:
    name: str
    version: str = "0.1.0"
    description: str = ""
    authors: list[str] = field(default_factory=list)
    edition: str = "2024"
    pkg_type: str = "binary"  # "binary" or "library"


@dataclass
class WorkspaceConfig:
    members: list[str] = field(default_factory=list)


# ── Manifest ──────────────────────────────────────────────────────────────────


@dataclass
class Manifest:
    package: Optional[PackageMeta] = None
    workspace: Optional[WorkspaceConfig] = None
    dependencies: dict[str, Dependency] = field(default_factory=dict)
    dev_dependencies: dict[str, Dependency] = field(default_factory=dict)
    bins: list[BinTarget] = field(default_factory=list)
    lib: Optional[LibTarget] = None
    tests: list[TestTarget] = field(default_factory=list)
    build: BuildConfig = field(default_factory=BuildConfig)

    # Runtime attribute — not serialised
    _root: Optional[Path] = field(default=None, compare=False, repr=False)

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("Manifest root path is not set")
        return self._root

    # ── Loaders ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load manifest from a directory or a direct path to rolang.toml."""
        if path.is_dir():
            manifest_path = path / MANIFEST_FILENAME
        else:
            manifest_path = path
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No {MANIFEST_FILENAME} found at {manifest_path}"
            )
        with open(manifest_path, "rb") as fh:
            raw = tomllib.load(fh)
        return cls._from_dict(raw, manifest_path.parent)

    @classmethod
    def _from_dict(cls, data: dict, root: Path) -> "Manifest":
        package: Optional[PackageMeta] = None
        if "package" in data:
            p = data["package"]
            package = PackageMeta(
                name=p["name"],
                version=p.get("version", "0.1.0"),
                description=p.get("description", ""),
                authors=p.get("authors", []),
                edition=p.get("edition", "2024"),
                pkg_type=p.get("type", "binary"),
            )

        workspace: Optional[WorkspaceConfig] = None
        if "workspace" in data:
            workspace = WorkspaceConfig(
                members=data["workspace"].get("members", [])
            )

        deps: dict[str, Dependency] = {
            n: _parse_dependency(n, v)
            for n, v in data.get("dependencies", {}).items()
        }
        dev_deps: dict[str, Dependency] = {
            n: _parse_dependency(n, v)
            for n, v in data.get("dev-dependencies", {}).items()
        }

        bins = [
            BinTarget(name=b["name"], path=b.get("path", "src/main.rl"))
            for b in data.get("bin", [])
        ]

        lib_data = data.get("lib")
        lib: Optional[LibTarget] = None
        if lib_data:
            lib = LibTarget(
                name=lib_data.get("name", package.name if package else "lib"),
                path=lib_data.get("path", "src/lib.rl"),
            )

        tests = [
            TestTarget(name=t["name"], path=t["path"])
            for t in data.get("test", [])
        ]

        bd = data.get("build", {})
        build = BuildConfig(
            opt_level=bd.get("opt-level", 0),
            target=bd.get("target"),
            output_dir=bd.get("output-dir", "build"),
        )

        m = cls(
            package=package,
            workspace=workspace,
            dependencies=deps,
            dev_dependencies=dev_deps,
            bins=bins,
            lib=lib,
            tests=tests,
            build=build,
        )
        m._root = root
        return m

    # ── Effective targets ─────────────────────────────────────────────────────

    def effective_bins(self) -> list[BinTarget]:
        """Return explicitly declared bins, or infer a default binary target."""
        if self.bins:
            return self.bins
        if self.package and self.package.pkg_type == "binary":
            return [BinTarget(name=self.package.name, path="src/main.rl")]
        return []

    def effective_lib(self) -> Optional[LibTarget]:
        """Return explicit lib target, or infer a default library target."""
        if self.lib:
            return self.lib
        if self.package and self.package.pkg_type == "library":
            return LibTarget(name=self.package.name, path="src/lib.rl")
        return None

    def all_targets(self) -> list[BinTarget | LibTarget | TestTarget]:
        """All targets (bins + lib + tests) that can be named explicitly."""
        result: list[BinTarget | LibTarget | TestTarget] = []
        result.extend(self.effective_bins())
        if lib := self.effective_lib():
            result.append(lib)
        result.extend(self.tests)
        return result

    # ── Serialization ─────────────────────────────────────────────────────────

    def save(self, dest: Path) -> None:
        """Write rolang.toml to *dest* (a directory or explicit file path)."""
        if dest.is_dir():
            target = dest / MANIFEST_FILENAME
        else:
            target = dest
        target.write_text(self._to_toml(), encoding="utf-8")

    def _to_toml(self) -> str:
        parts: list[str] = []

        if self.workspace:
            parts.append("[workspace]")
            parts.append(f"members = {_toml_str_array(self.workspace.members)}")
            parts.append("")

        if self.package:
            p = self.package
            parts.append("[package]")
            parts.append(f'name = "{p.name}"')
            parts.append(f'version = "{p.version}"')
            parts.append(f'description = "{p.description}"')
            parts.append(f"authors = {_toml_str_array(p.authors)}")
            parts.append(f'edition = "{p.edition}"')
            parts.append(f'type = "{p.pkg_type}"')
            parts.append("")

        if self.dependencies:
            parts.append("[dependencies]")
            for name, dep in self.dependencies.items():
                parts.append(f"{name} = {_dep_value(dep)}")
            parts.append("")

        if self.dev_dependencies:
            parts.append("[dev-dependencies]")
            for name, dep in self.dev_dependencies.items():
                parts.append(f"{name} = {_dep_value(dep)}")
            parts.append("")

        has_non_default_build = (
            self.build.opt_level != 0
            or self.build.target is not None
            or self.build.output_dir != "build"
        )
        if has_non_default_build:
            parts.append("[build]")
            if self.build.opt_level != 0:
                parts.append(f"opt-level = {self.build.opt_level}")
            if self.build.target:
                parts.append(f'target = "{self.build.target}"')
            if self.build.output_dir != "build":
                parts.append(f'output-dir = "{self.build.output_dir}"')
            parts.append("")

        for b in self.bins:
            parts.append("[[bin]]")
            parts.append(f'name = "{b.name}"')
            parts.append(f'path = "{b.path}"')
            parts.append("")

        if self.lib and self.package and self.package.pkg_type == "library":
            parts.append("[lib]")
            parts.append(f'name = "{self.lib.name}"')
            parts.append(f'path = "{self.lib.path}"')
            parts.append("")

        for t in self.tests:
            parts.append("[[test]]")
            parts.append(f'name = "{t.name}"')
            parts.append(f'path = "{t.path}"')
            parts.append("")

        return "\n".join(parts)


# ── TOML serialization helpers ────────────────────────────────────────────────


def _toml_str_array(items: list[str]) -> str:
    if not items:
        return "[]"
    inner = ", ".join(f'"{x}"' for x in items)
    return f"[{inner}]"


def _dep_value(dep: Dependency) -> str:
    if isinstance(dep, RegistryDependency):
        if dep.registry is None:
            return f'"{dep.version}"'
        return f'{{ version = "{dep.version}", registry = "{dep.registry}" }}'
    elif isinstance(dep, PathDependency):
        return f'{{ path = "{dep.path}" }}'
    elif isinstance(dep, GitDependency):
        kv = [f'git = "{dep.git}"']
        if dep.tag:
            kv.append(f'tag = "{dep.tag}"')
        if dep.branch:
            kv.append(f'branch = "{dep.branch}"')
        if dep.rev:
            kv.append(f'rev = "{dep.rev}"')
        return "{ " + ", ".join(kv) + " }"
    raise TypeError(f"Unknown dependency type: {type(dep)!r}")


# ── Workspace discovery ───────────────────────────────────────────────────────


def find_manifest_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from *start* and return the nearest directory containing rolang.toml."""
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / MANIFEST_FILENAME).exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
