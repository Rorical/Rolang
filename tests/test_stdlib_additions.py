"""End-to-end tests for the new stdlib + runtime additions:

  - process.rl  : argv/env/exec/stdin/exit
  - path.rl     : path manipulation + filesystem queries
  - panic.rl    : panic with custom message
  - set.rl      : generic Set<T>
  - fmt.rl      : multi-arg formatting (with_* chain)
  - string.rl   : split/lines/to_f64
  - bytestring.rl : pure Rolang Vec<u8>-backed byte strings
  - iter.rl     : Range / CharIter / vec_indices / dict_indices
  - linked_list.rl : generic singly linked list

These cover the runtime externs added to rolang_rt.c specifically to
unblock self-host work: argv/argc, env_get/set, system, exit, stdin,
panic_msg, path operations, dir listing, multi-arg fmt, str_split,
str_lines, str_to_f64.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from rolang.driver import CompileOptions, EmitKind, compile_source


def _stdlib_path() -> list[Path]:
    return [Path(__file__).parent.parent / "src" / "rolang" / "std"]


def _compile_and_run(
    tmp_path: Path,
    name: str,
    source: str,
    *,
    args: list[str] | None = None,
    stdin_data: str | None = None,
    expected_exit: int | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    source_path = tmp_path / f"{name}.rl"
    output_path = tmp_path / name
    source_path.write_text(source, encoding="utf-8")

    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=output_path,
            include_paths=_stdlib_path(),
        ),
    )
    if not result.success:
        msgs = "\n".join(d.message for d in result.diagnostics.diagnostics)
        raise AssertionError(f"compile failed:\n{msgs}")

    cmd = [str(output_path)] + (args or [])
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    completed = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    if expected_exit is not None:
        assert completed.returncode == expected_exit, (
            f"want exit {expected_exit} got {completed.returncode}\n"
            f"stdout: {completed.stdout!r}\nstderr: {completed.stderr!r}"
        )
    return completed.returncode, completed.stdout, completed.stderr


def test_bytestring_pure_rolang_ops(tmp_path: Path) -> None:
    code, out, err = _compile_and_run(
        tmp_path,
        "bytestring_ops",
        """
import "bytestring.rl"

def make_abc() -> ByteString {
    var s = ByteString.new();
    s.push(97 as u8);
    s.push(98 as u8);
    s.push(99 as u8);
    return s;
}

def make_bc() -> ByteString {
    var s = ByteString.new();
    s.push(98 as u8);
    s.push(99 as u8);
    return s;
}

def main() -> i32 {
    let abc = make_abc();
    let bc = make_bc();

    if abc.len() != 3 { return 1; }
    if abc.byte_at(0) != (97 as u8) { return 2; }
    if !abc.ends_with(bc) { return 3; }
    if !abc.contains(bc) { return 4; }
    if abc.find(bc) != 1 { return 5; }
    if !abc.substring(1, 2).equals(bc) { return 6; }
    if abc.compare_to(bc) >= 0 { return 7; }

    let doubled = bc.repeat(2);
    if doubled.len() != 4 { return 8; }
    if doubled.byte_at(2) != (98 as u8) { return 9; }

    return 0;
}
""",
    )
    assert code == 0, f"stdout={out!r} stderr={err!r}"


# ============================================================================
# process.rl — argv / argc
# ============================================================================

def test_process_argc_reports_argument_count(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "argc",
        """
import "process.rl"

def main() -> i32 {
    return argc();
}
""",
        args=["one", "two", "three"],
    )
    # argv[0] is the program name + 3 user args = 4
    assert rc == 4


def test_process_argv_returns_each_argument(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "argv",
        """
import "process.rl"
import "io.rl"

def main() -> i32 {
    let n = argc();
    var i: i32 = 1;
    while i < n {
        println(argv(i));
        i = i + 1;
    }
    return 0;
}
""",
        args=["alpha", "beta", "gamma"],
        expected_exit=0,
    )
    assert out.splitlines() == ["alpha", "beta", "gamma"]


def test_process_argv_out_of_range_returns_empty(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "argv_oob",
        """
import "process.rl"
import "string.rl"

