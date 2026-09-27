import "lexer.rl"
import "ast.rl"

pub def reserved(text: String) -> Bool {
    return text.equals("def") || text.equals("return") || text.equals("let") || text.equals("var") || text.equals("if") || text.equals("else") || text.equals("while") || text.equals("true") || text.equals("false") || text.equals("nil") || text.equals("pub") || text.equals("static") || text.equals("struct") || text.equals("import") || text.equals("typealias") || text.equals("for") || text.equals("in") || text.equals("as") || text.equals("break") || text.equals("continue") || text.equals("unsafe") || text.equals("guard") || text.equals("enum") || text.equals("case") || text.equals("default") || text.equals("where");
}
pub def primitive_type(text: String) -> Bool {
    return text.equals("i8") || text.equals("i16") || text.equals("i32") || text.equals("i64") || text.equals("u8") || text.equals("u16") || text.equals("u32") || text.equals("u64") || text.equals("f32") || text.equals("f64") || text.equals("Bool") || text.equals("Void") || text.equals("RawPtr");
}
pub def precedence(op: String) -> i32 {
    if op.equals("||") { return 1; }
    if op.equals("&&") { return 2; }
    if op.equals("==") || op.equals("!=") { return 4; }
    if op.equals("<") || op.equals(">") || op.equals("<=") || op.equals(">=") { return 4; }
    if op.equals("..<") || op.equals("...") { return 5; }
    if op.equals("+") || op.equals("-") { return 6; }
    if op.equals("*") || op.equals("/") || op.equals("%") { return 7; }
    return 0;
}
pub struct Parser {
    pub var tokens: Vec<Token>;
    pub var pos: i32;
    pub var error: String;
    pub var depth: i32;
    pub var allow_empty_literal: Bool;
    pub var program: Program;

