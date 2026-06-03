// Method Test - Methods defined in struct

struct Point {
    var x: i64
    var y: i64

    def sum() -> i64 {
        return self.x + self.y;
    }
}

def main() -> i32 {
    let p = Point { x: 10, y: 20 };
    let result = p.sum();
    result as i32  // Should return 30
}
