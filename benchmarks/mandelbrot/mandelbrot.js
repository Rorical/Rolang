function mandelbrot(c_re, c_im, max_iter) {
    let z_re = 0.0, z_im = 0.0;
    for (let i = 0; i < max_iter; i++) {
        const z_re_sq = z_re * z_re;
        const z_im_sq = z_im * z_im;
        if (z_re_sq + z_im_sq > 4.0) return i;
        z_im = 2.0 * z_re * z_im + c_im;
        z_re = z_re_sq - z_im_sq + c_re;
    }
    return max_iter;
}

let sum = 0;
for (let y = 0; y < 500; y++) {
    for (let x = 0; x < 500; x++) {
        const c_re = -2.0 + x * 3.0 / 499.0;
        const c_im = -1.5 + y * 3.0 / 499.0;
        sum += mandelbrot(c_re, c_im, 1000);
    }
}
console.log(sum);
