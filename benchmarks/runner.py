#!/usr/bin/env python3
"""
Rolang performance benchmark runner.

Compares Rolang against C, Rust, Go, Java, Node.js, and Python on:
  - fib:        Recursive Fibonacci fib(35) — function call overhead
  - mandelbrot: Mandelbrot 500x500@1K iters — raw float math throughput

Each benchmark runs warmed-up and timed across multiple iterations.
"""

import os
import sys
import time
import subprocess
import statistics
import shutil
import argparse
import functools
import dataclasses
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable

BENCHMARKS_DIR = Path(__file__).resolve().parent
BUILD_DIR = BENCHMARKS_DIR / "build"
WARMUP_RUNS = 5
TIMED_RUNS = 15
TIMEOUT_FIB = 60
TIMEOUT_MANDEL = 300
TIMEOUT_JSON = 120
TIMEOUT_BINARY_TREES = 120
TIMEOUT_NBODY = 120
TIMEOUT_WORD_FREQ = 60

EXPECTED_FIB = "9227465"
# Memory/compute-heavy benchmarks (added Batch 2). Expected values are tied to the
# workload sizes baked into each source (binary_trees maxDepth=14, nbody N=1000/steps=20,
# word_freq V=2000/T=2M); change those and these must be re-captured.
EXPECTED_BINARY_TREES = "3222190"
EXPECTED_NBODY = "2446731634"
EXPECTED_WORD_FREQ = "5308871524000000"


@dataclass
class BenchmarkResult:
    name: str
    times: list[float]
    mem_model: str
    success: bool = True
    error: str = ""

    @property
    def min_time(self) -> float:
        return min(self.times) if self.times else float("inf")

    @property
    def mean_time(self) -> float:
        return statistics.mean(self.times) if self.times else float("inf")

    @property
    def median_time(self) -> float:
        return statistics.median(self.times) if self.times else float("inf")

    @property
    def stddev_time(self) -> float:
        return statistics.stdev(self.times) if len(self.times) > 1 else 0.0


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run_cmd(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, stdout="", stderr="timeout")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, -1, stdout="", stderr=str(e))


def time_runs(
    cmd: list[str],
    verify_output: Optional[str],
    timeout: int,
    runs: int,
    warmup: int,
) -> list[float]:
    """Run warmup + timed iterations. Returns list of elapsed seconds."""
    times: list[float] = []
    expected = verify_output  # per-language baseline discovered in warmup

    for i in range(warmup + runs):
        try:
            t0 = time.perf_counter()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            elapsed = time.perf_counter() - t0

            if result.returncode != 0:
                sys.stderr.write(f"      exit={result.returncode}\n")
                return []

            output = result.stdout.strip()
            if verify_output is not None:
                if expected is None and output:
                    expected = output
                elif expected is not None and output != expected:
                    sys.stderr.write(
                        f"      WARN: output mismatch: got '{output[:40]}' expected '{expected[:40]}'\n"
                    )

            if i >= warmup:
                times.append(elapsed)

        except subprocess.TimeoutExpired:
            sys.stderr.write(f"      TIMEOUT after {timeout}s\n")
            return []
        except Exception as e:
            sys.stderr.write(f"      ERROR: {e}\n")
            return []

    return times


# ── Compilation ──────────────────────────────────────────────────────

def compile_c(src: Path, out: Path) -> bool:
    cc = which("gcc") or which("clang")
    if not cc:
        return False
    r = run_cmd([cc, "-O3", "-march=native", "-o", str(out), str(src)], 30)
    return r.returncode == 0


def compile_rust(src: Path, out: Path) -> bool:
    if not which("rustc"):
        return False
    r = run_cmd(
        ["rustc", "-C", "opt-level=3", "-C", "target-cpu=native", "-o", str(out), str(src)],
        120,
    )
    return r.returncode == 0


def compile_go(src: Path, out: Path) -> bool:
    if not which("go"):
        return False
    r = run_cmd(["go", "build", "-ldflags=-s -w", "-o", str(out), str(src)], 60)
    return r.returncode == 0


def compile_rolang(src: Path, out: Path, opt: int = 3) -> bool:
    if not which("rolangc"):
        return False
    r = run_cmd(["rolangc", f"-O{opt}", "-o", str(out), str(src)], 60)
    return r.returncode == 0


def compile_java(src: Path, out_dir: Path) -> Optional[str]:
    if not which("javac"):
        return None
    r = run_cmd(["javac", str(src), "-d", str(out_dir)], 30)
    if r.returncode != 0:
        sys.stderr.write(f"      javac: {r.stderr[:200]}\n")
        return None
    return src.stem


# ── Language registry ────────────────────────────────────────────────

