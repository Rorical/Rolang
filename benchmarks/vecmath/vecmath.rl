// vecmath — small-object temporary stress test.
//
// Adversarial-by-design for Rolang: every struct lives on the heap, so each
// of the T iterations constructs 4 fresh Vec3 objects (v1, v2, cross,
// add_mod) that die immediately — exactly the pattern value-type languages
// (C/Rust/Go) keep in registers and Java's escape analysis scalar-replaces.
// This measures the cost of NOT having stack allocation / escape analysis.
//
// Determinism: all components are integer-valued f64 (LCG32 gives 0..1023,
// cross products < 2^41, accumulator wrapped mod 2^20), so every operation
// is exact in IEEE f64 — output is byte-identical across languages and
// immune to FMA contraction differences.
//
// LCG: state = (state * 1664525 + 1013904223) % 2^32  (same as nbody).
// Output: i64(acc.x)*3 + i64(acc.y)*5 + i64(acc.z)*7.

import "io.rl"

struct Vec3 {
    var x: f64
    var y: f64
    var z: f64

    def cross(other: Vec3) -> Vec3 {
        return Vec3 {
            x: self.y * other.z - self.z * other.y,
            y: self.z * other.x - self.x * other.z,
            z: self.x * other.y - self.y * other.x,
        };
    }

    // (self + other) mod m, componentwise, result in [0, m).
    def add_mod(other: Vec3, m: f64) -> Vec3 {
        return Vec3 {
            x: (((self.x + other.x) % m) + m) % m,
            y: (((self.y + other.y) % m) + m) % m,
            z: (((self.z + other.z) % m) + m) % m,
        };
    }
}

def lcg_next(state: i64) -> i64 {
    return (state * 1664525 + 1013904223) % 4294967296;
}

def main() -> i32 {
    let t_iters: i64 = 8000000;
    let m: f64 = 1048576.0;

    var state: i64 = 42;
    var acc = Vec3 { x: 0.0, y: 0.0, z: 0.0 };

    var t: i64 = 0;
    while t < t_iters {
        state = lcg_next(state);
        let a1 = (state & 1023) as f64;
        state = lcg_next(state);
        let a2 = (state & 1023) as f64;
        state = lcg_next(state);
        let a3 = (state & 1023) as f64;
        let v1 = Vec3 { x: a1, y: a2, z: a3 };

        state = lcg_next(state);
        let b1 = (state & 1023) as f64;
        state = lcg_next(state);
        let b2 = (state & 1023) as f64;
        state = lcg_next(state);
        let b3 = (state & 1023) as f64;
        let v2 = Vec3 { x: b1, y: b2, z: b3 };

        let c = v1.cross(v2);
        acc = acc.add_mod(c, m);
        t = t + 1;
    }

    let checksum = (acc.x as i64) * 3 + (acc.y as i64) * 5 + (acc.z as i64) * 7;
    println_i64(checksum);
    return 0;
}
