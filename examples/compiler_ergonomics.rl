// A tiny compiler for decimal literals joined by '+'. Prints runnable C.
import std.result
import std.iterator
import std.code_writer
import std.io

enum Expr {
    case literal(value: i32);
    case add(left: Expr?, right: Expr?);
}

def parse_number(text: String) -> Result<Expr, String> {
    if text.is_empty() { return Result.err(error: "expected a number"); }
    var value = 0;
    var i = 0;
    while (i as i64) < text.len() {
        let digit = text.byte_at(i) - 48;
        if digit < 0 || digit > 9 {
            return Result.err(error: f"invalid digit at byte {i}: {text}");
        }
        if value > (2147483647 - digit) / 10 {
            return Result.err(error: "integer literal is too large");
        }
        value = value * 10 + digit;
        i = i + 1;
    }
    return Result.ok(value: Expr.literal(value: value));
}

def parse_expression(source: String) -> Result<Expr, String> {
    var root: Expr? = nil;
    for part in iter_vec(source.split("+")) {
        let next = parse_number(part.trim())?;
        if let previous = root {
            root = Expr.add(left: previous, right: next);
        } else { root = next; }
    }
    if let tree = root { return Result.ok(value: tree); }
    return Result.err(error: "expected an expression");
}

def emit_expression(node: Expr?) -> String {
    if let expr = node {
        switch expr {
            case .literal(let value): return f"{value}";
            case .add(let left, let right):
                return f"({emit_expression(left)} + {emit_expression(right)})";
        }
    }
    return "0";
}

def compile_expression(source: String) -> Result<String, String> {
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
