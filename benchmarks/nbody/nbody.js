// nbody — N=1000 bodies, 20 steps, O(N^2) gravity (mirrors nbody.c / .rl).
// Uses the SAME 20-iteration Newton's-method sqrt as the C/Rolang versions.
// JS numbers are IEEE-754 doubles with no implicit FMA, so output matches.
"use strict";

const N = 1000;
const STEPS = 20;
const DT = 0.01;

// Newton's method sqrt — 20 iterations, matches Rolang math.rl math_sqrt.
function mathSqrt(x) {
  if (x <= 0.0) return 0.0;
  let guess = x;
  for (let i = 0; i < 20; i++) {
    guess = (guess + x / guess) * 0.5;
  }
  return guess;
}

// LCG mod 2^32 — products stay below 2^53 so plain Number is exact.
function lcgNext(state) {
  return (state * 1664525 + 1013904223) % 4294967296;
}

function lcgFrac(state) {
  return (state & 65535) / 65536.0;
}

function main() {
  const px = new Float64Array(N);
  const py = new Float64Array(N);
  const pz = new Float64Array(N);
  const vx = new Float64Array(N);
  const vy = new Float64Array(N);
  const vz = new Float64Array(N);
  const mass = new Float64Array(N);

  // LCG initialization: seed = 12345
  let lcg = 12345;
  for (let i = 0; i < N; i++) {
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
  for (let step = 0; step < STEPS; step++) {
    for (let i = 0; i < N; i++) {
      for (let j = i + 1; j < N; j++) {
        const dx = px[j] - px[i];
        const dy = py[j] - py[i];
        const dz = pz[j] - pz[i];
        const distSq = dx * dx + dy * dy + dz * dz + 1e-10;
        const dist = mathSqrt(distSq);
        const force = DT / (distSq * dist);
        const fx = dx * force;
        const fy = dy * force;
        const fz = dz * force;
        vx[i] = vx[i] + fx * mass[j];
        vy[i] = vy[i] + fy * mass[j];
        vz[i] = vz[i] + fz * mass[j];
        vx[j] = vx[j] - fx * mass[i];
        vy[j] = vy[j] - fy * mass[i];
        vz[j] = vz[j] - fz * mass[i];
      }
    }
    for (let i = 0; i < N; i++) {
      px[i] = px[i] + vx[i] * DT;
      py[i] = py[i] + vy[i] * DT;
      pz[i] = pz[i] + vz[i] * DT;
    }
  }

  // Total kinetic + potential energy
  let energy = 0.0;
  for (let i = 0; i < N; i++) {
    energy = energy + 0.5 * mass[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i]);
  }
  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      const dx = px[j] - px[i];
      const dy = py[j] - py[i];
      const dz = pz[j] - pz[i];
      const dist = mathSqrt(dx * dx + dy * dy + dz * dz + 1e-10);
      energy = energy - mass[i] * mass[j] / dist;
    }
  }

  const result = Math.trunc(energy * 1000.0);
  console.log(result);
}

main();
