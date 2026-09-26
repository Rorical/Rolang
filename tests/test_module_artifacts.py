"""Native module reuse, generic metadata, and cross-module ownership."""
import json
import subprocess
import zipfile

import pytest

from rolang.driver import CompileOptions, EmitKind, OptLevel, compile_source


def build(path, source=None, *, emit=EmitKind.EXECUTABLE, level=OptLevel.O0):
    if source is not None:
        path.write_text(source)
    result = compile_source(path, CompileOptions(emit=emit, opt_level=level))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    return result.output_path


@pytest.mark.parametrize('level', list(OptLevel))
def test_native_generics_heap_and_async_without_source(tmp_path, level):
    library = tmp_path / 'lib.rl'
    artifact = build(library, '''
import std.task
pub struct Box {
    pub var text: String;
    pub def size() -> i32 { return self.text.len() as i32; }
}
def helper() -> i32 { return 42; }
pub def make() -> Box { return Box { text: "hello" }; }
pub def identity<T>(value: T) -> T { return value; }
pub def worker() async -> String {
    await yield_now();
    return "result" + helper().to_string();
}
''', emit=EmitKind.MODULE, level=level)
    # Relocate the artifact and remove the original source completely.
    relocated = tmp_path / 'dist'
    relocated.mkdir()
    artifact.rename(relocated / 'lib.rlm')
    library.unlink()
    main = tmp_path / 'main.rl'
    source = '''
import "dist/lib.rlm"
import std.io
import std.task
struct ClientOnly { var value: i32; }
def helper() -> i32 { return 7; }
def main() async -> i32 {
    let b = identity(make());
    let local = identity(ClientOnly { value: helper() });
    println(b.text);
    let task = spawn worker();
    println(await task);
    if b.size() != 5 { return 1; }
    if local.value != 7 { return 2; }
    return 0;
}
'''
    executable = build(main, source, level=level)
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'hello\nresult42\n', '')
    llvm = build(main, emit=EmitKind.LLVM_IR).read_text()
    assert 'declare ptr @__rl_' in llvm  # library native functions are declarations
    assert 'define weak_odr ptr @__rl_' in llvm  # client-only generic instantiation


def test_transitive_diamond_modules(tmp_path):
    base = tmp_path / 'base.rl'
    build(base, 'pub def value() -> i32 { return 10; }', emit=EmitKind.MODULE)
    build(tmp_path / 'left.rl', 'import "base.rlm"\npub def left() -> i32 { return value() + 1; }', emit=EmitKind.MODULE)
    build(tmp_path / 'right.rl', 'import "base.rlm"\npub def right() -> i32 { return value() + 2; }', emit=EmitKind.MODULE)
    for path in tmp_path.glob('*.rl'):
        path.unlink()
    (tmp_path / 'base.rlm').unlink()
    output = build(tmp_path / 'main.rl', '''
import "left.rlm"
import "right.rlm"
def main() -> i32 { return left() + right(); }
''')
    assert subprocess.run([str(output)], timeout=10).returncode == 23


