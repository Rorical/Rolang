// binary_trees — allocation-churn benchmark (mirrors binary_trees.c / .rl).
// maxDepth=14; nodes are heap-allocated pointers reclaimed by Go's GC.
package main

import "fmt"

type Tree struct {
	left, right *Tree
}

func makeTree(depth int) *Tree {
	if depth == 0 {
		return &Tree{}
	}
	return &Tree{left: makeTree(depth - 1), right: makeTree(depth - 1)}
}

func check(t *Tree) int64 {
	if t == nil {
		return 0
	}
	return 1 + check(t.left) + check(t.right)
}

func main() {
	minDepth := 4
	maxDepth := 14
	var total int64 = 0

	// stretch tree
	stretchDepth := maxDepth + 1
	stretch := makeTree(stretchDepth)
	total += check(stretch)

	// long-lived tree
	longLived := makeTree(maxDepth)

	// iteration loop
	for depth := minDepth; depth <= maxDepth; depth += 2 {
		exp := maxDepth - depth + minDepth
		iterations := int64(1) << uint(exp)
		for i := int64(0); i < iterations; i++ {
			t := makeTree(depth)
			total += check(t)
		}
	}

	// long-lived tree check
	total += check(longLived)

	fmt.Println(total)
}
