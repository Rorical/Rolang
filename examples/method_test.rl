// Simple method test

struct Point {
    var x: i64
    var y: i64

    def sum() -> i64 {
        self.x + self.y
    }
}

def main() -> i32 {
    let p = Point { x: 10, y: 20 };
    let s = p.sum();
    s as i32  // Should return 30
}
