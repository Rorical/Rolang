// vecmath — small-object temporary stress test (mirrors vecmath.rl).
// C's natural shape for a small vector is a by-value struct: every operation
// returns a fresh Vec3 VALUE, which the compiler keeps in registers. This is
// the baseline the heap-boxing languages are measured against.
#include <stdio.h>
#include <stdint.h>
#include <math.h>

typedef struct { double x, y, z; } Vec3;

static Vec3 cross(Vec3 a, Vec3 b) {
    Vec3 r;
    r.x = a.y * b.z - a.z * b.y;
    r.y = a.z * b.x - a.x * b.z;
    r.z = a.x * b.y - a.y * b.x;
    return r;
}

static double pmod(double v, double m) {
    return fmod(fmod(v, m) + m, m);
}

static Vec3 add_mod(Vec3 a, Vec3 b, double m) {
    Vec3 r;
    r.x = pmod(a.x + b.x, m);
    r.y = pmod(a.y + b.y, m);
    r.z = pmod(a.z + b.z, m);
    return r;
}

static int64_t lcg_next(int64_t state) {
    return (state * 1664525 + 1013904223) % 4294967296LL;
}

int main(void) {
    const int64_t T = 8000000;
    const double M = 1048576.0;

    int64_t state = 42;
    Vec3 acc = {0.0, 0.0, 0.0};

    for (int64_t t = 0; t < T; t++) {
        state = lcg_next(state);
        double a1 = (double)(state & 1023);
        state = lcg_next(state);
        double a2 = (double)(state & 1023);
        state = lcg_next(state);
        double a3 = (double)(state & 1023);
        Vec3 v1 = {a1, a2, a3};

        state = lcg_next(state);
        double b1 = (double)(state & 1023);
        state = lcg_next(state);
        double b2 = (double)(state & 1023);
        state = lcg_next(state);
        double b3 = (double)(state & 1023);
        Vec3 v2 = {b1, b2, b3};

        Vec3 c = cross(v1, v2);
        acc = add_mod(acc, c, M);
    }

    int64_t checksum = (int64_t)acc.x * 3 + (int64_t)acc.y * 5 + (int64_t)acc.z * 7;
    printf("%lld\n", (long long)checksum);
    return 0;
}
