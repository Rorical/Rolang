// Recursive Fibonacci in Rolang

def fib(n: i64) -> i64 {
    if n <= 1 {
        return n;
    }
    fib(n - 1) + fib(n - 2)
}

def main() -> i32 {
    let n: i64 = 10;
    fib(n) as i32
}
