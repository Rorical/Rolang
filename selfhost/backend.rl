import "lexer.rl"
import "ast.rl"
import std.string_builder

pub struct Binding { pub var code: String; pub var type_name: String; pub var mutable: Bool; }
pub struct Value { pub var code: String; pub var type_name: String; }
pub struct Backend {
    pub var program: Program;
    pub var functions: Dict<String, i32>;
    pub var scopes: Vec<Dict<String, Binding>>;
    pub var output: StringBuilder;
    pub var error: String;
    pub var next_id: i32;
    pub var depth: i32;
    pub var return_type: String;

    pub static def new(program: Program) -> Backend {
        return Backend { program: program, functions: Dict<String, i32>.with_capacity(16, 1),
            scopes: Vec<Dict<String, Binding>>.new(), output: StringBuilder.new(), error: "", next_id: 0, depth: 0, return_type: "i32" };
    }
    pub def fail(token: Token, message: String) -> Void {
        if self.error.is_empty() { self.error = location(token, message); }
    }
    pub def fresh() -> String {
        let name = "rl_t" + self.next_id.to_string();
        self.next_id = self.next_id + 1;
        return name;
    }
    pub def value(type_name: String, expression: String) -> Value {
        let name = self.fresh();
        self.output.append_line("int32_t " + name + " = " + expression + ";");
        return Value { code: name, type_name: type_name };
    }
    pub def lookup(name: String) -> Binding? {
        var i = self.scopes.len() - 1;
        while i >= 0 {
            if let binding = self.scopes.get(i).get(name) { return binding; }
            i = i - 1;
        }
        return nil;
    }
    pub def require_type(token: Token, actual: String, expected: String) -> Void {
        if !actual.equals(expected) { self.fail(token, "expected " + expected + ", got " + actual); }
    }
    pub def integer(token: Token, allow_min: Bool) -> String {
        var n: i64 = 0;
        var i = 0;
        while i < (token.text.len() as i32) {
            let digit = (token.text.byte_at(i) - 48) as i64;
            if n > (2147483648 - digit) / 10 { self.fail(token, "integer literal out of i32 range"); return "0"; }
            n = n * 10 + digit;
            i = i + 1;
        }
        if n == 2147483648 {
            if allow_min { return "INT32_MIN"; }
            self.fail(token, "integer literal out of i32 range"); return "0";
        }
        if allow_min { return "(-" + n.to_string() + ")"; }
        return n.to_string();
    }
    pub def expression(id: i32) -> Value {
        if !self.error.is_empty() { return Value { code: "0", type_name: "i32" }; }
        self.depth = self.depth + 1;
        if self.depth > 128 {
            self.fail(self.program.expressions.get(id).token, "expression tree nesting limit exceeded");
            self.depth = self.depth - 1;
            return Value { code: "0", type_name: "i32" };
        }
        let result = self.emit_expression(id);
        self.depth = self.depth - 1;
        return result;
    }
    pub def emit_expression(id: i32) -> Value {
        let expr = self.program.expressions.get(id);
        let token = expr.token;
        if expr.kind == 1 { return self.value("i32", self.integer(token, false)); }
        if expr.kind == 2 {
            if token.text.equals("true") { return self.value("Bool", "1"); }
            return self.value("Bool", "0");
        }
        if expr.kind == 3 {
            if let binding = self.lookup(token.text) { return self.value(binding.type_name, binding.code); }
            self.fail(token, "unknown variable '" + token.text + "'");
            return Value { code: "0", type_name: "i32" };
        }
        if expr.kind == 5 {
            let inner = self.program.expressions.get(expr.left);
            if token.text.equals("-") && inner.kind == 1 { return self.value("i32", self.integer(inner.token, true)); }
            let value = self.expression(expr.left);
            if token.text.equals("!") { self.require_type(token, value.type_name, "Bool"); return self.value("Bool", "!" + value.code); }
            self.require_type(token, value.type_name, "i32");
            if token.text.equals("+") { return value; }
            return self.value("i32", "rl_neg(" + value.code + ")");
        }
        if expr.kind == 6 {
            if let local = self.lookup(token.text) { self.fail(token, "local variable is not callable"); }
            if let index = self.functions.get(token.text) {
                let function = self.program.functions.get(index);
                if function.params.len() != expr.args.len() { self.fail(token, "wrong argument count"); }
                let args = StringBuilder.new();
                var i = 0;
                while i < expr.args.len() {
                    let value = self.expression(expr.args.get(i));
                    if i < function.params.len() { self.require_type(token, value.type_name, function.params.get(i).type_name); }
                    if i > 0 { args.append(", "); }
                    args.append(value.code);
                    i = i + 1;
                }
                return self.value(function.return_type, "rl_f" + index.to_string() + "(" + args.to_string() + ")");
            }
            self.fail(token, "unknown function '" + token.text + "'");
            return Value { code: "0", type_name: "i32" };
        }
        let left = self.expression(expr.left);
        let op = token.text;
        if op.equals("&&") || op.equals("||") {
            self.require_type(token, left.type_name, "Bool");
            let result = self.value("Bool", left.code);
            if op.equals("&&") { self.output.append_line("if (" + result.code + ") {"); }
            else { self.output.append_line("if (!" + result.code + ") {"); }
            let right = self.expression(expr.right);
            self.require_type(token, right.type_name, "Bool");
            self.output.append_line(result.code + " = " + right.code + ";\n}");
            return result;
        }
        let right = self.expression(expr.right);
        if op.equals("==") || op.equals("!=") {
            self.require_type(token, right.type_name, left.type_name);
            return self.value("Bool", left.code + " " + op + " " + right.code);
        }
        self.require_type(token, left.type_name, "i32");
        self.require_type(token, right.type_name, "i32");
        if op.equals("<") || op.equals(">") || op.equals("<=") || op.equals(">=") { return self.value("Bool", left.code + " " + op + " " + right.code); }
        var helper = "rl_add";
        if op.equals("-") { helper = "rl_sub"; }
        if op.equals("*") { helper = "rl_mul"; }
        if op.equals("/") { helper = "rl_div"; }
        if op.equals("%") { helper = "rl_rem"; }
        return self.value("i32", helper + "(" + left.code + ", " + right.code + ")");
    }
    pub def block(body: Vec<i32>, nested: Bool) -> Bool {
        if nested { self.scopes.push(Dict<String, Binding>.with_capacity(8, 1)); }
        var returned = false;
        for id in body {
            if self.statement(id) { returned = true; }
        }
        if nested { self.scopes.pop(); }
        return returned;
    }
    pub def statement(id: i32) -> Bool {
        let statement = self.program.statements.get(id);
        let token = statement.token;
        if statement.kind == 5 {
            self.output.append_line("while (1) {");
            let condition = self.expression(statement.expr);
            self.require_type(token, condition.type_name, "Bool");
            self.output.append_line("if (!" + condition.code + ") break;");
            self.block(statement.body, true);
            self.output.append_line("}");
            return false;
        }
        let value = self.expression(statement.expr);
        if statement.kind == 1 {
            self.require_type(token, value.type_name, self.return_type);
            self.output.append_line("return " + value.code + ";");
            return true;
        }
        if statement.kind == 2 {
            let scope = self.scopes.get(self.scopes.len() - 1);
            if scope.contains(token.text) { self.fail(token, "duplicate local '" + token.text + "'"); }
            if !statement.annotation.is_empty() { self.require_type(token, value.type_name, statement.annotation); }
            let name = self.fresh();
            self.output.append_line("int32_t " + name + " = " + value.code + ";");
            scope.set(token.text, Binding { code: name, type_name: value.type_name, mutable: statement.mutable });
        }
        if statement.kind == 3 {
            if let binding = self.lookup(token.text) {
                if !binding.mutable { self.fail(token, "cannot assign to let binding"); }
                self.require_type(token, value.type_name, binding.type_name);
                self.output.append_line(binding.code + " = " + value.code + ";");
            } else { self.fail(token, "unknown variable '" + token.text + "'"); }
        }
        if statement.kind == 4 {
            self.require_type(token, value.type_name, "Bool");
            self.output.append_line("if (" + value.code + ") {");
            let yes = self.block(statement.body, true);
            self.output.append_line("} else {");
            let no = self.block(statement.alternative, true);
            self.output.append_line("}");
            return yes && no;
        }
        return false;
    }
    pub def signature(index: i32) -> String {
        let function = self.program.functions.get(index);
        let text = StringBuilder.new();
        text.append("static int32_t rl_f" + index.to_string() + "(");
        var i = 0;
        while i < function.params.len() {
            if i > 0 { text.append(", "); }
            text.append("int32_t rl_p" + i.to_string());
            i = i + 1;
        }
        if i == 0 { text.append("void"); }
        text.append(")");
        return text.to_string();
    }
    pub def generate() -> Void {
        var i = 0;
        while i < self.program.functions.len() {
            let function = self.program.functions.get(i);
            if self.functions.contains(function.token.text) { self.fail(function.token, "duplicate function"); }
            self.functions.set(function.token.text, i);
            i = i + 1;
        }
        if let index = self.functions.get("main") {
            let main_function = self.program.functions.get(index);
            if main_function.params.len() != 0 || !main_function.return_type.equals("i32") { self.fail(main_function.token, "main must have signature def main() -> i32"); }
        } else { self.error = "1:1: missing main function"; }
        if !self.error.is_empty() { return; }
        self.output.append_line("#include <stdint.h>\n#include <limits.h>\n#include <stdlib.h>\n#include <string.h>\n#include <stdio.h>");
        self.output.append_line("static int32_t rl_bits(uint32_t x) { int32_t y; memcpy(&y, &x, 4); return y; }");
        self.output.append_line("static int32_t rl_add(int32_t a,int32_t b) { return rl_bits((uint32_t)a+(uint32_t)b); }");
        self.output.append_line("static int32_t rl_sub(int32_t a,int32_t b) { return rl_bits((uint32_t)a-(uint32_t)b); }");
        self.output.append_line("static int32_t rl_mul(int32_t a,int32_t b) { return rl_bits((uint32_t)a*(uint32_t)b); }");
        self.output.append_line("static int32_t rl_neg(int32_t a) { return rl_bits(0u-(uint32_t)a); }");
        self.output.append_line("static void rl_zero(void) { fputs(\"division by zero\\n\", stderr); exit(1); }");
        self.output.append_line("static int32_t rl_div(int32_t a,int32_t b) { if(!b) rl_zero(); if(a==INT32_MIN && b==-1) return INT32_MIN; return a/b; }");
        self.output.append_line("static int32_t rl_rem(int32_t a,int32_t b) { if(!b) rl_zero(); if(a==INT32_MIN && b==-1) return 0; return a%b; }");
        i = 0;
        while i < self.program.functions.len() { self.output.append_line(self.signature(i) + ";"); i = i + 1; }
        i = 0;
        while i < self.program.functions.len() && self.error.is_empty() {
            let function = self.program.functions.get(i);
            self.return_type = function.return_type;
            let scope = Dict<String, Binding>.with_capacity(8, 1);
            self.scopes.push(scope);
            var p = 0;
            for param in function.params {
                if scope.contains(param.token.text) { self.fail(param.token, "duplicate parameter"); }
                scope.set(param.token.text, Binding { code: "rl_p" + p.to_string(), type_name: param.type_name, mutable: false });
                p = p + 1;
            }
            self.output.append_line(self.signature(i) + " {");
            if !self.block(function.body, false) { self.fail(function.token, "function must return on every path"); }
            self.output.append_line("}");
            self.scopes.pop();
            i = i + 1;
        }
        if let index = self.functions.get("main") { self.output.append_line("int main(void) { return (int)rl_f" + index.to_string() + "(); }"); }
    }
}
