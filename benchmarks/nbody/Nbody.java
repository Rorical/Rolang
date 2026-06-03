// nbody — N=1000 bodies, 20 steps, O(N^2) gravity (mirrors nbody.c / .rl).
// Uses the SAME 20-iteration Newton's-method sqrt as the C/Rolang versions.
// Java float arithmetic is strict (no implicit FMA), so output matches.
public class Nbody {
    static final int N = 1000;
    static final int STEPS = 20;
    static final double DT = 0.01;

    // Newton's method sqrt — 20 iterations, matches Rolang math.rl math_sqrt.
    static double mathSqrt(double x) {
        if (x <= 0.0) return 0.0;
        double guess = x;
        for (int i = 0; i < 20; i++) {
            guess = (guess + x / guess) * 0.5;
        }
        return guess;
    }

    static long lcgNext(long state) {
        return (state * 1664525 + 1013904223) % 4294967296L;
    }

    static double lcgFrac(long state) {
        return (double) (state & 65535L) / 65536.0;
    }

    public static void main(String[] args) {
        double[] px = new double[N];
        double[] py = new double[N];
        double[] pz = new double[N];
        double[] vx = new double[N];
        double[] vy = new double[N];
        double[] vz = new double[N];
        double[] mass = new double[N];

        // LCG initialization: seed = 12345
        long lcg = 12345;
        for (int i = 0; i < N; i++) {
            lcg = lcgNext(lcg);
            px[i] = lcgFrac(lcg) * 2.0 - 1.0;
            lcg = lcgNext(lcg);
            py[i] = lcgFrac(lcg) * 2.0 - 1.0;
            lcg = lcgNext(lcg);
            pz[i] = lcgFrac(lcg) * 2.0 - 1.0;
            lcg = lcgNext(lcg);
            vx[i] = lcgFrac(lcg) * 0.2 - 0.1;
            lcg = lcgNext(lcg);
            vy[i] = lcgFrac(lcg) * 0.2 - 0.1;
            lcg = lcgNext(lcg);
            vz[i] = lcgFrac(lcg) * 0.2 - 0.1;
            lcg = lcgNext(lcg);
            mass[i] = lcgFrac(lcg) * 0.1 + 0.05;
        }

        // N-body simulation
        for (int step = 0; step < STEPS; step++) {
            for (int i = 0; i < N; i++) {
                for (int j = i + 1; j < N; j++) {
                    double dx = px[j] - px[i];
                    double dy = py[j] - py[i];
                    double dz = pz[j] - pz[i];
                    double distSq = dx * dx + dy * dy + dz * dz + 1e-10;
                    double dist = mathSqrt(distSq);
                    double force = DT / (distSq * dist);
                    double fx = dx * force;
                    double fy = dy * force;
                    double fz = dz * force;
                    vx[i] = vx[i] + fx * mass[j];
                    vy[i] = vy[i] + fy * mass[j];
                    vz[i] = vz[i] + fz * mass[j];
                    vx[j] = vx[j] - fx * mass[i];
                    vy[j] = vy[j] - fy * mass[i];
                    vz[j] = vz[j] - fz * mass[i];
                }
            }
            for (int i = 0; i < N; i++) {
                px[i] = px[i] + vx[i] * DT;
                py[i] = py[i] + vy[i] * DT;
                pz[i] = pz[i] + vz[i] * DT;
            }
        }

        // Total kinetic + potential energy
        double energy = 0.0;
        for (int i = 0; i < N; i++) {
            energy = energy + 0.5 * mass[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i]);
        }
        for (int i = 0; i < N; i++) {
            for (int j = i + 1; j < N; j++) {
                double dx = px[j] - px[i];
                double dy = py[j] - py[i];
                double dz = pz[j] - pz[i];
                double dist = mathSqrt(dx * dx + dy * dy + dz * dz + 1e-10);
                energy = energy - mass[i] * mass[j] / dist;
            }
        }

        long result = (long) (energy * 1000.0);
        System.out.println(result);
    }
}
