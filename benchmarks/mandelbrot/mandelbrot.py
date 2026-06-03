def mandelbrot(c_re, c_im, max_iter):
    z_re, z_im = 0.0, 0.0
    for i in range(max_iter):
        z_re_sq = z_re * z_re
        z_im_sq = z_im * z_im
        if z_re_sq + z_im_sq > 4.0:
            return i
        z_im = 2.0 * z_re * z_im + c_im
        z_re = z_re_sq - z_im_sq + c_re
    return max_iter

if __name__ == "__main__":
    total = 0
    for y in range(500):
        for x in range(500):
            c_re = -2.0 + x * 3.0 / 499.0
            c_im = -1.5 + y * 3.0 / 499.0
            total += mandelbrot(c_re, c_im, 1000)
    print(total)
