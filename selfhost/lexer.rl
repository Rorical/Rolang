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
    return prefix + token.line.to_string() + ":" + token.column.to_string() + ": " + message;
}
pub def identifier_start(c: i32) -> Bool {
    return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c == 95;
}
pub def decimal_digit(c: i32) -> Bool { return c >= 48 && c <= 57; }
pub def lex(source: String) -> LexResult {
    let tokens = Vec<Token>.new();
    if source.len() > 2147483646 { return LexResult { tokens: tokens, error: "source file too large" }; }
    let length = source.len() as i32;
    var pos = 0;
    var line = 1;
    var column = 1;
    while pos < length {
        let c = source.byte_at(pos);
        if c == 32 || c == 9 || c == 13 { pos = pos + 1; column = column + 1; continue; }
        if c == 10 { pos = pos + 1; line = line + 1; column = 1; continue; }
        if c == 47 && pos + 1 < length {
            if source.byte_at(pos + 1) == 47 {
                while pos < length && source.byte_at(pos) != 10 { pos = pos + 1; column = column + 1; }
                continue;
            }
        }
        if c == 47 && pos + 1 < length && source.byte_at(pos + 1) == 42 {
            let opening = Token { text: "/*", kind: 3, line: line, column: column, source: "" };
            pos = pos + 2; column = column + 2;
            var closed = false;
            while pos < length {
                if source.byte_at(pos) == 42 && pos + 1 < length && source.byte_at(pos + 1) == 47 {
                    pos = pos + 2; column = column + 2; closed = true; break;
                }
                if source.byte_at(pos) == 10 { line = line + 1; column = 1; }
                else { column = column + 1; }
                pos = pos + 1;
            }
            if !closed { return LexResult { tokens: tokens, error: location(opening, "unterminated block comment") }; }
            continue;
        }
        if c == 34 {
            let start = pos;
            let opening = Token { text: "", kind: 4, line: line, column: column, source: "" };
            pos = pos + 1; column = column + 1;
            var closed = false;
            var escaped = false;
            while pos < length {
                let byte = source.byte_at(pos);
                pos = pos + 1;
                if byte == 10 { line = line + 1; column = 1; }
                else { column = column + 1; }
                if escaped { escaped = false; }
                else {
                    if byte == 34 { closed = true; break; }
                    if byte == 92 { escaped = true; }
                }
            }
            if !closed { return LexResult { tokens: tokens, error: location(opening, "unterminated string") }; }
            opening.text = source.substring(start, pos - start);
            tokens.push(opening);
            continue;
        }
        let start = pos;
        let start_column = column;
        var kind = 3;
        if identifier_start(c) {
            kind = 1;
            pos = pos + 1;
            while pos < length {
                let next = source.byte_at(pos);
                if !identifier_start(next) && !decimal_digit(next) { break; }
                pos = pos + 1;
            }
        } else {
            if decimal_digit(c) {
                kind = 2;
                pos = pos + 1;
                while pos < length && decimal_digit(source.byte_at(pos)) { pos = pos + 1; }
            } else {
                pos = pos + 1;
                if pos < length {
                    let pair = source.substring(start, 2);
                    if pair.equals("->") || pair.equals("==") || pair.equals("!=") || pair.equals("<=") || pair.equals(">=") || pair.equals("&&") || pair.equals("||") {
                        pos = pos + 1;
                    }
                }
                let spelling = source.substring(start, pos - start);
                if !(spelling.equals("->") || spelling.equals("==") || spelling.equals("!=") || spelling.equals("<=") || spelling.equals(">=") || spelling.equals("&&") || spelling.equals("||") || "(){}[]?.:;,+-*/%=<>!".contains(spelling)) {
                    let bad = Token { text: spelling, kind: 3, line: line, column: column, source: "" };
                    return LexResult { tokens: tokens, error: location(bad, "unsupported character") };
                }
            }
        }
        tokens.push(Token { text: source.substring(start, pos - start), kind: kind, line: line, column: start_column, source: "" });
        column = column + pos - start;
    }
    tokens.push(Token { text: "", kind: 0, line: line, column: column, source: "" });
    return LexResult { tokens: tokens, error: "" };
}
