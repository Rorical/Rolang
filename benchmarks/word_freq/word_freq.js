// word_freq — Dict<String,i64> stress test (mirrors word_freq.rl).
// V=2000 base-26 keys pre-built once; T=2,000,000 tokens from a Knuth MMIX LCG
// drive an insert-or-increment into a JS Map. The LCG needs full 64-bit wrap,
// so state is a BigInt; the checksum is accumulated in BigInt for an exact
// result. Matches the C output (== 2654435761*T + T).
"use strict";

// base-26, least-significant letter first: 0->"a", 25->"z", 26->"ba", ...
function makeWordKey(id) {
  const alphabet = "abcdefghijklmnopqrstuvwxyz";
  let n = id;
  let result = "";
  while (true) {
    const digit = n % 26;
    result += alphabet[digit];
    n = Math.floor(n / 26);
    if (n === 0) break;
  }
  return result;
}

function main() {
  const V = 2000;
  const T = 2000000;

  // Pre-build all V key strings once.
  const keys = new Array(V);
  for (let i = 0; i < V; i++) keys[i] = makeWordKey(i);

  const counts = new Map();

  let state = 12345678901234567n;
  const K = 6364136223846793005n;
  const C = 1442695040888963407n;
  const MASK31 = 0x7fffffffn;
  const Vb = BigInt(V);
  for (let t = 0; t < T; t++) {
    // Knuth MMIX LCG step, wrapped to signed 64-bit.
    state = BigInt.asIntN(64, state * K + C);
    const w = Number(((state >> 33n) & MASK31) % Vb);
    const k = keys[w];
    counts.set(k, (counts.get(k) || 0) + 1);
  }

  let checksum = 0n;
  for (const c of counts.values()) {
    checksum += BigInt(c) * 2654435761n;
  }
  checksum += BigInt(T);

  console.log(checksum.toString());
}

main();