    pub static def new(tokens: Vec<Token>) -> Parser {
        return Parser { tokens: tokens, pos: 0, error: "", depth: 0, allow_empty_literal: true,
            program: Program { functions: Vec<Function>.new(), expressions: Vec<Expression>.new(), statements: Vec<Statement>.new(), declarations: Vec<Declaration>.new() } };
    }
    pub def peek() -> Token { return self.tokens.get(self.pos); }
    pub def ahead(offset: i32) -> Token {
        if self.pos + offset >= self.tokens.len() { return self.tokens.get(self.tokens.len() - 1); }
        return self.tokens.get(self.pos + offset);
    }
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
        if !self.take(text) { self.fail(self.peek(), f"expected '{text}'"); }
    }
    pub def name() -> Token {
        let token = self.advance();
        if token.kind != 1 || reserved(token.text) { self.fail(token, "expected identifier"); }
        return token;
    }
    pub def type_name() -> String {
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(self.peek(), "type nesting limit exceeded"); self.depth = self.depth - 1; return ""; }
        var text = "";
        if self.take("[") {
            text = f"[{self.type_name()}";
            if self.take(":") { text = f"{text}:{self.type_name()}"; }
            self.expect("]"); text = f"{text}]";
        } else {
            if self.take("(") {
                text = "(";
                if !self.take(")") {
                    while self.error.is_empty() {
                        text = text + self.type_name();
                        if !self.take(",") { break; }
                        text = f"{text},";
                    }
                    self.expect(")");
                }
                self.expect("->"); text = f"{text})->{self.type_name()}";
            } else {
                text = self.name().text;
                let primitive = primitive_type(text);
                while !primitive && self.take(".") { text = f"{text}.{self.name().text}"; }
                if !primitive && self.take("<") {
                    text = f"{text}<";
                    while self.error.is_empty() {
                        text = text + self.type_name();
                        if !self.take(",") { break; }
                        text = f"{text},";
                    }
                    self.expect(">"); text = f"{text}>";
                }
            }
        }
        if self.take("?") { text = f"{text}?"; }
        self.depth = self.depth - 1;
        return text;
    }
    pub def generics() -> Vec<String> {
        let names = Vec<String>.new();
        if self.take("<") {
            while self.error.is_empty() {
                names.push(self.name().text);
                if !self.take(",") { break; }
            }
            self.expect(">");
        }
        return names;
    }
    pub def modifiers() -> String {
        var text = "";
        if self.take("pub") { text = "pub "; }
        if self.take("unsafe") { text = f"{text}unsafe "; }
        if self.take("static") { text = f"{text}static "; }
        return text;
    }
    pub def add_expr(token: Token, kind: i32, left: i32, right: i32, args: Vec<i32>) -> i32 {
        let id = self.program.expressions.len();
        self.program.expressions.push(Expression { token: token, kind: kind, left: left, right: right, args: args, labels: Vec<String>.new(), arms: Vec<SwitchArm>.new(), type_name: "" });
        return id;
    }
    // Look ahead without mutating tokens: relational '<' must remain an operator.
    pub def generic_reference() -> Bool {
        if !self.peek().text.equals("<") { return false; }
        var offset = 0;
        var balance = 0;
        while self.ahead(offset).kind != 0 {
            let text = self.ahead(offset).text;
            if text.equals("<") { balance = balance + 1; }
            if text.equals(">") {
                balance = balance - 1;
                if balance == 0 { return self.ahead(offset + 1).text.equals(".") || self.ahead(offset + 1).text.equals("{"); }
            }
            if text.equals(";") || text.equals("{") || text.equals("}") { return false; }
            offset = offset + 1;
        }
        return false;
    }
    pub def value_expression() -> i32 {
        let previous = self.allow_empty_literal; self.allow_empty_literal = true;
        let result = self.expression(1); self.allow_empty_literal = previous; return result;
    }
    pub def condition() -> i32 {
        let previous = self.allow_empty_literal; self.allow_empty_literal = false;
        let result = self.expression(1); self.allow_empty_literal = previous; return result;
    }
    pub def reference_name(id: i32) -> String {
        var current = id; var suffix = "";
        while current >= 0 {
            let node = self.program.expressions.get(current);
            if node.kind == 3 { return node.token.text + suffix; }
            if node.kind == 13 { return node.type_name + suffix; }
            if node.kind != 9 { return ""; }
            suffix = f".{node.token.text}{suffix}"; current = node.left;
        }
        return "";
    }
    pub def switch_subject() -> Bool {
        let token = self.peek();
        return token.kind == 1 || token.kind == 2 || token.kind == 4 || token.text.equals("(") || token.text.equals("!");
    }
    pub def expression(minimum: i32) -> i32 {
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(self.peek(), "expression nesting limit exceeded"); self.depth = self.depth - 1; return -1; }
        let token = self.advance();
        var left = -1;
        let empty = Vec<i32>.new();
        if token.text.equals("switch") && self.switch_subject() {
            let subject = self.condition(); let arms = self.switch_arms(true);
            left = self.add_expr(token, 16, subject, -1, empty);
            let switch_node = self.program.expressions.get(left); switch_node.arms = arms;
        } else { if token.text.equals(".") {
            left = self.add_expr(self.name(), 17, -1, -1, empty);
        } else { if token.kind == 2 { left = self.add_expr(token, 1, -1, -1, empty); }
        else {
            if token.kind == 4 { left = self.add_expr(token, 7, -1, -1, empty); }
            else {
                if token.text.equals("true") || token.text.equals("false") { left = self.add_expr(token, 2, -1, -1, empty); }
                else {
                    if token.text.equals("nil") { left = self.add_expr(token, 8, -1, -1, empty); }
                    else {
                        if token.text.equals("(") { left = self.value_expression(); self.expect(")"); }
                        else {
                            if token.text.equals("-") || token.text.equals("!") || token.text.equals("+") {
                                let operand = self.expression(8);
                                left = self.add_expr(token, 5, operand, -1, empty);
                            } else {
                                if token.kind == 1 && !reserved(token.text) {
                                    left = self.add_expr(token, 3, -1, -1, empty);
                                    if self.generic_reference() {
                                        self.pos = self.pos - 1;
                                        let type_name = self.type_name();
                                        let node = self.program.expressions.get(left);
                                        node.kind = 13; node.type_name = type_name;
                                    }
                                } else { self.fail(token, "expected expression"); }
                            }
                        }
                    }
                }
            }
        }
        } }
        while self.error.is_empty() {
            if self.take(".") {
                let member = self.name();
                left = self.add_expr(member, 9, left, -1, Vec<i32>.new());
                continue;
            }
            if self.take("(") {
                let callee = self.program.expressions.get(left);
                let args = Vec<i32>.new(); let labels = Vec<String>.new();
                if !self.take(")") {
                    while self.error.is_empty() {
                        var label = "";
                        if self.peek().kind == 1 && self.ahead(1).text.equals(":") { label = self.name().text; self.expect(":"); }
                        labels.push(label); args.push(self.value_expression());
                        if !self.take(",") { break; }
                    }
                    self.expect(")");
                }
                var kind = 14;
                var target = left;
                if callee.kind == 3 { kind = 6; target = -1; }
                left = self.add_expr(callee.token, kind, target, -1, args);
                let call_node = self.program.expressions.get(left); call_node.labels = labels;
                continue;
            }
            if self.take("[") {
                let index = self.value_expression(); self.expect("]");
                left = self.add_expr(token, 12, left, index, Vec<i32>.new()); continue;
            }
            let callee = self.program.expressions.get(left);
            // A field label distinguishes literals from control-flow blocks.
            // In conditions an empty brace pair starts the body, unless grouped.
            var literal = false;
            if self.peek().text.equals("{") && !self.reference_name(left).is_empty() {
                literal = self.ahead(1).kind == 1 && !reserved(self.ahead(1).text) && self.ahead(2).text.equals(":");
                if self.ahead(1).text.equals("}") && self.allow_empty_literal { literal = true; }
            }
            if literal {
                self.advance();
                let args = Vec<i32>.new(); let labels = Vec<String>.new();
                if !self.take("}") {
                    while self.error.is_empty() {
                        labels.push(self.name().text); self.expect(":"); args.push(self.value_expression());
                        if !self.take(",") || self.peek().text.equals("}") { break; }
                    }
                    self.expect("}");
                }
                let type_name = self.reference_name(left);
                left = self.add_expr(callee.token, 10, -1, -1, args);
                let node = self.program.expressions.get(left); node.labels = labels;
                node.type_name = type_name;
                continue;
            }
            if minimum <= 4 && self.take("as") {
                let type_name = self.type_name();
                left = self.add_expr(token, 11, left, -1, Vec<i32>.new());
                let node = self.program.expressions.get(left); node.type_name = type_name;
                continue;
            }
            let op = self.peek(); let rank = precedence(op.text);
            if rank < minimum { break; }
            self.advance(); let right = self.expression(rank + 1);
            var kind = 4;
            if op.text.equals("..<") || op.text.equals("...") { kind = 15; }
            left = self.add_expr(op, kind, left, right, Vec<i32>.new());
        }
        self.depth = self.depth - 1;
        return left;
    }
    pub def pattern() -> Pattern {
        let token = self.advance();
        var result = Pattern.wildcard(token);
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(token, "pattern nesting limit exceeded"); self.depth = self.depth - 1; return result; }
        if token.text.equals(".") {
            let name = self.name(); let children = Vec<Pattern>.new();
            if self.take("(") && !self.take(")") {
                while self.error.is_empty() {
                    children.push(self.pattern());
                    if !self.take(",") { break; }
                }
                self.expect(")");
            }
            result = Pattern.variant(name, children);
        } else { if token.text.equals("let") || token.text.equals("var") {
            result = Pattern.binding(self.name(), token.text.equals("var"));
        } else { if token.text.equals("_") { result = Pattern.wildcard(token); }
        else { if token.kind == 2 || token.kind == 4 || token.text.equals("true") || token.text.equals("false") || token.text.equals("nil") || token.text.equals("-") {
            self.pos = self.pos - 1; result = Pattern.literal(token, self.expression(8));
        } else { if token.kind == 1 && !reserved(token.text) { result = Pattern.binding(token, false); }
        else { self.fail(token, "unsupported switch pattern"); } } } } }
        self.depth = self.depth - 1; return result;
    }
    pub def switch_arms(values: Bool) -> Vec<SwitchArm> {
        let arms = Vec<SwitchArm>.new(); self.expect("{");
        self.depth = self.depth + 1;
        if self.depth > 128 { self.fail(self.peek(), "switch nesting limit exceeded"); self.depth = self.depth - 1; return arms; }
        while self.error.is_empty() && !self.peek().text.equals("}") {
            let token = self.peek();
            var pattern = Pattern.wildcard(token);
            if !self.take("default") { self.expect("case"); pattern = self.pattern(); }
            var guard_expr = -1; if self.take("where") { guard_expr = self.condition(); }
            self.expect(":");
            var value = -1; let body = Vec<i32>.new();
            if values { value = self.value_expression(); self.expect(";"); }
            else {
                while self.error.is_empty() && !self.peek().text.equals("case") && !self.peek().text.equals("default") && !self.peek().text.equals("}") {
                    if self.peek().kind == 0 { self.fail(self.peek(), "expected '}'"); break; }
                    body.push(self.statement());
                }
            }
            arms.push(SwitchArm { token: token, pattern: pattern, guard_expr: guard_expr, value: value, body: body });
        }
        self.expect("}"); self.depth = self.depth - 1; return arms;
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
        self.expect("}"); self.depth = self.depth - 1;
        return body;
    }
    pub def statement() -> i32 {
        var token = self.peek(); var kind = 6; var expr = -1; var target = -1;
        var annotation = ""; var mutable = false;
        var body = Vec<i32>.new(); var alternative = Vec<i32>.new();
        var arms = Vec<SwitchArm>.new();
        if self.take("switch") { kind = 16; expr = self.condition(); arms = self.switch_arms(false); }
        else { if self.take("guard") {
            kind = 15;
            if self.take("let") { kind = 14; token = self.name(); self.expect("="); }
            expr = self.condition(); self.expect("else"); alternative = self.block();
        } else {
        if self.take("return") {
            kind = 1;
            if !self.peek().text.equals(";") { expr = self.expression(1); }
            self.expect(";");
        } else {
            if token.text.equals("let") || token.text.equals("var") {
                self.advance(); kind = 2; mutable = token.text.equals("var"); token = self.name();
                if self.take(":") { annotation = self.type_name(); }
                if self.take("=") { expr = self.expression(1); }
                else { if !mutable || annotation.is_empty() { self.fail(self.peek(), "expected initializer"); } }
                self.expect(";");
            } else {
                if self.take("if") {
                    kind = 4;
                    if self.take("let") { kind = 9; token = self.name(); self.expect("="); }
                    expr = self.condition(); body = self.block();
                    if self.take("else") { alternative = self.block(); }
                } else {
                    if self.take("while") { kind = 5; expr = self.condition(); body = self.block(); }
                    else {
                        if self.take("for") { kind = 8; token = self.name(); self.expect("in"); expr = self.condition(); body = self.block(); }
                        else {
                            if self.take("break") { kind = 10; self.expect(";"); }
                            else {
                                if self.take("continue") { kind = 11; self.expect(";"); }
                                else {
                                    if self.take("unsafe") { kind = 12; body = self.block(); }
                                    else {
                                        if self.peek().text.equals("{") { kind = 7; body = self.block(); }
                                        else {
                                            expr = self.expression(1);
                                            if self.error.is_empty() && self.take("=") {
                                                target = expr; let node = self.program.expressions.get(target);
                                                kind = 13;
                                                if node.kind == 3 { kind = 3; token = node.token; }
                                                else { if node.kind != 9 && node.kind != 12 { self.fail(node.token, "invalid assignment target"); } }
                                                expr = self.expression(1);
                                            }
                                            self.expect(";");
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        }
        }
        let id = self.program.statements.len();
        self.program.statements.push(Statement { token: token, kind: kind, expr: expr, target: target, annotation: annotation, mutable: mutable, body: body, alternative: alternative, arms: arms });
        return id;
    }
    pub def function(owner: String, modifiers: String) -> i32 {
        self.expect("def"); let token = self.name(); let generics = self.generics(); self.expect("(");
        let params = Vec<Parameter>.new();
        if !self.take(")") {
            while self.error.is_empty() {
                let param = self.name(); self.expect(":");
                params.push(Parameter { token: param, type_name: self.type_name() });
                if !self.take(",") { break; }
            }
            self.expect(")");
        }
        var return_type = "Void";
        if self.take("->") { return_type = self.type_name(); }
        let body = self.block(); let id = self.program.functions.len();
        self.program.functions.push(Function { token: token, params: params, return_type: return_type, body: body, owner: owner, modifiers: modifiers, generics: generics });
        return id;
    }
    pub def parse() -> Void {
        while self.error.is_empty() && self.peek().kind != 0 {
            let modifiers = self.modifiers(); let token = self.peek();
            if modifiers.contains("unsafe") && !token.text.equals("def") { self.fail(token, "unsafe modifier requires def"); return; }
            if token.text.equals("def") { self.function("", modifiers); }
            else {
                var name = token; var kind = 0; var value = "";
                var generics = Vec<String>.new(); let fields = Vec<Field>.new(); let variants = Vec<Variant>.new(); let methods = Vec<i32>.new();
                if self.take("import") {
                    kind = 1;
                    if self.peek().kind == 4 { value = self.advance().text; }
                    else { value = self.name().text; while self.take(".") { value = f"{value}.{self.name().text}"; } }
                    if self.take("as") { name = self.name(); }
                    self.take(";");
                } else {
                    if self.take("typealias") {
                        kind = 3; name = self.name(); self.expect("="); value = self.type_name(); self.expect(";");
                    } else {
                        if self.peek().text.equals("struct") || self.peek().text.equals("enum") {
                            kind = 2; if self.advance().text.equals("enum") { kind = 4; } name = self.name(); generics = self.generics(); self.expect("{");
                            while self.error.is_empty() && !self.peek().text.equals("}") {
                                let member_modifiers = self.modifiers();
                                if member_modifiers.contains("unsafe") && !self.peek().text.equals("def") { self.fail(self.peek(), "unsafe modifier requires def"); return; }
                                if self.peek().text.equals("def") { methods.push(self.function(name.text, member_modifiers)); }
                                else { if kind == 4 {
                                    self.expect("case");
                                    while self.error.is_empty() {
                                        let case_name = self.name(); let payload = Vec<Parameter>.new();
                                        if self.take("(") && !self.take(")") {
                                            while self.error.is_empty() {
                                                var label = Token { text: "", kind: 1, line: self.peek().line, column: self.peek().column, source: self.peek().source };
                                                if self.ahead(1).text.equals(":") { label = self.name(); self.expect(":"); }
                                                payload.push(Parameter { token: label, type_name: self.type_name() });
                                                if !self.take(",") { break; }
                                            }
                                            self.expect(")");
                                        }
                                        variants.push(Variant { token: case_name, payload: payload });
                                        if !self.take(",") { break; }
                                    }
                                    self.take(";");
                                } else {
                                    var mutable = false;
                                    if self.take("var") { mutable = true; } else { self.expect("let"); }
                                    let field = self.name(); self.expect(":"); let type_name = self.type_name();
                                    var expr = -1; if self.take("=") { expr = self.expression(1); }
                                    self.expect(";");
                                    fields.push(Field { token: field, type_name: type_name, mutable: mutable, modifiers: member_modifiers, expr: expr });
                                } }
                            }
                            self.expect("}");
                        } else { self.fail(token, "expected declaration (def, import, struct, or typealias)"); }
                    }
                }
                self.program.declarations.push(Declaration { token: name, kind: kind, value: value, modifiers: modifiers, generics: generics, fields: fields, variants: variants, methods: methods });
            }
        }
    }
}
