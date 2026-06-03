# binary_trees — allocation-churn benchmark (mirrors binary_trees.c / .rl).
# maxDepth=14; nodes are heap objects reclaimed by CPython's refcount/GC.

import sys


class Tree:
    __slots__ = ("left", "right")

    def __init__(self, left, right):
        self.left = left
        self.right = right


def make(depth):
    if depth == 0:
        return Tree(None, None)
    return Tree(make(depth - 1), make(depth - 1))


def check(t):
    if t is None:
        return 0
    return 1 + check(t.left) + check(t.right)


def main():
    min_depth = 4
    max_depth = 14
    total = 0

    # stretch tree
    stretch_depth = max_depth + 1
    stretch = make(stretch_depth)
    total += check(stretch)
    del stretch

    # long-lived tree
    long_lived = make(max_depth)

    # iteration loop
    depth = min_depth
    while depth <= max_depth:
        exp = max_depth - depth + min_depth
        iterations = 1 << exp
        for _ in range(iterations):
            t = make(depth)
            total += check(t)
        depth += 2

    # long-lived tree check
    total += check(long_lived)

    print(total)


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
