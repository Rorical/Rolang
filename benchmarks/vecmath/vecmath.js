// vecmath — small-object temporary stress test (mirrors vecmath.rl).
// Each operation allocates a fresh Vec3 object; V8's GC and escape analysis
// carry the load. The LCG is 32-bit so plain doubles stay exact (products
// < 2^53) — no BigInt needed in the hot loop.
"use strict";

class Vec3 {
  constructor(x, y, z) {
    this.x = x;
    this.y = y;
    this.z = z;
  }

  cross(b) {
    return new Vec3(
      this.y * b.z - this.z * b.y,
      this.z * b.x - this.x * b.z,
      this.x * b.y - this.y * b.x);
  }

  addMod(b, m) {
    return new Vec3(
      (((this.x + b.x) % m) + m) % m,
      (((this.y + b.y) % m) + m) % m,
      (((this.z + b.z) % m) + m) % m);
  }
}

function lcgNext(state) {
  return (state * 1664525 + 1013904223) % 4294967296;
}

function main() {
  const T = 8000000;
  const M = 1048576.0;

  let state = 42;
  let acc = new Vec3(0.0, 0.0, 0.0);

  for (let t = 0; t < T; t++) {
    state = lcgNext(state);
    const a1 = state & 1023;
    state = lcgNext(state);
    const a2 = state & 1023;
    state = lcgNext(state);
    const a3 = state & 1023;
    const v1 = new Vec3(a1, a2, a3);

    state = lcgNext(state);
    const b1 = state & 1023;
    state = lcgNext(state);
    const b2 = state & 1023;
    state = lcgNext(state);
    const b3 = state & 1023;
    const v2 = new Vec3(b1, b2, b3);

    const c = v1.cross(v2);
    acc = acc.addMod(c, M);
  }

  const checksum = Math.trunc(acc.x) * 3 + Math.trunc(acc.y) * 5 + Math.trunc(acc.z) * 7;
  console.log(checksum);
}

main();
