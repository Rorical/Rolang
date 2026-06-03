#include <stdio.h>

int mandelbrot(double c_re, double c_im, int max_iter) {
    double z_re = 0.0, z_im = 0.0;
    for (int i = 0; i < max_iter; i++) {
        double z_re_sq = z_re * z_re;
        double z_im_sq = z_im * z_im;
        if (z_re_sq + z_im_sq > 4.0) return i;
        z_im = 2.0 * z_re * z_im + c_im;
        z_re = z_re_sq - z_im_sq + c_re;
    }
    return max_iter;
}

int main(void) {
    long long sum = 0;
    for (int y = 0; y < 500; y++) {
        for (int x = 0; x < 500; x++) {
            double c_re = -2.0 + (double)x * 3.0 / 499.0;
            double c_im = -1.5 + (double)y * 3.0 / 499.0;
            sum += mandelbrot(c_re, c_im, 1000);
        }
    }
    printf("%lld\n", sum);
    return 0;
}
