"""
rolang — the Rolang toolchain CLI.

Wraps project creation, building, testing, and dependency management on top of
the low-level rolangc compiler.

Commands
--------
  new <name>        Create a new project directory
  init [--lib]      Initialise a project in the current directory
  build [--release] Compile the project
  run [--release]   Build then execute the project binary
  test [filter]     Compile and run declared test targets
  check             Type-check without producing any output files
  clean             Remove the build directory
  install           Fetch and install all dependencies
  add <name>        Add a dependency to rolang.toml
  remove <name>     Remove a dependency from rolang.toml
  info              Display project metadata
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from .toolchain.build import build_project
from .toolchain.deps import install_deps
from .toolchain.errors import ToolchainError
from .toolchain.lockfile import LockFile
from .toolchain.manifest import (
    Dependency,
    GitDependency,
    Manifest,
    PathDependency,
    RegistryDependency,
    find_manifest_root,
    MANIFEST_FILENAME,
)
from .toolchain.templates import scaffold_binary, scaffold_library



# ── Internal helpers ──────────────────────────────────────────────────────────


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _load_manifest() -> Optional[Manifest]:
    root = find_manifest_root()
    if root is None:
        print(
            f"error: could not find {MANIFEST_FILENAME} in the current directory "
            "or any parent directory",
            file=sys.stderr,
        )
        return None
    return Manifest.load(root)


def _dep_summary(dep: Dependency) -> str:
    if isinstance(dep, PathDependency):
        return f"path:{dep.path}"
    if isinstance(dep, GitDependency):
        ref = dep.tag or dep.branch or dep.rev or "HEAD"
        return f"git:{dep.git} ({ref})"
    if isinstance(dep, RegistryDependency):
        return dep.version
    return repr(dep)


def _get_version() -> str:
    try:
        from rolang import __version__
        return __version__
    except Exception:
        return "0.1.0"


# ── Command implementations ───────────────────────────────────────────────────


def cmd_new(args: argparse.Namespace) -> int:
    name: str = args.name
    dest = Path.cwd() / name
    if dest.exists():
        return _err(f"directory '{name}' already exists")

    is_lib: bool = args.lib
    if is_lib:
        scaffold_library(dest, name)
        kind = "library"
        main_file = "src/lib.rl"
    else:
        scaffold_binary(dest, name)
        kind = "binary"
        main_file = "src/main.rl"

    print(f"Created {kind} project '{name}'")
    print(f"  {dest}/")
    print(f"  {dest}/rolang.toml")
    print(f"  {dest}/{main_file}")
    print(f"\nRun 'cd {name} && rolang build' to compile.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path.cwd()
    if (dest / MANIFEST_FILENAME).exists():
        return _err(f"{MANIFEST_FILENAME} already exists in the current directory")

    name = dest.name
    is_lib: bool = args.lib
    if is_lib:
        scaffold_library(dest, name)
        kind = "library"
    else:
        scaffold_binary(dest, name)
        kind = "binary"

    print(f"Initialized {kind} project '{name}' in {dest}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    pkg_name = manifest.package.name if manifest.package else "project"
    release: bool = args.release
    verbose: bool = args.verbose

    print(f"Compiling {pkg_name} v{manifest.package.version if manifest.package else '?'}...")

    try:
        result = build_project(manifest, release=release, verbose=verbose)
    except ToolchainError as exc:
        return _err(str(exc))

    if result.success:
        for out in result.outputs:
            print(f"  Compiled -> {out}")
        return 0
    else:
        for err in result.errors:
            print(err, file=sys.stderr)
        print("error: build failed", file=sys.stderr)
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    pkg_name = manifest.package.name if manifest.package else "project"
    release: bool = args.release
    verbose: bool = args.verbose
    bin_name: Optional[str] = args.bin

    # Filter trailing "--" that argparse.REMAINDER may include
    run_args: list[str] = [a for a in (args.args or []) if a != "--"]

    targets = [bin_name] if bin_name else None
    print(f"Compiling {pkg_name}...")

    try:
        result = build_project(
            manifest, release=release, verbose=verbose, targets=targets
        )
    except ToolchainError as exc:
        return _err(str(exc))

    if not result.success:
        for err in result.errors:
            print(err, file=sys.stderr)
        return 1

    if not result.outputs:
        return _err("no binary was produced")

    binary = result.outputs[0]
    if verbose:
        print(f"Running {binary}" + (f" {' '.join(run_args)}" if run_args else ""))
    proc = subprocess.run([str(binary)] + run_args)
    return proc.returncode


def cmd_test(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    verbose: bool = args.verbose
    filter_str: Optional[str] = args.filter

    targets = manifest.tests
    if not targets:
        print(
            "No test targets defined in rolang.toml.\n"
            "Add a [[test]] section with name and path to declare a test target."
        )
        return 0

    if filter_str:
        targets = [t for t in targets if filter_str in t.name]
        if not targets:
            print(f"No tests matching '{filter_str}'.")
            return 0

    passed = 0
    failed = 0

    for test in targets:
        print(f"test {test.name} ... ", end="", flush=True)
        try:
            result = build_project(manifest, verbose=verbose, targets=[test.name])
        except ToolchainError as exc:
            print("FAILED")
            print(f"  build error: {exc}", file=sys.stderr)
            failed += 1
            continue

        if not result.success:
            print("FAILED")
            for err in result.errors:
                print(f"  {err}", file=sys.stderr)
            failed += 1
            continue

        if result.outputs:
            binary = result.outputs[0]
            proc = subprocess.run([str(binary)], capture_output=not verbose)
            if proc.returncode == 0:
                print("ok")
                passed += 1
            else:
                print(f"FAILED (exit {proc.returncode})")
                if not verbose:
                    if proc.stdout:
                        sys.stdout.buffer.write(proc.stdout)
                    if proc.stderr:
                        sys.stderr.buffer.write(proc.stderr)
                failed += 1
        else:
            # check-only build succeeded
            print("ok")
            passed += 1

    total = passed + failed
    print(f"\ntest result: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} failed")
    else:
        print()
    return 0 if failed == 0 else 1


def cmd_check(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    verbose: bool = args.verbose
    pkg_name = manifest.package.name if manifest.package else "project"
    print(f"Checking {pkg_name}...")

    try:
        result = build_project(manifest, check_only=True, verbose=verbose)
    except ToolchainError as exc:
        return _err(str(exc))

    if result.success:
        print("    Finished — no errors.")
        return 0
    for err in result.errors:
        print(err, file=sys.stderr)
    return 1


def cmd_clean(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    out_dir = manifest.root / manifest.build.output_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
        print(f"Removed {out_dir}")
    else:
        print(f"Nothing to clean ('{out_dir}' does not exist).")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    dev: bool = args.dev
    verbose: bool = args.verbose

    all_deps = dict(manifest.dependencies)
    if dev:
        all_deps.update(manifest.dev_dependencies)

    if not all_deps:
        print("No dependencies to install.")
        return 0

    print(f"Installing {len(all_deps)} package(s)...")
    lock = LockFile.load(manifest.root)

    try:
        resolved = install_deps(manifest, lock, dev=dev, verbose=verbose)
    except ToolchainError as exc:
        return _err(str(exc))

    lock.save(manifest.root)
    for name, rdep in resolved.items():
        print(f"  {name} {rdep.version}  ({rdep.source})")
    print(f"Done. {len(resolved)} package(s) installed.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    name: str = args.dep_name
    is_dev: bool = args.dev
    path_str: Optional[str] = args.path
    git_url: Optional[str] = args.git
    tag: Optional[str] = args.tag
    branch: Optional[str] = args.branch
    rev: Optional[str] = args.rev
    version: Optional[str] = args.version

    if path_str is not None:
        dep: Dependency = PathDependency(path=path_str)
        display = f"path:{path_str}"
    elif git_url is not None:
        dep = GitDependency(git=git_url, tag=tag, branch=branch, rev=rev)
        ref = tag or branch or rev or "HEAD"
        display = f"git:{git_url} ({ref})"
    elif version is not None:
        dep = RegistryDependency(version=version)
        display = version
    else:
        return _err(
            "Specify a source for the dependency:\n"
            "  --path PATH      local path\n"
            "  --git URL        git repository\n"
            "  VERSION          registry version constraint (e.g. '^1.0')"
        )

    bucket = manifest.dev_dependencies if is_dev else manifest.dependencies
    action = "Updated" if name in bucket else "Added"
    bucket[name] = dep
    manifest.save(manifest.root)

    kind = "dev-dependency" if is_dev else "dependency"
    print(f"{action} {kind} '{name}' = {display}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    name: str = args.dep_name
    removed = False

    if name in manifest.dependencies:
        del manifest.dependencies[name]
        removed = True
    if name in manifest.dev_dependencies:
        del manifest.dev_dependencies[name]
        removed = True

    if not removed:
        return _err(
            f"Dependency '{name}' not found in {MANIFEST_FILENAME}."
        )

    manifest.save(manifest.root)
    lock = LockFile.load(manifest.root)
    lock.remove(name)
    lock.save(manifest.root)
    print(f"Removed dependency '{name}'.")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1

    p = manifest.package
    if p is None:
        print("Workspace manifest (no [package] section)")
        if manifest.workspace:
            print(f"Members: {', '.join(manifest.workspace.members)}")
        return 0

    print(f"Name:        {p.name}")
    print(f"Version:     {p.version}")
    print(f"Type:        {p.pkg_type}")
    if p.description:
        print(f"Description: {p.description}")
    if p.authors:
        print(f"Authors:     {', '.join(p.authors)}")
    print(f"Edition:     {p.edition}")
    print(f"Root:        {manifest.root}")

    bins = manifest.effective_bins()
    if bins:
        print("Binaries:")
        for b in bins:
            print(f"  {b.name}  ({b.path})")

    lib = manifest.effective_lib()
    if lib:
        print(f"Library:     {lib.name}  ({lib.path})")

    if manifest.tests:
        print("Tests:")
        for t in manifest.tests:
            print(f"  {t.name}  ({t.path})")

    n_rt = len(manifest.dependencies)
    n_dev = len(manifest.dev_dependencies)
    if n_rt or n_dev:
        print(f"Dependencies ({n_rt} runtime, {n_dev} dev):")
        for dep_name, dep in manifest.dependencies.items():
            print(f"  {dep_name}  {_dep_summary(dep)}")
        for dep_name, dep in manifest.dev_dependencies.items():
            print(f"  {dep_name} (dev)  {_dep_summary(dep)}")
    else:
        print("Dependencies: none")

    return 0


# ── Argument parser ───────────────────────────────────────────────────────────


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rolang",
        description="The Rolang toolchain — project management and build system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Use 'rolang <command> --help' for more information about a command.

Examples:
  rolang new my-app              Create a new binary project
  rolang new my-lib --lib        Create a new library project
  rolang build                   Compile the current project
  rolang run                     Build and run
  rolang add utils --path ../u   Add a local path dependency
""",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"rolang {_get_version()}",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # new ─────────────────────────────────────────────────────────────────────
    p_new = sub.add_parser("new", help="Create a new project in a new directory")
    p_new.add_argument("name", help="Project name (also used as the directory name)")
    p_new.add_argument("--lib", action="store_true", default=False,
                       help="Create a library project instead of a binary")

    # init ────────────────────────────────────────────────────────────────────
    p_init = sub.add_parser("init", help="Initialise a project in the current directory")
    p_init.add_argument("--lib", action="store_true", default=False,
                        help="Initialise as a library")

    # build ───────────────────────────────────────────────────────────────────
    p_build = sub.add_parser("build", help="Compile the project")
    p_build.add_argument("--release", action="store_true", default=False,
                         help="Build with optimizations (opt-level 2)")
    p_build.add_argument("-v", "--verbose", action="store_true", default=False)

    # run ─────────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Build and run the project binary")
    p_run.add_argument("--release", action="store_true", default=False)
    p_run.add_argument("--bin", metavar="NAME", default=None,
                       help="Run a specific binary target (for multi-binary projects)")
    p_run.add_argument("-v", "--verbose", action="store_true", default=False)
    p_run.add_argument("args", nargs=argparse.REMAINDER,
                       help="Extra arguments forwarded to the binary (after --)")

    # test ────────────────────────────────────────────────────────────────────
    p_test = sub.add_parser("test", help="Compile and run test targets")
    p_test.add_argument("filter", nargs="?", default=None,
                        help="Only run tests whose name contains this string")
    p_test.add_argument("-v", "--verbose", action="store_true", default=False)

    # check ───────────────────────────────────────────────────────────────────
    p_check = sub.add_parser("check", help="Type-check without producing output files")
    p_check.add_argument("-v", "--verbose", action="store_true", default=False)

    # clean ───────────────────────────────────────────────────────────────────
    sub.add_parser("clean", help="Remove the build directory")

    # install ─────────────────────────────────────────────────────────────────
    p_install = sub.add_parser("install",
                                help="Fetch and install all declared dependencies")
    p_install.add_argument("--dev", action="store_true", default=False,
                           help="Also install dev-dependencies")
    p_install.add_argument("-v", "--verbose", action="store_true", default=False)

    # add ─────────────────────────────────────────────────────────────────────
    p_add = sub.add_parser("add", help="Add a dependency to rolang.toml")
    p_add.add_argument("dep_name", metavar="NAME", help="Dependency name")
    p_add.add_argument("version", nargs="?", default=None,
                       help="Version constraint (registry dep, e.g. '^1.0')")
    p_add.add_argument("--path", metavar="PATH", default=None,
                       help="Local path dependency")
    p_add.add_argument("--git", metavar="URL", default=None,
                       help="Git repository URL")
    p_add.add_argument("--tag", metavar="TAG", default=None)
    p_add.add_argument("--branch", metavar="BRANCH", default=None)
    p_add.add_argument("--rev", metavar="REV", default=None)
    p_add.add_argument("--dev", action="store_true", default=False,
                       help="Add as a dev-dependency")

    # remove ──────────────────────────────────────────────────────────────────
    p_remove = sub.add_parser("remove", help="Remove a dependency from rolang.toml")
    p_remove.add_argument("dep_name", metavar="NAME")

    # info ────────────────────────────────────────────────────────────────────
    sub.add_parser("info", help="Display project metadata from rolang.toml")

    return parser


# ── Dispatch table ────────────────────────────────────────────────────────────

_COMMANDS = {
    "new": cmd_new,
    "init": cmd_init,
    "build": cmd_build,
    "run": cmd_run,
    "test": cmd_test,
    "check": cmd_check,
    "clean": cmd_clean,
    "install": cmd_install,
    "add": cmd_add,
    "remove": cmd_remove,
    "info": cmd_info,
}


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = create_parser()
    args = parser.parse_args(argv)

    try:
        fn = _COMMANDS[args.command]
        sys.exit(fn(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"error: internal toolchain error: {exc}", file=sys.stderr)
        if any(flag in (argv or sys.argv) for flag in ("-v", "--verbose")):
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
