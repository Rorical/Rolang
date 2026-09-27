import "io.rl"
import "vec.rl"
import "math.rl"

// N-body gravitational simulation benchmark
// N=1000 bodies, 20 time steps, O(N^2) force computation
// Deterministic LCG initialization, integer output

def lcg_next(state: i64) -> i64 {
    return (state * 1664525 + 1013904223) % 4294967296;
}

def lcg_frac(state: i64) -> f64 {
    let bits: i64 = state & 65535;
    return (bits as f64) / 65536.0;
}

def math_sqrt_local(x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    var guess: f64 = x;
    for i in 0..<20 {
        guess = (guess + x / guess) * 0.5;
    }
    return guess;
}

def main() -> i32 {
    let n: i32 = 1000;
    let steps: i32 = 20;
    let dt: f64 = 0.01;

    var px = Vec<f64>.with_capacity(n);
    var py = Vec<f64>.with_capacity(n);
    var pz = Vec<f64>.with_capacity(n);
    var vx = Vec<f64>.with_capacity(n);
    var vy = Vec<f64>.with_capacity(n);
    var vz = Vec<f64>.with_capacity(n);
    var mass = Vec<f64>.with_capacity(n);

    // LCG initialization: seed = 12345
    var lcg: i64 = 12345;
    for init_i in 0..<n {
        lcg = lcg_next(lcg);
        px.push(lcg_frac(lcg) * 2.0 - 1.0);
        lcg = lcg_next(lcg);
        py.push(lcg_frac(lcg) * 2.0 - 1.0);
        lcg = lcg_next(lcg);
        pz.push(lcg_frac(lcg) * 2.0 - 1.0);
        lcg = lcg_next(lcg);
        vx.push(lcg_frac(lcg) * 0.2 - 0.1);
        lcg = lcg_next(lcg);
        vy.push(lcg_frac(lcg) * 0.2 - 0.1);
        lcg = lcg_next(lcg);
        vz.push(lcg_frac(lcg) * 0.2 - 0.1);
        lcg = lcg_next(lcg);
        mass.push(lcg_frac(lcg) * 0.1 + 0.05);
    }

    // N-body simulation: steps timesteps
    for step in 0..<steps {
        // Compute pairwise forces and update velocities
        for i in 0..<n {
            for j in i + 1..<n {
                let dx = px.get(j) - px.get(i);
                let dy = py.get(j) - py.get(i);
                let dz = pz.get(j) - pz.get(i);
                let dist_sq = dx*dx + dy*dy + dz*dz + 1e-10;
                let dist = math_sqrt_local(dist_sq);
                let force = dt / (dist_sq * dist);
                let fx = dx * force;
                let fy = dy * force;
                let fz = dz * force;
                let mj = mass.get(j);
                let mi = mass.get(i);
                vx.set(i, vx.get(i) + fx * mj);
                vy.set(i, vy.get(i) + fy * mj);
                vz.set(i, vz.get(i) + fz * mj);
                vx.set(j, vx.get(j) - fx * mi);
                vy.set(j, vy.get(j) - fy * mi);
                vz.set(j, vz.get(j) - fz * mi);
            }
        }
        // Update positions
        for pi in 0..<n {
            px.set(pi, px.get(pi) + vx.get(pi) * dt);
            py.set(pi, py.get(pi) + vy.get(pi) * dt);
            pz.set(pi, pz.get(pi) + vz.get(pi) * dt);
        }
    }

    // Compute total kinetic + potential energy
    var energy: f64 = 0.0;
    // Kinetic energy
    for ei in 0..<n {
        let vxi = vx.get(ei);
        let vyi = vy.get(ei);
        let vzi = vz.get(ei);
        let mi = mass.get(ei);
        energy = energy + 0.5 * mi * (vxi*vxi + vyi*vyi + vzi*vzi);
    }
    // Potential energy
    for pi2 in 0..<n {
        for pj in pi2 + 1..<n {
            let dx = px.get(pj) - px.get(pi2);
            let dy = py.get(pj) - py.get(pi2);
            let dz = pz.get(pj) - pz.get(pi2);
            let dist = math_sqrt_local(dx*dx + dy*dy + dz*dz + 1e-10);
            energy = energy - mass.get(pi2) * mass.get(pj) / dist;
        }
    }

    // Output as integer (multiply by 1000)
    let result = (energy * 1000.0) as i64;
    println_i64(result);

    return 0;
}
