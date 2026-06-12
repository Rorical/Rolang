# vecmath — small-object temporary stress test (mirrors vecmath.rl).
# Each operation allocates a fresh Vec3 instance; CPython's allocator and
# refcounting carry the load.

import sys


class Vec3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def cross(self, b):
        return Vec3(
            self.y * b.z - self.z * b.y,
            self.z * b.x - self.x * b.z,
            self.x * b.y - self.y * b.x,
        )

    def add_mod(self, b, m):
        return Vec3(
            (((self.x + b.x) % m) + m) % m,
            (((self.y + b.y) % m) + m) % m,
            (((self.z + b.z) % m) + m) % m,
        )


def main():
    T = 8000000
    M = 1048576.0

    state = 42
    acc = Vec3(0.0, 0.0, 0.0)

    for _ in range(T):
        state = (state * 1664525 + 1013904223) % 4294967296
        a1 = float(state & 1023)
        state = (state * 1664525 + 1013904223) % 4294967296
        a2 = float(state & 1023)
        state = (state * 1664525 + 1013904223) % 4294967296
        a3 = float(state & 1023)
        v1 = Vec3(a1, a2, a3)

        state = (state * 1664525 + 1013904223) % 4294967296
        b1 = float(state & 1023)
        state = (state * 1664525 + 1013904223) % 4294967296
        b2 = float(state & 1023)
        state = (state * 1664525 + 1013904223) % 4294967296
        b3 = float(state & 1023)
        v2 = Vec3(b1, b2, b3)

        c = v1.cross(v2)
        acc = acc.add_mod(c, M)

    checksum = int(acc.x) * 3 + int(acc.y) * 5 + int(acc.z) * 7
    sys.stdout.write(f"{checksum}\n")


if __name__ == "__main__":
    main()
