import "io.rl"

def fib(n: i64) -> i64 {
    if n <= 1 {
        return n;
    }
    fib(n - 1) + fib(n - 2)
}

def main() -> i32 {
    println_i64(fib(35));
    return 0;
}
