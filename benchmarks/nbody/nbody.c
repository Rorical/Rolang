#include <stdio.h>
#include <stdlib.h>

// N-body gravitational simulation benchmark
// N=1000 bodies, 20 time steps, O(N^2) force computation
// Deterministic LCG initialization (explicit mod 2^32 so it matches Rolang i64 exactly)
// Integer output to avoid float formatting issues
// Uses Newton's-method sqrt (20 iterations) matching Rolang's math_sqrt exactly

#define N 1000
#define STEPS 20
#define DT 0.01

// Newton's method sqrt — matches Rolang math.rl math_sqrt exactly (20 iterations)
static double math_sqrt(double x) {
    if (x <= 0.0) return 0.0;
    double guess = x;
    for (int i = 0; i < 20; i++) {
        guess = (guess + x / guess) * 0.5;
    }
    return guess;
}

// LCG: explicit mod 2^32 arithmetic
static long long lcg_next(long long state) {
    return (state * 1664525LL + 1013904223LL) % 4294967296LL;
}

// Extract a value in [0, 1) from lcg state (lower 16 bits / 65536.0)
static double lcg_frac(long long state) {
    return (double)(state & 0xFFFF) / 65536.0;
}

int main(void) {
    double *px = (double*)malloc(N * sizeof(double));
    double *py = (double*)malloc(N * sizeof(double));
    double *pz = (double*)malloc(N * sizeof(double));
    double *vx = (double*)malloc(N * sizeof(double));
    double *vy = (double*)malloc(N * sizeof(double));
    double *vz = (double*)malloc(N * sizeof(double));
    double *mass = (double*)malloc(N * sizeof(double));

    // LCG initialization: seed = 12345
    long long lcg = 12345LL;
    for (int i = 0; i < N; i++) {
        lcg = lcg_next(lcg);
        px[i] = lcg_frac(lcg) * 2.0 - 1.0;
        lcg = lcg_next(lcg);
        py[i] = lcg_frac(lcg) * 2.0 - 1.0;
        lcg = lcg_next(lcg);
        pz[i] = lcg_frac(lcg) * 2.0 - 1.0;
        lcg = lcg_next(lcg);
        vx[i] = lcg_frac(lcg) * 0.2 - 0.1;
        lcg = lcg_next(lcg);
        vy[i] = lcg_frac(lcg) * 0.2 - 0.1;
        lcg = lcg_next(lcg);
        vz[i] = lcg_frac(lcg) * 0.2 - 0.1;
        lcg = lcg_next(lcg);
        mass[i] = lcg_frac(lcg) * 0.1 + 0.05;
    }

    // N-body simulation: STEPS timesteps
    for (int step = 0; step < STEPS; step++) {
        // Compute pairwise forces and update velocities
        for (int i = 0; i < N; i++) {
            for (int j = i + 1; j < N; j++) {
                double dx = px[j] - px[i];
                double dy = py[j] - py[i];
                double dz = pz[j] - pz[i];
                double dist_sq = dx*dx + dy*dy + dz*dz + 1e-10;
                double dist = math_sqrt(dist_sq);
                double force = DT / (dist_sq * dist);
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
        // Update positions
        for (int i = 0; i < N; i++) {
            px[i] = px[i] + vx[i] * DT;
            py[i] = py[i] + vy[i] * DT;
            pz[i] = pz[i] + vz[i] * DT;
        }
    }

    // Compute total kinetic + potential energy
    double energy = 0.0;
    // Kinetic energy
    for (int i = 0; i < N; i++) {
        energy = energy + 0.5 * mass[i] * (vx[i]*vx[i] + vy[i]*vy[i] + vz[i]*vz[i]);
    }
    // Potential energy
    for (int i = 0; i < N; i++) {
        for (int j = i + 1; j < N; j++) {
            double dx = px[j] - px[i];
            double dy = py[j] - py[i];
            double dz = pz[j] - pz[i];
            double dist = math_sqrt(dx*dx + dy*dy + dz*dz + 1e-10);
            energy = energy - mass[i] * mass[j] / dist;
        }
    }

    // Output as integer (multiply by 1000 to preserve 3 decimal places)
    long long result = (long long)(energy * 1000.0);
    printf("%lld\n", result);

    free(px); free(py); free(pz);
    free(vx); free(vy); free(vz);
    free(mass);
    return 0;
}
