// A small compiler-style tree with typed variants and recursive payloads.
enum Expr {
    case number(i32);
    case add(left: Expr, right: Expr);
    case missing;

    def evaluate() -> i32 {
        return switch self {
            case .number(let value): value;
            case .add(let left, let right): left.evaluate() + right.evaluate();
            case .missing: 0;
        };
    }
}

def main() -> i32 {
    let tree = Expr.add(left: Expr.number(20), right: Expr.number(22));
    switch tree {
        case .add(.number(let left), .number(let right)) where left > 0:
            return left + right;
        default:
            return tree.evaluate();
    }
}
