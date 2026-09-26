"""SemVer precedence examples and prerelease admission at range boundaries."""
import pytest

from rolang.toolchain.errors import DependencyError
from rolang.toolchain.versions import Version, matches, version_tuple


def test_semver_spec_precedence_chain():
    versions = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta',
                '1.0.0-beta', '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0']
    assert sorted(reversed(versions), key=version_tuple) == versions
    assert all(version_tuple(a) < version_tuple(b) for a, b in zip(versions, versions[1:]))


@pytest.mark.parametrize('left, right', [
    ('1.0.0', '1.0.0+build.007'),
    ('1.0.0-beta.2+linux', '1.0.0-beta.2+macos'),
])
def test_metadata_does_not_change_precedence(left, right):
    assert version_tuple(left) == version_tuple(right)
    assert matches(left, '=' + right)
    assert matches(right, left)


@pytest.mark.parametrize('version, requirement, expected', [
    ('1.2.0-beta.2', '*', False),
    ('1.2.0-beta.2', '^1', False),
    ('1.2.0-beta.2', '>=1.0.0, <2.0.0', False),
    ('1.2.0-beta.2', '^1.2.0-beta.1', True),
    ('1.2.0-beta.10', '>=1.2.0-beta.2, <1.2.0', True),
    ('1.2.0-beta.1', '>=1.2.0-beta.2, <1.2.0', False),
    ('1.2.0-beta.2', '1.2.0-beta.2', True),
    ('1.2.0-beta.3', '1.2.0-beta.2', False),
    ('1.2.0', '^1.2.0-beta.1', True),
    ('1.3.0', '^1.2.0-beta.1', True),
    ('1.3.0-beta.1', '^1.2.0-beta.1', False),
    ('1.3.0-beta.1', '^1.2.0-beta.1, >=1.3.0-beta.1', True),
    ('2.0.0-beta.1', '^1.2.0-beta.1, >=2.0.0-beta.1', False),
    ('1.3.0-beta.1', '~1.2.0, >=1.3.0-beta.1', False),
    ('1.3.0-beta.1', '1.2, >=1.3.0-beta.1', False),
    ('0.0.1-rc.2', '^0.0.1-rc.1', True),
    ('0.0.1', '^0.0.1-rc.1', True),
    ('0.0.2', '^0.0.1-rc.1', False),
    ('0.3.0-rc.1', '^0.2.0-rc.1', False),
    ('1.2.0+build.1', '*', True),
    ('1.2.0+build.1', '1.2.0+build.2', True),
    ('1.2.0-beta+build.1', '^1.2.0-beta+build.2', True),
    ('1.2.0-beta', '=1.2.0', False),
])
def test_prerelease_requirements(version, requirement, expected):
    assert matches(version, requirement) == expected


@pytest.mark.parametrize('text', [
    '1.0', 'v1.0.0', '01.0.0', '1.00.0', '1.0.00', '1.0.0-', '1.0.0+',
    '1.0.0-alpha..1', '1.0.0-alpha.01', '1.0.0-00', '1.0.0+build..1',
    '1.0.0+foo+bar', '1.0.0-alpha_beta', '1.0.0-β', '1.0.0\n',
    '../1.0.0', None, 1,
])
def test_invalid_versions_are_diagnostic(text):
    with pytest.raises(DependencyError):
        Version.parse(text)


@pytest.mark.parametrize('text', ['1.0.0-0', '1.0.0-01a', '1.0.0--', '1.0.0+001', '1.0.0-a-b+build.007'])
def test_valid_identifiers(text):
    assert Version.parse(text)


@pytest.mark.parametrize('requirement', ['^1.2-beta', '~1-beta', '1.2+build', '>=1.0.0-01', '=1.0.0+', '1.0.0, nonsense'])
def test_invalid_constraints_are_checked_even_for_nonmatching_candidates(requirement):
    with pytest.raises(DependencyError):
        matches('0.0.0', requirement)
