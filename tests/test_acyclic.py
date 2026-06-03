from rolang.acyclic import cyclic_capable_ids


def test_self_loop_is_cyclic():
    # 0 -> 0
    assert cyclic_capable_ids(num_ids=1, edges={0: [0]}, conservative=set()) == {0}


def test_two_cycle_is_cyclic():
    # 0 -> 1 -> 0
    assert cyclic_capable_ids(num_ids=2, edges={0: [1], 1: [0]}, conservative=set()) == {0, 1}


def test_acyclic_chain():
    # 0 -> 1 -> 2 (no back edge): none can reach themselves
    assert cyclic_capable_ids(num_ids=3, edges={0: [1], 1: [2]}, conservative=set()) == set()


def test_conservative_node_is_cyclic_and_taints_reachers():
    # 1 is conservative (points-to-unknown). 0 -> 1 means 0 can reach back to itself
    # through the unknown node, so both are cyclic-capable.
    result = cyclic_capable_ids(num_ids=2, edges={0: [1]}, conservative={1})
    assert result == {0, 1}


def test_node_pointing_into_cycle_but_not_reachable_is_acyclic():
    # 0 -> 1 -> 2 -> 1 : the 1<->2 cycle is cyclic, but 0 cannot reach 0.
    result = cyclic_capable_ids(num_ids=3, edges={0: [1], 1: [2], 2: [1]}, conservative=set())
    assert result == {1, 2}
