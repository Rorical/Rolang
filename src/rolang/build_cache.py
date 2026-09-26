"""Disposable, content-addressed cache for completed native compilations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
import zlib

from llvmlite import binding as llvm

from .module_artifact import compiler_abi

FORMAT = 1
MAX_ARTIFACT = 512 * 1024 * 1024
BUILD_ENV = ('CC', 'PATH', 'ROLANG_RT_CFLAGS', 'SDKROOT', 'DEVELOPER_DIR',
             'MACOSX_DEPLOYMENT_TARGET', 'CPATH', 'C_INCLUDE_PATH',
             'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH', 'COMPILER_PATH',
             'GCC_EXEC_PREFIX', 'SOURCE_DATE_EPOCH', 'ZERO_AR_DATE')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(driver, *, inputs=True) -> str:
    if inputs:
        payload = {
            'base': driver._cache_base_key,
            'sources': sorted((str(p), digest(s.encode())) for p, s in driver.source_files.items()),
            'imports': sorted((str(m.path), m.import_paths) for m in driver.compile_order),
            'objects': sorted((k, digest(v)) for k, v in driver.module_objects.items()),
        }
        return digest(json.dumps(payload, sort_keys=True).encode())
    options = driver.options
    cc = shutil.which(os.environ.get('CC', 'cc'))
    compiler = None
    if options.emit.name == 'EXECUTABLE':
        if cc is None:
            raise OSError('C compiler not found')
        report = subprocess.run([cc, '--version'], capture_output=True, timeout=10, check=True)
        compiler = [str(Path(cc).resolve()), digest(Path(cc).read_bytes()),
                    report.stdout.decode(errors='replace'), report.stderr.decode(errors='replace')]
    sdk = None
    if platform.system() == 'Darwin' and options.emit.name == 'EXECUTABLE':
        sdk = []
        for flag in ('--show-sdk-path', '--show-sdk-version'):
            report = subprocess.run(['xcrun', flag], capture_output=True, timeout=10, check=True)
            sdk.append(report.stdout.decode(errors='replace'))
    payload = {
        'format': FORMAT, 'compiler': compiler_abi(), 'cc': compiler, 'sdk': sdk,
        'host': [platform.system(), platform.machine(), llvm.get_default_triple(), llvm.llvm_version_info],
        'entry': str(driver.entry_path), 'emit': options.emit.name,
        'optimization': options.opt_level.value, 'target': options.target_triple,
        'include_paths': [str(p.resolve()) for p in options.include_paths],
        'runtime': digest(options.runtime_path.read_bytes()) if options.runtime_path else None,
        'environment': {key: os.environ.get(key) for key in BUILD_ENV},
        'context': options.cache_context,
        # Preserve behavior of wrappers or flags that embed the requested path.
        'output': str(options.output_path.resolve()),
    }
    return digest(json.dumps(payload, sort_keys=True).encode())


class BuildCache:
    def __init__(self, directory: Path, key: str):
        self.path = directory / (key + '.zip')

    def restore(self, output: Path) -> bool:
        try:
            with zipfile.ZipFile(self.path) as archive:
                if archive.getinfo('metadata.json').file_size > 4096 or archive.getinfo('artifact').file_size > MAX_ARTIFACT:
                    return False
                metadata = json.loads(archive.read('metadata.json'))
                if metadata['format'] != FORMAT:
                    return False
                data = archive.read('artifact')
            if digest(data) != metadata['sha256']:
                return False
            mode = metadata['mode']
            if not isinstance(mode, int) or not 0 <= mode <= 0o777:
                return False
            if output.is_file() and not output.is_symlink() and output.read_bytes() == data and stat.S_IMODE(output.stat().st_mode) == mode:
                return True
            output.parent.mkdir(parents=True, exist_ok=True)
            self._replace(output, data, mode)
            return True
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError, zlib.error, zipfile.BadZipFile):
            return False

    def store(self, output: Path) -> None:
        if output.stat().st_size > MAX_ARTIFACT:
            return
        data = output.read_bytes()
        metadata = {'format': FORMAT, 'sha256': digest(data),
                    'mode': stat.S_IMODE(output.stat().st_mode) & 0o777}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('metadata.json', json.dumps(metadata))
                archive.writestr('artifact', data)
            temporary.replace(self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _replace(path: Path, data: bytes, mode: int) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()


def snapshot(path: Path) -> dict:
    resolved = str(path.resolve())
    if path.is_file():
        return {'resolved': resolved, 'kind': 'file', 'sha256': digest(path.read_bytes())}
    return {'resolved': resolved, 'kind': 'directory' if path.exists() else 'missing'}


def restore_index(directory: Path, base: str, output: Path) -> bool:
    try:
        path = directory / (base + '.index.json')
        if path.stat().st_size > 4 * 1024 * 1024:
            return False
        record = json.loads(path.read_text())
        if record['format'] != FORMAT or not re.fullmatch('[0-9a-f]{64}', record['key']):
            return False
        watches = record['watches']
        if not isinstance(watches, dict) or not watches:
            return False
        if any(snapshot(Path(name)) != expected for name, expected in watches.items()):
            return False
        return BuildCache(directory, record['key']).restore(output)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False


def save_index(directory: Path, base: str, cache: BuildCache, watches: dict) -> None:
    record = {'format': FORMAT, 'key': cache.path.stem, 'watches': watches}
    BuildCache._replace(directory / (base + '.index.json'), json.dumps(record, sort_keys=True).encode(), 0o600)
