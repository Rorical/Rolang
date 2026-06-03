// nbody — N=1000 bodies, 20 steps, O(N^2) gravity (mirrors nbody.c / .rl).
// Uses the SAME 20-iteration Newton's-method sqrt as the C/Rolang versions
// (not the hardware sqrt). The final output is an integer (energy*1000
// truncated), which is robust to the sub-ULP differences FMA contraction
// would introduce, so this stays idiomatic Go and matches the baseline.
package main

import "fmt"

const (
	N     = 1000
	STEPS = 20
	DT    = 0.01
)

func mathSqrt(x float64) float64 {
	if x <= 0.0 {
		return 0.0
	}
	guess := x
	for i := 0; i < 20; i++ {
		guess = (guess + x/guess) * 0.5
	}
	return guess
}

func lcgNext(state int64) int64 {
	return (state*1664525 + 1013904223) % 4294967296
}

func lcgFrac(state int64) float64 {
	return float64(state&65535) / 65536.0
}

func main() {
	px := make([]float64, N)
	py := make([]float64, N)
	pz := make([]float64, N)
	vx := make([]float64, N)
	vy := make([]float64, N)
	vz := make([]float64, N)
	mass := make([]float64, N)

	// LCG initialization: seed = 12345
	var lcg int64 = 12345
	for i := 0; i < N; i++ {
		lcg = lcgNext(lcg)
		px[i] = lcgFrac(lcg)*2.0 - 1.0
		lcg = lcgNext(lcg)
		py[i] = lcgFrac(lcg)*2.0 - 1.0
		lcg = lcgNext(lcg)
		pz[i] = lcgFrac(lcg)*2.0 - 1.0
		lcg = lcgNext(lcg)
		vx[i] = lcgFrac(lcg)*0.2 - 0.1
		lcg = lcgNext(lcg)
		vy[i] = lcgFrac(lcg)*0.2 - 0.1
		lcg = lcgNext(lcg)
		vz[i] = lcgFrac(lcg)*0.2 - 0.1
		lcg = lcgNext(lcg)
		mass[i] = lcgFrac(lcg)*0.1 + 0.05
	}

	// N-body simulation
	for step := 0; step < STEPS; step++ {
		for i := 0; i < N; i++ {
			for j := i + 1; j < N; j++ {
				dx := px[j] - px[i]
				dy := py[j] - py[i]
				dz := pz[j] - pz[i]
				distSq := dx*dx + dy*dy + dz*dz + 1e-10
				dist := mathSqrt(distSq)
				force := DT / (distSq * dist)
				fx := dx * force
				fy := dy * force
				fz := dz * force
				vx[i] = vx[i] + fx*mass[j]
				vy[i] = vy[i] + fy*mass[j]
				vz[i] = vz[i] + fz*mass[j]
				vx[j] = vx[j] - fx*mass[i]
				vy[j] = vy[j] - fy*mass[i]
				vz[j] = vz[j] - fz*mass[i]
			}
		}
		for i := 0; i < N; i++ {
			px[i] = px[i] + vx[i]*DT
			py[i] = py[i] + vy[i]*DT
			pz[i] = pz[i] + vz[i]*DT
		}
	}

	// Total kinetic + potential energy
	energy := 0.0
	for i := 0; i < N; i++ {
		energy = energy + 0.5*mass[i]*(vx[i]*vx[i]+vy[i]*vy[i]+vz[i]*vz[i])
	}
	for i := 0; i < N; i++ {
		for j := i + 1; j < N; j++ {
			dx := px[j] - px[i]
			dy := py[j] - py[i]
			dz := pz[j] - pz[i]
			dist := mathSqrt(dx*dx + dy*dy + dz*dz + 1e-10)
			energy = energy - mass[i]*mass[j]/dist
		}
	}

	result := int64(energy * 1000.0)
	fmt.Println(result)
}
