// vecmath — small-object temporary stress test (mirrors vecmath.rl).
// Go's natural shape is a by-value struct: methods return fresh Vec3 VALUES
// that escape analysis keeps on the stack / in registers.
package main

import (
	"fmt"
	"math"
)

type Vec3 struct{ x, y, z float64 }

func (a Vec3) cross(b Vec3) Vec3 {
	return Vec3{
		x: a.y*b.z - a.z*b.y,
		y: a.z*b.x - a.x*b.z,
		z: a.x*b.y - a.y*b.x,
	}
}

func pmod(v, m float64) float64 {
	return math.Mod(math.Mod(v, m)+m, m)
}

func (a Vec3) addMod(b Vec3, m float64) Vec3 {
	return Vec3{
		x: pmod(a.x+b.x, m),
		y: pmod(a.y+b.y, m),
		z: pmod(a.z+b.z, m),
	}
}

func lcgNext(state int64) int64 {
	return (state*1664525 + 1013904223) % 4294967296
}

func main() {
	const T = 8000000
	const M = 1048576.0

	var state int64 = 42
	acc := Vec3{}

	for t := 0; t < T; t++ {
		state = lcgNext(state)
		a1 := float64(state & 1023)
		state = lcgNext(state)
		a2 := float64(state & 1023)
		state = lcgNext(state)
		a3 := float64(state & 1023)
		v1 := Vec3{a1, a2, a3}

		state = lcgNext(state)
		b1 := float64(state & 1023)
		state = lcgNext(state)
		b2 := float64(state & 1023)
		state = lcgNext(state)
		b3 := float64(state & 1023)
		v2 := Vec3{b1, b2, b3}

		c := v1.cross(v2)
		acc = acc.addMod(c, M)
	}

	checksum := int64(acc.x)*3 + int64(acc.y)*5 + int64(acc.z)*7
	fmt.Println(checksum)
}
