import "lexer.rl"
import "ast.rl"
import "parser.rl"
import "string_codegen.rl"
import "vector_codegen.rl"
import "dict_codegen.rl"
import "builder_codegen.rl"
import "os_codegen.rl"
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
    pub var loop_depth: i32;
    pub var unsafe_depth: i32;
    pub var return_type: String;

    pub static def new(program: Program) -> Backend {
        return Backend { program: program, functions: Dict<String, i32>.with_capacity(16, 1),
            structs: Dict<String, i32>.with_capacity(16, 1), scopes: Vec<Dict<String, Binding>>.new(), output: StringBuilder.new(), error: "", next_id: 0, depth: 0, loop_depth: 0, unsafe_depth: 0, return_type: "i32" };
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
    pub def widens(actual: String, expected: String) -> Bool {
        return (actual.equals("i32") && expected.equals("i64")) || (actual.equals("u8") && (expected.equals("i32") || expected.equals("i64")));
    }
    pub def expression_as(id: i32, expected: String, token: Token) -> Value {
        let expr = self.program.expressions.get(id); var inner = expected;
        if self.is_optional(inner) { inner = self.optional_inner(inner); }
        if self.numeric(inner) {
            if expr.kind == 1 { return self.coerce(self.integer_value(expr.token, false, inner), expected, token); }
            if expr.kind == 5 && expr.token.text.equals("-") {
                let literal = self.program.expressions.get(expr.left);
                if literal.kind == 1 { return self.coerce(self.integer_value(literal.token, true, inner), expected, token); }
            }
        }
        return self.coerce(self.expression(id), expected, token);
    }
    pub def require_type(token: Token, actual: String, expected: String) -> Void {
        if !actual.equals(expected) && !self.widens(actual, expected) { self.fail(token, "expected " + expected + ", got " + actual); }
    }
    pub def is_optional(name: String) -> Bool { return name.ends_with("?"); }
    pub def optional_inner(name: String) -> String { return name.substring(0, (name.len() as i32) - 1); }
    // Presence is boxed separately from the payload, including zero and nested nil.
    pub def coerce(value: Value, expected: String, token: Token) -> Value {
        if value.type_name.equals(expected) { return value; }
        if self.is_optional(expected) {
            if value.type_name.equals("nil") { return self.value(expected, "NULL"); }
            let inner = self.optional_inner(expected);
            if value.type_name.equals(inner) || self.widens(value.type_name, inner) {
                return self.value(expected, "rl_some(" + self.slot(value) + ")");
            }
        }
        self.require_type(token, value.type_name, expected); return value;
    }
    pub def integer_value(token: Token, negative: Bool, expected: String) -> Value {
        var start = 0; let length = token.text.len() as i32;
        while start < length - 1 && token.text.byte_at(start) == 48 { start = start + 1; }
        let digits = token.text.substring(start, length - start);
        var type_name = expected; var limit = "2147483647";
        if negative { limit = "2147483648"; }
        if type_name.is_empty() {
            type_name = "i32";
            if digits.len() > limit.len() || (digits.len() == limit.len() && digits.compare_to(limit) > 0) { type_name = "i64"; }
        }
        if type_name.equals("i64") { limit = "9223372036854775807"; if negative { limit = "9223372036854775808"; } }
        if type_name.equals("u8") { limit = "255"; if negative { limit = "0"; } }
        if digits.len() > limit.len() || (digits.len() == limit.len() && digits.compare_to(limit) > 0) {
            self.fail(token, "integer literal out of " + type_name + " range"); return self.invalid();
        }
        if negative && digits.equals("2147483648") && type_name.equals("i32") { return self.value("i32", "INT32_MIN"); }
        if negative && digits.equals("9223372036854775808") && type_name.equals("i64") { return self.value("i64", "INT64_MIN"); }
        var code = digits;
        if type_name.equals("i64") { code = "INT64_C(" + digits + ")"; }
        if negative { code = "(-" + code + ")"; }
        return self.value(type_name, code);
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
        if expr.kind == 8 { return Value { code: "NULL", type_name: "nil" }; }
        if expr.kind == 7 { return self.string_literal(token); }
        if expr.kind == 11 {
            let value = self.expression(expr.left);
            if value.type_name.equals("RawPtr") || expr.type_name.equals("RawPtr") {
                if self.unsafe_depth == 0 { self.fail(token, "RawPtr casts require unsafe"); return self.invalid(); }
                if expr.type_name.equals("RawPtr") {
                    // The existing compiler treats nonliteral casts to RawPtr as
                    // address-of-storage operations; do not silently reinterpret values.
                    if self.program.expressions.get(expr.left).kind != 1 {
                        self.fail(token, "C backend supports only integer literals to RawPtr; address-of casts are not implemented"); return self.invalid();
                    }
                    return self.value("RawPtr", "(void *)(uintptr_t)" + value.code);
                }
                if value.type_name.equals("RawPtr") {
                    if expr.type_name.equals("i64") { return self.value("i64", "rl_bits64((uint64_t)(uintptr_t)" + value.code + ")"); }
                    if expr.type_name.equals("i32") { return self.value("i32", "rl_bits((uint32_t)(uintptr_t)" + value.code + ")"); }
                    if expr.type_name.equals("u8") { return self.value("u8", "(uint8_t)(uintptr_t)" + value.code); }
                }
                self.fail(token, "C backend supports RawPtr casts with integers only"); return self.invalid();
            }
            if !self.numeric(value.type_name) || !self.numeric(expr.type_name) { self.fail(token, "C backend supports numeric u8/i32/i64 casts only"); return self.invalid(); }
            if expr.type_name.equals("u8") { return self.value("u8", "(uint8_t)" + value.code); }
            if expr.type_name.equals("i32") { return self.value("i32", "rl_bits((uint32_t)" + value.code + ")"); }
            return self.value("i64", "(int64_t)" + value.code);
        }
        if expr.kind == 1 { return self.integer_value(token, false, ""); }
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
            if token.text.equals("-") && inner.kind == 1 { return self.integer_value(inner.token, true, ""); }
            let value = self.expression(expr.left);
            if token.text.equals("!") { self.require_type(token, value.type_name, "Bool"); return self.value("Bool", "!" + value.code); }
            if !self.numeric(value.type_name) { self.fail(token, "expected integer operand"); }
            if token.text.equals("+") { return value; }
            if value.type_name.equals("i64") { return self.value("i64", "rl_neg64(" + value.code + ")"); }
            if value.type_name.equals("u8") { return self.value("u8", "(uint8_t)rl_neg(" + value.code + ")"); }
            return self.value("i32", "rl_neg(" + value.code + ")");
        }
        if expr.kind == 6 {
            if let local = self.lookup(token.text) { self.fail(token, "local variable is not callable"); }
            if let index = self.functions.get(token.text) { return self.call(index, expr.args, "", token); }
            if !self.stdlib_module(token.text).is_empty() { return self.stdlib_call(token, expr.args); }
            self.fail(token, "unknown function '" + token.text + "'"); return self.invalid();
        }
        if expr.kind == 9 {
            let object = self.expression(expr.left);
            let field = self.field_index(object.type_name, token);
            if field < 0 { return self.invalid(); }
            let declaration = self.program.declarations.get(self.struct_index(object.type_name));
            return self.value(declaration.fields.get(field).type_name, object.code + "->rl_m" + field.to_string());
        }
        if expr.kind == 12 {
            let object = self.expression(expr.left);
            let index = self.index_value(object, expr.right, token);
            if self.is_dictionary(object.type_name) {
                let key = self.coerce(index, self.dict_key(object.type_name), token);
                return self.value(self.dict_value(object.type_name) + "?", "rl_dict_get(" + object.code + ", " + self.slot(key) + ")");
            }
            return self.vector_get(object, index, token);
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
            if self.is_optional(left.type_name) || left.type_name.equals("nil") { self.fail(token, "optional comparison unsupported; use if let"); }
            if left.type_name.equals("StringBuilder") || self.structs.contains(left.type_name) || self.is_vector(left.type_name) || self.is_dictionary(left.type_name) { self.fail(token, "cannot compare struct values"); }
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
        if left.type_name.equals("u8") && right.type_name.equals("u8") { type_name = "u8"; }
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
        if statement.kind == 7 || statement.kind == 12 {
            self.output.append_line("{");
            if statement.kind == 12 { self.unsafe_depth = self.unsafe_depth + 1; }
            let returned = self.block(statement.body, true);
            if statement.kind == 12 { self.unsafe_depth = self.unsafe_depth - 1; }
            self.output.append_line("}"); return returned;
        }
        if statement.kind == 10 || statement.kind == 11 {
            if self.loop_depth == 0 { self.fail(token, "loop control outside a loop"); }
            if statement.kind == 10 { self.output.append_line("break;"); } else { self.output.append_line("continue;"); }
            return false;
        }
        if statement.kind == 8 { return self.for_loop(statement); }
        if statement.kind == 9 { return self.if_let(statement); }
        if statement.kind == 13 {
            let target = self.program.expressions.get(statement.target);
            if target.kind == 12 {
                // Indexed assignment follows method-call order: receiver, index, RHS.
                let object = self.expression(target.left); let index = self.index_value(object, target.right, token);
                if !self.is_vector(object.type_name) && !self.is_dictionary(object.type_name) { self.fail(token, "index receiver must be a Vec or Dict"); return false; }
                var expected = self.vector_element(object.type_name);
                if self.is_dictionary(object.type_name) { expected = self.dict_value(object.type_name); }
                var value = self.expression_as(statement.expr, expected, token);
                if self.is_dictionary(object.type_name) {
                    let key = self.coerce(index, self.dict_key(object.type_name), token);
                    value = self.coerce(value, self.dict_value(object.type_name), token);
                    self.output.append_line("rl_dict_set(" + object.code + ", " + self.slot(key) + ", " + self.slot(value) + ");"); return false;
                }
                if !self.is_vector(object.type_name) { self.fail(token, "index receiver must be a Vec or Dict"); return false; }
                self.require_type(token, index.type_name, "i32"); value = self.coerce(value, self.vector_element(object.type_name), token);
                self.output.append_line("rl_vec_set(" + object.code + ", " + index.code + ", " + self.slot(value) + ");"); return false;
            }
            if target.kind != 9 { self.fail(token, "assignment unsupported by C backend"); return false; }
            // Rolang evaluates an assignment's RHS before resolving its target.
            var value = self.expression(statement.expr);
            let object = self.expression(target.left);
            let field = self.field_index(object.type_name, target.token);
            if field < 0 { return false; }
            let property = self.program.declarations.get(self.struct_index(object.type_name)).fields.get(field);
            value = self.coerce(value, property.type_name, token);
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
            self.loop_depth = self.loop_depth + 1;
            self.block(statement.body, true);
            self.loop_depth = self.loop_depth - 1;
            self.output.append_line("}");
            return false;
        }
        var value = Value { code: "NULL", type_name: "nil" };
        if statement.expr >= 0 {
            var expected = "";
            if statement.kind == 1 { expected = self.return_type; }
            if statement.kind == 2 { expected = statement.annotation; }
            if statement.kind == 3 { if let binding = self.lookup(token.text) { expected = binding.type_name; } }
            if expected.is_empty() { value = self.expression(statement.expr); }
            else { value = self.expression_as(statement.expr, expected, token); }
        }
        if statement.kind == 1 {
            value = self.coerce(value, self.return_type, token);
            self.output.append_line("return " + value.code + ";");
            return true;
        }
        if statement.kind == 2 {
            let scope = self.scopes.get(self.scopes.len() - 1);
            if scope.contains(token.text) { self.fail(token, "duplicate local '" + token.text + "'"); }
            if !statement.annotation.is_empty() { value = self.coerce(value, statement.annotation, token); }
            if value.type_name.equals("Void") { self.fail(token, "Void is not a value"); }
            if value.type_name.equals("nil") { self.fail(token, "nil requires a concrete type annotation"); }
            var type_name = value.type_name;
            if !statement.annotation.is_empty() { type_name = statement.annotation; }
            let name = self.fresh();
            self.output.append_line(self.c_type(type_name) + " " + name + " = " + value.code + ";");
            scope.set(token.text, Binding { code: name, type_name: type_name, mutable: statement.mutable });
        }
        if statement.kind == 3 {
            if let binding = self.lookup(token.text) {
                if !binding.mutable { self.fail(token, "cannot assign to let binding"); }
                value = self.coerce(value, binding.type_name, token);
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
        if self.is_optional(type_name) { self.supported_type(token, self.optional_inner(type_name)); return; }
        if self.is_dictionary(type_name) {
            let key = self.dict_key(type_name); let value = self.dict_value(type_name);
            self.supported_type(token, key); self.supported_type(token, value);
            if self.is_optional(key) { self.fail(token, "optional dictionary keys unsupported by C backend"); }
            return;
        }
        if self.is_vector(type_name) { self.supported_type(token, self.vector_element(type_name)); return; }
        if !self.numeric(type_name) && !type_name.equals("Bool") && !type_name.equals("String") && !type_name.equals("StringBuilder") && !type_name.equals("RawPtr") && !self.structs.contains(type_name) {
            self.fail(token, "C backend supports only u8, i32, i64, Bool, RawPtr, String, StringBuilder, collections, or declared non-generic structs");
        }
    }
    pub def validate_subset() -> Void {
        var index = 0;
        for declaration in self.program.declarations {
            if declaration.kind == 1 && self.builtin_module(declaration.value) && declaration.token.text.equals("import") { index = index + 1; continue; }
            if declaration.kind != 2 || declaration.generics.len() != 0 { self.fail(declaration.token, "declaration unsupported by C backend"); }
            else {
                let name = declaration.token.text;
                if self.structs.contains(name) || primitive_type(name) || name.equals("String") || name.equals("Vec") || name.equals("Dict") || name.equals("StringBuilder") { self.fail(declaration.token, "duplicate or reserved struct name"); }
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
            if expression.kind > 6 && expression.kind != 7 && expression.kind != 8 && expression.kind != 11 && expression.kind != 12 && expression.kind != 13 && expression.kind != 9 && expression.kind != 10 && expression.kind != 14 { self.fail(expression.token, "expression unsupported by C backend"); }
        }
        for statement in self.program.statements {
            if statement.expr < 0 && ((statement.kind == 2 && !self.is_optional(statement.annotation)) || statement.kind == 9 || statement.kind == 3 || statement.kind == 4 || statement.kind == 5 || statement.kind == 6 || statement.kind == 8 || statement.kind == 13) { self.fail(statement.token, "C backend requires an expression or initializer"); }
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
        self.output.append_line("#ifndef _XOPEN_SOURCE\n#define _XOPEN_SOURCE 700\n#endif\n#include <sys/stat.h>\n#include <stdint.h>\n#include <limits.h>\n#include <stdlib.h>\n#include <string.h>\n#include <stdio.h>");
        self.output.append_line("typedef struct rl_string rl_string; typedef struct rl_vec rl_vec; typedef struct rl_optional rl_optional; typedef struct rl_dict rl_dict; typedef struct rl_builder rl_builder;");
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
        emit_builder_runtime(self.output);
        emit_os_runtime(self.output);
        emit_vector_runtime(self.output);
        self.output.append_line("struct rl_optional { rl_slot value; };");
        self.output.append_line("static rl_optional *rl_some(rl_slot value) { rl_optional *p = rl_allocate(sizeof(*p)); p->value = value; return p; }");
        emit_dict_runtime(self.output);
        i = 0;
        while i < self.program.functions.len() { self.output.append_line(self.signature(i) + ";"); i = i + 1; }
        i = 0;
        while i < self.program.functions.len() && self.error.is_empty() {
            let function = self.program.functions.get(i);
            self.return_type = function.return_type;
            self.unsafe_depth = 0;
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
        if let index = self.functions.get("main") { self.output.append_line("int main(int argc, char **argv) { rl_argc = argc; rl_argv = argv; if (atexit(rl_cleanup)) return 1; return (int)rl_f" + index.to_string() + "(); }"); }
    }

    pub def invalid() -> Value { return Value { code: "0", type_name: "i32" }; }
    pub def struct_index(name: String) -> i32 {
        if let index = self.structs.get(name) { return index; }
        return -1;
    }
    pub def c_type(name: String) -> String {
        if self.is_optional(name) { return "rl_optional*"; }
        if name.equals("Void") { return "void"; }
        if name.equals("i64") { return "int64_t"; }
        if name.equals("u8") { return "uint8_t"; }
        if name.equals("StringBuilder") { return "rl_builder*"; }
        if name.equals("RawPtr") { return "void*"; }
        if name.equals("String") { return "rl_string*"; }
        if self.is_vector(name) { return "rl_vec*"; }
        if self.is_dictionary(name) { return "rl_dict*"; }
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
        if function.modifiers.contains("unsafe") && self.unsafe_depth == 0 { self.fail(token, "unsafe function call requires unsafe"); }
        if function.params.len() != values.len() { self.fail(token, "wrong argument count"); }
        let args = StringBuilder.new(); args.append(receiver);
        var i = 0;
        while i < values.len() {
            var value = self.invalid();
            if i < function.params.len() { value = self.expression_as(values.get(i), function.params.get(i).type_name, token); }
            else { value = self.expression(values.get(i)); }
            if i > 0 || !receiver.is_empty() { args.append(", "); }
            args.append(value.code); i = i + 1;
        }
        return self.value(function.return_type, "rl_f" + index.to_string() + "(" + args.to_string() + ")");
    }
    pub def method_call(expr: Expression) -> Value {
        let member = self.program.expressions.get(expr.left);
        if member.kind != 9 { self.fail(expr.token, "call target unsupported by C backend"); return self.invalid(); }
        let receiver = self.program.expressions.get(member.left);
        if receiver.kind == 13 {
            self.supported_type(receiver.token, receiver.type_name);
            if self.is_dictionary(receiver.type_name) { return self.dict_constructor(receiver.type_name, member.token, expr.args); }
            if self.is_vector(receiver.type_name) { return self.vector_constructor(receiver.type_name, member.token, expr.args); }
            self.fail(receiver.token, "generic receiver unsupported by C backend"); return self.invalid();
        }
        var owner = ""; var code = ""; var static_call = false;
        if receiver.kind == 3 && (self.structs.contains(receiver.token.text) || receiver.token.text.equals("StringBuilder")) {
            if let local = self.lookup(receiver.token.text) { static_call = false; }
            else { static_call = true; owner = receiver.token.text; }
        }
        if !static_call {
            let object = self.expression(member.left); owner = object.type_name; code = object.code;
        }
        if owner.equals("StringBuilder") {
            if static_call {
                if !member.token.text.equals("new") { self.fail(member.token, "unknown StringBuilder constructor"); return self.invalid(); }
                if expr.args.len() != 0 { self.fail(member.token, "wrong argument count"); return self.invalid(); }
                return self.value("StringBuilder", "rl_builder_new()");
            }
            return self.builder_method(code, member.token, expr.args);
        }
        if !static_call && self.is_dictionary(owner) { return self.dict_method(owner, code, member.token, expr.args); }
        if !static_call && self.is_vector(owner) { return self.vector_method(owner, code, member.token, expr.args); }
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
            let value = self.expression_as(expr.args.get(i), declaration.fields.get(field).type_name, token);
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
            if self.program.declarations.get(i).kind == 2 { self.output.append_line("typedef struct rl_s" + i.to_string() + " rl_s" + i.to_string() + ";"); } i = i + 1;
        }
        i = 0;
        for declaration in self.program.declarations {
            if declaration.kind != 2 { i = i + 1; continue; }
            self.output.append_line("struct rl_s" + i.to_string() + " {");
            var field = 0;
            for property in declaration.fields {
                self.output.append_line(self.c_type(property.type_name) + " rl_m" + field.to_string() + ";"); field = field + 1;
            }
            if field == 0 { self.output.append_line("unsigned char rl_empty;"); }
            self.output.append_line("};"); i = i + 1;
        }
    }

    pub def numeric(name: String) -> Bool { return name.equals("u8") || name.equals("i32") || name.equals("i64"); }
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
            if name.equals("to_string") && !owner.equals("u8") { helper = "rl_integer_string"; result = "String"; }
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
            let value = self.expression_as(args.get(i), expected.get(i), token);
            code.append(", " + value.code); i = i + 1;
        }
        code.append(")");
        if helper.equals("length") { return self.value("i64", receiver + "->length"); }
        if helper.equals("empty") { return self.value("Bool", receiver + "->length == 0"); }
        if name.equals("equals") { code.append(" == 0"); }
        return self.value(result, code.to_string());
    }

    pub def is_vector(name: String) -> Bool { return name.starts_with("Vec<") && name.ends_with(">"); }
    pub def vector_element(name: String) -> String { return name.substring(4, (name.len() as i32) - 5); }
    pub def slot(value: Value) -> String {
        if self.numeric(value.type_name) || value.type_name.equals("Bool") { return "((rl_slot){.number = " + value.code + "})"; }
        return "((rl_slot){.reference = " + value.code + "})";
    }
    pub def unpack(type_name: String, expression: String) -> Value {
        var field = "reference";
        if self.numeric(type_name) || type_name.equals("Bool") { field = "number"; }
        return self.value(type_name, "(" + self.c_type(type_name) + ")(" + expression + ")." + field);
    }
    pub def index_value(object: Value, id: i32, token: Token) -> Value {
        if self.is_dictionary(object.type_name) { return self.expression_as(id, self.dict_key(object.type_name), token); }
        if self.is_vector(object.type_name) { return self.expression_as(id, "i32", token); }
        return self.expression(id);
    }
    pub def vector_get(object: Value, index: Value, token: Token) -> Value {
        if !self.is_vector(object.type_name) { self.fail(token, "index receiver must be a Vec or Dict"); return self.invalid(); }
        self.require_type(token, index.type_name, "i32");
        return self.unpack(self.vector_element(object.type_name), "rl_vec_get(" + object.code + ", " + index.code + ")");
    }
    pub def vector_constructor(type_name: String, token: Token, args: Vec<i32>) -> Value {
        var capacity = "8"; var count = 0;
        if token.text.equals("with_capacity") { count = 1; }
        else { if !token.text.equals("new") { self.fail(token, "unknown Vec constructor"); return self.invalid(); } }
        if args.len() != count { self.fail(token, "wrong argument count"); return self.invalid(); }
        if count == 1 { let value = self.expression(args.get(0)); self.require_type(token, value.type_name, "i32"); capacity = value.code; }
        return self.value(type_name, "rl_vec_new(" + capacity + ")");
    }
    pub def vector_method(owner: String, receiver: String, token: Token, args: Vec<i32>) -> Value {
        let name = token.text; let element = self.vector_element(owner); let expected = Vec<String>.new();
        if name.equals("push") { expected.push(element); }
        else {
            if name.equals("get") || name.equals("resize") { expected.push("i32"); }
            else {
                if name.equals("set") { expected.push("i32"); expected.push(element); }
                else { if !name.equals("len") && !name.equals("pop") { self.fail(token, "Vec method unsupported by C backend"); return self.invalid(); } }
            }
        }
        if args.len() != expected.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        let values = Vec<Value>.new(); var i = 0;
        while i < args.len() { let value = self.expression_as(args.get(i), expected.get(i), token); values.push(value); i = i + 1; }
        if name.equals("len") { return self.value("i32", receiver + "->length"); }
        if name.equals("pop") { return self.unpack(element, "rl_vec_pop(" + receiver + ")"); }
        if name.equals("get") { return self.unpack(element, "rl_vec_get(" + receiver + ", " + values.get(0).code + ")"); }
        if name.equals("push") { return self.value("Void", "rl_vec_push(" + receiver + ", " + self.slot(values.get(0)) + ")"); }
        if name.equals("set") { return self.value("Void", "rl_vec_set(" + receiver + ", " + values.get(0).code + ", " + self.slot(values.get(1)) + ")"); }
        return self.value("Void", "rl_vec_resize(" + receiver + ", " + values.get(0).code + ")");
    }
    pub def builtin_module(name: String) -> Bool {
        return name.equals("std.string_builder") || name.equals("std.process") || name.equals("std.fs") || name.equals("std.path") || name.equals("std.io");
    }
    pub def has_module(name: String) -> Bool {
        for declaration in self.program.declarations { if declaration.kind == 1 && declaration.value.equals(name) && declaration.token.text.equals("import") { return true; } }
        return false;
    }
    pub def stdlib_module(name: String) -> String {
        if name.equals("argc") || name.equals("argv") { return "std.process"; }
        if name.equals("print") || name.equals("println") || name.equals("print_i32") || name.equals("println_i32") || name.equals("println_i64") { return "std.io"; }
        if name.equals("fs_open") || name.equals("fs_close") || name.equals("fs_read_all") || name.equals("fs_read_line") || name.equals("fs_write_str") || name.equals("fs_flush") || name.equals("fs_seek") || name.equals("fs_tell") || name.equals("fs_eof") { return "std.fs"; }
        if name.equals("path_join") || name.equals("path_dirname") || name.equals("path_basename") || name.equals("path_extension") || name.equals("path_exists") || name.equals("path_is_dir") || name.equals("path_is_file") || name.equals("path_resolve") { return "std.path"; }
        return "";
    }
    pub def stdlib_call(token: Token, args: Vec<i32>) -> Value {
        let module = self.stdlib_module(token.text); let name = token.text;
        if !self.has_module(module) { self.fail(token, name + " requires import " + module); return self.invalid(); }
        let expected = Vec<String>.new(); var result = "i32"; var helper = "";
        if module.equals("std.process") {
            if name.equals("argc") { helper = "argc"; }
            else { expected.push("i32"); result = "String"; helper = "rl_argument"; }
        }
        if module.equals("std.io") {
            result = "Void"; var type_name = "String";
            if name.equals("print_i32") || name.equals("println_i32") { type_name = "i32"; }
            if name.equals("println_i64") { type_name = "i64"; }
            expected.push(type_name); helper = "rl_println";
            if name.equals("print") || name.equals("print_i32") { helper = "rl_print"; }
        }
        if module.equals("std.fs") {
            if name.equals("fs_open") { expected.push("String"); expected.push("i32"); result = "RawPtr"; helper = "rl_file_open"; }
            else {
                expected.push("RawPtr"); helper = "rl_file_" + name.substring(3, (name.len() as i32) - 3);
                if name.equals("fs_close") { result = "Void"; }
                if name.equals("fs_read_all") || name.equals("fs_read_line") { result = "String"; }
                if name.equals("fs_write_str") { expected.push("String"); helper = "rl_file_write"; }
                if name.equals("fs_seek") { expected.push("i64"); expected.push("i32"); }
                if name.equals("fs_tell") { result = "i64"; }
            }
        }
        if module.equals("std.path") {
            expected.push("String"); result = "String"; helper = "rl_" + name;
            if name.equals("path_join") { expected.push("String"); }
            if name.equals("path_exists") || name.equals("path_is_dir") || name.equals("path_is_file") { result = "Bool"; }
        }
        if expected.len() != args.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        let code = StringBuilder.new(); code.append(helper + "("); var i = 0;
        while i < args.len() {
            let value = self.expression_as(args.get(i), expected.get(i), token);
            if i > 0 { code.append(", "); }
            if module.equals("std.io") && !expected.get(i).equals("String") { code.append("rl_integer_string(" + value.code + ")"); }
            else { code.append(value.code); }
            i = i + 1;
        }
        code.append(")");
        if name.equals("argc") { return self.value("i32", "rl_argc"); }
        return self.value(result, code.to_string());
    }
    pub def builder_method(receiver: String, token: Token, args: Vec<i32>) -> Value {
        let name = token.text; var expected = ""; var helper = ""; var result = "Void";
        if name.equals("append") { expected = "String"; helper = "rl_builder_append"; }
        if name.equals("append_line") { expected = "String"; helper = "rl_builder_line"; }
        if name.equals("append_byte") { expected = "u8"; helper = "rl_builder_byte"; }
        if name.equals("clear") { helper = "rl_builder_clear"; }
        if name.equals("to_string") { helper = "rl_builder_text"; result = "String"; }
        if name.equals("len") { helper = "length"; result = "i64"; }
        if helper.is_empty() { self.fail(token, "StringBuilder method unsupported by C backend"); return self.invalid(); }
        var count = 0; if !expected.is_empty() { count = 1; }
        if args.len() != count { self.fail(token, "wrong argument count"); return self.invalid(); }
        if helper.equals("length") { return self.value("i64", receiver + "->length"); }
        var code = helper + "(" + receiver;
        if count == 1 { let value = self.expression_as(args.get(0), expected, token); code = code + ", " + value.code; }
        return self.value(result, code + ")");
    }
    pub def is_dictionary(name: String) -> Bool { return name.starts_with("Dict<") && name.ends_with(">"); }
    pub def dict_separator(name: String) -> i32 {
        var depth = 0; var i = 5;
        while i < (name.len() as i32) - 1 {
            let byte = name.byte_at(i);
            if byte == 60 { depth = depth + 1; }
            if byte == 62 { depth = depth - 1; }
            if byte == 44 && depth == 0 { return i; }
            i = i + 1;
        }
        return -1;
    }
    pub def dict_key(name: String) -> String {
        let comma = self.dict_separator(name);
        if comma < 0 { return ""; }
        return name.substring(5, comma - 5);
    }
    pub def dict_value(name: String) -> String {
        let comma = self.dict_separator(name);
        if comma < 0 { return ""; }
        return name.substring(comma + 1, (name.len() as i32) - comma - 2);
    }
    pub def dict_constructor(owner: String, token: Token, args: Vec<i32>) -> Value {
        var count = 2;
        if token.text.equals("new") { count = 4; }
        else { if !token.text.equals("with_capacity") { self.fail(token, "unknown Dict constructor"); return self.invalid(); } }
        if args.len() != count { self.fail(token, "wrong argument count"); return self.invalid(); }
        let values = Vec<Value>.new();
        for arg in args { let value = self.expression(arg); self.require_type(token, value.type_name, "i32"); values.push(value); }
        let key = self.dict_key(owner); var reference = "1"; var string = "0";
        if self.numeric(key) || key.equals("Bool") { reference = "0"; }
        if key.equals("String") { string = "1"; }
        // Like std.Dict.new, type-id arguments are evaluated but types determine representation.
        return self.value(owner, "rl_dict_new(" + values.get(0).code + ", " + values.get(1).code + ", " + reference + ", " + string + ")");
    }
    pub def dict_method(owner: String, receiver: String, token: Token, args: Vec<i32>) -> Value {
        let name = token.text; let key = self.dict_key(owner); let value = self.dict_value(owner);
        let expected = Vec<String>.new();
        if name.equals("set") || name.equals("entry_index") { expected.push(key); expected.push(value); }
        else { if name.equals("get") || name.equals("contains") || name.equals("remove") { expected.push(key); }
        else { if name.equals("value_at") { expected.push("i64"); }
        else { if name.equals("set_value_at") { expected.push("i64"); expected.push(value); }
        else { if !name.equals("len") && !name.equals("clear") && !name.equals("keys") && !name.equals("values") { self.fail(token, "Dict method unsupported by C backend"); return self.invalid(); } } } } }
        if args.len() != expected.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        let values = Vec<Value>.new(); var i = 0;
        while i < args.len() { values.push(self.expression_as(args.get(i), expected.get(i), token)); i = i + 1; }
        if name.equals("len") { return self.value("i64", receiver + "->keys->length"); }
        if name.equals("clear") { return self.value("Void", "rl_dict_clear(" + receiver + ")"); }
        if name.equals("keys") { return self.value("Vec<" + key + ">", "rl_dict_snapshot(" + receiver + "->keys)"); }
        if name.equals("values") { return self.value("Vec<" + value + ">", "rl_dict_snapshot(" + receiver + "->values)"); }
        if name.equals("value_at") { return self.unpack(value, "rl_dict_value_at(" + receiver + ", " + values.get(0).code + ")"); }
        if name.equals("set_value_at") { return self.value("Void", "rl_dict_set_at(" + receiver + ", " + values.get(0).code + ", " + self.slot(values.get(1)) + ")"); }
        let first = receiver + ", " + self.slot(values.get(0));
        if name.equals("set") { return self.value("Void", "rl_dict_set(" + first + ", " + self.slot(values.get(1)) + ")"); }
        if name.equals("entry_index") { return self.value("i64", "rl_dict_entry(" + first + ", " + self.slot(values.get(1)) + ")"); }
        if name.equals("contains") { return self.value("Bool", "rl_dict_find(" + first + ") >= 0"); }
        return self.value(value + "?", "rl_dict_" + name + "(" + first + ")");
    }
    pub def if_let(statement: Statement) -> Bool {
        let optional = self.expression(statement.expr);
        if !self.is_optional(optional.type_name) { self.fail(statement.token, "if let requires an optional value"); return false; }
        self.output.append_line("if (" + optional.code + " != NULL) {");
        let inner = self.optional_inner(optional.type_name);
        let value = self.unpack(inner, optional.code + "->value");
        let scope = Dict<String, Binding>.with_capacity(8, 1);
        scope.set(statement.token.text, Binding { code: value.code, type_name: inner, mutable: false });
        self.scopes.push(scope);
        let yes = self.block(statement.body, false);
        self.scopes.pop(); self.output.append_line("} else {");
        let no = self.block(statement.alternative, true);
        self.output.append_line("}"); return yes && no;
    }
    pub def for_loop(statement: Statement) -> Bool {
        let object = self.expression(statement.expr);
        if !self.is_vector(object.type_name) { self.fail(statement.token, "for-loop iterable must be a Vec"); return false; }
        let element = self.vector_element(object.type_name); let index = self.fresh();
        self.output.append_line("for (int32_t " + index + " = 0; " + index + " < " + object.code + "->length; ++" + index + ") {");
        let value = self.unpack(element, "rl_vec_get(" + object.code + ", " + index + ")");
        let scope = Dict<String, Binding>.with_capacity(8, 1);
        scope.set(statement.token.text, Binding { code: value.code, type_name: element, mutable: false }); self.scopes.push(scope);
        self.loop_depth = self.loop_depth + 1; self.block(statement.body, false); self.loop_depth = self.loop_depth - 1;
        self.scopes.pop(); self.output.append_line("}"); return false;
    }
}
