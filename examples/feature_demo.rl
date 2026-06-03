// Feature Demo - Basic language features

struct Point {
    var x: i64
    var y: i64
}

def add(a: Point, b: Point) -> Point {
    Point { x: a.x + b.x, y: a.y + b.y }
}

def magnitude_sq(p: Point) -> i64 {
    p.x * p.x + p.y * p.y
}

def main() -> i32 {
    let p1 = Point { x: 3, y: 4 };
    let p2 = Point { x: 1, y: 2 };

    let sum = add(p1, p2);
    let mag = magnitude_sq(sum);

    // sum = (4, 6), mag = 16 + 36 = 52
    mag as i32
}
