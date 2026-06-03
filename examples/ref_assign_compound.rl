// In v2 every struct lives on the heap with ARC; field assignment through
// the value name is observed by all aliases, so no ``Ref<T>`` wrapper is
// needed.
struct Complex {
    var re: i64
    var im: i64
}

def main() -> i32 {
    let c = Complex { re: 5, im: 10 };

    // Initial values
    let a = c.re;  // 5
    let b = c.im;  // 10

    // In-place mutation through the heap-allocated struct.
    c.re = a + 10;  // 15
    c.im = b + 5;   // 15

    // Read back modified values
    (c.re + c.im) as i32  // Expected: 30
}
