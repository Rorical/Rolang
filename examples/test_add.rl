def test1(a: i32, b: i32) -> i32 {
    return a + b;
}

def test2(a: i32, b: i32) -> i32 {
    let x = a;
    let y = b;
    return x + y;
}

def main() {
    let r1 = test1(3, 4);
    let r2 = test2(3, 4);
}
