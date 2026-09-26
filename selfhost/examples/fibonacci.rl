def fib(n: i32) -> i32 {
    if n < 2 { return n; }
    return fib(n - 1) + fib(n - 2);
}
def main() -> i32 {
    var sum = 0;
    var i = 0;
    while i < 10 { sum = sum + fib(i); i = i + 1; }
    return sum;
}
