# Compiler rewrite foundations

This increment adds source-language and library support for a Rolang-written
compiler. It does not port the compiler itself or remove the C runtime dependency.

## Transparent type aliases

```rolang
typealias SymbolId = i32;
typealias Symbols = Vec<SymbolId>;
typealias Lookup = (SymbolId) -> String?;
```

Aliases are top-level, non-generic declarations. Their targets can be instantiated
generic types, optionals, function types, or other aliases. Forward references
are supported; cycles and generic arguments applied to an alias are diagnosed.
Aliases can be public, imported, re-exported, and carried in native module
metadata. An exported alias cannot expose a private underlying type.

Aliases have exactly the identity and representation of their targets. `SymbolId`
and `i32` are interchangeable: aliases do **not** prevent mixing distinct kinds
of integer IDs. Struct literals and static methods work through aliases to their
underlying types. Generic alias declarations and distinct value types remain
future work.

## Collection operations

`Dict<K,V>` now provides:

| Method | Result / behavior |
|---|---|
| `remove(key)` | `V?`; transfers the removed value to the caller |
| `clear()` | Releases all stored keys/values and preserves capacity |
| `keys()` | `Vec<K>` snapshot |
| `values()` | `Vec<V>` snapshot |
| `entries()` | `Vec<DictEntry<K,V>>` snapshot, with public `key` and `value` |

Snapshots own references to keys/values. Later dictionary updates, removals, or
clears do not change the snapshot's bindings. Objects inside the snapshot retain
normal reference semantics; this is not a deep copy.

Removal preserves the existing insertion order. It repairs the hash probe chain,
compacts entries, and adjusts bucket indices, taking O(length + capacity) time.
Lookup and insertion retain the existing hash-table behavior. All entry indices
must be treated as invalid after mutation. Do not structurally mutate a dictionary
during live iteration (`dict_keys` or `for key in dict`); traverse a snapshot when
removing bindings. `Set<T>` gains `remove`, `clear`, and a `values` snapshot.

## Structural-key maps

Import `std.hash_map` for `HashMap<K,V>`. Supply `(K) -> i64` hashing and
`(K,K) -> Bool` equality callbacks to `HashMap<K,V>.new(hash, equal)`. Named
functions and captured closures are supported. The map provides `set`, `get`,
`contains`, `remove`, `clear`, `len`, `is_empty`, and `entries` snapshots.

This library is implemented in Rolang using hash buckets of entries. Hash
collisions are resolved using the supplied equality callback. Equal keys must
have equal hashes; key fields used for hashing/equality must not be mutated while
stored. Callback behavior (including captured state) must remain stable for stored
keys. Callbacks must not mutate the map being queried. Iteration order is
unspecified. Lookup and insertion are expected O(1) with well-distributed hashes;
colliding keys require linear bucket scans. Removing the final key in a bucket
also incurs the underlying ordered dictionary's linear removal cost.

This supports structural keys for type interning and specialization caches without
changing `Dict`'s existing byte/string key semantics.

## Text construction

Import `std.string_builder` for `StringBuilder.new()`:

- `append(String)` and `append_line(String)` append text.
- `append_byte(u8)` appends a raw byte, including NUL; UTF-8 validity is the caller's responsibility.
- `len()` returns the byte length as `i64`.
- `to_string()` copies the current buffer into an independent `String`.
- `clear()` resets the length and retains capacity for reuse.

Capacity grows geometrically, so repeated appends have amortized linear total
copying cost. The builder has reference semantics; aliases share mutations.
Repeated `to_string()` calls each copy the entire current contents.

## Identifier interning

Import `std.interner` for `StringInterner.new()`:

- `intern(text)` returns a stable, zero-based `i32` ID; equal text reuses an ID.
- `lookup(text)` returns an existing `i32?` without inserting.
- `resolve(id)` returns `String?`, or nil for an invalid ID.
- `len()` counts distinct strings.

IDs belong to one interner instance. There is deliberately no removal or reset
operation that could silently reuse a live ID. Interned strings remain retained
until the interner is destroyed.

## Compiler fixes found by these workloads

- Scalar replacement now excludes struct references extracted from another
  object's fields. Previously, nested reads could use uninitialized scalar locals
  and trap at O2/O3.
- Calls through function-valued fields now lower as indirect calls. Previously,
  the compiler emitted calls to nonexistent method symbols.

## Example and verification

[compiler_frontend.rl](../examples/compiler_frontend.rl) scans ASCII identifiers,
records source offsets, interns names, counts uses, and emits a report. It is a
small compiler workload, not a complete lexer or parser.

`tests/test_compiler_std.py` exercises dictionary growth/removal/collision repair,
snapshots, destructor accounting, builder reuse and embedded NUL bytes, interner
growth, structural keys, captured callbacks, and nested struct reads at O0–O3.
`tests/test_type_aliases.py` covers aliases, diagnostics, imports/re-exports, and
native module metadata.

All 14 O0/O3 executions of the compiler-oriented library tests passed with an
AddressSanitizer-instrumented runtime (`detect_leaks=0` on macOS). Destructor
accounting separately verifies that snapshots and removals release owned values.
The C runtime also passes `cc -Wall -Wextra -fsyntax-only`.

Final full regression run: **984 passed, no skips**, in 609.07 seconds on
Apple Silicon macOS. This includes the earlier async networking and incremental
build work as well as the new compiler-rewrite foundations.
