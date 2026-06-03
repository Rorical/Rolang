package main

import "fmt"

func mandelbrot(c_re, c_im float64, max_iter int32) int32 {
	z_re, z_im := 0.0, 0.0
	for i := int32(0); i < max_iter; i++ {
		z_re_sq := z_re * z_re
		z_im_sq := z_im * z_im
		if z_re_sq+z_im_sq > 4.0 {
			return i
		}
		z_im = 2.0*z_re*z_im + c_im
		z_re = z_re_sq - z_im_sq + c_re
	}
	return max_iter
}

func main() {
	var sum int64 = 0
	for y := 0; y < 500; y++ {
		for x := 0; x < 500; x++ {
			c_re := -2.0 + float64(x)*3.0/499.0
			c_im := -1.5 + float64(y)*3.0/499.0
			sum += int64(mandelbrot(c_re, c_im, 1000))
		}
	}
	fmt.Println(sum)
}
