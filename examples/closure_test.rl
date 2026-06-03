// Test file for closures

def main() -> i32 {
    let x: i64 = 10;
    let f = { a: i64 in
        return x + a;
    };

    // Call the closure
    let result = f(5);

    result as i32  // Should return 15
}
