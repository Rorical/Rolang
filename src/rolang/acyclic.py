"""Type-level cycle analysis for the GC.

A heap type T is *cyclic-capable* iff an instance of T can be part of a
reference cycle — equivalently, iff T can reach itself in the may-point-to
graph over descriptor ids. Types that point to "unknown" targets
(existentials, closures, containers whose element walk the GC can't see
statically) are `conservative`: modeled as pointing to a synthetic TOP node
that points to everything, so any type reaching a conservative node becomes
cyclic-capable. Everything not cyclic-capable is acyclic and safe to skip in
the cycle collector.
"""
from typing import Dict, List, Set


def cyclic_capable_ids(num_ids: int, edges: Dict[int, List[int]],
                       conservative: Set[int]) -> Set[int]:
    TOP = num_ids  # synthetic node id
    adj: Dict[int, List[int]] = {i: list(edges.get(i, [])) for i in range(num_ids)}
    for c in conservative:
        adj.setdefault(c, []).append(TOP)
    # TOP may point to anything (including itself).
    adj[TOP] = list(range(num_ids)) + [TOP]

    # A node is cyclic-capable iff it can reach itself via >=1 edge.
    def reaches_self(start: int) -> bool:
        stack = list(adj.get(start, []))
        seen: Set[int] = set()
        while stack:
            n = stack.pop()
            if n == start:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj.get(n, []))
        return False

    return {i for i in range(num_ids) if reaches_self(i)}
