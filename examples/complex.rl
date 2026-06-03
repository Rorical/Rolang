// Complex number operations in Rolang — in-place style.
//
// Under v2, every struct lives on the heap and is reference-counted.
// Methods freely mutate self in place; no new allocations needed.
//
// Operators (+, -, *) are overloaded via `__add__`/`__sub__`/`__mul__` methods and
// *also* mutate the receiver.  Use `clone()` if you need the original.
//
// All math functions (sqrt, sin, cos, atan2) are implemented in pure
// Rolang via the standard library – no libm dependency.

import "math.rl"

struct Complex {
    var re: f64
    var im: f64

    // ---- helpers -----------------------------------------------

    def clone() -> Complex {
        Complex { re: self.re, im: self.im }
    }

    // ---- arithmetic (mutate self, return self for chaining) ----

    def __add__(other: Complex) -> Complex {
        self.re = self.re + other.re;
        self.im = self.im + other.im;
        return self;
    }

    def __sub__(other: Complex) -> Complex {
        self.re = self.re - other.re;
        self.im = self.im - other.im;
        return self;
    }

    def __mul__(other: Complex) -> Complex {
        let r = self.re * other.re - self.im * other.im;
        let i = self.re * other.im + self.im * other.re;
        self.re = r;
        self.im = i;
        return self;
    }

    // ---- unary operations (mutate self) ------------------------

    def conjugate() -> Complex {
        self.im = -self.im;
        return self;
    }

    def reciprocal() -> Complex {
        let d = self.re * self.re + self.im * self.im;
        let r = self.re / d;
        self.im = -self.im / d;
        self.re = r;
        return self;
    }

    def negate() -> Complex {
        self.re = -self.re;
        self.im = -self.im;
        return self;
    }

    // ---- queries -----------------------------------------------

    def mag_sq() -> f64 {
        self.re * self.re + self.im * self.im
    }

    def modulus() -> f64 {
        sqrt(self.mag_sq())
    }

    def arg() -> f64 {
        atan2(self.im, self.re)
    }

    // ---- rotation (mutate self) ---------------------------------

    def rotate(theta: f64) -> Complex {
        let s = sin(theta);
        let c = cos(theta);
        let r = self.re * c - self.im * s;
        let i = self.re * s + self.im * c;
        self.re = r;
        self.im = i;
        return self;
    }
}

// ===================================================================
// Mandelbrot — rewritten for in-place Complex operations
// ===================================================================

def mandelbrot(c_re: f64, c_im: f64, max_iter: i32) -> i32 {
    var z = Complex { re: 0.0, im: 0.0 };
    let c = Complex { re: c_re, im: c_im };
    var i: i32 = 0;

    while i < max_iter {
        if z.mag_sq() > 4.0 {
            return i;
        }
        z.__mul__(z);
        z.__add__(c);
        i = i + 1;
    }
    return max_iter;
}

// ===================================================================
// Main – demonstrate all operations
// ===================================================================

def main() -> i32 {
    let a = Complex { re: 3.0, im: 4.0 };
    let b = Complex { re: 1.0, im: 2.0 };

    // ---- basic arithmetic ----

    let sum  = a.clone();  sum.__add__(b);       // (3+4i)+(1+2i)=(4+6i)
    let diff = a.clone();  diff.__sub__(b);      // (3+4i)-(1+2i)=(2+2i)
    let prod = a.clone();  prod.__mul__(b);         // (3*1-4*2)+(3*2+4*1)=(-5+10i)

    // ---- unary operations ----

    let conj = a.clone();  conj.conjugate();    // (3-4i)
    let neg  = a.clone();  neg.negate();        // (-3-4i)

    // ---- queries (a = 3+4i) ----

    let msq = a.mag_sq();                       // 9+16 = 25
    let modu = a.modulus();                     // sqrt(25) = 5
    let angle = a.arg();                        // atan2(4,3) ≈ 0.927

    // ---- rotation: rotate (1+0i) by 90° → (0+1i) ----

    let unit = Complex { re: 1.0, im: 0.0 };
    unit.rotate(1.57079632679);                 // ≈ π/2

    // ---- Mandelbrot: point (0,0) should reach max_iter ----

    let mb = mandelbrot(0.0, 0.0, 100);

    // ---- verify results ----
    // sum = (4,6),   diff = (2,2),   prod = (-5,10)
    // conj = (3,-4), neg = (-3,-4)
    // a = (3,4), msq = 25, modu = 5, angle ≈ 0.927
    // unit after rotation ≈ (0, 1)
    // mb = 100

    return 0;
}
