import "string.rl"
import "io.rl"
import "char.rl"

struct Parser {
    var src: String
    var pos: i32

    def peek() -> i32 { self.src.char_at(self.pos) }
    def advance() -> Void { self.pos = self.pos + 1; }
    def eof() -> Bool { self.pos as i64 >= self.src.len() }
    def is_digit(c: i32) -> Bool { char_is_digit(c) }

    def skip_ws() -> Void {
        while !self.eof() {
            let c = self.peek();
            if char_is_space(c) { self.advance(); }
            else { break; }
        }
    }

    def parse() -> i64 {
        self.skip_ws();
        if self.eof() { return 0; }
        let c = self.peek();
        if c == 34 { return self.parse_string() as i64; }
        if c == 116 { self.advance(); self.advance(); self.advance(); self.advance(); return 1; }
        if c == 102 { self.advance(); self.advance(); self.advance(); self.advance(); self.advance(); return 0; }
        if c == 110 { self.advance(); self.advance(); self.advance(); self.advance(); return 0; }
        if c == 45 || self.is_digit(c) { return self.parse_number(); }
        if c == 91 { return self.parse_array(); }
        if c == 123 { return self.parse_object(); }
        return 0;
    }

    def parse_string() -> i64 {
        self.advance();
        var len: i64 = 0;
        while self.peek() != 34 {
            self.advance();
            len = len + 1;
        }
        self.advance();
        return len;
    }

    def parse_number() -> i64 {
        var sign: i64 = 1;
        if self.peek() == 45 { self.advance(); sign = -1; }
        var val: i64 = 0;
        while !self.eof() && self.is_digit(self.peek()) {
            val = val * 10 + ((self.peek() - 48) as i64);
            self.advance();
        }
        return sign * val;
    }

    def parse_array() -> i64 {
        self.advance(); self.skip_ws();
        if self.peek() == 93 { self.advance(); return 0; }
        var sum: i64 = 0;
        while true {
            sum = sum + self.parse();
            self.skip_ws();
            if self.peek() == 44 { self.advance(); }
            else { break; }
        }
        self.advance();
        return sum;
    }

    def parse_object() -> i64 {
        self.advance(); self.skip_ws();
        if self.peek() == 125 { self.advance(); return 0; }
        var sum: i64 = 0;
        while true {
            self.skip_ws();
            self.parse_string();  // discard key
            self.skip_ws();
            self.advance();  // skip ':'
            sum = sum + self.parse();
            self.skip_ws();
            if self.peek() == 44 { self.advance(); }
            else { break; }
        }
        self.advance();
        return sum;
    }
}

def main() -> i32 {
    var src = "{\"users\":[{\"id\":1,\"name\":\"Alice\",\"active\":true,\"scores\":[85,92,78]},{\"id\":2,\"name\":\"Bob\",\"active\":false,\"scores\":[91,88,95]},{\"id\":3,\"name\":\"Charlie\",\"active\":true,\"scores\":[76,84,90]}],\"metadata\":{\"version\":2,\"generated\":false,\"tags\":[\"benchmark\",\"json\",\"test\"],\"config\":{\"timeout\":30,\"retries\":3}}}";
    var total: i64 = 0;
    var parser = Parser { src: src, pos: 0 };
    var iter: i32 = 0;
    while iter < 100000 {
        parser.pos = 0;
        total = total + parser.parse();
        iter = iter + 1;
    }
    println_i64(total);
    return 0;
}
