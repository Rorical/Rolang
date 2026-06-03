SRC = '{"users":[{"id":1,"name":"Alice","active":true,"scores":[85,92,78]},{"id":2,"name":"Bob","active":false,"scores":[91,88,95]},{"id":3,"name":"Charlie","active":true,"scores":[76,84,90]}],"metadata":{"version":2,"generated":false,"tags":["benchmark","json","test"],"config":{"timeout":30,"retries":3}}}'


class Parser:
    def __init__(self, src):
        self.src = src
        self.pos = 0

    def peek(self):
        return ord(self.src[self.pos]) if self.pos < len(self.src) else 0

    def advance(self):
        self.pos += 1

    def eof(self):
        return self.pos >= len(self.src)

    def skip_ws(self):
        while not self.eof() and chr(self.peek()) in ' \t\n\r':
            self.advance()

    @staticmethod
    def is_digit(c):
        return 48 <= c <= 57

    def parse_value(self):
        self.skip_ws()
        if self.eof():
            return 0
        c = self.peek()
        if c == 34:   # '"'
            return self.parse_string()
        if c == 116:  # 't'
            self.advance(); self.advance(); self.advance(); self.advance()
            return 1
        if c == 102:  # 'f'
            self.advance(); self.advance(); self.advance(); self.advance(); self.advance()
            return 0
        if c == 110:  # 'n'
            self.advance(); self.advance(); self.advance(); self.advance()
            return 0
        if c == 45 or self.is_digit(c):  # '-'
            return self.parse_number()
        if c == 91:   # '['
            return self.parse_array()
        if c == 123:  # '{'
            return self.parse_object()
        return 0

    def parse_string(self):
        self.advance()
        n = 0
        while self.peek() != 34:
            self.advance()
            n += 1
        self.advance()
        return n

    def parse_number(self):
        sign = 1
        if self.peek() == 45:
            self.advance()
            sign = -1
        val = 0
        while not self.eof() and self.is_digit(self.peek()):
            val = val * 10 + (self.peek() - 48)
            self.advance()
        return sign * val

    def parse_array(self):
        self.advance()
        self.skip_ws()
        if self.peek() == 93:
            self.advance()
            return 0
        s = 0
        while True:
            s += self.parse_value()
            self.skip_ws()
            if self.peek() == 44:
                self.advance()
            else:
                break
        self.advance()
        return s

    def parse_object(self):
        self.advance()
        self.skip_ws()
        if self.peek() == 125:
            self.advance()
            return 0
        s = 0
        while True:
            self.skip_ws()
            self.parse_string()
            self.skip_ws()
            self.advance()  # ':'
            s += self.parse_value()
            self.skip_ws()
            if self.peek() == 44:
                self.advance()
            else:
                break
        self.advance()
        return s


if __name__ == "__main__":
    parser = Parser(SRC)
    total = 0
    for _ in range(5000):
        parser.pos = 0
        total += parser.parse_value()
    print(total)