def main() -> i32 {
    let s = argv(999);
    return s.len() as i32;
}
""",
        expected_exit=0,
    )
    assert rc == 0


# ============================================================================
# process.rl — environment
# ============================================================================

def test_env_get_reads_variable(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "env_read",
        """
import "process.rl"
import "io.rl"

def main() -> i32 {
    let v = env_get("ROLANG_TEST_VAR");
    println(v);
    return 0;
}
""",
        env={"ROLANG_TEST_VAR": "hello-from-env"},
    )
    assert out.strip() == "hello-from-env"


def test_env_get_missing_returns_empty(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "env_missing",
        """
import "process.rl"
import "string.rl"

def main() -> i32 {
    let v = env_get("THIS_DEFINITELY_DOES_NOT_EXIST_12345");
    return v.len() as i32;
}
""",
        expected_exit=0,
    )


def test_env_set_then_get_roundtrips(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "env_set_get",
        """
import "process.rl"
import "io.rl"

def main() -> i32 {
    env_set("ROLANG_RT_KEY", "value-42");
    let v = env_get("ROLANG_RT_KEY");
    println(v);
    return 0;
}
""",
    )
    assert out.strip() == "value-42"


# ============================================================================
# process.rl — system / exit
# ============================================================================

def test_process_shell_returns_child_exit_code(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "shell",
        """
import "process.rl"

def main() -> i32 {
    return shell("exit 7");
}
""",
    )
    assert rc == 7


def test_process_exit_short_circuits_with_provided_code(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "exit_now",
        """
import "process.rl"

def main() -> i32 {
    process_exit(42);
    return 0;
}
""",
    )
    assert rc == 42


# ============================================================================
# process.rl — stdin
# ============================================================================

def test_stdin_read_line_reads_one_line(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "stdin_line",
        """
import "process.rl"
import "io.rl"

def main() -> i32 {
    let line = stdin_read_line();
    print(line);
    return 0;
}
""",
        stdin_data="first line\nsecond line\n",
    )
    assert out == "first line\n"


def test_stdin_read_all_reads_to_eof(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "stdin_all",
        """
import "process.rl"
import "io.rl"

def main() -> i32 {
    let s = stdin_read_all();
    print(s);
    return 0;
}
""",
        stdin_data="alpha\nbeta\ngamma\n",
    )
    assert out == "alpha\nbeta\ngamma\n"


# ============================================================================
# panic.rl
# ============================================================================

def _is_aborted(rc: int) -> bool:
    """subprocess.run returns either 128+signal or -signal depending on shell."""
    return rc == 134 or rc == -6


def test_panic_aborts_with_message_on_stderr(tmp_path: Path):
    rc, _, err = _compile_and_run(
        tmp_path,
        "use_panic",
        """
import "panic.rl"
import "io.rl"

def main() -> i32 {
    println("before");
    panic("explicit panic for test");
    return 0;
}
""",
    )
    assert _is_aborted(rc), f"unexpected rc={rc}"
    assert "rolang panic: explicit panic for test" in err


def test_panic_flushes_stdout_before_aborting(tmp_path: Path):
    """Regression: output buffered on stdout before the panic must be visible."""
    rc, out, _ = _compile_and_run(
        tmp_path,
        "use_panic_flush",
        """
import "panic.rl"
import "io.rl"

def main() -> i32 {
    println("MARKER_LINE");
    panic("oops");
    return 0;
}
""",
    )
    assert _is_aborted(rc), f"unexpected rc={rc}"
    assert "MARKER_LINE" in out


# ============================================================================
# path.rl
# ============================================================================

def test_path_join_simple_segments(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "path_join",
        """
import "path.rl"
import "io.rl"

def main() -> i32 {
    println(path_join("src", "rolang"));
    println(path_join("/abs", "child"));
    println(path_join("", "lone"));
    println(path_join("trailing/", "child"));
    return 0;
}
""",
    )
    assert out.splitlines() == [
        "src/rolang",
        "/abs/child",
        "lone",
        "trailing/child",
    ]


def test_path_join_absolute_rhs_wins(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "path_join_abs",
        """
import "path.rl"
import "io.rl"

