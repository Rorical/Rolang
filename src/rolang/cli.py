"""
Command-line interface for the Rolang compiler.

Usage:
    rolangc <file.rl>                # Compile to executable
    rolangc -c <file.rl> -o out.o    # Compile to object
    rolangc --emit llvm <file.rl>    # Emit LLVM IR
    rolangc --emit mir <file.rl>     # Emit MIR (debug)
    rolangc -O0|-O1|-O2|-O3          # Optimization level
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .driver import (
    CompilationDriver,
    CompileOptions,
    CompileResult,
    EmitKind,
    OptLevel,
)
from .diagnostics import create_formatter


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="rolangc",
        description="Rolang compiler - compile .rl files to executables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  rolangc hello.rl              Compile hello.rl to ./hello
  rolangc hello.rl -o greet     Compile to ./greet
  rolangc -c hello.rl -o hi.o   Compile to object file
  rolangc --emit llvm hello.rl  Emit LLVM IR to stdout
  rolangc -O2 hello.rl          Compile with optimizations
""",
    )

    parser.add_argument(
        "source",
        metavar="FILE",
        type=str,
        help="Source file to compile (.rl)",
    )

    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        type=str,
        help="Output file path",
    )

    parser.add_argument(
        "-c", "--compile-only",
        action="store_true",
        help="Compile to object file only, don't link",
    )

    parser.add_argument(
        "--emit",
        choices=["llvm", "mir", "obj"],
        default=None,
        help="Emit intermediate representation (llvm=LLVM IR, mir=MIR, obj=object file)",
    )

    # Optimization levels
    opt_group = parser.add_mutually_exclusive_group()
    opt_group.add_argument(
        "-O0",
        action="store_const",
        const=OptLevel.O0,
        dest="opt_level",
        help="No optimization (default)",
    )
    opt_group.add_argument(
        "-O1",
        action="store_const",
        const=OptLevel.O1,
        dest="opt_level",
        help="Basic optimizations",
    )
    opt_group.add_argument(
        "-O2",
        action="store_const",
        const=OptLevel.O2,
        dest="opt_level",
        help="Standard optimizations",
    )
    opt_group.add_argument(
        "-O3",
        action="store_const",
        const=OptLevel.O3,
        dest="opt_level",
        help="Aggressive optimizations",
    )

    parser.add_argument(
        "--target",
        metavar="TRIPLE",
        type=str,
        help="Target triple (e.g., x86_64-unknown-linux-gnu)",
    )

    parser.add_argument(
        "--runtime",
        metavar="PATH",
        type=str,
        help="Path to runtime library (rolang_rt.c)",
    )

    parser.add_argument(
        "-I", "--include-path",
        metavar="PATH",
        type=str,
        action="append",
        default=[],
        dest="include_paths",
        help="Add include path for import resolution (repeatable)",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    return parser


def determine_emit_kind(args: argparse.Namespace) -> EmitKind:
    """Determine the output format from arguments."""
    if args.emit:
        emit_map = {
            "llvm": EmitKind.LLVM_IR,
            "mir": EmitKind.MIR,
            "obj": EmitKind.OBJECT,
        }
        return emit_map[args.emit]
    elif args.compile_only:
        return EmitKind.OBJECT
    else:
        return EmitKind.EXECUTABLE


def build_options(args: argparse.Namespace) -> CompileOptions:
    """Build CompileOptions from parsed arguments."""
    emit = determine_emit_kind(args)
    opt_level = args.opt_level if args.opt_level else OptLevel.O0

    output_path = None
    if args.output:
        output_path = Path(args.output)

    runtime_path = None
    if args.runtime:
        runtime_path = Path(args.runtime)

    use_color = None if not args.no_color else False

    return CompileOptions(
        emit=emit,
        opt_level=opt_level,
        output_path=output_path,
        target_triple=args.target,
        runtime_path=runtime_path,
        include_paths=[Path(p) for p in args.include_paths],
        verbose=args.verbose,
        use_color=use_color,
    )


def run_compiler(args: argparse.Namespace) -> int:
    """
    Run the compiler with parsed arguments.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    source_path = Path(args.source)

    # Validate source file
    if not source_path.exists():
        print(f"error: file not found: {source_path}", file=sys.stderr)
        return 1

    if not source_path.suffix == ".rl":
        print(f"warning: expected .rl file, got {source_path.suffix}", file=sys.stderr)

    # Build options and compile
    options = build_options(args)
    driver = CompilationDriver(options)
    result = driver.compile_file(source_path)

    # Create formatter for diagnostics
    use_color = None if not args.no_color else False
    formatter = create_formatter(use_color)

    # Emit diagnostics
    if result.diagnostics:
        result.diagnostics.emit_all(formatter)

        if result.diagnostics.has_errors():
            summary = result.diagnostics.summary()
            print(f"error: aborting due to {summary}", file=sys.stderr)
            return 1

    # Handle output
    if result.success:
        if options.emit in (EmitKind.LLVM_IR, EmitKind.MIR):
            # Print IR to stdout unless -o was specified
            if not args.output and result.output_content:
                print(result.output_content)
            elif result.output_path:
                if options.verbose:
                    print(f"Wrote {result.output_path}")
        elif result.output_path:
            if options.verbose:
                print(f"Compiled to {result.output_path}")

    return 0 if result.success else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Main entry point for the CLI.

    Args:
        argv: Command-line arguments (uses sys.argv if None)

    Returns:
        Exit code
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    try:
        return run_compiler(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"error: internal compiler error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            print(
                "note: re-run with --verbose to see the Python traceback, and "
                "please report this at https://github.com/anomalyco/rolang/issues",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
