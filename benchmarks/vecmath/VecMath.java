// vecmath — small-object temporary stress test (mirrors vecmath.rl).
// Java's natural shape is an object per vector; each operation allocates a
// fresh Vec3. The JIT's escape analysis is expected to scalar-replace the
// short-lived temporaries — that capability is part of what this measures.
public class VecMath {
    static final class Vec3 {
        final double x, y, z;

        Vec3(double x, double y, double z) {
            this.x = x;
            this.y = y;
            this.z = z;
        }

        Vec3 cross(Vec3 b) {
            return new Vec3(
                y * b.z - z * b.y,
                z * b.x - x * b.z,
                x * b.y - y * b.x);
        }

        Vec3 addMod(Vec3 b, double m) {
            return new Vec3(
                (((x + b.x) % m) + m) % m,
                (((y + b.y) % m) + m) % m,
                (((z + b.z) % m) + m) % m);
        }
    }

    static long lcgNext(long state) {
        return (state * 1664525L + 1013904223L) % 4294967296L;
    }

    public static void main(String[] args) {
        final long T = 8000000;
        final double M = 1048576.0;

        long state = 42;
        Vec3 acc = new Vec3(0.0, 0.0, 0.0);

        for (long t = 0; t < T; t++) {
            state = lcgNext(state);
            double a1 = (double) (state & 1023);
            state = lcgNext(state);
            double a2 = (double) (state & 1023);
            state = lcgNext(state);
            double a3 = (double) (state & 1023);
            Vec3 v1 = new Vec3(a1, a2, a3);

            state = lcgNext(state);
            double b1 = (double) (state & 1023);
            state = lcgNext(state);
            double b2 = (double) (state & 1023);
            state = lcgNext(state);
            double b3 = (double) (state & 1023);
            Vec3 v2 = new Vec3(b1, b2, b3);

            Vec3 c = v1.cross(v2);
            acc = acc.addMod(c, M);
        }

        long checksum = (long) acc.x * 3 + (long) acc.y * 5 + (long) acc.z * 7;
        System.out.println(checksum);
    }
}
