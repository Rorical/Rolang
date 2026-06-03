"""Regression tests for the module / import / namespace system.

Each test fixes one previously-found bug in the import pipeline. See the
inline `Bug N` references for the original issue numbers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Optional

from rolang.driver import CompileOptions, EmitKind, compile_source


def _compile(
    workdir: Path,
    files: Dict[str, str],
    main_src: str,
    *,
    include_paths: Optional[list] = None,
):
    """Helper: write `files` + `main.rl`, compile, return (result, rc)."""
    for fname, content in files.items():
        full = workdir / fname
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    entry = workdir / "main.rl"
    entry.write_text(main_src, encoding="utf-8")
    out = workdir / "main"
    result = compile_source(
        entry,
        CompileOptions(
            emit=EmitKind.EXECUTABLE,
            output_path=out,
            include_paths=include_paths or [],
        ),
    )
    rc = None
    if result.success:
        rc = subprocess.run([str(out)], check=False).returncode
    return result, rc


def _diag_messages(result):
    return [d.message for d in result.diagnostics.diagnostics]


# ============================================================================
# Bug 1: stem-collision — two files with the same basename in different
# directories must both be loadable.
# ============================================================================

def test_stem_collision_two_files_same_basename(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "a/util.rl": "pub def value() -> i32 { return 11; }",
            "b/util.rl": "pub def other() -> i32 { return 22; }",
        },
        '''
import "a/util.rl"
import "b/util.rl"

def main() -> i32 {
    return value() + other();
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 33


# ============================================================================
# Bug 2: a library's `internal` symbol must not collide with an importer's
# same-named local declaration (unified-merge symbol pollution).
# ============================================================================

def test_internal_lib_helper_does_not_collide(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "lib.rl": '''
def helper() -> i32 { return 5; }
pub def use_helper() -> i32 { return helper(); }
''',
        },
        '''
import "lib.rl"

def helper() -> i32 { return 99; }

def main() -> i32 {
    return helper() + use_helper();
}
''',
    )
    assert result.success, _diag_messages(result)
    # main's helper (99) + lib's helper via use_helper (5) = 104
    assert rc == 104


# ============================================================================
# Bug 3: an importer must be able to shadow an imported `pub` name with
# its own local definition.
# ============================================================================

def test_main_can_shadow_imported_pub_function(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"lib.rl": "pub def helper() -> i32 { return 1; }"},
        '''
import "lib.rl"

def helper() -> i32 { return 99; }

def main() -> i32 {
    return helper();
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 99


# ============================================================================
# Bug 4: an aliased import must isolate the imported type so an
# unrelated local type with the same name is allowed.
# ============================================================================

def test_aliased_import_isolates_struct_name(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"lib.rl": "pub struct Point { var x: i32; var y: i32; }"},
        '''
import "lib.rl" as L

struct Point { var z: i32; }

def main() -> i32 {
    let p = Point { z: 5 };
    return p.z;
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 5


# ============================================================================
# Bug 5: `import "x.rl" as None` previously was silently stripped because
# of a `alias != "None"` string check. Now the alias is honored.
# ============================================================================

def test_as_None_alias_is_honored(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"lib.rl": "pub def hello() -> i32 { return 77; }"},
        '''
import "lib.rl" as None

def main() -> i32 {
    return None.hello();
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 77


# ============================================================================
# Bug 6: a `private` / default-`internal` extension method must not be
# callable from a different module, while remaining callable inside its
# own module.
# ============================================================================

def test_private_extension_not_visible_across_modules(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {
            "lib.rl": '''
pub struct Box { var n: i32; }
private extension Box {
    def secret_double() -> i32 { return self.n * 2; }
}
''',
        },
        '''
import "lib.rl"

def main() -> i32 {
    let b = Box { n: 21 };
    return b.secret_double();
}
''',
    )
    assert not result.success
    assert any("secret_double" in m for m in _diag_messages(result))


def test_default_internal_extension_not_visible_across_modules(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {
            "lib.rl": '''
pub struct Box { var n: i32; }
extension Box {
    def secret() -> i32 { return self.n; }
}
''',
        },
        '''
import "lib.rl"

def main() -> i32 {
    let b = Box { n: 21 };
    return b.secret();
}
''',
    )
    assert not result.success
    assert any("secret" in m for m in _diag_messages(result))


def test_pub_extension_works_across_modules(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "lib.rl": '''
pub struct Box { var n: i32; }
pub extension Box {
    def doublev() -> i32 { return self.n * 2; }
}
''',
        },
        '''
import "lib.rl"

def main() -> i32 {
    let b = Box { n: 21 };
    return b.doublev();
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 42


def test_private_extension_callable_inside_own_module(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "lib.rl": '''
pub struct Box { var n: i32; }
private extension Box {
    def inner() -> i32 { return self.n + 1; }
}
pub def use_inner(b: Box) -> i32 {
    return b.inner();
}
''',
        },
        '''
import "lib.rl"

def main() -> i32 {
    let b = Box { n: 41 };
    return use_inner(b);
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 42


# ============================================================================
# Bug 8: two `pub` extensions with the same method on the same type must
# produce a diagnostic, not a codegen crash.
# ============================================================================

def test_duplicate_pub_extension_methods_report_diagnostic(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {
            "common.rl": (
                "pub struct Box { var n: i32; }\n"
                "pub def make_box(n: i32) -> Box { let b = Box { n: n }; return b; }\n"
            ),
            "lib_a.rl": (
                'import "common.rl"\n'
                "pub extension Box { def doit() -> i32 { return 100; } }\n"
            ),
            "lib_b.rl": (
                'import "common.rl"\n'
                "pub extension Box { def doit() -> i32 { return 200; } }\n"
            ),
        },
        '''
import "common.rl"
import "lib_a.rl"
import "lib_b.rl"

def main() -> i32 {
    let b = make_box(0);
    return b.doit();
}
''',
    )
    assert not result.success
    assert any(
        "already defined on type" in m or "doit" in m
        for m in _diag_messages(result)
    )


def test_distinct_private_extensions_same_method_different_modules(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "common.rl": (
                "pub struct Box { var n: i32; }\n"
                "pub def make_box(n: i32) -> Box { let b = Box { n: n }; return b; }\n"
            ),
            "lib_a.rl": (
                'import "common.rl"\n'
                "private extension Box { def doit() -> i32 { return 7; } }\n"
                "pub def call_a(b: Box) -> i32 { return b.doit(); }\n"
            ),
            "lib_b.rl": (
                'import "common.rl"\n'
                "private extension Box { def doit() -> i32 { return 11; } }\n"
                "pub def call_b(b: Box) -> i32 { return b.doit(); }\n"
            ),
        },
        '''
import "common.rl"
import "lib_a.rl"
import "lib_b.rl"

def main() -> i32 {
    let b = make_box(0);
    return call_a(b) + call_b(b);
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 18  # 7 + 11, each module uses its own private extension


# ============================================================================
# Bugs 9 / 10 / 11: alias name validation.
# ============================================================================

def test_alias_cannot_be_builtin_type_name(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {"lib.rl": "pub def hello() -> i32 { return 7; }"},
        '''
import "lib.rl" as i32

def main() -> i32 { return 0; }
''',
    )
    assert not result.success
    assert any("built-in" in m or "builtin" in m for m in _diag_messages(result))


def test_alias_cannot_be_reserved_word(tmp_path: Path):
    for keyword in ("def", "let", "if", "struct", "return", "import"):
        result, _ = _compile(
            tmp_path,
            {"lib.rl": "pub def hello() -> i32 { return 7; }"},
            f'''
import "lib.rl" as {keyword}

def main() -> i32 {{ return 0; }}
''',
        )
        assert not result.success, f"keyword '{keyword}' was accepted as alias"
        assert any(
            "reserved word" in m for m in _diag_messages(result)
        ), f"no reserved-word diagnostic for alias '{keyword}'"


# ============================================================================
# Bug 12: dropping the fuzzy `endswith` fallback in type_resolver means
# bare `Foo` from `import "lib.rl" as L` no longer resolves as a type.
# ============================================================================

def test_aliased_import_does_not_leak_bare_type_name(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {
            "lib.rl": (
                "pub struct Foo { var n: i32; }\n"
                "pub def make_foo() -> Foo { let f = Foo { n: 17 }; return f; }\n"
            ),
        },
        '''
import "lib.rl" as L

def main() -> i32 {
    let f: Foo = L.make_foo();
    return f.n;
}
''',
    )
    assert not result.success
    assert any("Undefined type 'Foo'" in m for m in _diag_messages(result))


def test_aliased_import_with_qualified_type_still_works(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "lib.rl": (
                # Field is marked `pub` so cross-module access works. In v2
                # field visibility defaults to `internal`, so a public
                # struct does not automatically get public fields.
                "pub struct Foo { pub var n: i32; }\n"
                "pub def make_foo() -> Foo { let f = Foo { n: 17 }; return f; }\n"
            ),
        },
        '''
import "lib.rl" as L

def main() -> i32 {
    let f: L.Foo = L.make_foo();
    return f.n;
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 17


# ============================================================================
# Bug 17: empty import path produces a diagnostic, not a silent no-op.
# ============================================================================

def test_empty_import_path_is_rejected(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {},
        '''
import ""

def main() -> i32 { return 0; }
''',
    )
    assert not result.success
    assert any("Empty import path" in m for m in _diag_messages(result))


# ============================================================================
# Bug 18: non-.rl import path produces a warning.
# ============================================================================

def test_non_rl_extension_warns(tmp_path: Path):
    (tmp_path / "stuff.txt").write_text(
        "pub def v() -> i32 { return 8; }\n", encoding="utf-8"
    )
    result, rc = _compile(
        tmp_path,
        {},
        '''
import "stuff.txt"

def main() -> i32 { return v(); }
''',
    )
    assert result.success
    assert rc == 8
    # Warning surfaced for the suspicious file extension.
    assert any(
        "does not end in '.rl'" in m for m in _diag_messages(result)
    )


# ============================================================================
# Bug 20: duplicate imports produce a warning (but still compile).
# ============================================================================

def test_duplicate_import_warns(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"lib.rl": "pub def f() -> i32 { return 5; }"},
        '''
import "lib.rl"
import "lib.rl"

def main() -> i32 { return f(); }
''',
    )
    assert result.success
    assert rc == 5
    assert any(
        "Duplicate import" in m for m in _diag_messages(result)
    )


# ============================================================================
# Bug 21: self-import is reported with a clear message, not as a
# generic circular-dependency error.
# ============================================================================

def test_self_import_clear_diagnostic(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {},
        '''
import "main.rl"

def main() -> i32 { return 0; }
''',
    )
    assert not result.success
    assert any(
        "imports itself" in m for m in _diag_messages(result)
    )


# ============================================================================
# Bug 35: a library can declare `def main()` (or any other top-level
# function) without colliding with the entry module's `main`.
# ============================================================================

def test_lib_internal_main_does_not_break_entry(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"lib.rl": "def main() -> i32 { return 1; }"},
        '''
import "lib.rl"

def main() -> i32 { return 42; }
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 42  # entry's main, not lib's


# ============================================================================
# Diamond imports: a depends on b and c, both of which depend on d.
# Verifies the new path-keyed module graph + aggregated resolution still
# compiles a diamond correctly.
# ============================================================================

def test_diamond_imports(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {
            "d.rl": "pub def base() -> i32 { return 1; }",
            "b.rl": (
                'import "d.rl"\n'
                "pub def b_val() -> i32 { return base() + 10; }\n"
            ),
            "c.rl": (
                'import "d.rl"\n'
                "pub def c_val() -> i32 { return base() + 100; }\n"
            ),
        },
        '''
import "b.rl"
import "c.rl"

def main() -> i32 {
    return b_val() + c_val();
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 112  # (1+10) + (1+100)


# ============================================================================
# Bug 13: a `pub` function's signature must not expose a non-`pub` user type.
# ============================================================================

def test_pub_fn_returning_private_type_is_rejected(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {
            "lib.rl": (
                "private struct Hidden { var n: i32; }\n"
                "pub def make() -> Hidden { let h = Hidden { n: 42 }; return h; }\n"
            ),
        },
        '''
import "lib.rl"

def main() -> i32 { return 0; }
''',
    )
    assert not result.success
    msgs = _diag_messages(result)
    assert any("non-public type 'Hidden'" in m for m in msgs)
    assert any("return type" in m for m in msgs)


def test_pub_fn_taking_private_type_is_rejected(tmp_path: Path):
    result, _ = _compile(
        tmp_path,
        {
            "lib.rl": (
                "private struct Hidden { var n: i32; }\n"
                "pub def use_it(h: Hidden) -> i32 { return h.n; }\n"
            ),
        },
        '''
import "lib.rl"

def main() -> i32 { return 0; }
''',
    )
    assert not result.success
    msgs = _diag_messages(result)
    assert any(
        "non-public type 'Hidden'" in m and "parameter" in m for m in msgs
    )


def test_internal_fn_with_private_type_is_ok(tmp_path: Path):
    """Non-public functions can freely use non-public types."""
    result, rc = _compile(
        tmp_path,
        {
            "lib.rl": (
                "private struct Hidden { var n: i32; }\n"
                "def make_hidden() -> Hidden { let h = Hidden { n: 5 }; return h; }\n"
                "pub def call_me() -> i32 { let h = make_hidden(); return h.n; }\n"
            ),
        },
        '''
import "lib.rl"

def main() -> i32 { return call_me(); }
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 5


# ============================================================================
# Bug 16: warn when a user file shadows a bundled stdlib file.
# ============================================================================

def test_user_io_rl_shadows_stdlib_with_warning(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"io.rl": "pub def my_marker() -> i32 { return 99; }"},
        '''
import "io.rl"

def main() -> i32 { return my_marker(); }
''',
    )
    assert result.success
    assert rc == 99
    assert any(
        "shadows the bundled standard library" in m
        for m in _diag_messages(result)
    )


# ============================================================================
# Bug 22: dotted-form imports are now flat by default; `as` adds a namespace.
# ============================================================================

def test_dotted_import_is_flat_by_default(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"std/foo.rl": "pub def hello() -> i32 { return 99; }"},
        '''
import std.foo

def main() -> i32 { return hello(); }
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 99


def test_dotted_import_with_alias_namespaces(tmp_path: Path):
    result, rc = _compile(
        tmp_path,
        {"std/foo.rl": "pub def hello() -> i32 { return 99; }"},
        '''
import std.foo as F

def main() -> i32 { return F.hello(); }
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 99


# ============================================================================
# Bug 24: `pub import` re-exports symbols through the importing module.
# ============================================================================

def test_pub_import_re_exports_symbols(tmp_path: Path):
    """main imports b.rl; b.rl re-exports c.rl's `value` through `pub import`."""
    result, rc = _compile(
        tmp_path,
        {
            "c.rl": "pub def value() -> i32 { return 42; }",
            "b.rl": (
                'pub import "c.rl"\n'
                "pub def use_c() -> i32 { return value() + 1; }\n"
            ),
        },
        '''
import "b.rl"

def main() -> i32 {
    // value() came from c.rl but is re-exported via b.rl's pub import.
    return value();
}
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 42


def test_plain_import_does_not_re_export(tmp_path: Path):
    """A plain (non-pub) `import` does NOT re-export."""
    result, _ = _compile(
        tmp_path,
        {
            "c.rl": "pub def value() -> i32 { return 42; }",
            "b.rl": (
                'import "c.rl"\n'
                "pub def use_c() -> i32 { return value() + 1; }\n"
            ),
        },
        '''
import "b.rl"

def main() -> i32 { return value(); }
''',
    )
    assert not result.success
    assert any(
        "Undefined variable or function 'value'" in m
        for m in _diag_messages(result)
    )


def test_pub_import_with_alias_re_exports_under_alias(tmp_path: Path):
    """`pub import "x" as M` makes the symbols visible as `M.name` downstream."""
    result, rc = _compile(
        tmp_path,
        {
            "c.rl": "pub def value() -> i32 { return 42; }",
            "b.rl": (
                'pub import "c.rl" as C\n'
                "pub def use_c() -> i32 { return C.value() + 1; }\n"
            ),
        },
        '''
import "b.rl" as B

def main() -> i32 { return B.C.value(); }
''',
    )
    assert result.success, _diag_messages(result)
    assert rc == 42


# ============================================================================
# Bug 19: absolute-path imports produce a portability warning.
# ============================================================================

def test_absolute_path_import_warns(tmp_path: Path):
    outside = tmp_path.parent / "remote_lib.rl"
    outside.write_text(
        "pub def remote_val() -> i32 { return 31; }\n", encoding="utf-8"
    )
    try:
        result, rc = _compile(
            tmp_path,
            {},
            f'''
import "{outside}"

def main() -> i32 {{ return remote_val(); }}
''',
        )
        assert result.success
        assert rc == 31
        assert any(
            "absolute path" in m.lower() for m in _diag_messages(result)
        )
    finally:
        outside.unlink(missing_ok=True)
