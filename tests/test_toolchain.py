"""Tests for the Rolang toolchain (project and package management)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rolang.toolchain.lockfile import LockFile, LockedPackage
from rolang.toolchain.manifest import (
    GitDependency,
    Manifest,
    PackageMeta,
    PathDependency,
    RegistryDependency,
    find_manifest_root,
    MANIFEST_FILENAME,
)
from rolang.toolchain.templates import scaffold_binary, scaffold_library


# ── Manifest round-trip ───────────────────────────────────────────────────────


def test_manifest_load_binary(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "hello"
version = "1.2.3"
description = "A greeting"
authors = ["Alice <alice@example.com>"]
edition = "2024"
type = "binary"
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    assert m.package is not None
    assert m.package.name == "hello"
    assert m.package.version == "1.2.3"
    assert m.package.description == "A greeting"
    assert m.package.authors == ["Alice <alice@example.com>"]
    assert m.package.pkg_type == "binary"
    assert m.root == tmp_path


def test_manifest_load_library(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "mylib"
version = "0.3.0"
type = "library"
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    assert m.package is not None
    assert m.package.pkg_type == "library"
    lib = m.effective_lib()
    assert lib is not None
    assert lib.name == "mylib"
    assert lib.path == "src/lib.rl"


def test_manifest_effective_bins_default(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\ntype = "binary"\n',
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    bins = m.effective_bins()
    assert len(bins) == 1
    assert bins[0].name == "app"
    assert bins[0].path == "src/main.rl"


def test_manifest_explicit_bin(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "multi"
version = "0.1.0"
type = "binary"

[[bin]]
name = "cli"
path = "src/cli.rl"

[[bin]]
name = "daemon"
path = "src/daemon.rl"
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    bins = m.effective_bins()
    assert [b.name for b in bins] == ["cli", "daemon"]


def test_manifest_dependencies_path(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "app"
version = "0.1.0"
type = "binary"

[dependencies]
utils = { path = "../utils" }
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    assert "utils" in m.dependencies
    dep = m.dependencies["utils"]
    assert isinstance(dep, PathDependency)
    assert dep.path == "../utils"


def test_manifest_dependencies_git(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "app"
version = "0.1.0"
type = "binary"

[dependencies]
mylib = { git = "https://example.com/org/mylib.git", tag = "v1.0.0" }
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    dep = m.dependencies["mylib"]
    assert isinstance(dep, GitDependency)
    assert dep.git == "https://example.com/org/mylib.git"
    assert dep.tag == "v1.0.0"


def test_manifest_dependencies_registry(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "app"
version = "0.1.0"
type = "binary"

[dependencies]
awesome = "^2.3"
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    dep = m.dependencies["awesome"]
    assert isinstance(dep, RegistryDependency)
    assert dep.version == "^2.3"


def test_manifest_save_and_reload(tmp_path: Path) -> None:
    m = Manifest(
        package=PackageMeta(
            name="roundtrip",
            version="0.5.0",
            description="Test",
            authors=["Bob"],
            pkg_type="binary",
        ),
        dependencies={"utils": PathDependency(path="../utils")},
    )
    m._root = tmp_path
    m.save(tmp_path)

    assert (tmp_path / MANIFEST_FILENAME).exists()
    m2 = Manifest.load(tmp_path)
    assert m2.package is not None
    assert m2.package.name == "roundtrip"
    assert m2.package.version == "0.5.0"
    assert "utils" in m2.dependencies
    assert isinstance(m2.dependencies["utils"], PathDependency)


def test_manifest_tests_section(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "app"
version = "0.1.0"
type = "binary"

[[test]]
name = "integration"
path = "tests/integration.rl"
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    assert len(m.tests) == 1
    assert m.tests[0].name == "integration"
    assert m.tests[0].path == "tests/integration.rl"


def test_manifest_workspace(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[workspace]
members = ["core", "utils"]
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    assert m.workspace is not None
    assert m.workspace.members == ["core", "utils"]


def test_manifest_build_config(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        """
[package]
name = "fast"
version = "0.1.0"
type = "binary"

