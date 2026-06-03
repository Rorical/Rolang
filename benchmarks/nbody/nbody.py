# nbody — N=1000 bodies, 20 steps, O(N^2) gravity (mirrors nbody.c / .rl).
# Uses the SAME 20-iteration Newton's-method sqrt as the C/Rolang versions.
#
# The hot loops inline the sqrt and cache the per-i values in locals. These are
# value-preserving transforms (identical float operations in identical order),
# done only so pure-CPython finishes comfortably under the runner's timeout.


def lcg_next(state):
    return (state * 1664525 + 1013904223) % 4294967296


def lcg_frac(state):
    return (state & 65535) / 65536.0


def main():
    N = 1000
    STEPS = 20
    DT = 0.01

    px = [0.0] * N
    py = [0.0] * N
    pz = [0.0] * N
    vx = [0.0] * N
    vy = [0.0] * N
    vz = [0.0] * N
    mass = [0.0] * N

    # LCG initialization: seed = 12345
    lcg = 12345
    for i in range(N):
        lcg = lcg_next(lcg)
        px[i] = lcg_frac(lcg) * 2.0 - 1.0
        lcg = lcg_next(lcg)
        py[i] = lcg_frac(lcg) * 2.0 - 1.0
        lcg = lcg_next(lcg)
        pz[i] = lcg_frac(lcg) * 2.0 - 1.0
        lcg = lcg_next(lcg)
        vx[i] = lcg_frac(lcg) * 0.2 - 0.1
        lcg = lcg_next(lcg)
        vy[i] = lcg_frac(lcg) * 0.2 - 0.1
        lcg = lcg_next(lcg)
        vz[i] = lcg_frac(lcg) * 0.2 - 0.1
        lcg = lcg_next(lcg)
        mass[i] = lcg_frac(lcg) * 0.1 + 0.05

    # N-body simulation
    for _ in range(STEPS):
        for i in range(N):
            pxi = px[i]
            pyi = py[i]
            pzi = pz[i]
            mi = mass[i]
            vxi = vx[i]
            vyi = vy[i]
            vzi = vz[i]
            for j in range(i + 1, N):
                dx = px[j] - pxi
                dy = py[j] - pyi
                dz = pz[j] - pzi
                dist_sq = dx * dx + dy * dy + dz * dz + 1e-10
                # inline math_sqrt(dist_sq): dist_sq > 0 so the guard never trips
                guess = dist_sq
                for _k in range(20):
                    guess = (guess + dist_sq / guess) * 0.5
                dist = guess
                force = DT / (dist_sq * dist)
                fx = dx * force
                fy = dy * force
                fz = dz * force
                mj = mass[j]
                vxi = vxi + fx * mj
                vyi = vyi + fy * mj
                vzi = vzi + fz * mj
                vx[j] = vx[j] - fx * mi
                vy[j] = vy[j] - fy * mi
                vz[j] = vz[j] - fz * mi
            vx[i] = vxi
            vy[i] = vyi
            vz[i] = vzi
        for i in range(N):
            px[i] = px[i] + vx[i] * DT
            py[i] = py[i] + vy[i] * DT
            pz[i] = pz[i] + vz[i] * DT

    # Total kinetic + potential energy
    energy = 0.0
    for i in range(N):
        vxi = vx[i]
        vyi = vy[i]
        vzi = vz[i]
        energy = energy + 0.5 * mass[i] * (vxi * vxi + vyi * vyi + vzi * vzi)
    for i in range(N):
        pxi = px[i]
        pyi = py[i]
        pzi = pz[i]
        mi = mass[i]
        for j in range(i + 1, N):
            dx = px[j] - pxi
            dy = py[j] - pyi
            dz = pz[j] - pzi
            dist_sq = dx * dx + dy * dy + dz * dz + 1e-10
            guess = dist_sq
            for _k in range(20):
                guess = (guess + dist_sq / guess) * 0.5
            dist = guess
            energy = energy - mi * mass[j] / dist

    result = int(energy * 1000.0)
    print(result)


if __name__ == "__main__":
    main()
