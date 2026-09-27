// A small compiler workload: scan identifiers, intern names, count uses,
// preserve source offsets, and assemble a deterministic report.
import std.interner
import std.string_builder
import std.io

typealias SymbolId = i32;
typealias Offset = i32;
typealias Tokens = Vec<Token>;

struct Token {
    var symbol: SymbolId;
    var start: Offset;
    var end: Offset;
}

def is_identifier_start(byte: i32) -> Bool {
    return (byte >= 65 && byte <= 90) || (byte >= 97 && byte <= 122) || byte == 95;
}

def scan(source: String, names: StringInterner) -> Tokens {
    let tokens = Tokens.new();
    var i: Offset = 0;
    while i < (source.len() as i32) {
        if is_identifier_start(source.byte_at(i)) {
            let start = i;
            i = i + 1;
            while i < (source.len() as i32) {
                let byte = source.byte_at(i);
                if !is_identifier_start(byte) && !(byte >= 48 && byte <= 57) { break; }
                i = i + 1;
            }
            let name = source[start..<i];
            tokens.push(Token { symbol: names.intern(name), start: start, end: i });
        } else { i = i + 1; }
    }
    return tokens;
}

def main() -> i32 {
    let names = StringInterner.new();
    let tokens = scan("let answer = input + input;", names);
    let counts = Dict<SymbolId, i32>.with_capacity(8, 0);
    for token in tokens {
        let index = counts.entry_index(token.symbol, 0);
        counts.set_value_at(index, counts.value_at(index) + 1);
    }
    let report = StringBuilder.new();
    for entry in counts.entries() {
        if let name = names.resolve(entry.key) {
            report.append_line(f"{name}: {entry.value}");
        }
        counts.remove(entry.key);
    }
    print(report.to_string());
    if counts.len() != 0 { return 1; }
    return 0;
}