def main() -> i32 {
    println(path_join("/foo", "/abs"));
    return 0;
}
""",
    )
    assert out.strip() == "/abs"


def test_path_dirname_basename(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "path_split",
        """
import "path.rl"
import "io.rl"

def main() -> i32 {
    println(path_dirname("/a/b/c.rl"));
    println(path_basename("/a/b/c.rl"));
    println(path_dirname("bare.rl"));
    println(path_basename("bare.rl"));
    println(path_dirname("/"));
    return 0;
}
""",
    )
    assert out.splitlines() == [
        "/a/b",
        "c.rl",
        ".",
        "bare.rl",
        "/",
    ]


def test_path_extension(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "path_ext",
        """
import "path.rl"
import "string.rl"
import "io.rl"

def main() -> i32 {
    println(path_extension("hello.rl"));
    println(path_extension("Makefile"));
    println(path_extension(".gitignore"));
    println(path_extension("dir.with.dots/file"));
    return 0;
}
""",
    )
    lines = out.splitlines()
    assert lines[0] == "rl"
    # ext-less inputs print empty lines
    assert lines[1] == ""
    assert lines[2] == ""
    assert lines[3] == ""


def test_path_exists_and_is_file_is_dir(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "path_query",
        f"""
import "path.rl"
import "io.rl"

def main() -> i32 {{
    if path_exists("{tmp_path}") {{ println("tmp_exists"); }}
    if path_is_dir("{tmp_path}") {{ println("tmp_is_dir"); }}
    if !path_is_file("{tmp_path}") {{ println("tmp_not_file"); }}
    if !path_exists("{tmp_path}/nonexistent_path_xyz") {{ println("missing_ok"); }}
    return 0;
}}
""",
    )
    assert out.splitlines() == ["tmp_exists", "tmp_is_dir", "tmp_not_file", "missing_ok"]


def test_dir_list_returns_heap_strings(tmp_path: Path):
    (tmp_path / "alpha.txt").write_text("", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("", encoding="utf-8")
    (tmp_path / "nested").mkdir()

    rc, _, _ = _compile_and_run(
        tmp_path,
        "dir_list_entries",
        f"""
import "path.rl"
import "string.rl"

def main() -> i32 {{
    let maybe_entries = dir_list("{tmp_path}");
    if let entries = maybe_entries {{
        var found_alpha = 0;
        var found_beta = 0;
        var found_nested = 0;
        let n = entries.len();
        var i: i32 = 0;
        while i < n {{
            let entry = entries.get(i);
            if entry.equals("alpha.txt") {{ found_alpha = 1; }}
            if entry.equals("beta.txt") {{ found_beta = 1; }}
            if entry.equals("nested") {{ found_nested = 1; }}
            i = i + 1;
        }}
        return found_alpha + found_beta + found_nested;
    }}
    return 99;
}}
""",
    )
    assert rc == 3


# ============================================================================
# set.rl
# ============================================================================

def test_set_string_basic_operations(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "set_str",
        """
import "set.rl"

def main() -> i32 {
    var s = Set<String>.new(16, 1, 0);
    s.add("alpha");
    s.add("beta");
    s.add("alpha");  // duplicate
    s.add("gamma");
    if s.len() != 3 { return 1; }
    if !s.contains("alpha") { return 2; }
    if !s.contains("gamma") { return 3; }
    if s.contains("missing") { return 4; }
    if s.is_empty() { return 5; }
    s.free();
    return 0;
}
""",
        expected_exit=0,
    )


def test_set_i32_basic_operations(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "set_i32",
        """
import "set.rl"

def main() -> i32 {
    var s = Set<i32>.new(16, 0, 0);
    s.add(10);
    s.add(20);
    s.add(10);  // dup
    if s.len() != 2 { return 1; }
    if !s.contains(10) { return 2; }
    if s.contains(99) { return 3; }
    return 0;
}
""",
        expected_exit=0,
    )


# ============================================================================
# linked_list.rl
# ============================================================================

def test_linked_list_push_front_and_pop_front(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "linked_list_front",
        """
import "linked_list.rl"

