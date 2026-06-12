// cycle_churn — cyclic-garbage stress test (mirrors cycle_churn.rl).
// Rings are dropped with the cycle intact; Go's tracing GC reclaims them as
// ordinary garbage.
package main

import "fmt"

type Node struct {
	next  *Node
	value int64
}

func main() {
	const R = 150000
	const K = 64
	var total int64 = 0

	for r := int64(0); r < R; r++ {
		// Build the ring: first -> ... -> last -> first.
		first := &Node{value: r}
		prev := first
		for i := int64(1); i < K; i++ {
			n := &Node{value: r + i}
			prev.next = n
			prev = n
		}
		prev.next = first // close the cycle

		// Traverse exactly K steps, summing values.
		cur := first
		for step := 0; step < K; step++ {
			total += cur.value
			cur = cur.next
		}
		// Ring dropped here with the cycle intact.
	}

	fmt.Println(total)
}
