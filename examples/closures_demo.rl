// Closures Demo - Demonstrates lambda expressions with captures

def main() -> i32 {
    let x: i64 = 10;

    // Create a closure that captures 'x'
    let addX: (i64) -> i64 = { y in x + y };

    // Call the closure
    let result = addX(5);

    result as i32  // Should return 15
}
