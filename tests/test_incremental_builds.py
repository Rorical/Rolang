"""Incremental builds must skip compilation without hiding changed inputs."""
import os
from pathlib import Path
import subprocess

import pytest

from rolang.driver import CompilationDriver, CompileOptions, EmitKind, OptLevel
from rolang.toolchain.build import build_project
from rolang.toolchain.manifest import Manifest


def compile_cached(root, *, level=OptLevel.O0, emit=EmitKind.EXECUTABLE, includes=(), context=''):
    driver = CompilationDriver(CompileOptions(
        output_path=root / 'program', cache_dir=root / 'cache', opt_level=level,
        emit=emit, include_paths=list(includes), cache_context=context))
    result = driver.compile_file(root / 'main.rl')
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    return driver, result.output_path


def run(path):
    return subprocess.run([str(path)], timeout=10).returncode


def test_unchanged_build_skips_pipeline_and_restores_output(tmp_path, monkeypatch):
    (tmp_path / 'main.rl').write_text('def main() -> i32 { return 42; }')
    first, output = compile_cached(tmp_path)
    assert not first.cache_hit
    timestamp = output.stat().st_mtime_ns
    def forbidden(*args, **kwargs):
        pytest.fail('A cache hit must not run resolution/typechecking/codegen/linking')
    monkeypatch.setattr(CompilationDriver, '_compile_unified', forbidden)
    monkeypatch.setattr('rolang.driver.parse', forbidden)
    second, _ = compile_cached(tmp_path)
    assert second.cache_hit and output.stat().st_mtime_ns == timestamp
    output.unlink()
    third, _ = compile_cached(tmp_path)
    assert third.cache_hit and run(output) == 42
    output.write_bytes(b'broken')
    fourth, _ = compile_cached(tmp_path)
    assert fourth.cache_hit and run(output) == 42


def test_source_and_transitive_dependency_content_invalidation(tmp_path):
    main = tmp_path / 'main.rl'
    main.write_text('import "lib.rl"\ndef main() -> i32 { return value(); }')
    lib = tmp_path / 'lib.rl'
    lib.write_text('pub def value() -> i32 { return 1; }')
    compile_cached(tmp_path)
    old = lib.stat()
    lib.write_text('pub def value() -> i32 { return 2; }')
    os.utime(lib, ns=(old.st_atime_ns, old.st_mtime_ns))
    changed, output = compile_cached(tmp_path)
    assert not changed.cache_hit and run(output) == 2
    main.write_text(main.read_text().replace('value()', 'value() + 1'))
    changed, output = compile_cached(tmp_path)
    assert not changed.cache_hit and run(output) == 3
    lib.unlink()
    driver = CompilationDriver(CompileOptions(output_path=output, cache_dir=tmp_path / 'cache'))
    assert not driver.compile_file(main).success


def test_new_shadowing_import_is_discovered(tmp_path):
    vendor = tmp_path / 'vendor'
    vendor.mkdir()
    (vendor / 'helper.rl').write_text('pub def value() -> i32 { return 1; }')
    (tmp_path / 'main.rl').write_text('import "helper.rl"\ndef main() -> i32 { return value(); }')
    compile_cached(tmp_path, includes=[vendor])
    (tmp_path / 'helper.rl').write_text('pub def value() -> i32 { return 2; }')
    changed, output = compile_cached(tmp_path, includes=[vendor])
    assert not changed.cache_hit and run(output) == 2


def test_modes_context_and_environment_invalidate(tmp_path, monkeypatch):
    (tmp_path / 'main.rl').write_text('def main() -> i32 { return 7; }')
    compile_cached(tmp_path)
    assert not compile_cached(tmp_path, level=OptLevel.O3)[0].cache_hit
    assert compile_cached(tmp_path)[0].cache_hit  # old configuration is reusable
    assert not compile_cached(tmp_path, context='changed manifest/lock')[0].cache_hit
    monkeypatch.setenv('ROLANG_RT_CFLAGS', '-DROLANG_CHECK_PAYLOAD')
    assert not compile_cached(tmp_path)[0].cache_hit


def test_corrupt_cache_rebuilds_and_repairs(tmp_path):
    (tmp_path / 'main.rl').write_text('def main() -> i32 { return 7; }')
    compile_cached(tmp_path)
    artifact = next((tmp_path / 'cache').glob('*.zip'))
    artifact.write_bytes(b'bad cache')
    driver, output = compile_cached(tmp_path)
    assert not driver.cache_hit and run(output) == 7
    assert compile_cached(tmp_path)[0].cache_hit


def test_failed_build_is_not_cached(tmp_path):
    path = tmp_path / 'main.rl'
    path.write_text('def main() -> i32 { return missing(); }')
    driver = CompilationDriver(CompileOptions(output_path=tmp_path / 'program', cache_dir=tmp_path / 'cache'))
    assert not driver.compile_file(path).success
    assert not list((tmp_path / 'cache').glob('*.zip'))
    path.write_text('def main() -> i32 { return 0; }')
    assert driver.compile_file(path).success
    assert not driver.cache_hit
    assert driver.compile_file(path).success and driver.cache_hit


def test_project_rebuilds_only_affected_target_and_can_bypass(tmp_path, monkeypatch):
    (tmp_path / 'rolang.toml').write_text('''
[package]
name="app"
version="0.1.0"
[[bin]]
name="one"
path="one.rl"
[[bin]]
name="two"
path="two.rl"
''')
    for name in ('one', 'two'):
        (tmp_path / f'{name}.rl').write_text('def main() -> i32 { return 0; }')
    manifest = Manifest.load(tmp_path)
    calls = []
    original = CompilationDriver._compile_unified
    def track(self, entry, modules):
        calls.append(entry.name)
        return original(self, entry, modules)
    monkeypatch.setattr(CompilationDriver, '_compile_unified', track)
    assert build_project(manifest).success
    assert calls == ['one.rl', 'two.rl']
    calls.clear()
    assert build_project(manifest).success and calls == []
    (tmp_path / 'two.rl').write_text('def main() -> i32 { return 2; }')
    assert build_project(manifest).success and calls == ['two.rl']
    calls.clear()
    assert build_project(manifest, use_cache=False).success
    assert calls == ['one.rl', 'two.rl']


