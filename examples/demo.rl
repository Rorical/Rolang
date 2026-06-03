// Demo: Arithmetic and control flow

def square(x: i32) -> i32 {
    return x * x;
}

def sum_squares(a: i32, b: i32) -> i32 {
    let sq_a = square(a);
    let sq_b = square(b);
    let result = sq_a + sq_b;
    return result;
}

def abs(x: i32) -> i32 {
    if x < 0 {
        return 0 - x;
    }
    return x;
}

def main() {
    // 3^2 + 4^2 = 9 + 16 = 25
    let result = sum_squares(3, 4);

    // Test abs
    let neg = abs(0 - 10);

    // Combined: 25 + 10 = 35
    let final_val = result + neg;
}
