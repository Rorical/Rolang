"""Static package registries: version indexes and checksummed source archives."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from .errors import DependencyError
from .manifest import Manifest
from .versions import matches, version_tuple

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]*\Z')


def validate_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise DependencyError(f'Invalid dependency name: {name!r}')


def registry_url(value, project_root):
    value = value or os.environ.get('ROLANG_REGISTRY_URL')
    if not value:
        raise DependencyError('No registry configured; set ROLANG_REGISTRY_URL or a dependency registry URL')
    if not isinstance(value, str):
        raise DependencyError('Registry must be a URL or directory path')
    parsed = urlparse(value)
    if not parsed.scheme:
        value = (project_root / value).resolve().as_uri()
    parsed = urlparse(value)
    if parsed.scheme not in ('https', 'http', 'file') or parsed.query or parsed.fragment:
        raise DependencyError(f'Unsupported registry URL: {value}')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise DependencyError('Remote registries require HTTPS; HTTP is allowed only for loopback testing')
    if parsed.scheme == 'file' and parsed.netloc not in ('', 'localhost'):
        raise DependencyError('File registries must be local')
    return value.rstrip('/') + '/'


def read_url(url, limit):
    try:
        with urlopen(url, timeout=30) as response:
            final = urlparse(response.geturl())
            original = urlparse(url)
            if original.scheme == 'https' and final.scheme != 'https':
                raise DependencyError('Registry redirect must preserve HTTPS')
            data = response.read(limit + 1)
            if len(data) > limit:
                raise DependencyError(f'Registry resource exceeds size limit: {url}')
            return data
    except (OSError, ValueError) as exc:
        raise DependencyError(f'Cannot fetch {url}: {exc}') from exc


def releases(name, base):
    validate_name(name)
    try:
        index = json.loads(read_url(urljoin(base, f'packages/{name}.json'), 2 * 1024 * 1024))
        if index['format'] != 1 or index['name'] != name:
            raise ValueError('wrong index format or package name')
        result = []
        seen = set()
        for release in index['versions']:
            version_tuple(release['version'])
            if release['version'] in seen:
                raise ValueError('duplicate release version')
            seen.add(release['version'])
            if not re.fullmatch('[0-9a-f]{64}', release['sha256']):
                raise ValueError('invalid SHA-256 checksum')
            archive = release['archive']
            parsed = urlparse(archive)
            if parsed.scheme or parsed.netloc or archive.startswith('/') or '..' in PurePosixPath(archive).parts or '\\' in archive or '%' in archive:
                raise ValueError('archive must be a relative path within the registry')
            if not archive or parsed.query or parsed.fragment:
                raise ValueError('invalid archive path')
            if not isinstance(release.get('yanked', False), bool):
                raise ValueError('yanked must be boolean')
            result.append(release)
        # Equal-precedence build variants use spelling order for deterministic
        # selection independent of index order; lockfiles retain exact spelling.
        result.sort(key=lambda r: r['version'])
        return sorted(result, key=lambda r: version_tuple(r['version']), reverse=True)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DependencyError(f'Invalid registry index for {name}: {exc}') from exc


def unpack(data, destination):
    """Extract regular files/directories only, into a fresh staging directory."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            total = 0
            seen = set()
            for index, member in enumerate(archive):
                path = PurePosixPath(member.name)
                if (index >= 10000 or member.size < 0 or path.is_absolute()
                        or '..' in path.parts or '\\' in member.name
                        or not path.parts or str(path) in seen
                        or not (member.isfile() or member.isdir())):
                    raise DependencyError(f'Unsafe package archive member: {member.name}')
                seen.add(str(path))
                total += member.size
                if total > MAX_UNPACKED:
                    raise DependencyError('Unpacked package exceeds size limit')
                target = destination.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise DependencyError(f'Invalid package archive: {exc}') from exc


def fetch_package(name, release, base, cache):
    try:
        return _fetch_package(name, release, base, cache)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DependencyError(f'Cannot install registry package {name}: {exc}') from exc


def _fetch_package(name, release, base, cache):
    validate_name(name)
    version_tuple(release['version'])
    if not isinstance(release['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', release['sha256']):
        raise DependencyError('Invalid locked package checksum')
    archive_path = release['archive']
    if (not isinstance(archive_path, str) or not archive_path
            or urlparse(archive_path).scheme or urlparse(archive_path).netloc
            or archive_path.startswith('/') or '..' in PurePosixPath(archive_path).parts
            or any(c in archive_path for c in ('\\', '%', '?', '#'))):
        raise DependencyError('Invalid locked archive path')
    checksum = release['sha256']
    folder = cache / 'registry' / hashlib.sha256(base.encode()).hexdigest() / name / release['version'] / checksum
    blob = folder / 'source.tar.gz'
    folder.mkdir(parents=True, exist_ok=True)
    if blob.exists() and blob.stat().st_size > MAX_ARCHIVE:
        raise DependencyError('Cached archive exceeds size limit')
    data = blob.read_bytes() if blob.exists() else read_url(urljoin(base, release['archive']), MAX_ARCHIVE)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != checksum:
        raise DependencyError(f'Checksum mismatch for {name} {release["version"]}')
    # Re-extract verified bytes; modified cache files cannot become trusted input.
    with tempfile.TemporaryDirectory(prefix='extract-', dir=folder) as directory:
        stage = Path(directory) / 'package'
        stage.mkdir()
        unpack(data, stage)
        try:
            manifest = Manifest.load(stage)
            if not manifest.package or (manifest.package.name, manifest.package.version) != (name, release['version']):
                raise DependencyError(f'Package identity mismatch for {name} {release["version"]}')
            library = manifest.effective_lib()
            if library and (Path(library.path).is_absolute() or '..' in Path(library.path).parts):
                raise DependencyError('Registry library entry must stay within its package')
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DependencyError(f'Invalid package manifest for {name}: {exc}') from exc
        destination = folder / 'package'
        if not _same_tree(stage, destination):
            if destination.is_symlink():
                destination.unlink()
            elif destination.exists():
                shutil.rmtree(destination)
            stage.rename(destination)
        if not blob.exists():
            temp = Path(directory) / 'source.tar.gz'
            temp.write_bytes(data)
            temp.replace(blob)
    return destination


def _same_tree(expected, actual):
    """Keep verified cache trees stable when another build may be using them."""
    if actual.is_symlink() or not actual.is_dir():
        return False
    wanted = {p.relative_to(expected): p for p in expected.rglob('*')}
    found = {p.relative_to(actual): p for p in actual.rglob('*')}
    if wanted.keys() != found.keys() or any(p.is_symlink() for p in found.values()):
        return False
    for name, source in wanted.items():
        target = found[name]
        if source.is_dir() != target.is_dir():
            return False
        if source.is_file() and (not target.is_file() or source.stat().st_size != target.stat().st_size
                                 or source.read_bytes() != target.read_bytes()):
            return False
    return True
