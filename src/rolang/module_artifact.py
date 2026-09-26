"""Versioned, non-executable JSON metadata and native objects in .rlm archives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

FORMAT_VERSION = 1
MAX_BYTES = 128 * 1024 * 1024


def compiler_abi():
    root = Path(__file__).parent
    import llvmlite
    h = hashlib.sha256(llvmlite.__version__.encode())
    for path in sorted(root.rglob('*')):
        if path.suffix in ('.py', '.rl', '.c', '.lark'):
            h.update(str(path.relative_to(root)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_artifact(path, entry, sources, objects, target):
    manifest = {'format': FORMAT_VERSION, 'compiler': compiler_abi(), 'target': target,
                'entry': entry, 'sources': sources, 'objects': {k: sha(v) for k, v in objects.items()}}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.rlm.tmp', delete=False) as temp:
            temporary = Path(temp.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True))
            for value in objects.values():
                name = 'objects/' + sha(value) + '.o'
                if name not in archive.namelist():
                    archive.writestr(name, value)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def read_artifact(path, target):
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_BYTES:
                raise ValueError('module artifact exceeds size limit')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest['format'] != FORMAT_VERSION:
                raise ValueError('unsupported module artifact format')
            if manifest['compiler'] != compiler_abi():
                raise ValueError('module was built by an incompatible compiler; rebuild it')
            if manifest['target'] != target:
                raise ValueError(f"module target {manifest['target']} does not match {target}")
            sources = manifest['sources']
            objects = {}
            for name, fingerprint in manifest['objects'].items():
                if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
                    raise ValueError('invalid object fingerprint')
                data = archive.read('objects/' + fingerprint + '.o')
                if sha(data) != fingerprint:
                    raise ValueError('module object checksum mismatch')
                objects[name] = data
            for name, record in sources.items():
                if (not isinstance(name, str) or not name.startswith(('std:', 'user:'))
                        or not isinstance(record['text'], str)
                        or not isinstance(record['imports'], dict)
                        or any(not isinstance(k, str) or not isinstance(v, str)
                               for k, v in record['imports'].items())):
                    raise ValueError('invalid module source metadata')
                if any(dep not in sources for dep in record['imports'].values()):
                    raise ValueError('module dependency metadata is incomplete')
            if any(name not in sources for name in objects):
                raise ValueError('native object has no source metadata')
            if manifest['entry'] not in sources or manifest['entry'] not in objects:
                raise ValueError('module entry metadata is incomplete')
            return manifest['entry'], sources, objects
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, zipfile.BadZipFile) as error:
        raise ValueError(f'Cannot import {path.name}: {error}') from error
