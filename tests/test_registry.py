"""Static registry installs, graph selection, pinning, and archive validation."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from rolang.toolchain.deps import install_deps, build_include_paths
from rolang.toolchain.errors import DependencyError
from rolang.toolchain.lockfile import LockFile
from rolang.toolchain.manifest import Manifest
from rolang.toolchain.registry import matches, unpack
from rolang.driver import CompileOptions, compile_source


@pytest.fixture
def registry(tmp_path, monkeypatch):
    base = tmp_path / 'registry'
    (base / 'packages').mkdir(parents=True)
    monkeypatch.setenv('ROLANG_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setenv('ROLANG_REGISTRY_URL', str(base))
    return base


def publish(base, name, version, dependencies='', source='pub def value() -> i32 { return 42; }', yanked=False):
    manifest = f'[package]\nname = "{name}"\nversion = "{version}"\ntype = "library"\n[dependencies]\n{dependencies}\n'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for path, content in {'rolang.toml': manifest, 'src/lib.rl': source}.items():
            data = content.encode()
            member = tarfile.TarInfo(path)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    blob = buffer.getvalue()
    filename = f'{name}-{version}.tar.gz'
    (base / filename).write_bytes(blob)
    index_path = base / 'packages' / f'{name}.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {'format': 1, 'name': name, 'versions': []}
    index['versions'].append({'version': version, 'archive': filename, 'sha256': hashlib.sha256(blob).hexdigest(), 'yanked': yanked})
    index_path.write_text(json.dumps(index))


def project(tmp_path, deps):
    root = tmp_path / 'project'
    root.mkdir(exist_ok=True)
    (root / 'rolang.toml').write_text('[package]\nname="app"\nversion="0.1.0"\n[dependencies]\n' + deps)
    return Manifest.load(root)


@pytest.mark.parametrize('version, requirement, expected', [
    ('1.2.3', '^1.0', True), ('2.0.0', '^1.0', False),
    ('0.2.9', '^0.2.1', True), ('0.3.0', '^0.2.1', False),
    ('0.0.2', '^0.0.1', False), ('0.9.0', '^0', True),
    ('1.3.0', '~1.2', False), ('1.2.9', '~1.2.3', True),
    ('1.9.0', '1', True), ('1.2.4', '1.2.3', False),
    ('1.2.4', '>=1.0.0, <2.0.0', True), ('1.2.4', '*', True),
])
def test_requirements(version, requirement, expected):
    assert matches(version, requirement) == expected


@pytest.mark.parametrize('requirement', ['^01.2', '>=1', '', '1 || 2', '1.0.0-beta..1'])
def test_invalid_requirement(requirement):
    with pytest.raises(DependencyError):
        matches('1.2.3', requirement)


def test_transitive_install_build_and_offline_pin(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    publish(registry, 'upper', '1.0.0', 'leaf="^1.0"', 'import leaf\npub def answer() -> i32 { return value(); }')
    manifest = project(tmp_path, 'upper="^1.0"')
    lock = LockFile()
    installed = install_deps(manifest, lock)
    assert set(installed) == {'upper', 'leaf'}
    assert lock.find('upper').dependencies == ['leaf']
    lock.save(manifest.root)
    # New release must not change a compatible pin.
    publish(registry, 'upper', '1.1.0')
    assert install_deps(manifest, LockFile.load(manifest.root))['upper'].version == '1.0.0'
    # Corrupt unpacked content; reinstall restores it from verified archive bytes.
    (installed['leaf'].local_path / 'src/lib.rl').write_text('broken')
    registry.rename(registry.with_name('offline'))
    install_deps(manifest, LockFile.load(manifest.root))
    main = manifest.root / 'main.rl'
    main.write_text('import upper\ndef main() -> i32 { return answer(); }')
    result = compile_source(main, CompileOptions(include_paths=build_include_paths(manifest.root)))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    assert subprocess.run([str(result.output_path)], timeout=10).returncode == 42


def test_backtracking_and_yanked_versions(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    publish(registry, 'leaf', '2.0.0')
    publish(registry, 'upper', '1.0.0', 'leaf="1.0.0"')
    publish(registry, 'upper', '1.1.0', 'leaf="2.0.0"')
    publish(registry, 'upper', '1.2.0', 'leaf="1.0.0"', yanked=True)
    installed = install_deps(project(tmp_path, 'upper="^1.0"\nleaf="1.0.0"'), LockFile())
    assert installed['upper'].version == '1.0.0'
    assert installed['leaf'].version == '1.0.0'


def test_conflict_does_not_mutate_lock_or_installation(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    publish(registry, 'leaf', '2.0.0')
    publish(registry, 'upper', '1.0.0', 'leaf="2.0.0"')
    lock = LockFile()
    manifest = project(tmp_path, 'upper="1.0.0"\nleaf="1.0.0"')
    with pytest.raises(DependencyError, match='Conflicting'):
        install_deps(manifest, lock)
    assert not lock.packages
    assert not (manifest.root / '.rolang/deps').exists()


def test_checksum_failure(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    (registry / 'leaf-1.0.0.tar.gz').write_bytes(b'corrupt')
    with pytest.raises(DependencyError, match='Checksum mismatch'):
        install_deps(project(tmp_path, 'leaf="1"'), LockFile())


@pytest.mark.parametrize('path, kind', [('../escape', 'file'), ('/absolute', 'file'), ('link', 'symlink'), ('hard', 'hardlink')])
def test_unsafe_archive(tmp_path, path, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        member = tarfile.TarInfo(path)
        if kind != 'file':
            member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
            member.linkname = '../outside'
        archive.addfile(member)
    with pytest.raises(DependencyError, match='Unsafe'):
        unpack(stream.getvalue(), tmp_path)


def test_cycle(tmp_path, registry):
    publish(registry, 'one', '1.0.0', 'two="1"')
    publish(registry, 'two', '1.0.0', 'one="1"')
    with pytest.raises(DependencyError, match='Dependency cycle'):
        install_deps(project(tmp_path, 'one="1"'), LockFile())


def test_registry_configuration_and_cli(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    manifest = project(tmp_path, '')
    from rolang.toolchain_cli import main
    import os
    previous = os.getcwd()
    try:
        os.chdir(manifest.root)
        for args in (['add', 'leaf', '^1.0', '--registry', str(registry)], ['install']):
            with pytest.raises(SystemExit) as result:
                main(args)
            assert result.value.code == 0
        assert LockFile.load(manifest.root).find('leaf').checksum
    finally:
        os.chdir(previous)


def test_loopback_http_registry(tmp_path, registry, monkeypatch):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    publish(registry, 'leaf', '1.0.0')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(registry)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('ROLANG_REGISTRY_URL', f'http://127.0.0.1:{server.server_port}')
        installed = install_deps(project(tmp_path, 'leaf="1"'), LockFile())
        assert installed['leaf'].version == '1.0.0'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_corrupt_cached_archive_is_rejected(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    manifest = project(tmp_path, 'leaf="1"')
    lock = LockFile()
    installed = install_deps(manifest, lock)
    (installed['leaf'].local_path.parent / 'source.tar.gz').write_bytes(b'corrupt')
    with pytest.raises(DependencyError, match='Checksum mismatch'):
        install_deps(manifest, lock)


def test_changed_requirement_reselects_release(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    publish(registry, 'leaf', '2.0.0')
    lock = LockFile()
    install_deps(project(tmp_path, 'leaf="1"'), lock)
    assert install_deps(project(tmp_path, 'leaf="2"'), lock)['leaf'].version == '2.0.0'


@pytest.mark.parametrize('damage', ['identity', 'archive_path', 'checksum'])
def test_bad_index(tmp_path, registry, damage):
    publish(registry, 'leaf', '1.0.0')
    path = registry / 'packages/leaf.json'
    index = json.loads(path.read_text())
    if damage == 'identity':
        index['name'] = 'wrong'
    elif damage == 'archive_path':
        index['versions'][0]['archive'] = '../outside.tar.gz'
    else:
        index['versions'][0]['sha256'] = 'not-a-checksum'
    path.write_text(json.dumps(index))
    with pytest.raises(DependencyError):
        install_deps(project(tmp_path, 'leaf="1"'), LockFile())


def test_registry_dev_dependency_for_test_target(tmp_path, registry):
    from rolang.toolchain.build import build_project
    publish(registry, 'leaf', '1.0.0')
    manifest = project(tmp_path, '')
    path = manifest.root / 'rolang.toml'
    path.write_text(path.read_text() + '\n[dev-dependencies]\nleaf="1"\n[[test]]\nname="check"\npath="check.rl"\n')
    (manifest.root / 'check.rl').write_text('import leaf\ndef main() -> i32 { return value() - 42; }')
    result = build_project(Manifest.load(manifest.root), targets=['check'])
    assert result.success, result.errors
    assert subprocess.run([str(result.outputs[0])], timeout=10).returncode == 0


def test_null_archive_is_a_diagnostic(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    path = registry / 'packages/leaf.json'
    index = json.loads(path.read_text())
    index['versions'][0]['archive'] = None
    path.write_text(json.dumps(index))
    with pytest.raises(DependencyError, match='Invalid registry index'):
        install_deps(project(tmp_path, 'leaf="1"'), LockFile())


def test_removed_transitive_dependency_links(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    publish(registry, 'upper', '1.0.0', 'leaf="1"')
    publish(registry, 'upper', '2.0.0')
    lock = LockFile()
    manifest = project(tmp_path, 'upper="1"')
    install_deps(manifest, lock)
    install_deps(project(tmp_path, 'upper="2"'), lock)
    assert lock.find('leaf') is None
    assert not (manifest.root / '.rolang/deps/leaf').is_symlink()
    assert not (manifest.root / '.rolang/deps/leaf.rl').is_symlink()


def test_production_install_retains_dev_pins(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    manifest = project(tmp_path, '')
    path = manifest.root / 'rolang.toml'
    path.write_text(path.read_text() + '\n[dev-dependencies]\nleaf="^1"\n')
    manifest = Manifest.load(manifest.root)
    lock = LockFile()
    install_deps(manifest, lock, dev=True)
    publish(registry, 'leaf', '1.1.0')
    assert not install_deps(manifest, lock)
    assert lock.find('leaf').version == '1.0.0'
    assert install_deps(manifest, lock, dev=True)['leaf'].version == '1.0.0'


def test_prerelease_selection_and_offline_lock(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0')
    publish(registry, 'leaf', '1.1.0-beta.2+build.007')
    publish(registry, 'leaf', '1.1.0-beta.11+build.008')
    publish(registry, 'leaf', '1.2.0-beta.1')
    assert install_deps(project(tmp_path, 'leaf="^1"'), LockFile())['leaf'].version == '1.0.0'
    manifest = project(tmp_path, 'leaf="^1.1.0-beta.1"')
    lock = LockFile()
    installed = install_deps(manifest, lock)
    assert installed['leaf'].version == '1.1.0-beta.11+build.008'
    lock.save(manifest.root)
    publish(registry, 'leaf', '1.1.0')
    # Keep a compatible prerelease pin after the stable release appears.
    assert install_deps(manifest, LockFile.load(manifest.root))['leaf'].version == installed['leaf'].version
    # Fresh resolution prefers stable over its prereleases.
    assert install_deps(manifest, LockFile())['leaf'].version == '1.1.0'
    registry.rename(registry.with_name('offline'))
    restored = install_deps(manifest, LockFile.load(manifest.root))
    assert restored['leaf'].version == '1.1.0-beta.11+build.008'
    main = manifest.root / 'main.rl'
    main.write_text('import leaf\ndef main() -> i32 { return value(); }')
    result = compile_source(main, CompileOptions(include_paths=build_include_paths(manifest.root)))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    assert subprocess.run([str(result.output_path)], timeout=10).returncode == 42


def test_build_variant_selection_is_independent_of_index_order(tmp_path, registry):
    from rolang.toolchain.registry import releases
    publish(registry, 'leaf', '1.0.0+z')
    publish(registry, 'leaf', '1.0.0+a')
    url = registry.as_uri() + '/'
    before = [r['version'] for r in releases('leaf', url)]
    path = registry / 'packages/leaf.json'
    index = json.loads(path.read_text())
    index['versions'].reverse()
    path.write_text(json.dumps(index))
    assert [r['version'] for r in releases('leaf', url)] == before == ['1.0.0+a', '1.0.0+z']
    lock = LockFile()
    manifest = project(tmp_path, 'leaf="1.0.0"')
    assert install_deps(manifest, lock)['leaf'].version == '1.0.0+a'
    publish(registry, 'leaf', '1.0.0')
    assert install_deps(manifest, lock)['leaf'].version == '1.0.0+a'
    assert install_deps(manifest, LockFile())['leaf'].version == '1.0.0'


def test_transitive_stable_requirement_rejects_prerelease_pin(tmp_path, registry):
    publish(registry, 'leaf', '1.0.0-beta.1')
    publish(registry, 'upper', '1.0.0', 'leaf="^1.0"')
    lock = LockFile()
    install_deps(project(tmp_path, 'leaf="1.0.0-beta.1"'), lock)
    with pytest.raises(DependencyError, match='Conflicting|No matching'):
        install_deps(project(tmp_path, 'leaf="1.0.0-beta.1"\nupper="1"'), lock)
    assert lock.find('leaf').version == '1.0.0-beta.1'
