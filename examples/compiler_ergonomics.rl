// A tiny compiler for decimal literals joined by '+'. Prints runnable C.
import std.result
import std.iterator
import std.code_writer
import std.io

typealias ParseResult<T> = Result<T, String>;

enum Expr {
    case literal(value: i32);
    case add(left: Expr?, right: Expr?);
}

def parse_number(text: String) -> ParseResult<Expr> {
    if text.is_empty() { return Result.err(error: "expected a number"); }
    var value = 0;
    for i in 0..<(text.len() as i32) {
        let digit = text.byte_at(i) - 48;
        if digit < 0 || digit > 9 {
            return Result.err(error: f"invalid digit at byte {i}: {text}");
        }
        if value > (2147483647 - digit) / 10 {
            return Result.err(error: "integer literal is too large");
        }
        value = value * 10 + digit;
    }
    return Result.ok(value: Expr.literal(value: value));
}

def parse_expression(source: String) -> ParseResult<Expr> {
    var root: Expr? = nil;
    for part in source.split("+").iter().map({ part in part.trim() }) {
        let next = parse_number(part)?;
        if let previous = root {
            root = Expr.add(left: previous, right: next);
        } else { root = next; }
    }
    if let tree = root { return Result.ok(value: tree); }
    return Result.err(error: "expected an expression");
}

def emit_expression(node: Expr?) -> String {
    guard let expr = node else { return "0"; }
    return switch expr {
        case .literal(let value): f"{value}";
        case .add(let left, let right):
            f"({emit_expression(left)} + {emit_expression(right)})";
    };
}

def compile_expression(source: String) -> ParseResult<String> {
    let tree = parse_expression(source)?;
    let writer = CodeWriter.new();
    writer.line("int main(void) {");
    writer.indent();
    writer.line(f"return {emit_expression(tree)};");
    writer.dedent();
    writer.line("}");
    return Result.ok(value: writer.to_string());
}

def main() -> i32 {
    switch compile_expression("20 + 22") {
        case .ok(let output): print(output); return 0;
        case .err(let diagnostic): println(diagnostic); return 1;
    }
}
