// word_freq — Dict<String,i64> stress test (mirrors word_freq.rl).
// V=2000 base-26 keys pre-built once; T=2,000,000 tokens from a Knuth MMIX LCG
// drive an insert-or-increment into Go's native map. Checksum matches the C
// output (== 2654435761*T + T).
package main

import "fmt"

// base-26, least-significant letter first: 0->"a", 25->"z", 26->"ba", ...
func makeWordKey(id int64) string {
	alphabet := "abcdefghijklmnopqrstuvwxyz"
	n := id
	result := ""
	for {
		digit := n % 26
		result += alphabet[digit : digit+1]
		n /= 26
		if n == 0 {
			break
		}
	}
	return result
}

func main() {
	var V int64 = 2000
	var T int64 = 2000000

	// Pre-build all V key strings once.
	keys := make([]string, V)
	for i := int64(0); i < V; i++ {
		keys[i] = makeWordKey(i)
	}

	counts := make(map[string]int64)

	var state int64 = 12345678901234567
	for t := int64(0); t < T; t++ {
		// Knuth MMIX LCG step (int64 overflow wraps in Go).
		state = state*6364136223846793005 + 1442695040888963407
		w := ((state >> 33) & 0x7FFFFFFF) % V
		counts[keys[w]]++
	}

	var checksum int64 = 0
	for _, c := range counts {
		checksum += c * 2654435761
	}
	checksum += T

	fmt.Println(checksum)
}
