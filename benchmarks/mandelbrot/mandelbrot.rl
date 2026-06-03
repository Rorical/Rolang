import "io.rl"

def mandelbrot(c_re: f64, c_im: f64, max_iter: i32) -> i32 {
    var z_re: f64 = 0.0;
    var z_im: f64 = 0.0;
    var i: i32 = 0;
    while i < max_iter {
        let z_re_sq = z_re * z_re;
        let z_im_sq = z_im * z_im;
        if z_re_sq + z_im_sq > 4.0 {
            return i;
        }
        z_im = 2.0 * z_re * z_im + c_im;
        z_re = z_re_sq - z_im_sq + c_re;
        i = i + 1;
    }
    return max_iter;
}

def main() -> i32 {
    var sum: i64 = 0;
    var y: i32 = 0;
    while y < 500 {
        var x: i32 = 0;
        while x < 500 {
            let c_re = -2.0 + (x as f64) * 3.0 / 499.0;
            let c_im = -1.5 + (y as f64) * 3.0 / 499.0;
            sum = sum + (mandelbrot(c_re, c_im, 1000) as i64);
            x = x + 1;
        }
        y = y + 1;
    }
    println_i64(sum);
    return 0;
}