def test_module_artifact_cache_and_consumer_invalidation(tmp_path):
    library = tmp_path / 'lib.rl'
    library.write_text('pub def value() -> i32 { return 1; }')
    opts = CompileOptions(emit=EmitKind.MODULE, output_path=tmp_path / 'lib.rlm', cache_dir=tmp_path / 'cache')
    lib_driver = CompilationDriver(opts)
    assert lib_driver.compile_file(library).success
    assert lib_driver.compile_file(library).success and lib_driver.cache_hit
    (tmp_path / 'main.rl').write_text('import "lib.rlm"\ndef main() -> i32 { return value(); }')
    compile_cached(tmp_path)
    library.write_text('pub def value() -> i32 { return 2; }')
    assert lib_driver.compile_file(library).success and not lib_driver.cache_hit
    consumer, output = compile_cached(tmp_path)
    assert not consumer.cache_hit and run(output) == 2


def test_compiler_fingerprint_invalidation(tmp_path, monkeypatch):
    import rolang.build_cache as cache
    (tmp_path / 'main.rl').write_text('pub def value() -> i32 { return 7; }')
    compile_cached(tmp_path, emit=EmitKind.OBJECT)
    assert compile_cached(tmp_path, emit=EmitKind.OBJECT)[0].cache_hit
    monkeypatch.setattr(cache, 'compiler_abi', lambda: 'different-compiler')
    assert not compile_cached(tmp_path, emit=EmitKind.OBJECT)[0].cache_hit


def test_warnings_are_not_hidden_by_cache(tmp_path):
    (tmp_path / 'lib.rl').write_text('pub def value() -> i32 { return 7; }')
    (tmp_path / 'main.rl').write_text('import "lib.rl"\nimport "lib.rl"\npub def test() -> i32 { return value(); }')
    assert not compile_cached(tmp_path, emit=EmitKind.OBJECT)[0].cache_hit
    assert not compile_cached(tmp_path, emit=EmitKind.OBJECT)[0].cache_hit
    assert not list((tmp_path / 'cache').glob('*.zip'))


def test_unwritable_cache_is_optional(tmp_path):
    (tmp_path / 'main.rl').write_text('pub def value() -> i32 { return 7; }')
    (tmp_path / 'cache').write_text('not a directory')
    driver, output = compile_cached(tmp_path, emit=EmitKind.OBJECT)
    assert not driver.cache_hit and output.stat().st_size > 0


def test_cli_cache_and_clean(tmp_path, monkeypatch, capsys):
    from rolang.toolchain_cli import main
    (tmp_path / 'rolang.toml').write_text('[package]\nname="app"\nversion="0.1.0"\n[[bin]]\nname="app"\npath="main.rl"\n')
    (tmp_path / 'main.rl').write_text('def main() -> i32 { return 0; }')
    monkeypatch.chdir(tmp_path)
    for args in (['build'], ['build', '-v'], ['build', '--no-cache', '-v'], ['clean']):
        with pytest.raises(SystemExit) as result:
            main(args)
        assert result.value.code == 0
        captured = capsys.readouterr().out
        if args == ['build', '-v']:
            assert 'Cached ->' in captured
        elif '--no-cache' in args:
            assert 'Cached ->' not in captured
    assert not (tmp_path / 'build').exists()



def test_import_symlink_retargeting_invalidates(tmp_path):
    (tmp_path / 'first.rl').write_text('pub def value() -> i32 { return 1; }')
    (tmp_path / 'second.rl').write_text('pub def value() -> i32 { return 2; }')
    link = tmp_path / 'lib.rl'
    link.symlink_to('first.rl')
    (tmp_path / 'main.rl').write_text('import "lib.rl"\ndef main() -> i32 { return value(); }')
    compile_cached(tmp_path)
    link.unlink()
    link.symlink_to('second.rl')
    driver, output = compile_cached(tmp_path)
    assert not driver.cache_hit and run(output) == 2


def test_corrupt_index_recovers_without_wrong_output(tmp_path, monkeypatch):
    (tmp_path / 'main.rl').write_text('def main() -> i32 { return 7; }')
    compile_cached(tmp_path)
    next((tmp_path / 'cache').glob('*.index.json')).write_text('broken index')
    _, output = compile_cached(tmp_path)
    assert run(output) == 7
    def forbidden(*args, **kwargs):
        pytest.fail('Repaired index should allow the next build to skip parsing')
    monkeypatch.setattr('rolang.driver.parse', forbidden)
    assert compile_cached(tmp_path)[0].cache_hit


def test_edit_during_compilation_does_not_poison_cache(tmp_path, monkeypatch):
    source = tmp_path / 'main.rl'
    source.write_text('def main() -> i32 { return 1; }')
    original = CompilationDriver._compile_unified
    def edit(self, entry, modules):
        source.write_text('def main() -> i32 { return 2; }')
        return original(self, entry, modules)
    monkeypatch.setattr(CompilationDriver, '_compile_unified', edit)
    _, output = compile_cached(tmp_path)
    assert run(output) == 1
    assert not list((tmp_path / 'cache').glob('*.zip'))
    monkeypatch.setattr(CompilationDriver, '_compile_unified', original)
    driver, output = compile_cached(tmp_path)
    assert not driver.cache_hit and run(output) == 2
