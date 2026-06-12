# cycle_churn — cyclic-garbage stress test (mirrors cycle_churn.rl).
# Rings are dropped with the cycle intact; CPython's refcounting cannot free
# them, so its generational cycle GC does the work — the same division of
# labor as Rolang's ARC + cycle collector.

import sys


class Node:
    __slots__ = ("next", "value")

    def __init__(self, value):
        self.next = None
        self.value = value


def main():
    R = 150000
    K = 64
    total = 0

    for r in range(R):
        # Build the ring: first -> ... -> last -> first.
        first = Node(r)
        prev = first
        for i in range(1, K):
            n = Node(r + i)
            prev.next = n
            prev = n
        prev.next = first  # close the cycle

        # Traverse exactly K steps, summing values.
        cur = first
        for _ in range(K):
            total += cur.value
            cur = cur.next
        # Ring dropped here with the cycle intact.

    sys.stdout.write(f"{total}\n")


if __name__ == "__main__":
    main()
