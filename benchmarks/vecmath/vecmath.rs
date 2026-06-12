// vecmath — small-object temporary stress test (mirrors vecmath.rl).
// Rust's natural shape is a Copy struct: methods return fresh Vec3 VALUES
// the compiler keeps in registers.

#[derive(Clone, Copy)]
struct Vec3 {
    x: f64,
    y: f64,
    z: f64,
}

impl Vec3 {
    fn cross(self, b: Vec3) -> Vec3 {
        Vec3 {
            x: self.y * b.z - self.z * b.y,
            y: self.z * b.x - self.x * b.z,
            z: self.x * b.y - self.y * b.x,
        }
    }

    fn add_mod(self, b: Vec3, m: f64) -> Vec3 {
        Vec3 {
            x: (((self.x + b.x) % m) + m) % m,
            y: (((self.y + b.y) % m) + m) % m,
            z: (((self.z + b.z) % m) + m) % m,
        }
    }
}

fn lcg_next(state: i64) -> i64 {
    (state.wrapping_mul(1664525).wrapping_add(1013904223)) % 4294967296
}

fn main() {
    const T: i64 = 8000000;
    const M: f64 = 1048576.0;

    let mut state: i64 = 42;
    let mut acc = Vec3 { x: 0.0, y: 0.0, z: 0.0 };

    for _ in 0..T {
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
        acc = acc.add_mod(c, M);
    }

    let checksum = (acc.x as i64) * 3 + (acc.y as i64) * 5 + (acc.z as i64) * 7;
    println!("{}", checksum);
}
