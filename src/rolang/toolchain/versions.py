"""Semantic versions and Rolang's registry requirement syntax.

Precedence follows SemVer 2.0.0. Prereleases require an explicit comparator
mentioning a prerelease with the same major/minor/patch core.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import DependencyError

NUMBER = r'(?:0|[1-9][0-9]*)'
IDENTIFIERS = r'[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*'
VERSION = re.compile(
    rf'({NUMBER})\.({NUMBER})\.({NUMBER})(?:-({IDENTIFIERS}))?(?:\+({IDENTIFIERS}))?\Z'
)
PARTIAL = re.compile(rf'{NUMBER}(?:\.{NUMBER})?\Z')


@dataclass(frozen=True)
class Version:
    core: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Version:
        match = VERSION.fullmatch(text) if isinstance(text, str) else None
        if match is None:
            raise DependencyError(f'Invalid semantic version: {text!r}')
        major, minor, patch, pre, build = match.groups()
        identifiers = tuple(pre.split('.')) if pre else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith('0') for part in identifiers):
            raise DependencyError(f'Numeric prerelease identifiers cannot have leading zeroes: {text!r}')
        try:
            core = (int(major), int(minor), int(patch))
            # Check numeric identifiers here so oversized input is diagnostic.
            for part in identifiers:
                if part.isdigit():
                    int(part)
        except ValueError as exc:
            raise DependencyError(f'Version number is too large: {text!r}') from exc
        return cls(core, identifiers, tuple(build.split('.')) if build else ())

    @property
    def precedence(self) -> tuple:
        identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                            for part in self.prerelease)
        return (*self.core, 0 if self.prerelease else 1, identifiers)


def version_tuple(value: str) -> tuple:
    """Sortable precedence key; build metadata deliberately has no effect."""
    return Version.parse(value).precedence


def matches(version: str, requirement: str) -> bool:
    current = Version.parse(version)
    if not isinstance(requirement, str) or not requirement.strip():
        raise DependencyError(f'Invalid version requirement: {requirement!r}')
    if requirement.strip() == '*':
        return not current.prerelease

    allowed_prerelease_cores = set()
    results = []
    for term in requirement.split(','):
        match = re.fullmatch(r'\s*(\^|~|>=|<=|>|<|=)?\s*(\S+)\s*', term)
        if not match:
            raise DependencyError(f'Unsupported version requirement: {requirement!r}')
        op, text = match.groups()
        if PARTIAL.fullmatch(text):
            try:
                parts = tuple(map(int, text.split('.')))
            except ValueError as exc:
                raise DependencyError(f'Version number is too large: {text!r}') from exc
            lower = Version(parts + (0,) * (3 - len(parts)))
        else:
            lower = Version.parse(text)
            parts = lower.core
        if lower.prerelease:
            allowed_prerelease_cores.add(lower.core)
        key, bound = current.precedence, lower.precedence
        if op in ('>=', '<=', '>', '<', '='):
            if len(parts) != 3:
                raise DependencyError('Comparison constraints require MAJOR.MINOR.PATCH')
            results.append({'>=': key >= bound, '<=': key <= bound,
                            '>': key > bound, '<': key < bound, '=': key == bound}[op])
        elif op == '^' or op == '~' or len(parts) < 3:
            if op == '^':
                index = next((i for i, n in enumerate(parts) if n), len(parts) - 1)
            else:
                index = 0 if len(parts) == 1 else 1
            upper_core = lower.core[:index] + (lower.core[index] + 1,) + (0,) * (2 - index)
            # Exclude prereleases of the next incompatible core, even if a
            # separate comparator opts into that core's prereleases.
            upper = Version(upper_core, ('0',)).precedence
            results.append(bound <= key < upper)
        else:
            results.append(key == bound)
    return all(results) and (not current.prerelease or current.core in allowed_prerelease_cores)