@dataclass
class Language:
    name: str
    ext: str
    compile: Optional[Callable] = None
    run_prefix: list[str] = field(default_factory=list)
    mem_model: str = ""

    def available(self) -> bool:
        if self.name == "Python":
            return bool(which("python3"))
        if self.name == "Node.js":
            return bool(which("node"))
        if self.name == "C":
            return bool(which("gcc") or which("clang"))
        if self.name == "Java":
            return bool(which("javac") and which("java"))
        return self.compile is not None


LANGS = [
    Language("C",       ".c",   compile_c,      [],                "none"),
    Language("Rust",    ".rs",  compile_rust,   [],                "none"),
    Language("Go",      ".go",  compile_go,     [],                "gc"),
    Language("Rolang",  ".rl",  compile_rolang, [],                "arc"),
    Language("Java",    ".java", compile_java,   ["java", "-cp"],   "jit"),
    Language("Node.js", ".js",  None,           ["node"],          "jit"),
    Language("Python",  ".py",  None,           ["python3"],       "jit"),
]

def select_langs(names):
    """Return the subset of LANGS whose names match `names` (case-insensitive).
    `names=None` returns all languages, preserving LANGS order."""
    if not names:
        return list(LANGS)
    wanted = {n.strip().lower() for n in names}
    return [l for l in LANGS if l.name.lower() in wanted]


def write_markdown(path, all_results, rolang_opt):
    lines = [f"## Benchmark results (Rolang -O{rolang_opt})", ""]
    for bench_name, results in all_results.items():
        lines.append(f"### {bench_name}")
        lines.append("")
        lines.append("| Lang | Min (ms) | Mean (ms) | Median (ms) | StdDev (ms) | Model |")
        lines.append("|------|---------:|----------:|------------:|------------:|-------|")
        for r in sorted([x for x in results if x.success], key=lambda r: r.min_time):
            lines.append(
                f"| {r.name} | {r.min_time*1000:.2f} | {r.mean_time*1000:.2f} | "
                f"{r.median_time*1000:.2f} | {r.stddev_time*1000:.2f} | {r.mem_model} |"
            )
        lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


BENCH_SOURCE_NAMES = {
    ("fib", "Java"): "Fib",
    ("mandelbrot", "Java"): "Mandelbrot",
    ("json_parse", "Java"): "JsonParse",
    ("binary_trees", "Java"): "BinaryTrees",
    ("nbody", "Java"): "Nbody",
    ("word_freq", "Java"): "WordFreq",
}


def run_benchmark(
    bench_name: str,
    verify_output: Optional[str],
    langs: Optional[list] = None,
    warmup: int = WARMUP_RUNS,
    runs: int = TIMED_RUNS,
) -> list[BenchmarkResult]:
    bench_dir = BENCHMARKS_DIR / bench_name
    build_dir = BUILD_DIR / bench_name
    build_dir.mkdir(parents=True, exist_ok=True)

    timeout = {
        "fib": TIMEOUT_FIB, "mandelbrot": TIMEOUT_MANDEL, "json_parse": TIMEOUT_JSON,
        "binary_trees": TIMEOUT_BINARY_TREES, "nbody": TIMEOUT_NBODY, "word_freq": TIMEOUT_WORD_FREQ,
    }[bench_name]
    results: list[BenchmarkResult] = []
    active_langs = langs if langs is not None else LANGS

    print(f"\n{'='*72}")
    print(f"  {bench_name.upper()}")
    print(f"  {warmup} warmup + {runs} timed runs, timeout={timeout}s")
    print(f"{'='*72}")

    for lang in active_langs:
        if not lang.available():
            continue

        src_name = BENCH_SOURCE_NAMES.get((bench_name, lang.name), bench_name)
        source = bench_dir / f"{src_name}{lang.ext}"
        if not source.exists():
            continue

        print(f"  {lang.name:12s} ...", end=" ", flush=True)

        # ── Compile ──
        if lang.compile:
            if lang.name == "Java":
                class_name = lang.compile(source, build_dir)
                if class_name is None:
                    print("COMPILE FAILED")
                    results.append(BenchmarkResult(lang.name, [], lang.mem_model, False, "compile"))
                    continue
                exe_cmd = [*lang.run_prefix, str(build_dir), class_name]
            else:
                exe = build_dir / bench_name
                if not lang.compile(source, exe):
                    print("COMPILE FAILED")
                    results.append(BenchmarkResult(lang.name, [], lang.mem_model, False, "compile"))
                    continue
                exe_cmd = [str(exe)]
        else:
            exe_cmd = [*lang.run_prefix, str(source)]

        # ── Run ──
        times = time_runs(exe_cmd, verify_output, timeout, runs, warmup)

        if times:
            result = BenchmarkResult(lang.name, times, lang.mem_model)
            results.append(result)
            print(f"min={result.min_time*1000:.1f}ms  mean={result.mean_time*1000:.1f}ms  "
                  f"±{result.stddev_time*1000:.1f}ms")
        else:
            results.append(BenchmarkResult(lang.name, [], lang.mem_model, False, "timeout/error"))
            print("FAILED")

    return results


