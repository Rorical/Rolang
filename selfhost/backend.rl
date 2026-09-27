import "lexer.rl"
import "ast.rl"
import "parser.rl"
import "string_codegen.rl"
import std.string_builder

pub struct Binding { pub var code: String; pub var type_name: String; pub var mutable: Bool; }
pub struct Value { pub var code: String; pub var type_name: String; }
pub struct Backend {
    pub var program: Program;
    pub var functions: Dict<String, i32>;
    pub var scopes: Vec<Dict<String, Binding>>;
    pub var structs: Dict<String, i32>;
    pub var output: StringBuilder;
    pub var error: String;
    pub var next_id: i32;
    pub var depth: i32;
    pub var return_type: String;

    pub static def new(program: Program) -> Backend {
        return Backend { program: program, functions: Dict<String, i32>.with_capacity(16, 1),
            structs: Dict<String, i32>.with_capacity(16, 1), scopes: Vec<Dict<String, Binding>>.new(), output: StringBuilder.new(), error: "", next_id: 0, depth: 0, return_type: "i32" };
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
        if type_name.equals("Void") {
            self.output.append_line(expression + ";");
            return Value { code: "", type_name: "Void" };
        }
        self.output.append_line(self.c_type(type_name) + " " + name + " = " + expression + ";");
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
        if !actual.equals(expected) && !(actual.equals("i32") && expected.equals("i64")) { self.fail(token, "expected " + expected + ", got " + actual); }
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
        if expr.kind == 7 { return self.string_literal(token); }
        if expr.kind == 11 {
            let value = self.expression(expr.left);
            if !self.numeric(value.type_name) || !self.numeric(expr.type_name) { self.fail(token, "C backend supports numeric i32/i64 casts only"); return self.invalid(); }
            if expr.type_name.equals("i32") { return self.value("i32", "rl_bits((uint32_t)" + value.code + ")"); }
            return self.value("i64", "(int64_t)" + value.code);
        }
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
            if !self.numeric(value.type_name) { self.fail(token, "expected integer operand"); }
            if token.text.equals("+") { return value; }
            if value.type_name.equals("i64") { return self.value("i64", "rl_neg64(" + value.code + ")"); }
            return self.value("i32", "rl_neg(" + value.code + ")");
        }
        if expr.kind == 6 {
            if let local = self.lookup(token.text) { self.fail(token, "local variable is not callable"); }
            if let index = self.functions.get(token.text) { return self.call(index, expr.args, "", token); }
            self.fail(token, "unknown function '" + token.text + "'"); return self.invalid();
        }
        if expr.kind == 9 {
            let object = self.expression(expr.left);
            let field = self.field_index(object.type_name, token);
            if field < 0 { return self.invalid(); }
            let declaration = self.program.declarations.get(self.struct_index(object.type_name));
            return self.value(declaration.fields.get(field).type_name, object.code + "->rl_m" + field.to_string());
        }
        if expr.kind == 10 { return self.construct(expr); }
        if expr.kind == 14 { return self.method_call(expr); }
        if expr.kind != 4 { self.fail(token, "expression unsupported by C backend"); return self.invalid(); }
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
        if op.equals("+") && left.type_name.equals("String") {
            self.require_type(token, right.type_name, "String");
            return self.value("String", "rl_string_concat(" + left.code + ", " + right.code + ")");
        }
        if op.equals("==") || op.equals("!=") {
            if !(self.numeric(left.type_name) && self.numeric(right.type_name)) { self.require_type(token, right.type_name, left.type_name); }
            if left.type_name.equals("String") { self.fail(token, "use String.equals for string comparison"); }
            if left.type_name.equals("Void") { self.fail(token, "Void is not a value"); }
            if self.structs.contains(left.type_name) { self.fail(token, "cannot compare struct values"); }
            return self.value("Bool", left.code + " " + op + " " + right.code);
        }
        if !self.numeric(left.type_name) || !self.numeric(right.type_name) { self.fail(token, "expected integer operands"); }
        if op.equals("<") || op.equals(">") || op.equals("<=") || op.equals(">=") { return self.value("Bool", left.code + " " + op + " " + right.code); }
        var helper = "rl_add";
        if op.equals("-") { helper = "rl_sub"; }
        if op.equals("*") { helper = "rl_mul"; }
        if op.equals("/") { helper = "rl_div"; }
        if op.equals("%") { helper = "rl_rem"; }
        var type_name = "i32";
        if left.type_name.equals("i64") || right.type_name.equals("i64") { type_name = "i64"; helper = helper + "64"; }
        return self.value(type_name, helper + "(" + left.code + ", " + right.code + ")");
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
        if statement.kind == 13 {
            let target = self.program.expressions.get(statement.target);
            if target.kind != 9 { self.fail(token, "assignment unsupported by C backend"); return false; }
            // Rolang evaluates an assignment's RHS before resolving its target.
            let value = self.expression(statement.expr);
            let object = self.expression(target.left);
            let field = self.field_index(object.type_name, target.token);
            if field < 0 { return false; }
            let property = self.program.declarations.get(self.struct_index(object.type_name)).fields.get(field);
            self.require_type(token, value.type_name, property.type_name);
            self.output.append_line(object.code + "->rl_m" + field.to_string() + " = " + value.code + ";");
            return false;
        }
        if statement.kind == 1 && statement.expr < 0 {
            self.require_type(token, "Void", self.return_type); self.output.append_line("return;"); return true;
        }
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
            if value.type_name.equals("Void") { self.fail(token, "Void is not a value"); }
            var type_name = value.type_name;
            if !statement.annotation.is_empty() { type_name = statement.annotation; }
            let name = self.fresh();
            self.output.append_line(self.c_type(type_name) + " " + name + " = " + value.code + ";");
            scope.set(token.text, Binding { code: name, type_name: type_name, mutable: statement.mutable });
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
        text.append("static " + self.c_type(function.return_type) + " rl_f" + index.to_string() + "(");
        let instance = !function.owner.is_empty() && !function.modifiers.contains("static");
        if instance { text.append(self.c_type(function.owner) + " rl_self"); }
        var i = 0;
        while i < function.params.len() {
            if i > 0 || instance { text.append(", "); }
            text.append(self.c_type(function.params.get(i).type_name) + " rl_p" + i.to_string());
            i = i + 1;
        }
        if i == 0 && !instance { text.append("void"); }
        text.append(")");
        return text.to_string();
    }
    // Validate every node, including unreachable constructs, before emission.
    pub def supported_type(token: Token, type_name: String) -> Void {
        if !self.numeric(type_name) && !type_name.equals("Bool") && !type_name.equals("String") && !self.structs.contains(type_name) {
            self.fail(token, "C backend supports only i32, i64, Bool, String, or declared non-generic structs");
        }
    }
    pub def validate_subset() -> Void {
        var index = 0;
        for declaration in self.program.declarations {
            if declaration.kind != 2 || declaration.generics.len() != 0 { self.fail(declaration.token, "declaration unsupported by C backend"); }
            else {
                let name = declaration.token.text;
                if self.structs.contains(name) || primitive_type(name) || name.equals("String") { self.fail(declaration.token, "duplicate or reserved struct name"); }
                self.structs.set(name, index);
            }
            index = index + 1;
        }
        for declaration in self.program.declarations {
            let names = Dict<String, i32>.with_capacity(8, 1);
            for field in declaration.fields {
                self.supported_type(field.token, field.type_name);
                if names.contains(field.token.text) { self.fail(field.token, "duplicate field"); }
                names.set(field.token.text, 1);
                if field.expr >= 0 { self.fail(field.token, "field defaults unsupported by C backend"); }
            }
            for method in declaration.methods {
                let function = self.program.functions.get(method);
                if names.contains(function.token.text) { self.fail(function.token, "duplicate member"); }
                names.set(function.token.text, 1);
                if function.token.text.starts_with("__") { self.fail(function.token, "lifecycle hooks unsupported by C backend"); }
            }
        }
        for function in self.program.functions {
            if function.generics.len() != 0 || (function.owner.is_empty() && function.modifiers.contains("static")) { self.fail(function.token, "function unsupported by C backend"); }
            if !function.return_type.equals("Void") { self.supported_type(function.token, function.return_type); }
            for param in function.params { self.supported_type(param.token, param.type_name); }
            if function.owner.is_empty() && self.structs.contains(function.token.text) { self.fail(function.token, "function conflicts with struct name"); }
        }
        for expression in self.program.expressions {
            if expression.kind > 6 && expression.kind != 7 && expression.kind != 11 && expression.kind != 9 && expression.kind != 10 && expression.kind != 14 { self.fail(expression.token, "expression unsupported by C backend"); }
        }
        for statement in self.program.statements {
            if statement.kind > 6 && statement.kind != 13 { self.fail(statement.token, "statement unsupported by C backend"); }
            if statement.expr < 0 && statement.kind != 1 { self.fail(statement.token, "C backend requires an expression or initializer"); }
            if !statement.annotation.is_empty() { self.supported_type(statement.token, statement.annotation); }
        }
    }
    pub def generate() -> Void {
        self.validate_subset();
        if !self.error.is_empty() { return; }
        var i = 0;
        while i < self.program.functions.len() {
            let function = self.program.functions.get(i);
            var key = function.token.text;
            if !function.owner.is_empty() { key = function.owner + "." + key; }
            if self.functions.contains(key) { self.fail(function.token, "duplicate function"); }
            self.functions.set(key, i);
            i = i + 1;
        }
        if let index = self.functions.get("main") {
            let main_function = self.program.functions.get(index);
            if main_function.params.len() != 0 || !main_function.return_type.equals("i32") { self.fail(main_function.token, "main must have signature def main() -> i32"); }
        } else { self.error = "1:1: missing main function"; }
        if !self.error.is_empty() { return; }
        self.output.append_line("#include <stdint.h>\n#include <limits.h>\n#include <stdlib.h>\n#include <string.h>\n#include <stdio.h>");
        self.output.append_line("typedef struct rl_string rl_string;");
        self.emit_structs();
        self.output.append_line("static int32_t rl_bits(uint32_t x) { int32_t y; memcpy(&y, &x, 4); return y; }");
        self.output.append_line("static int32_t rl_add(int32_t a,int32_t b) { return rl_bits((uint32_t)a+(uint32_t)b); }");
        self.output.append_line("static int32_t rl_sub(int32_t a,int32_t b) { return rl_bits((uint32_t)a-(uint32_t)b); }");
        self.output.append_line("static int32_t rl_mul(int32_t a,int32_t b) { return rl_bits((uint32_t)a*(uint32_t)b); }");
        self.output.append_line("static int32_t rl_neg(int32_t a) { return rl_bits(0u-(uint32_t)a); }");
        self.output.append_line("static void rl_zero(void) { fputs(\"division by zero\\n\", stderr); exit(1); }");
        self.output.append_line("static int32_t rl_div(int32_t a,int32_t b) { if(!b) rl_zero(); if(a==INT32_MIN && b==-1) return INT32_MIN; return a/b; }");
        self.output.append_line("static int32_t rl_rem(int32_t a,int32_t b) { if(!b) rl_zero(); if(a==INT32_MIN && b==-1) return 0; return a%b; }");
        emit_string_runtime(self.output);
        i = 0;
        while i < self.program.functions.len() { self.output.append_line(self.signature(i) + ";"); i = i + 1; }
        i = 0;
        while i < self.program.functions.len() && self.error.is_empty() {
            let function = self.program.functions.get(i);
            self.return_type = function.return_type;
            let scope = Dict<String, Binding>.with_capacity(8, 1);
            self.scopes.push(scope);
            if !function.owner.is_empty() && !function.modifiers.contains("static") {
                scope.set("self", Binding { code: "rl_self", type_name: function.owner, mutable: false });
            }
            var p = 0;
            for param in function.params {
                if scope.contains(param.token.text) { self.fail(param.token, "duplicate parameter"); }
                scope.set(param.token.text, Binding { code: "rl_p" + p.to_string(), type_name: param.type_name, mutable: false });
                p = p + 1;
            }
            self.output.append_line(self.signature(i) + " {");
            if !self.block(function.body, false) && !function.return_type.equals("Void") { self.fail(function.token, "function must return on every path"); }
            self.output.append_line("}");
            self.scopes.pop();
            i = i + 1;
        }
        if let index = self.functions.get("main") { self.output.append_line("int main(void) { if (atexit(rl_cleanup)) return 1; return (int)rl_f" + index.to_string() + "(); }"); }
    }

    pub def invalid() -> Value { return Value { code: "0", type_name: "i32" }; }
    pub def struct_index(name: String) -> i32 {
        if let index = self.structs.get(name) { return index; }
        return -1;
    }
    pub def c_type(name: String) -> String {
        if name.equals("Void") { return "void"; }
        if name.equals("i64") { return "int64_t"; }
        if name.equals("String") { return "rl_string*"; }
        let index = self.struct_index(name);
        if index >= 0 { return "rl_s" + index.to_string() + "*"; }
        return "int32_t";
    }
    pub def field_index(owner: String, token: Token) -> i32 {
        let index = self.struct_index(owner);
        if index < 0 { self.fail(token, "field receiver must be a struct"); return -1; }
        let fields = self.program.declarations.get(index).fields;
        var i = 0;
        while i < fields.len() {
            if fields.get(i).token.text.equals(token.text) { return i; }
            i = i + 1;
        }
        self.fail(token, "unknown field '" + token.text + "'"); return -1;
    }
    pub def call(index: i32, values: Vec<i32>, receiver: String, token: Token) -> Value {
        let function = self.program.functions.get(index);
        if function.params.len() != values.len() { self.fail(token, "wrong argument count"); }
        let args = StringBuilder.new(); args.append(receiver);
        var i = 0;
        while i < values.len() {
            let value = self.expression(values.get(i));
            if i < function.params.len() { self.require_type(token, value.type_name, function.params.get(i).type_name); }
            if i > 0 || !receiver.is_empty() { args.append(", "); }
            args.append(value.code); i = i + 1;
        }
        return self.value(function.return_type, "rl_f" + index.to_string() + "(" + args.to_string() + ")");
    }
    pub def method_call(expr: Expression) -> Value {
        let member = self.program.expressions.get(expr.left);
        if member.kind != 9 { self.fail(expr.token, "call target unsupported by C backend"); return self.invalid(); }
        let receiver = self.program.expressions.get(member.left);
        var owner = ""; var code = ""; var static_call = false;
        if receiver.kind == 3 && self.structs.contains(receiver.token.text) {
            if let local = self.lookup(receiver.token.text) { static_call = false; }
            else { static_call = true; owner = receiver.token.text; }
        }
        if !static_call {
            let object = self.expression(member.left); owner = object.type_name; code = object.code;
        }
        if !static_call && (owner.equals("String") || self.numeric(owner)) { return self.builtin_method(owner, code, member.token, expr.args); }
        if let index = self.functions.get(owner + "." + member.token.text) {
            let function = self.program.functions.get(index);
            if function.modifiers.contains("static") != static_call { self.fail(member.token, "static/instance method receiver mismatch"); return self.invalid(); }
            return self.call(index, expr.args, code, member.token);
        }
        self.fail(member.token, "unknown method '" + member.token.text + "'"); return self.invalid();
    }
    pub def construct(expr: Expression) -> Value {
        let index = self.struct_index(expr.type_name);
        if index < 0 { self.fail(expr.token, "unknown struct '" + expr.type_name + "'"); return self.invalid(); }
        let declaration = self.program.declarations.get(index);
        let seen = Dict<String, i32>.with_capacity(8, 1);
        let result = self.value(expr.type_name, "rl_allocate(sizeof(rl_s" + index.to_string() + "))");
        var i = 0;
        while i < expr.args.len() && self.error.is_empty() {
            let label = expr.labels.get(i);
            let token = Token { text: label, kind: 1, line: expr.token.line, column: expr.token.column };
            let field = self.field_index(expr.type_name, token);
            if field < 0 { return self.invalid(); }
            if seen.contains(label) { self.fail(token, "duplicate field initializer"); }
            seen.set(label, 1);
            let value = self.expression(expr.args.get(i));
            self.require_type(token, value.type_name, declaration.fields.get(field).type_name);
            self.output.append_line(result.code + "->rl_m" + field.to_string() + " = " + value.code + ";");
            i = i + 1;
        }
        if seen.len() != declaration.fields.len() { self.fail(expr.token, "missing field initializer"); }
        return result;
    }
    pub def emit_structs() -> Void {
        self.output.append_line("typedef struct rl_allocation { void *object; struct rl_allocation *next; } rl_allocation;");
        self.output.append_line("static rl_allocation *rl_allocations;");
        self.output.append_line("static void rl_cleanup(void) { while (rl_allocations) { rl_allocation *p = rl_allocations; rl_allocations = p->next; free(p->object); free(p); } }");
        self.output.append_line("static void *rl_allocate(size_t size) { void *p = calloc(1, size); rl_allocation *a = malloc(sizeof(*a)); if (!p || !a) { free(p); free(a); fputs(\"allocation failed\\n\", stderr); exit(1); } a->object = p; a->next = rl_allocations; rl_allocations = a; return p; }");
        var i = 0;
        while i < self.program.declarations.len() {
            self.output.append_line("typedef struct rl_s" + i.to_string() + " rl_s" + i.to_string() + ";"); i = i + 1;
        }
        i = 0;
        for declaration in self.program.declarations {
            self.output.append_line("struct rl_s" + i.to_string() + " {");
            var field = 0;
            for property in declaration.fields {
                self.output.append_line(self.c_type(property.type_name) + " rl_m" + field.to_string() + ";"); field = field + 1;
            }
            if field == 0 { self.output.append_line("unsigned char rl_empty;"); }
            self.output.append_line("};"); i = i + 1;
        }
    }

    pub def numeric(name: String) -> Bool { return name.equals("i32") || name.equals("i64"); }
    pub def string_literal(token: Token) -> Value {
        let text = StringBuilder.new(); text.append_byte(34 as u8);
        var i = 1; var length = 0;
        while i < (token.text.len() as i32) - 1 {
            var byte = token.text.byte_at(i); i = i + 1;
            if byte == 92 {
                byte = token.text.byte_at(i); i = i + 1;
                if byte == 110 { byte = 10; }
                else { if byte == 116 { byte = 9; }
                else { if byte == 114 { byte = 13; }
                else { if byte == 48 { byte = 0; } } } }
            }
            text.append_byte(92 as u8);
            text.append_byte((48 + byte / 64) as u8);
            text.append_byte((48 + (byte / 8) % 8) as u8);
            text.append_byte((48 + byte % 8) as u8);
            length = length + 1;
        }
        text.append_byte(34 as u8);
        return self.value("String", "rl_string_new((const unsigned char *)" + text.to_string() + ", " + length.to_string() + ")");
    }
    pub def builtin_method(owner: String, receiver: String, token: Token, args: Vec<i32>) -> Value {
        let name = token.text; let expected = Vec<String>.new();
        var result = "i32"; var helper = "";
        if self.numeric(owner) {
            if name.equals("to_string") { helper = "rl_integer_string"; result = "String"; }
        } else {
            if name.equals("len") { result = "i64"; helper = "length"; }
            if name.equals("is_empty") { result = "Bool"; helper = "empty"; }
            if name.equals("equals") || name.equals("compare_to") {
                expected.push("String"); helper = "rl_string_compare";
                if name.equals("equals") { result = "Bool"; }
            }
            if name.equals("concat") { expected.push("String"); helper = "rl_string_concat"; result = "String"; }
            if name.equals("contains") || name.equals("starts_with") || name.equals("ends_with") {
                expected.push("String"); helper = "rl_string_" + name; result = "Bool";
            }
            if name.equals("byte_at") || name.equals("char_at") { expected.push("i32"); helper = "rl_string_byte"; }
            if name.equals("find_char") { expected.push("i32"); expected.push("i32"); helper = "rl_string_find_char"; }
            if name.equals("substring") { expected.push("i32"); expected.push("i32"); helper = "rl_string_substring"; result = "String"; }
        }
        if helper.is_empty() { self.fail(token, "method unsupported by C backend"); return self.invalid(); }
        if expected.len() != args.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        let code = StringBuilder.new(); code.append(helper + "(" + receiver);
        var i = 0;
        while i < args.len() {
            let value = self.expression(args.get(i)); self.require_type(token, value.type_name, expected.get(i));
            code.append(", " + value.code); i = i + 1;
        }
        code.append(")");
        if helper.equals("length") { return self.value("i64", receiver + "->length"); }
        if helper.equals("empty") { return self.value("Bool", receiver + "->length == 0"); }
        if name.equals("equals") { code.append(" == 0"); }
        return self.value(result, code.to_string());
    }
}
