struct Point {
    var x: i64
    var y: i64
}

extension Point {
    def magnitude_sq() -> i64 {
        return self.x * self.x + self.y * self.y;
    }

    def add(other: Point) -> Void {
        self.x = self.x + other.x;
        self.y = self.y + other.y;
    }
}

def main() -> i32 {
    var p1 = Point { x: 3, y: 4 };
    let p2 = Point { x: 1, y: 2 };
    p1.add(p2);
    return (p1.x + p1.y) as i32;  // 4 + 6 = 10
}