@pytest.mark.parametrize('damage, expected', [
    ('target', 'does not match'), ('compiler', 'incompatible compiler'),
    ('object', 'checksum mismatch'), ('sources', 'Cannot import'),
])
def test_invalid_artifact_diagnostic(tmp_path, damage, expected):
    artifact = build(tmp_path / 'lib.rl', 'pub def value() -> i32 { return 1; }', emit=EmitKind.MODULE)
    with zipfile.ZipFile(artifact) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(contents['manifest.json'])
    if damage == 'object':
        name = next(n for n in contents if n.endswith('.o'))
        contents[name] += b'corrupt'
    else:
        manifest[damage] = [] if damage == 'sources' else 'incompatible'
    contents['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(artifact, 'w') as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    main = tmp_path / 'main.rl'
    main.write_text('import "lib.rlm"\ndef main() -> i32 { return value(); }')
    result = compile_source(main)
    assert not result.success
    assert expected in '\n'.join(d.message for d in result.diagnostics.diagnostics)


def test_source_dependency_requires_native_module(tmp_path):
    (tmp_path / 'lib.rl').write_text('pub def value() -> i32 { return 1; }')
    main = tmp_path / 'other.rl'
    main.write_text('import "lib.rl"\npub def other() -> i32 { return value(); }')
    result = compile_source(main, CompileOptions(emit=EmitKind.MODULE))
    assert not result.success
    assert any('Compile dependency' in d.message for d in result.diagnostics.diagnostics)


def test_generic_methods_collections_callbacks_and_destruction(tmp_path):
    build(tmp_path / 'lib.rl', '''
import std.io
pub struct Holder<T> {
    pub var value: T;
    pub def get() -> T { return self.value; }
}
pub struct Item {
    pub var n: i32;
    def __release__() -> Void { println("dropped"); }
}
pub def make() -> Holder<Item> {
    return Holder<Item> { value: Item { n: 42 } };
}
pub def items() -> Vec<String> {
    let values = Vec<String>.new();
    values.push("one");
    values.push("two");
    return values;
}
pub def apply(f: (i32) -> i32, x: i32) -> i32 { return f(x); }
''', emit=EmitKind.MODULE)
    out = build(tmp_path / 'main.rl', '''
import "lib.rlm"
import std.io
def plus(n: i32) -> i32 { return n + 1; }
def main() -> i32 {
    let h = make();
    let other = Holder<String> { value: "other" };
    println(other.get());
    let values = items();
    println(values.get(0));
    if apply(plus, h.get().n) != 43 { return 1; }
    return 0;
}
''', level=OptLevel.O3)
    run = subprocess.run([str(out)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'other\none\ndropped\n', '')


def test_module_backend_products(tmp_path):
    build(tmp_path / 'lib.rl', 'pub def value() -> i32 { return 42; }', emit=EmitKind.MODULE)
    main = tmp_path / 'main.rl'
    main.write_text('import "lib.rlm"\ndef main() -> i32 { return value(); }')
    from llvmlite import binding as llvm
    for kind in (EmitKind.MIR, EmitKind.MIR_OPTIMIZED, EmitKind.LLVM_IR,
                 EmitKind.LLVM_OPTIMIZED, EmitKind.ASSEMBLY, EmitKind.OBJECT):
        output = build(main, emit=kind, level=OptLevel.O3)
        assert output.stat().st_size > 0
        if kind in (EmitKind.LLVM_IR, EmitKind.LLVM_OPTIMIZED):
            with llvm.parse_assembly(output.read_text()) as module:
                module.verify()
        if kind == EmitKind.ASSEMBLY:
            subprocess.run(['cc', '-c', str(output), '-o', str(tmp_path / 'assembled.o')], check=True)


@pytest.mark.parametrize('level', [OptLevel.O0, OptLevel.O3])
def test_module_cycle_collection(tmp_path, level):
    build(tmp_path / 'lib.rl', '''
pub struct Node { pub var next: Node?; pub var text: String; }
pub def make() -> Node { return Node { next: nil, text: "live" }; }
''', emit=EmitKind.MODULE, level=level)
    out = build(tmp_path / 'main.rl', '''
import "lib.rlm"
extern "C" def rt_gc_collect() -> Void;
extern "C" def rt_obj_live_count() -> i64;
def cycle() -> Void {
    let first = make();
    let second = make();
    first.next = second;
    second.next = first;
}
def main() -> i32 {
    let root = make();
    root.next = make();
    var baseline: i64 = 0;
    unsafe { baseline = rt_obj_live_count(); }
    var i = 0;
    while i < 30000 { cycle(); i = i + 1; }
    unsafe {
        // Collection is allocation-triggered; some recent cycles can remain.
        rt_gc_collect();
        if rt_obj_live_count() > baseline + 20000 { return 1; }
    }
    if let child = root.next {
        if child.text.len() == 4 { return 0; }
    }
    return 2;
}
''', level=level)
    run = subprocess.run([str(out)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stderr) == (0, '')


def test_conflicting_library_versions(tmp_path):
    library = tmp_path / 'lib.rl'
    artifact = build(library, 'pub def value() -> i32 { return 1; }', emit=EmitKind.MODULE)
    artifact.rename(tmp_path / 'old.rlm')
    build(library, 'pub def value() -> i32 { return 2; }', emit=EmitKind.MODULE)
    main = tmp_path / 'main.rl'
    main.write_text('import "old.rlm"\nimport "lib.rlm"\ndef main() -> i32 { return value(); }')
    result = compile_source(main)
    assert not result.success
    assert any('Conflicting module' in d.message for d in result.diagnostics.diagnostics)


def test_artifact_conflicts_with_already_loaded_source(tmp_path):
    library = tmp_path / 'lib.rl'
    build(library, 'pub def value() -> i32 { return 1; }', emit=EmitKind.MODULE)
    library.write_text('pub def value() -> i32 { return 2; }')
    main = tmp_path / 'main.rl'
    main.write_text('import "lib.rl"\nimport "lib.rlm"\ndef main() -> i32 { return value(); }')
    result = compile_source(main)
    assert not result.success
    assert any('Conflicting module source' in d.message for d in result.diagnostics.diagnostics)
