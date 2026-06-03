const SRC: &str = r#"{"users":[{"id":1,"name":"Alice","active":true,"scores":[85,92,78]},{"id":2,"name":"Bob","active":false,"scores":[91,88,95]},{"id":3,"name":"Charlie","active":true,"scores":[76,84,90]}],"metadata":{"version":2,"generated":false,"tags":["benchmark","json","test"],"config":{"timeout":30,"retries":3}}}"#;

struct Parser<'a> {
    src: &'a [u8],
    pos: usize,
}

impl<'a> Parser<'a> {
    fn peek(&self) -> u8 { self.src[self.pos] }
    fn advance(&mut self) { self.pos += 1; }
    fn eof(&self) -> bool { self.pos >= self.src.len() }

    fn skip_ws(&mut self) {
        while !self.eof() {
            let c = self.peek();
            if c == b' ' || c == b'\t' || c == b'\n' || c == b'\r' { self.advance(); }
            else { break; }
        }
    }

    fn is_digit(c: u8) -> bool { c >= b'0' && c <= b'9' }

    fn parse_value(&mut self) -> i64 {
        self.skip_ws();
        if self.eof() { return 0; }
        match self.peek() {
            b'"' => self.parse_string(),
            b't' => { self.advance(); self.advance(); self.advance(); self.advance(); 1 }
            b'f' => { self.advance(); self.advance(); self.advance(); self.advance(); self.advance(); 0 }
            b'n' => { self.advance(); self.advance(); self.advance(); self.advance(); 0 }
            b'-' | b'0'..=b'9' => self.parse_number(),
            b'[' => self.parse_array(),
            b'{' => self.parse_object(),
            _ => 0,
        }
    }

    fn parse_string(&mut self) -> i64 {
        self.advance();
        let mut n: i64 = 0;
        while self.peek() != b'"' {
            self.advance();
            n += 1;
        }
        self.advance();
        n
    }

    fn parse_number(&mut self) -> i64 {
        let mut sign: i64 = 1;
        if self.peek() == b'-' { self.advance(); sign = -1; }
        let mut val: i64 = 0;
        while !self.eof() && Self::is_digit(self.peek()) {
            val = val * 10 + ((self.peek() - b'0') as i64);
            self.advance();
        }
        sign * val
    }

    fn parse_array(&mut self) -> i64 {
        self.advance(); self.skip_ws();
        if self.peek() == b']' { self.advance(); return 0; }
        let mut sum: i64 = 0;
        loop {
            sum += self.parse_value();
            self.skip_ws();
            if self.peek() == b',' { self.advance(); } else { break; }
        }
        self.advance();
        sum
    }

    fn parse_object(&mut self) -> i64 {
        self.advance(); self.skip_ws();
        if self.peek() == b'}' { self.advance(); return 0; }
        let mut sum: i64 = 0;
        loop {
            self.skip_ws();
            self.parse_string();  // discard key
            self.skip_ws();
            self.advance();  // ':'
            sum += self.parse_value();
            self.skip_ws();
            if self.peek() == b',' { self.advance(); } else { break; }
        }
        self.advance();
        sum
    }
}

fn main() {
    let src_bytes = SRC.as_bytes();
    let mut parser = Parser { src: src_bytes, pos: 0 };
    let mut total: i64 = 0;
    for _ in 0..100_000 {
        parser.pos = 0;
        total += parser.parse_value();
    }
    println!("{}", total);
}
