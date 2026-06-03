// binary_trees — allocation-churn benchmark (mirrors binary_trees.c / .rl).
// maxDepth=14; nodes are heap objects reclaimed by V8's GC.
"use strict";

function make(depth) {
  if (depth === 0) return { left: null, right: null };
  return { left: make(depth - 1), right: make(depth - 1) };
}

function check(t) {
  if (t === null) return 0;
  return 1 + check(t.left) + check(t.right);
}

function main() {
  const minDepth = 4;
  const maxDepth = 14;
  let total = 0;

  // stretch tree
  const stretchDepth = maxDepth + 1;
  const stretch = make(stretchDepth);
  total += check(stretch);

  // long-lived tree
  const longLived = make(maxDepth);

  // iteration loop
  for (let depth = minDepth; depth <= maxDepth; depth += 2) {
    const exp = maxDepth - depth + minDepth;
    const iterations = 1 << exp;
    for (let i = 0; i < iterations; i++) {
      const t = make(depth);
      total += check(t);
    }
  }

  // long-lived tree check
  total += check(longLived);

  console.log(total);
}

main();