def fmt(seconds: float) -> str:
    if seconds == float("inf"):
        return "   FAIL"
    if seconds < 0.001:
        return f"{seconds*1_000_000:6.0f}us"
    elif seconds < 1.0:
        return f"{seconds*1000:6.1f}ms"
    else:
        return f"{seconds:6.2f}s"


def print_results(results: list[BenchmarkResult], bench_name: str):
    if not results:
        return

    sorted_res = sorted(results, key=lambda r: r.min_time)

    print(f"\n{' ' * 2}{'─'*65}")
    print(f"  {bench_name:12s}  {'Min':>10s}  {'Mean':>10s}  {'Median':>10s}  "
          f"{'StdDev':>8s}  Model  vs best")
    print(f"  {'─'*12}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*5}  {'─'*8}")

    baseline = sorted_res[0].min_time if sorted_res[0].success else None

    for r in sorted_res:
        if not r.success:
            print(f"  {r.name:12s}  {'FAILED':>10s}")
            continue
        ratio = f"{r.min_time / baseline:5.1f}x" if baseline and baseline > 0 else ""
        print(f"  {r.name:12s}  {fmt(r.min_time):>10s}  {fmt(r.mean_time):>10s}  "
              f"{fmt(r.median_time):>10s}  {fmt(r.stddev_time):>8s}  "
              f"{r.mem_model:>5s}  {ratio}")


def main():
    parser = argparse.ArgumentParser(description="Rolang benchmark runner")
    parser.add_argument(
        "--langs",
        type=str,
        default=None,
        help="Comma-separated list of languages to run (e.g. C,Rolang). Default: all.",
    )
    parser.add_argument(
        "--rolang-opt",
        type=int,
        default=3,
        metavar="LEVEL",
        help="Rolang optimization level (0, 1, 2, 3). Default: 3.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        metavar="PATH",
        help="Append markdown results to this file.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=None,
        metavar="N",
        help=f"Number of warmup runs (default: {WARMUP_RUNS}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        metavar="N",
        help=f"Number of timed runs (default: {TIMED_RUNS}).",
    )
    args = parser.parse_args()

    # Resolve effective warmup/runs
    effective_warmup = args.warmup if args.warmup is not None else WARMUP_RUNS
    effective_runs = args.runs if args.runs is not None else TIMED_RUNS

    # Resolve language selection
    lang_names = [s.strip() for s in args.langs.split(",")] if args.langs else None
    selected_langs = select_langs(lang_names)

    # Build a new list with the Rolang compile patched, without mutating the shared LANGS
    rolang_opt = args.rolang_opt
    active_langs = [
        dataclasses.replace(l, compile=functools.partial(compile_rolang, opt=rolang_opt))
        if l.name == "Rolang" else l
        for l in selected_langs
    ]

    print("Rolang Benchmark Suite")
    print(f"  warmup={effective_warmup}, timed={effective_runs}")
    print(f"  rolang-opt={rolang_opt}")

    available = [l.name for l in active_langs if l.available()]
    print(f"  detected: {', '.join(available)}")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    all_results = {}

    for bench_name, verify in [
        ("fib", EXPECTED_FIB), ("mandelbrot", None), ("json_parse", None),
        ("binary_trees", EXPECTED_BINARY_TREES), ("nbody", EXPECTED_NBODY),
        ("word_freq", EXPECTED_WORD_FREQ),
    ]:
        results = run_benchmark(bench_name, verify, langs=active_langs, warmup=effective_warmup, runs=effective_runs)
        all_results[bench_name] = results
        print_results(results, bench_name)

    if args.out:
        write_markdown(args.out, all_results, rolang_opt)
        print(f"\nWrote {args.out}")

    # ── Summary plot ──
    print(f"\n{'='*72}")
    print("  SUMMARY  (min wall-clock, lower = better)")
    print(f"{'='*72}")

    for bench_name, results in all_results.items():
        success = [r for r in results if r.success]
        if not success:
            continue
        best = min(success, key=lambda r: r.min_time)
        print(f"\n  {bench_name} — baseline: {best.name} ({best.min_time*1000:.1f}ms)")
        max_ratio = max(r.min_time / best.min_time for r in success if best.min_time > 0)
        for r in sorted(success, key=lambda r: r.min_time):
            ratio = r.min_time / best.min_time if best.min_time > 0 else 0
            bar_len = max(1, int(ratio / max_ratio * 40)) if max_ratio > 0 else 1
            bar = "█" * bar_len
            print(f"    {r.name:12s} {ratio:5.1f}x  {bar}")
    print()


if __name__ == "__main__":
    main()
