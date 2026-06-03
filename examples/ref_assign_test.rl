// In v2 every struct lives on the heap and is passed by reference, so
// the explicit ``Ref<T>`` wrapper that v0.1 needed for in-place mutation
// is no longer required.
struct Complex {
    var re: i64
    var im: i64
}

def main() -> i32 {
    let c = Complex { re: 1, im: 2 };
    c.re = 10;
    c.im = 20;
    (c.re + c.im) as i32  // Expected: 30
}
