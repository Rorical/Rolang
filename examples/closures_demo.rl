// Closures Demo - Demonstrates lambda expressions with captures

def main() -> i32 {
    let x: i64 = 10;

    // Create a closure that captures 'x'
    let addX = { y: i64 in
        return x + y;
    };

    // Call the closure
    let result = addX(5);

    result as i32  // Should return 15
}