def main() -> i32 {
    var xs: LinkedList<i32> = LinkedList<i32>.new();
    if !xs.is_empty() { return 1; }

    xs.push_front(10);
    xs.push_front(20);
    xs.push_front(12);

    if xs.len() != 3 { return 2; }
    if (xs.front() ?? 0) != 12 { return 3; }

    let a = xs.pop_front() ?? 0;
    let b = xs.pop_front() ?? 0;
    let c = xs.pop_front() ?? 0;
    let d = xs.pop_front() ?? 7;

    if !xs.is_empty() { return 4; }
    return a + b + c + d;
}
""",
    )
    assert rc == 49


def test_linked_list_push_back_preserves_order(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "linked_list_back",
        """
import "linked_list.rl"

def main() -> i32 {
    var xs: LinkedList<i32> = LinkedList<i32>.new();
    xs.push_back(5);
    xs.push_back(10);
    xs.push_back(20);

    let a = xs.pop_front() ?? 0;
    let b = xs.pop_front() ?? 0;
    let c = xs.pop_front() ?? 0;

    return a + b + c;
}
""",
    )
    assert rc == 35


# ============================================================================
# fmt.rl — multi-arg chained format
# ============================================================================

def test_fmt_chained_substitution_two_args(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "fmt_chain",
        """
import "fmt.rl"
import "io.rl"

def main() -> i32 {
    let m = "expected {}, got {}".with_str("foo").with_str("bar");
    println(m);
    return 0;
}
""",
    )
    assert out.strip() == "expected foo, got bar"


def test_fmt_chained_mixed_types(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "fmt_mix",
        """
import "fmt.rl"
import "io.rl"

def main() -> i32 {
    let m = "name={}, age={}, ok={}"
        .with_str("alice")
        .with_i32(30)
        .with_bool(true);
    println(m);
    return 0;
}
""",
    )
    assert out.strip() == "name=alice, age=30, ok=true"


def test_fmt_format_f64(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "fmt_f64",
        """
import "fmt.rl"
import "io.rl"

def main() -> i32 {
    let m = format_f64("pi ~ {}", 3.14);
    println(m);
    return 0;
}
""",
    )
    assert "3.14" in out


# ============================================================================
# string.rl — split / lines / to_f64
# ============================================================================

def test_string_split_basic(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "split",
        """
import "string.rl"
import "io.rl"

def main() -> i32 {
    let v = "a,b,c,d".split(",");
    let n = v.len();
    var i: i32 = 0;
    while i < n {
        println(v.get(i));
        i = i + 1;
    }
    return n;
}
""",
    )
    assert rc == 4
    assert out.splitlines() == ["a", "b", "c", "d"]


def test_string_lines_strips_cr_lf(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "lines",
        """
import "string.rl"
import "io.rl"

def main() -> i32 {
    let v = "hello\\r\\nworld\\nbye".lines();
    let n = v.len();
    var i: i32 = 0;
    while i < n {
        println(v.get(i));
        i = i + 1;
    }
    return n;
}
""",
    )
    assert rc == 3
    assert out.splitlines() == ["hello", "world", "bye"]


def test_string_to_f64(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "to_f64",
        """
import "string.rl"
import "fmt.rl"
import "io.rl"

def main() -> i32 {
    let v = "3.14".to_f64();
    let m = format_f64("got {}", v);
    println(m);
    return 0;
}
""",
    )
    assert "3.14" in out


# ============================================================================
# iter.rl — chars / range
# ============================================================================

def test_iter_chars_of_string(tmp_path: Path):
    rc, out, _ = _compile_and_run(
        tmp_path,
        "chars",
        """
import "iter.rl"
import "io.rl"

def main() -> i32 {
    var count: i32 = 0;
    for ch in chars_of("abc") {
        count = count + 1;
        println_i32(ch);
    }
    return count;
}
""",
    )
    assert rc == 3
    assert out.splitlines() == ["97", "98", "99"]


def test_iter_range_from_to(tmp_path: Path):
    rc, _, _ = _compile_and_run(
        tmp_path,
        "range",
        """
import "iter.rl"

def main() -> i32 {
    var sum: i32 = 0;
    for i in Range.from_to(5, 10) {
        sum = sum + i;
    }
    return sum;
}
""",
    )
    # 5+6+7+8+9 = 35
    assert rc == 35
