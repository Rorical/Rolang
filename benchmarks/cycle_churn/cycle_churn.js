// cycle_churn — cyclic-garbage stress test (mirrors cycle_churn.rl).
// Rings are dropped with the cycle intact; V8's tracing GC reclaims them as
// ordinary garbage. total stays below 2^53, so plain numbers are exact.
"use strict";

class Node {
  constructor(value) {
    this.next = null;
    this.value = value;
  }
}

function main() {
  const R = 150000;
  const K = 64;
  let total = 0;

  for (let r = 0; r < R; r++) {
    // Build the ring: first -> ... -> last -> first.
    const first = new Node(r);
    let prev = first;
    for (let i = 1; i < K; i++) {
      const n = new Node(r + i);
      prev.next = n;
      prev = n;
    }
    prev.next = first; // close the cycle

    // Traverse exactly K steps, summing values.
    let cur = first;
    for (let step = 0; step < K; step++) {
      total += cur.value;
      cur = cur.next;
    }
    // Ring dropped here with the cycle intact.
  }

  console.log(total);
}

main();
