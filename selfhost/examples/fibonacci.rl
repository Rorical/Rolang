def fib(n: i32) -> i32 {
    if n < 2 { return n; }
    return fib(n - 1) + fib(n - 2);
}
def main() -> i32 {
    var sum = 0;
    for i in 0..<10 { sum = sum + fib(i); }
    return sum;
}
