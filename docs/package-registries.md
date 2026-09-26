# Package registry dependencies

Rolang can install source packages from a static registry served over HTTPS or
stored in a local directory. No public default registry is configured.

```sh
export ROLANG_REGISTRY_URL=https://packages.example.org/rolang/
rolang add geometry '^1.2'
rolang install
rolang build
```

Or select a registry per dependency:

```sh
rolang add geometry '^1.2' --registry /absolute/path/to/registry
```

```toml
[dependencies]
geometry = { version = "^1.2", registry = "https://packages.example.org/rolang/" }
```

A relative registry directory in the root manifest is resolved against the project
root. HTTP is accepted only on loopback hosts for local testing. HTTPS redirects
must remain HTTPS.

## Versions and dependency graphs

Versions support `MAJOR.MINOR.PATCH`, prerelease identifiers, and build metadata,
following [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
For example, `1.2.0-beta.2` sorts before `1.2.0-beta.11`, which sorts before
`1.2.0`. Numeric prerelease identifiers cannot contain leading zeroes; build
identifiers such as `+build.007` can.

| Requirement | Meaning |
|---|---|
| `1.2.3` or `=1.2.3` | Exactly that release |
| `1` | At least 1.0.0, below 2.0.0 |
| `1.2` or `~1.2` | At least 1.2.0, below 1.3.0 |
| `~1.2.3` | At least 1.2.3, below 1.3.0 |
| `^1.2` | At least 1.2.0, below 2.0.0 |
| `^0.2.1` | At least 0.2.1, below 0.3.0 |
| `^0.0.1` | Exactly 0.0.1 |
| `>=1.2.0, <2.0.0` | Both comparisons must hold |
| `*` | Any stable release |

Alternative ranges (`||`) and wildcard components such as `1.*` are not supported;
invalid requirements produce diagnostics.

### Selecting prereleases

Prereleases are excluded from `*`, partial versions, and stable-only requirements.
A requirement admits prereleases only when one of its comparators explicitly
mentions a prerelease with the same major/minor/patch core. All comparisons must
still hold.

```sh
rolang add geometry '^1.2.0-beta.1'
```

This admits `1.2.0-beta.2`, `1.2.0-rc.1`, and stable versions from `1.2.0` up to
but excluding `2.0.0`. It excludes `1.3.0-beta.1`; opting into one prerelease
series does not opt into every future prerelease. Use
`>=1.2.0-beta.1, <1.2.0` to stay on the 1.2.0 prerelease series, or
`=1.2.0-beta.1` to select that exact prerelease precedence. Labels require all
three version components (`1.2-beta` is invalid).

Build metadata is ignored in range matching and precedence, so requirements
`1.2.0+linux` and `1.2.0+macos` match the same version precedence. Equal-precedence
release variants are tried in ascending full-version spelling order, independent
of index order. This tie rule prefers `1.2.0` over its metadata variants. Metadata
in a requirement does not select a particular artifact; lockfiles retain the
exact chosen spelling, archive path, and checksum.

A compatible prerelease lockfile pin remains pinned after a stable release
appears. Fresh resolution prefers the stable release. A changed stable-only
requirement no longer admits a prerelease pin.

The resolver installs transitive dependencies, trying compatible lockfile pins
first and then releases from highest to lowest. It backtracks when a dependency
requires a different compatible release. One version and source per package name
is allowed in the graph; unresolved conflicts and cycles produce diagnostics.
Resolution is bounded to 200 packages and 10,000 search steps, with a depth limit.

Registry packages must declare registry dependencies. An omitted child registry
inherits the parent's registry. Use an explicit registry URL for a different
registry. Path and Git dependencies in local projects retain their existing
support and now participate in transitive resolution.

## Lockfiles and cached installs

`rolang.lock` records each selected version, registry URL, relative archive path,
SHA-256 checksum, and direct dependency names. Commit it to version control.
Compatible pins are retained even if newer versions appear. Change the requirement
or remove a package's lock entry to request reselection. Yanked releases are
excluded from new selection; an existing compatible pin remains usable.

Archive bytes are checked before extraction. Cached archives are checked again on
reuse, and extracted package files are verified against a fresh extraction. A
compatible locked graph can be reinstalled without the registry being reachable
when all of its archives are cached. There is no dedicated offline-only flag.

The resolver finishes before changing project installation links or lock entries.
Lockfiles are replaced atomically. Generated links recorded in the previous lockfile and no longer needed are
cleaned up. Production installs retain development-dependency pins for later test
builds, and building a test target installs its development dependencies. Concurrent installation into the same project or repair of the same
corrupted cache entry is not supported.

## Static registry format

A registry is a directory containing indexes and gzip-compressed tar archives:

```text
registry/
  packages/
    geometry.json
  archives/
    geometry-1.2.0.tar.gz
```

`packages/geometry.json`:

```json
{
  "format": 1,
  "name": "geometry",
  "versions": [
    {
      "version": "1.2.0",
      "archive": "archives/geometry-1.2.0.tar.gz",
      "sha256": "REPLACE_WITH_64_LOWERCASE_HEX_DIGITS",
      "yanked": false
    }
  ]
}
```

The archive has `rolang.toml` at its root, plus the package's source files. Its
manifest name and version must match the index. For example:

```sh
tar -czf geometry-1.2.0.tar.gz -C geometry rolang.toml src
shasum -a 256 geometry-1.2.0.tar.gz
```

Copy the archive and index into your registry directory or static HTTPS host.
Archive paths must be relative to the registry root. Names start with an ASCII
letter and then contain letters, digits, underscores, or hyphens. Archives may
contain regular files and directories only. Extraction rejects traversal paths,
links, duplicate entries, excessive file counts, and oversized contents. Limits
are 2 MiB per index, 64 MiB per archive, 128 MiB unpacked, and 10,000 entries.

This implements registry consumption and the static registry format. Hosted
registry operations, authentication, a publish API/command,
and multiple versions of the same package in one graph remain future work.
Registry packages currently use source compilation; automatic `.rlm` caching is
also separate work.


## Validation

On Apple Silicon macOS, the full suite passed **856 tests**. After the final
development-dependency and malformed-index fixes, **72 registry/toolchain tests**
passed. The registry tests include a real loopback HTTP server, a compiled program
using transitive packages, offline pinned reinstalls, version backtracking,
checksum failures, archive traversal/link rejection, cycle/conflict diagnostics,
cache repair, development dependencies, and CLI configuration.


Prerelease follow-up: **130 tests passed** across version semantics, registry
installation, and the toolchain. Coverage includes the SemVer precedence example,
invalid identifiers, explicit prerelease range admission, compatible lockfile
pins, deterministic build-variant selection, offline reinstalls, and a compiled
consumer of a prerelease package.
