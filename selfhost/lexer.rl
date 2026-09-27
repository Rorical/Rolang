import std.string_builder

// Stage-0 source scanner. Positions use one-based lines and byte columns.
pub struct Token {
    pub var text: String;
    pub var kind: i32; // 0 EOF, 1 identifier, 2 decimal integer, 3 punctuation, 4 quoted string
    pub var line: i32;
    pub var column: i32;
    pub var source: String;
}
pub struct LexResult {
    pub var tokens: Vec<Token>;
    pub var error: String;
}
pub def location(token: Token, message: String) -> String {
    var prefix = "";
    if !token.source.is_empty() { prefix = token.source + ":"; }
    return f"{prefix}{token.line}:{token.column}: {message}";
}
pub def identifier_start(c: i32) -> Bool {
    return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c == 95;
}
pub def decimal_digit(c: i32) -> Bool { return c >= 48 && c <= 57; }
// One scanner owns positions even while recursively scanning interpolation.
// String templates lower to ordinary concatenation and to_string calls.
struct Scanner {
    var source: String;
    var tokens: Vec<Token>;
    var pos: i32;
    var line: i32;
    var column: i32;
    var depth: i32;
    var error: String;

    def peek(offset: i32) -> i32 { return self.source.byte_at(self.pos + offset); }
    def step() -> Void {
        if self.peek(0) == 10 { self.line = self.line + 1; self.column = 1; }
        else { self.column = self.column + 1; }
        self.pos = self.pos + 1;
    }
    def token(text: String, kind: i32) -> Token {
        return Token { text: text, kind: kind, line: self.line, column: self.column, source: "" };
    }
    def emit(text: String, kind: i32, at: Token) -> Void {
        self.tokens.push(Token { text: text, kind: kind, line: at.line, column: at.column, source: at.source });
    }
    def fail(at: Token, message: String) -> Void {
        if self.error.is_empty() { self.error = location(at, message); }
    }
    def segment(text: StringBuilder, at: Token) -> Void {
        self.emit("\"" + text.to_string() + "\"", 4, at);
        text.clear();
    }
    def interpolation(at: Token) -> Void {
        var braces = 0;
        while self.peek(0) >= 0 && self.error.is_empty() {
            if self.peek(0) == 125 && braces == 0 { self.step(); return; }
            if self.peek(0) == 123 { braces = braces + 1; }
            if self.peek(0) == 125 { braces = braces - 1; }
            self.scan_token();
        }
        self.fail(at, "unterminated interpolation");
    }
    def string(raw: Bool, template: Bool, at: Token) -> Void {
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(at, "string nesting limit exceeded"); return; }
        self.step();
        var triple = false;
        if self.peek(0) == 34 && self.peek(1) == 34 {
            triple = true; self.step(); self.step();
        }
        let text = StringBuilder.new();
        if template { self.emit("(", 3, at); }
        var closed = false;
        while self.peek(0) >= 0 && self.error.is_empty() {
            let byte = self.peek(0);
            if byte == 34 && (!triple || (self.peek(1) == 34 && self.peek(2) == 34)) {
                self.step();
                if triple { self.step(); self.step(); }
                closed = true; break;
            }
            self.step();
            if !raw && byte == 92 {
                text.append_byte(92 as u8);
                if self.peek(0) >= 0 { text.append_byte(self.peek(0) as u8); self.step(); }
            } else {
                if template && byte == 123 {
                    if self.peek(0) == 123 { text.append_byte(123 as u8); self.step(); }
                    else {
                        self.segment(text, at); self.emit("+", 3, at); self.emit("(", 3, at);
                        self.interpolation(at);
                        self.emit(")", 3, at); self.emit(".", 3, at); self.emit("to_string", 1, at);
                        self.emit("(", 3, at); self.emit(")", 3, at); self.emit("+", 3, at);
                    }
                } else {
                    if template && byte == 125 {
                        if self.peek(0) == 125 { text.append_byte(125 as u8); self.step(); }
                        else { self.fail(at, "unescaped '}' in interpolation"); }
                    } else {
                        if byte == 34 || (raw && byte == 92) { text.append_byte(92 as u8); }
                        text.append_byte(byte as u8);
                    }
                }
            }
        }
        if !closed { self.fail(at, "unterminated string"); }
        self.segment(text, at);
        if template { self.emit(")", 3, at); }
        self.depth = self.depth - 1;
    }
    def scan_token() -> Void {
        let byte = self.peek(0); let at = self.token("", 3);
        if byte == 32 || byte == 9 || byte == 13 || byte == 10 { self.step(); return; }
        if byte == 47 && self.peek(1) == 47 {
            while self.peek(0) >= 0 && self.peek(0) != 10 { self.step(); }
            return;
        }
        if byte == 47 && self.peek(1) == 42 {
            self.step(); self.step();
            while self.peek(0) >= 0 {
                if self.peek(0) == 42 && self.peek(1) == 47 { self.step(); self.step(); return; }
                self.step();
            }
            self.fail(at, "unterminated block comment"); return;
        }
        if (byte == 114 || byte == 102) && self.peek(1) == 34 {
            self.step(); self.string(byte == 114, byte == 102, at); return;
        }
        if byte == 34 { self.string(false, false, at); return; }
        let start = self.pos; var kind = 3;
        self.step();
        if identifier_start(byte) {
            kind = 1;
            while identifier_start(self.peek(0)) || decimal_digit(self.peek(0)) { self.step(); }
        } else {
            if decimal_digit(byte) {
                kind = 2;
                while decimal_digit(self.peek(0)) { self.step(); }
            } else {
                let pair = self.source.substring(start, 2);
                if pair.equals("..") && (self.peek(1) == 60 || self.peek(1) == 46) {
                    self.step(); self.step();
                } else {
                    if pair.equals("->") || pair.equals("==") || pair.equals("!=") || pair.equals("<=") || pair.equals(">=") || pair.equals("&&") || pair.equals("||") { self.step(); }
                }
                let spelling = self.source[start..<self.pos];
                if !"(){}[];,:.+-*/%!=<>?&|".contains(spelling.substring(0, 1)) {
                    self.fail(at, "unsupported character"); return;
                }
            }
        }
        self.emit(self.source[start..<self.pos], kind, at);
    }
}

pub def lex(source: String) -> LexResult {
    let tokens = Vec<Token>.new();
    if source.len() > 2147483646 { return LexResult { tokens: tokens, error: "source file too large" }; }
    let scanner = Scanner { source: source, tokens: tokens, pos: 0, line: 1, column: 1, depth: 0, error: "" };
    while scanner.pos < (source.len() as i32) && scanner.error.is_empty() { scanner.scan_token(); }
    tokens.push(scanner.token("", 0));
    return LexResult { tokens: tokens, error: scanner.error };
}
