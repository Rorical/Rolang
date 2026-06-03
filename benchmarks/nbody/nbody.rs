// nbody — N=1000 bodies, 20 steps, O(N^2) gravity (mirrors nbody.c / .rl).
// Uses the SAME 20-iteration Newton's-method sqrt as the C/Rolang versions
// (not the hardware sqrt) so every language produces bit-identical output.
// Rust/LLVM does not contract a*b+c to FMA by default, so float results match.

const N: usize = 1000;
const STEPS: usize = 20;
const DT: f64 = 0.01;

// Newton's method sqrt — 20 iterations, matches Rolang math.rl math_sqrt.
fn math_sqrt(x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    let mut guess = x;
    for _ in 0..20 {
        guess = (guess + x / guess) * 0.5;
    }
    guess
}

// LCG: explicit mod 2^32 arithmetic (stays well within i64, no overflow).
fn lcg_next(state: i64) -> i64 {
    (state * 1664525 + 1013904223) % 4294967296
}

// Value in [0, 1) from lcg state (lower 16 bits / 65536).
fn lcg_frac(state: i64) -> f64 {
    ((state & 65535) as f64) / 65536.0
}

fn main() {
    let mut px = vec![0.0f64; N];
    let mut py = vec![0.0f64; N];
    let mut pz = vec![0.0f64; N];
    let mut vx = vec![0.0f64; N];
    let mut vy = vec![0.0f64; N];
    let mut vz = vec![0.0f64; N];
    let mut mass = vec![0.0f64; N];

    // LCG initialization: seed = 12345
    let mut lcg: i64 = 12345;
    for i in 0..N {
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

    // N-body simulation
    for _ in 0..STEPS {
        for i in 0..N {
            for j in (i + 1)..N {
                let dx = px[j] - px[i];
                let dy = py[j] - py[i];
                let dz = pz[j] - pz[i];
                let dist_sq = dx * dx + dy * dy + dz * dz + 1e-10;
                let dist = math_sqrt(dist_sq);
                let force = DT / (dist_sq * dist);
                let fx = dx * force;
                let fy = dy * force;
                let fz = dz * force;
                vx[i] = vx[i] + fx * mass[j];
                vy[i] = vy[i] + fy * mass[j];
                vz[i] = vz[i] + fz * mass[j];
                vx[j] = vx[j] - fx * mass[i];
                vy[j] = vy[j] - fy * mass[i];
                vz[j] = vz[j] - fz * mass[i];
            }
        }
        for i in 0..N {
            px[i] = px[i] + vx[i] * DT;
            py[i] = py[i] + vy[i] * DT;
            pz[i] = pz[i] + vz[i] * DT;
        }
    }

    // Total kinetic + potential energy
    let mut energy = 0.0f64;
    for i in 0..N {
        energy = energy + 0.5 * mass[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i]);
    }
    for i in 0..N {
        for j in (i + 1)..N {
            let dx = px[j] - px[i];
            let dy = py[j] - py[i];
            let dz = pz[j] - pz[i];
            let dist = math_sqrt(dx * dx + dy * dy + dz * dz + 1e-10);
            energy = energy - mass[i] * mass[j] / dist;
        }
    }

    let result = (energy * 1000.0) as i64;
    println!("{}", result);
}