[build]
opt-level = 2
output-dir = "dist"
""",
        encoding="utf-8",
    )
    m = Manifest.load(tmp_path)
    assert m.build.opt_level == 2
    assert m.build.output_dir == "dist"


def test_manifest_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Manifest.load(tmp_path / "nonexistent")


# ── find_manifest_root ────────────────────────────────────────────────────────


def test_find_manifest_root_direct(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        '[package]\nname="x"\nversion="0.1.0"\ntype="binary"\n'
    )
    assert find_manifest_root(tmp_path) == tmp_path


def test_find_manifest_root_nested(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        '[package]\nname="x"\nversion="0.1.0"\ntype="binary"\n'
    )
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_manifest_root(nested) == tmp_path


def test_find_manifest_root_not_found(tmp_path: Path) -> None:
    # tmp_path itself has no manifest and neither do its parents (up to /)
    # Use a path where we know no rolang.toml exists
    assert find_manifest_root(tmp_path) is None


# ── LockFile ──────────────────────────────────────────────────────────────────


def test_lockfile_empty(tmp_path: Path) -> None:
    lock = LockFile.load(tmp_path)
    assert lock.packages == []


def test_lockfile_upsert_and_find(tmp_path: Path) -> None:
    lock = LockFile()
    lock.upsert(LockedPackage(name="utils", version="1.0.0", source="path:../utils"))
    assert lock.find("utils") is not None
    assert lock.find("utils").version == "1.0.0"  # type: ignore
    assert lock.find("missing") is None


def test_lockfile_remove(tmp_path: Path) -> None:
    lock = LockFile()
    lock.upsert(LockedPackage(name="foo", version="1.0.0", source="path:../foo"))
    assert lock.remove("foo") is True
    assert lock.remove("foo") is False
    assert lock.find("foo") is None


def test_lockfile_save_and_reload(tmp_path: Path) -> None:
    lock = LockFile()
    lock.upsert(LockedPackage(name="mylib", version="2.3.0", source="path:../mylib"))
    lock.save(tmp_path)
    assert (tmp_path / "rolang.lock").exists()

    lock2 = LockFile.load(tmp_path)
    assert len(lock2.packages) == 1
    assert lock2.packages[0].name == "mylib"
    assert lock2.packages[0].version == "2.3.0"


# ── Templates / scaffolding ───────────────────────────────────────────────────


def test_scaffold_binary(tmp_path: Path) -> None:
    dest = tmp_path / "my-app"
    scaffold_binary(dest, "my-app")
    assert (dest / "rolang.toml").exists()
    assert (dest / "src" / "main.rl").exists()
    assert (dest / ".gitignore").exists()

    m = Manifest.load(dest)
    assert m.package is not None
    assert m.package.name == "my-app"
    assert m.package.pkg_type == "binary"


def test_scaffold_library(tmp_path: Path) -> None:
    dest = tmp_path / "my-lib"
    scaffold_library(dest, "my-lib")
    assert (dest / "rolang.toml").exists()
    assert (dest / "src" / "lib.rl").exists()

    m = Manifest.load(dest)
    assert m.package is not None
    assert m.package.name == "my-lib"
    assert m.package.pkg_type == "library"


# ── CLI smoke tests ───────────────────────────────────────────────────────────


def _run_rolang(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rolang.toolchain_cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def test_cli_version() -> None:
    result = _run_rolang("--version")
    assert result.returncode == 0
    assert "rolang" in result.stdout


def test_cli_new_binary(tmp_path: Path) -> None:
    result = _run_rolang("new", "my-project", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "my-project" / "rolang.toml").exists()
    assert (tmp_path / "my-project" / "src" / "main.rl").exists()


def test_cli_new_library(tmp_path: Path) -> None:
    result = _run_rolang("new", "my-lib", "--lib", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "my-lib" / "rolang.toml").exists()
    assert (tmp_path / "my-lib" / "src" / "lib.rl").exists()


def test_cli_new_duplicate_fails(tmp_path: Path) -> None:
    _run_rolang("new", "dup", cwd=tmp_path)
    result = _run_rolang("new", "dup", cwd=tmp_path)
    assert result.returncode != 0


def test_cli_init(tmp_path: Path) -> None:
    result = _run_rolang("init", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "rolang.toml").exists()


def test_cli_init_twice_fails(tmp_path: Path) -> None:
    _run_rolang("init", cwd=tmp_path)
    result = _run_rolang("init", cwd=tmp_path)
    assert result.returncode != 0


def test_cli_info(tmp_path: Path) -> None:
    _run_rolang("new", "demo-info", cwd=tmp_path)
    proj = tmp_path / "demo-info"
    result = _run_rolang("info", cwd=proj)
    assert result.returncode == 0, result.stderr
    assert "demo-info" in result.stdout


def test_cli_add_path_dependency(tmp_path: Path) -> None:
    # Create a dep library
    dep_path = tmp_path / "mylib"
    scaffold_library(dep_path, "mylib")

    # Create app
    _run_rolang("new", "app", cwd=tmp_path)
    app_path = tmp_path / "app"

    result = _run_rolang(
        "add", "mylib", "--path", str(dep_path), cwd=app_path
    )
    assert result.returncode == 0, result.stderr

    m = Manifest.load(app_path)
    assert "mylib" in m.dependencies
    assert isinstance(m.dependencies["mylib"], PathDependency)


def test_cli_add_then_remove(tmp_path: Path) -> None:
    dep_path = tmp_path / "deplib"
    scaffold_library(dep_path, "deplib")

    _run_rolang("new", "proj", cwd=tmp_path)
    proj = tmp_path / "proj"

    _run_rolang("add", "deplib", "--path", str(dep_path), cwd=proj)
    result = _run_rolang("remove", "deplib", cwd=proj)
    assert result.returncode == 0, result.stderr

    m = Manifest.load(proj)
    assert "deplib" not in m.dependencies


def test_cli_remove_nonexistent(tmp_path: Path) -> None:
    _run_rolang("new", "app2", cwd=tmp_path)
    result = _run_rolang("remove", "phantom", cwd=tmp_path / "app2")
    assert result.returncode != 0


def test_cli_clean(tmp_path: Path) -> None:
    _run_rolang("new", "cleanme", cwd=tmp_path)
    proj = tmp_path / "cleanme"
    build_dir = proj / "build"
    build_dir.mkdir()
    result = _run_rolang("clean", cwd=proj)
    assert result.returncode == 0, result.stderr
    assert not build_dir.exists()


def test_cli_clean_nothing_to_clean(tmp_path: Path) -> None:
    _run_rolang("new", "emptyproj", cwd=tmp_path)
    result = _run_rolang("clean", cwd=tmp_path / "emptyproj")
    assert result.returncode == 0


@pytest.mark.skip(reason="requires full compiler + LLVM — run separately")
def test_cli_build_hello_world(tmp_path: Path) -> None:
    _run_rolang("new", "helloworld", cwd=tmp_path)
    proj = tmp_path / "helloworld"
    result = _run_rolang("build", cwd=proj)
    assert result.returncode == 0, result.stderr
    assert (proj / "build" / "helloworld").exists()


@pytest.mark.skip(reason="requires full compiler + LLVM — run separately")
def test_cli_run_hello_world(tmp_path: Path) -> None:
    _run_rolang("new", "runme", cwd=tmp_path)
    proj = tmp_path / "runme"
    result = _run_rolang("run", cwd=proj)
    assert result.returncode == 0, result.stderr
    assert "Hello, World!" in result.stdout
