import "lexer.rl"
import "ast.rl"

pub def reserved(text: String) -> Bool {
    return text.equals("def") || text.equals("return") || text.equals("let") || text.equals("var") || text.equals("if") || text.equals("else") || text.equals("while") || text.equals("true") || text.equals("false") || text.equals("i32") || text.equals("Bool");
}
pub def precedence(op: String) -> i32 {
    if op.equals("||") { return 1; }
    if op.equals("&&") { return 2; }
    if op.equals("==") || op.equals("!=") { return 3; }
    if op.equals("<") || op.equals(">") || op.equals("<=") || op.equals(">=") { return 4; }
    if op.equals("+") || op.equals("-") { return 5; }
    if op.equals("*") || op.equals("/") || op.equals("%") { return 6; }
    return 0;
}
pub struct Parser {
    pub var tokens: Vec<Token>;
    pub var pos: i32;
    pub var error: String;
    pub var depth: i32;
    pub var program: Program;

    pub static def new(tokens: Vec<Token>) -> Parser {
        return Parser { tokens: tokens, pos: 0, error: "", depth: 0,
            program: Program { functions: Vec<Function>.new(), expressions: Vec<Expression>.new(), statements: Vec<Statement>.new() } };
    }
    pub def peek() -> Token { return self.tokens.get(self.pos); }
    pub def advance() -> Token {
        let token = self.peek();
        if token.kind != 0 { self.pos = self.pos + 1; }
        return token;
    }
    pub def fail(token: Token, message: String) -> Void {
        if self.error.is_empty() { self.error = location(token, message); }
    }
    pub def take(text: String) -> Bool {
        if self.peek().text.equals(text) { self.advance(); return true; }
        return false;
    }
    pub def expect(text: String) -> Void {
        if !self.take(text) { self.fail(self.peek(), "expected '" + text + "'"); }
    }
    pub def name() -> Token {
        let token = self.advance();
        if token.kind != 1 || reserved(token.text) { self.fail(token, "expected identifier"); }
        return token;
    }
    pub def type_name() -> String {
        let token = self.advance();
        if !token.text.equals("i32") && !token.text.equals("Bool") {
            self.fail(token, "stage 0 supports only i32 and Bool types");
        }
        return token.text;
    }
    pub def add_expr(token: Token, kind: i32, left: i32, right: i32, args: Vec<i32>) -> i32 {
        let id = self.program.expressions.len();
        self.program.expressions.push(Expression { token: token, kind: kind, left: left, right: right, args: args });
        return id;
    }
    pub def expression(minimum: i32) -> i32 {
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(self.peek(), "expression nesting limit exceeded"); self.depth = self.depth - 1; return -1; }
        let token = self.advance();
        var left = -1;
        let empty = Vec<i32>.new();
        if token.kind == 2 { left = self.add_expr(token, 1, -1, -1, empty); }
        else {
            if token.text.equals("true") || token.text.equals("false") { left = self.add_expr(token, 2, -1, -1, empty); }
            else {
                if token.text.equals("(") {
                    left = self.expression(1); self.expect(")");
                } else {
                    if token.text.equals("-") || token.text.equals("!") || token.text.equals("+") {
                        let operand = self.expression(7);
                        left = self.add_expr(token, 5, operand, -1, empty);
                    } else {
                        if token.kind == 1 && !reserved(token.text) {
                            if self.take("(") {
                                let args = Vec<i32>.new();
                                if !self.take(")") {
                                    while self.error.is_empty() {
                                        args.push(self.expression(1));
                                        if !self.take(",") { break; }
                                    }
                                    self.expect(")");
                                }
                                left = self.add_expr(token, 6, -1, -1, args);
                            } else { left = self.add_expr(token, 3, -1, -1, empty); }
                        } else { self.fail(token, "expected expression"); }
                    }
                }
            }
        }
        while self.error.is_empty() {
            let op = self.peek();
            let rank = precedence(op.text);
            if rank < minimum { break; }
            self.advance();
            let right = self.expression(rank + 1);
            left = self.add_expr(op, 4, left, right, Vec<i32>.new());
        }
        self.depth = self.depth - 1;
        return left;
    }
    pub def block() -> Vec<i32> {
        let body = Vec<i32>.new();
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(self.peek(), "block nesting limit exceeded"); self.depth = self.depth - 1; return body; }
        self.expect("{");
        while self.error.is_empty() && !self.peek().text.equals("}") {
            if self.peek().kind == 0 { self.fail(self.peek(), "expected '}'"); break; }
            body.push(self.statement());
        }
        self.expect("}");
        self.depth = self.depth - 1;
        return body;
    }
    pub def statement() -> i32 {
        var token = self.peek();
        var kind = 6;
        var expr = -1;
        var annotation = "";
        var mutable = false;
        var body = Vec<i32>.new();
        var alternative = Vec<i32>.new();
        if self.take("return") { kind = 1; expr = self.expression(1); self.expect(";"); }
        else {
            if token.text.equals("let") || token.text.equals("var") {
                self.advance(); kind = 2; mutable = token.text.equals("var"); token = self.name();
                if self.take(":") { annotation = self.type_name(); }
                self.expect("="); expr = self.expression(1); self.expect(";");
            } else {
                if self.take("if") {
                    kind = 4; expr = self.expression(1); body = self.block();
                    if self.take("else") { alternative = self.block(); }
                } else {
                    if self.take("while") { kind = 5; expr = self.expression(1); body = self.block(); }
                    else {
                        if token.kind == 1 && self.tokens.get(self.pos + 1).text.equals("=") {
                            kind = 3; token = self.name(); self.expect("="); expr = self.expression(1); self.expect(";");
                        } else { expr = self.expression(1); self.expect(";"); }
                    }
                }
            }
        }
        let id = self.program.statements.len();
        self.program.statements.push(Statement { token: token, kind: kind, expr: expr, annotation: annotation, mutable: mutable, body: body, alternative: alternative });
        return id;
    }
    pub def parse() -> Void {
        while self.error.is_empty() && self.peek().kind != 0 {
            self.expect("def");
            let token = self.name();
            self.expect("(");
            let params = Vec<Parameter>.new();
            if !self.take(")") {
                while self.error.is_empty() {
                    let param = self.name(); self.expect(":");
                    params.push(Parameter { token: param, type_name: self.type_name() });
                    if !self.take(",") { break; }
                }
                self.expect(")");
            }
            self.expect("->");
            let return_type = self.type_name();
            let body = self.block();
            self.program.functions.push(Function { token: token, params: params, return_type: return_type, body: body });
        }
    }
}
