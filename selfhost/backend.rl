import "lexer.rl"
import "ast.rl"
import "parser.rl"
import "generics.rl"
import "string_codegen.rl"
import "vector_codegen.rl"
import "dict_codegen.rl"
import "builder_codegen.rl"
import "range_codegen.rl"
import "os_codegen.rl"
import std.string_builder

pub struct Binding { pub var code: String; pub var type_name: String; pub var mutable: Bool; }
pub struct Value { pub var code: String; pub var type_name: String; }
pub struct SwitchResult { pub var value: Value; pub var returned: Bool; }
pub struct Backend {
    pub var program: Program;
    pub var functions: Dict<String, i32>;
    pub var scopes: Vec<Dict<String, Binding>>;
    pub var structs: Dict<String, i32>;
    pub var templates: Dict<String, i32>;
    pub var instances: Dict<String, i32>;
    pub var specialization_count: i32;
    pub var specialization_depth: i32;
    pub var expected_type: String;
    pub var type_depth: i32;
    pub var output: StringBuilder;
    pub var error: String;
    pub var next_id: i32;
    pub var depth: i32;
    pub var loop_depth: i32;
    pub var unsafe_depth: i32;
    pub var return_type: String;

    pub static def new(program: Program) -> Backend {
        return Backend { program: program, functions: Dict<String, i32>.with_capacity(16, 1),
            structs: Dict<String, i32>.with_capacity(16, 1), templates: Dict<String, i32>.with_capacity(16, 1), instances: Dict<String, i32>.with_capacity(16, 1), specialization_count: 0, specialization_depth: 0, expected_type: "", type_depth: 0, scopes: Vec<Dict<String, Binding>>.new(), output: StringBuilder.new(), error: "", next_id: 0, depth: 0, loop_depth: 0, unsafe_depth: 0, return_type: "i32" };
    }
    pub def fail(token: Token, message: String) -> Void {
        if self.error.is_empty() { self.error = location(token, message); }
    }
    pub def fresh() -> String {
        let name = f"rl_t{self.next_id}";
        self.next_id = self.next_id + 1;
        return name;
    }
    pub def value(type_name: String, expression: String) -> Value {
        let name = self.fresh();
        if type_name.equals("Void") {
            self.output.append_line(f"{expression};");
            return Value { code: "", type_name: "Void" };
        }
        self.output.append_line(f"{self.c_type(type_name)} {name} = {expression};");
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
        if expr.kind == 16 { return self.switch_value(expr.left, expr.arms, expected, true, token).value; }
        if expr.kind == 17 { return self.coerce(self.enum_construct(inner, expr.token, expr.args, expr.labels, inner), expected, token); }
        if expr.kind == 14 && self.program.expressions.get(expr.left).kind == 17 {
            return self.coerce(self.enum_construct(inner, expr.token, expr.args, expr.labels, inner), expected, token);
        }
        if self.numeric(inner) {
            if expr.kind == 1 { return self.coerce(self.integer_value(expr.token, false, inner), expected, token); }
            if expr.kind == 5 && expr.token.text.equals("-") {
                let literal = self.program.expressions.get(expr.left);
                if literal.kind == 1 { return self.coerce(self.integer_value(literal.token, true, inner), expected, token); }
            }
        }
        let previous = self.expected_type; self.expected_type = expected;
        let value = self.expression(id); self.expected_type = previous;
        return self.coerce(value, expected, token);
    }
    pub def require_type(token: Token, actual: String, expected: String) -> Void {
        if !actual.equals(expected) && !self.widens(actual, expected) { self.fail(token, f"expected {expected}, got {actual}"); }
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
                return self.value(expected, f"rl_some({self.slot(value)})");
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
            self.fail(token, f"integer literal out of {type_name} range"); return self.invalid();
        }
        if negative && digits.equals("2147483648") && type_name.equals("i32") { return self.value("i32", "INT32_MIN"); }
        if negative && digits.equals("9223372036854775808") && type_name.equals("i64") { return self.value("i64", "INT64_MIN"); }
        var code = digits;
        if type_name.equals("i64") { code = f"INT64_C({digits})"; }
        if negative { code = f"(-{code})"; }
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
        let expected = self.expected_type; self.expected_type = "";
        let result = self.emit_expression(id, expected);
        self.depth = self.depth - 1;
        return result;
    }
    pub def emit_expression(id: i32, expected: String) -> Value {
        let expr = self.program.expressions.get(id);
        let token = expr.token;
        if expr.kind == 16 { return self.switch_value(expr.left, expr.arms, "", true, token).value; }
        if expr.kind == 17 { self.fail(token, "enum shorthand requires a contextual type"); return self.invalid(); }
        if expr.kind == 8 { return Value { code: "NULL", type_name: "nil" }; }
        if expr.kind == 15 {
            let start = self.expression_as(expr.left, "i32", token);
            let end = self.expression_as(expr.right, "i32", token);
            var inclusive = "0"; if token.text.equals("...") { inclusive = "1"; }
            return self.value("IndexRange", f"rl_range_new({start.code}, {end.code}, {inclusive})");
        }
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
                    return self.value("RawPtr", f"(void *)(uintptr_t){value.code}");
                }
                if value.type_name.equals("RawPtr") {
                    if expr.type_name.equals("i64") { return self.value("i64", f"rl_bits64((uint64_t)(uintptr_t){value.code})"); }
                    if expr.type_name.equals("i32") { return self.value("i32", f"rl_bits((uint32_t)(uintptr_t){value.code})"); }
                    if expr.type_name.equals("u8") { return self.value("u8", f"(uint8_t)(uintptr_t){value.code}"); }
                }
                self.fail(token, "C backend supports RawPtr casts with integers only"); return self.invalid();
            }
            if !self.numeric(value.type_name) || !self.numeric(expr.type_name) { self.fail(token, "C backend supports numeric u8/i32/i64 casts only"); return self.invalid(); }
            if expr.type_name.equals("u8") { return self.value("u8", f"(uint8_t){value.code}"); }
            if expr.type_name.equals("i32") { return self.value("i32", f"rl_bits((uint32_t){value.code})"); }
            return self.value("i64", f"(int64_t){value.code}");
        }
        if expr.kind == 1 { return self.integer_value(token, false, ""); }
        if expr.kind == 2 {
            if token.text.equals("true") { return self.value("Bool", "1"); }
            return self.value("Bool", "0");
        }
        if expr.kind == 3 {
            if let binding = self.lookup(token.text) { return self.value(binding.type_name, binding.code); }
            self.fail(token, f"unknown variable '{token.text}'");
            return Value { code: "0", type_name: "i32" };
        }
        if expr.kind == 5 {
            let inner = self.program.expressions.get(expr.left);
            if token.text.equals("-") && inner.kind == 1 { return self.integer_value(inner.token, true, ""); }
            let value = self.expression(expr.left);
            if token.text.equals("!") { self.require_type(token, value.type_name, "Bool"); return self.value("Bool", f"!{value.code}"); }
            if !self.numeric(value.type_name) { self.fail(token, "expected integer operand"); }
            if token.text.equals("+") { return value; }
            if value.type_name.equals("i64") { return self.value("i64", f"rl_neg64({value.code})"); }
            if value.type_name.equals("u8") { return self.value("u8", f"(uint8_t)rl_neg({value.code})"); }
            return self.value("i32", f"rl_neg({value.code})");
        }
        if expr.kind == 6 {
            self.positional_arguments(expr);
            if let local = self.lookup(token.text) { self.fail(token, "local variable is not callable"); }
            var name = token.text;
            if !expr.type_name.is_empty() { name = expr.type_name; }
            if name.starts_with("$ambiguous$") { self.fail(token, "ambiguous imported function"); return self.invalid(); }
            if name.starts_with("$std$") { return self.stdlib_call(token, expr.args, true); }
            if let index = self.functions.get(name) { return self.call(index, expr.args, "", token, expected); }
            if !self.stdlib_module(token.text).is_empty() { return self.stdlib_call(token, expr.args, false); }
            self.fail(token, f"unknown function '{token.text}'"); return self.invalid();
        }
        if expr.kind == 9 {
            let owner = self.static_owner(expr.left);
            if self.is_enum(owner) { return self.enum_construct(owner, token, expr.args, expr.labels, expected); }
            let object = self.expression(expr.left);
            let field = self.field_index(object.type_name, token);
            if field < 0 { return self.invalid(); }
            let declaration = self.program.declarations.get(self.struct_index(object.type_name));
            return self.value(declaration.fields.get(field).type_name, f"{object.code}->rl_m{field}");
        }
        if expr.kind == 12 {
            let object = self.expression(expr.left);
            let index = self.index_value(object, expr.right, token);
            if self.is_dictionary(object.type_name) {
                let key = self.coerce(index, self.dict_key(object.type_name), token);
                return self.value(f"{self.dict_value(object.type_name)}?", f"rl_dict_get({object.code}, {self.slot(key)})");
            }
            if index.type_name.equals("IndexRange") {
                if object.type_name.equals("String") || self.is_vector(object.type_name) {
                    var helper = "rl_vec_slice"; if object.type_name.equals("String") { helper = "rl_string_slice"; }
                    return self.value(object.type_name, f"{helper}({object.code}, {index.code})");
                }
            }
            return self.vector_get(object, index, token);
        }
        if expr.kind == 10 { return self.construct(expr); }
        if expr.kind == 14 { return self.method_call(expr, expected); }
        if expr.kind != 4 { self.fail(token, "expression unsupported by C backend"); return self.invalid(); }
        let left = self.expression(expr.left);
        let op = token.text;
        if op.equals("&&") || op.equals("||") {
            self.require_type(token, left.type_name, "Bool");
            let result = self.value("Bool", left.code);
            if op.equals("&&") { self.output.append_line(f"if ({result.code}) {{"); }
            else { self.output.append_line(f"if (!{result.code}) {{"); }
            let right = self.expression(expr.right);
            self.require_type(token, right.type_name, "Bool");
            self.output.append_line(f"{result.code} = {right.code};\n}}");
            return result;
        }
        let right = self.expression(expr.right);
        if op.equals("+") && left.type_name.equals("String") {
            self.require_type(token, right.type_name, "String");
            return self.value("String", f"rl_string_concat({left.code}, {right.code})");
        }
        if op.equals("==") || op.equals("!=") {
            if !(self.numeric(left.type_name) && self.numeric(right.type_name)) { self.require_type(token, right.type_name, left.type_name); }
            if left.type_name.equals("String") { self.fail(token, "use String.equals for string comparison"); }
            if left.type_name.equals("Void") { self.fail(token, "Void is not a value"); }
            if self.is_optional(left.type_name) || left.type_name.equals("nil") { self.fail(token, "optional comparison unsupported; use if let"); }
            if left.type_name.equals("StringBuilder") || self.structs.contains(left.type_name) || self.is_vector(left.type_name) || self.is_dictionary(left.type_name) { self.fail(token, "cannot compare struct values"); }
            return self.value("Bool", f"{left.code} {op} {right.code}");
        }
        if !self.numeric(left.type_name) || !self.numeric(right.type_name) { self.fail(token, "expected integer operands"); }
        if op.equals("<") || op.equals(">") || op.equals("<=") || op.equals(">=") { return self.value("Bool", f"{left.code} {op} {right.code}"); }
        var helper = "rl_add";
        if op.equals("-") { helper = "rl_sub"; }
        if op.equals("*") { helper = "rl_mul"; }
        if op.equals("/") { helper = "rl_div"; }
        if op.equals("%") { helper = "rl_rem"; }
        var type_name = "i32";
        if left.type_name.equals("u8") && right.type_name.equals("u8") { type_name = "u8"; }
        if left.type_name.equals("i64") || right.type_name.equals("i64") { type_name = "i64"; helper = f"{helper}64"; }
        return self.value(type_name, f"{helper}({left.code}, {right.code})");
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
        if !statement.annotation.is_empty() { self.supported_type(token, statement.annotation); }
        if statement.kind == 16 { return self.switch_value(statement.expr, statement.arms, "", false, token).returned; }
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
        if statement.kind == 14 || statement.kind == 15 { return self.guard_statement(statement); }
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
                    self.output.append_line(f"rl_dict_set({object.code}, {self.slot(key)}, {self.slot(value)});"); return false;
                }
                if !self.is_vector(object.type_name) { self.fail(token, "index receiver must be a Vec or Dict"); return false; }
                self.require_type(token, index.type_name, "i32"); value = self.coerce(value, self.vector_element(object.type_name), token);
                self.output.append_line(f"rl_vec_set({object.code}, {index.code}, {self.slot(value)});"); return false;
            }
            if target.kind != 9 { self.fail(token, "assignment unsupported by C backend"); return false; }
            // Rolang evaluates an assignment's RHS before resolving its target.
            var value = self.expression(statement.expr);
            let object = self.expression(target.left);
            let field = self.field_index(object.type_name, target.token);
            if field < 0 { return false; }
            let property = self.program.declarations.get(self.struct_index(object.type_name)).fields.get(field);
            value = self.coerce(value, property.type_name, token);
            self.output.append_line(f"{object.code}->rl_m{field} = {value.code};");
            return false;
        }
        if statement.kind == 1 && statement.expr < 0 {
            self.require_type(token, "Void", self.return_type); self.output.append_line("return;"); return true;
        }
        if statement.kind == 5 {
            self.output.append_line("while (1) {");
            let condition = self.expression(statement.expr);
            self.require_type(token, condition.type_name, "Bool");
            self.output.append_line(f"if (!{condition.code}) break;");
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
            self.output.append_line(f"return {value.code};");
            return true;
        }
        if statement.kind == 2 {
            let scope = self.scopes.get(self.scopes.len() - 1);
            if scope.contains(token.text) { self.fail(token, f"duplicate local '{token.text}'"); }
            if !statement.annotation.is_empty() { value = self.coerce(value, statement.annotation, token); }
            if value.type_name.equals("Void") { self.fail(token, "Void is not a value"); }
            if value.type_name.equals("nil") { self.fail(token, "nil requires a concrete type annotation"); }
            var type_name = value.type_name;
            if !statement.annotation.is_empty() { type_name = statement.annotation; }
            let name = self.fresh();
            self.output.append_line(f"{self.c_type(type_name)} {name} = {value.code};");
            scope.set(token.text, Binding { code: name, type_name: type_name, mutable: statement.mutable });
        }
        if statement.kind == 3 {
            if let binding = self.lookup(token.text) {
                if !binding.mutable { self.fail(token, "cannot assign to let binding"); }
                value = self.coerce(value, binding.type_name, token);
                self.output.append_line(f"{binding.code} = {value.code};");
            } else { self.fail(token, f"unknown variable '{token.text}'"); }
        }
        if statement.kind == 4 {
            self.require_type(token, value.type_name, "Bool");
            self.output.append_line(f"if ({value.code}) {{");
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
        text.append(f"static {self.c_type(function.return_type)} rl_f{index}(");
        let instance = !function.owner.is_empty() && !function.modifiers.contains("static");
        if instance { text.append(f"{self.c_type(function.owner)} rl_self"); }
        for i in 0..<function.params.len() {
            if i > 0 || instance { text.append(", "); }
            text.append(f"{self.c_type(function.params.get(i).type_name)} rl_p{i}");
        }
        if function.params.len() == 0 && !instance { text.append("void"); }
        text.append(")");
        return text.to_string();
    }
    // Validate every node, including unreachable constructs, before emission.
    pub def validate_parameters(names: Vec<String>, token: Token) -> Void {
        let seen = Dict<String, Bool>.with_capacity(8, 1);
        for name in names {
            if seen.contains(name) { self.fail(token, "duplicate generic parameter"); }
            if primitive_type(name) || name.equals("Vec") || name.equals("Dict") || name.equals("String") || name.equals("StringBuilder") || name.equals("IndexRange") { self.fail(token, "reserved generic parameter name"); }
            seen.set(name, true);
        }
    }
    pub def function_parameters(function: Function) -> Vec<String> {
        let names = Vec<String>.new();
        if let index = self.templates.get(function.owner) { for name in self.program.declarations[index].generics { names.push(name); } }
        for name in function.generics { names.push(name); } return names;
    }
    pub def template_type(name: String, parameters: Vec<String>, token: Token) -> Void {
        if !type_unbound(name, parameters) { self.supported_type(token, name); return; }
        for param in parameters { if name.equals(param) { return; } }
        if self.is_optional(name) { self.template_type(self.optional_inner(name), parameters, token); return; }
        let base = type_base(name); let args = type_arguments(name); var arity = -1;
        if base.equals("Vec") { arity = 1; }
        if base.equals("Dict") { arity = 2; }
        if let index = self.templates.get(base) { arity = self.program.declarations[index].generics.len(); }
        if arity < 0 || args.len() != arity { self.fail(token, "unknown generic type or wrong type argument count"); return; }
        for arg in args { self.template_type(arg, parameters, token); }
    }
    pub def specialization_budget(token: Token) -> Bool {
        if self.specialization_count >= 256 || self.specialization_depth >= 32 { self.fail(token, "generic specialization limit exceeded"); return false; }
        self.specialization_count = self.specialization_count + 1; return true;
    }
    pub def instantiate_type(name: String, token: Token) -> Void {
        if !self.error.is_empty() || self.structs.contains(name) { return; }
        let args = type_arguments(name); if args.len() == 0 { return; }
        guard let template_index = self.templates.get(type_base(name)) else { return; }
        let template = self.program.declarations[template_index];
        if template.generics.len() != args.len() { self.fail(token, "wrong generic type argument count"); return; }
        if name.len() > 8192 { self.fail(token, "generic type size limit exceeded"); return; }
        if !self.specialization_budget(token) { return; }
        self.specialization_depth = self.specialization_depth + 1;
        let bindings = Dict<String, String>.with_capacity(8, 1);
        for i in 0..<args.len() { self.supported_type(token, args[i]); bindings.set(template.generics[i], args[i]); }
        if !self.error.is_empty() { self.specialization_depth = self.specialization_depth - 1; return; }
        let copier = Specializer.new(self.program, bindings); let declaration = copier.declaration(template, name);
        let index = self.program.declarations.len(); self.program.declarations.push(declaration);
        // Publish the layout identity before visiting recursive payload types.
        self.structs.set(name, index);
        for method in template.methods {
            let function = self.program.functions[method];
            let clone = Specializer.new(self.program, bindings).function(function, function.token.text, name, copy_items(function.generics));
            let id = self.program.functions.len(); self.program.functions.push(clone); declaration.methods.push(id);
            self.functions.set(f"{name}.{function.token.text}", id);
        }
        for field in declaration.fields { self.supported_type(field.token, field.type_name); }
        for variant in declaration.variants { for param in variant.payload { self.supported_type(param.token, param.type_name); } }
        self.specialization_depth = self.specialization_depth - 1;
    }
    pub def enum_index(name: String) -> i32 {
        let index = self.struct_index(name); if index >= 0 { return index; }
        if let template = self.templates.get(name) { return template; } return -1;
    }
    pub def infer(formal: String, actual: String, parameters: Vec<String>, bindings: Dict<String, String>, token: Token, invariant: Bool) -> Void {
        if !self.error.is_empty() { return; }
        // nil provides no information, but another argument or the return
        // context may still determine an optional parameter's concrete type.
        if actual.equals("nil") { return; }
        for param in parameters {
            if formal.equals(param) {
                if actual.equals("Void") { self.fail(token, "cannot infer a generic value type from Void"); return; }
                if let previous = bindings.get(param) {
                    if !previous.equals(actual) && (invariant || !self.widens(actual, previous)) { self.fail(token, f"conflicting generic argument for {param}: {previous} and {actual}"); }
                } else { bindings.set(param, actual); }
                return;
            }
        }
        if self.is_optional(formal) {
            var inner = actual; if self.is_optional(actual) { inner = self.optional_inner(actual); }
            self.infer(self.optional_inner(formal), inner, parameters, bindings, token, invariant); return;
        }
        let wanted = type_arguments(formal); let supplied = type_arguments(actual);
        if wanted.len() > 0 {
            if !type_base(formal).equals(type_base(actual)) || wanted.len() != supplied.len() { self.fail(token, f"generic type mismatch: expected {formal}, got {actual}"); return; }
            for i in 0..<wanted.len() { self.infer(wanted[i], supplied[i], parameters, bindings, token, true); } return;
        }
        let concrete = type_substitute(formal, bindings);
        if !concrete.equals(actual) && (invariant || !self.widens(actual, concrete)) { self.fail(token, f"expected {concrete}, got {actual}"); }
    }
    pub def arguments_for(parameters: Vec<String>, bindings: Dict<String, String>, token: Token) -> Vec<String> {
        let result = Vec<String>.new();
        for name in parameters {
            if let value = bindings.get(name) { self.supported_type(token, value); result.push(value); }
            else { self.fail(token, f"cannot infer generic parameter {name}; provide a concrete type context"); result.push("i32"); }
        }
        return result;
    }
    pub def generic_call(index: i32, args: Vec<i32>, receiver: String, token: Token, expected: String) -> Value {
        let function = self.program.functions[index]; let bindings = Dict<String, String>.with_capacity(8, 1);
        var context = expected;
        if self.is_optional(context) && !self.is_optional(function.return_type) && type_arguments(function.return_type).len() > 0 { context = self.optional_inner(context); }
        // For a bare T and optional context, first let the arguments decide
        // whether T itself is optional or the return needs optional lifting.
        let defer_context = self.is_optional(context) && !self.is_optional(function.return_type) && type_arguments(function.return_type).len() == 0;
        if !context.is_empty() && !defer_context && type_unbound(function.return_type, function.generics) {
            self.infer(function.return_type, context, function.generics, bindings, token, false);
        }
        let values = Vec<Value>.new();
        for i in 0..<args.len() {
            let formal = function.params[i].type_name; let substituted = type_substitute(formal, bindings); var value = self.invalid();
            if type_unbound(substituted, function.generics) { value = self.expression(args[i]); }
            else { value = self.expression_as(args[i], substituted, token); }
            self.infer(formal, value.type_name, function.generics, bindings, token, false); values.push(value);
        }
        if !context.is_empty() && type_unbound(type_substitute(function.return_type, bindings), function.generics) {
            self.infer(function.return_type, context, function.generics, bindings, token, false);
        }
        let types = self.arguments_for(function.generics, bindings, token);
        if !self.error.is_empty() { return self.invalid(); }
        let key = type_apply(index.to_string(), types); var concrete = -1;
        if let cached = self.instances.get(key) { concrete = cached; }
        else {
            if !self.specialization_budget(token) { return self.invalid(); }
            let clone = Specializer.new(self.program, bindings).function(function, type_apply(function.token.text, types), function.owner, Vec<String>.new());
            concrete = self.program.functions.len(); self.program.functions.push(clone); self.instances.set(key, concrete);
            if !clone.return_type.equals("Void") { self.supported_type(token, clone.return_type); }
            for param in clone.params { self.supported_type(token, param.type_name); }
        }
        let instance = self.program.functions[concrete]; let code = StringBuilder.new(); code.append(receiver);
        for i in 0..<values.len() {
            let value = self.coerce(values[i], instance.params[i].type_name, token);
            if i > 0 || !receiver.is_empty() { code.append(", "); } code.append(value.code);
        }
        return self.value(instance.return_type, f"rl_f{concrete}({code})");
    }
    pub def supported_type(token: Token, type_name: String) -> Void {
        if !self.error.is_empty() { return; }
        if type_name.len() > 8192 || self.type_depth >= 128 { self.fail(token, "generic specialization limit exceeded (type size or nesting)"); return; }
        self.type_depth = self.type_depth + 1;
        self.check_supported_type(token, type_name);
        self.type_depth = self.type_depth - 1;
    }
    pub def check_supported_type(token: Token, type_name: String) -> Void {
        if self.is_optional(type_name) { self.supported_type(token, self.optional_inner(type_name)); return; }
        if self.is_dictionary(type_name) {
            let key = self.dict_key(type_name); let value = self.dict_value(type_name);
            self.supported_type(token, key); self.supported_type(token, value);
            if self.is_optional(key) { self.fail(token, "optional dictionary keys unsupported by C backend"); }
            return;
        }
        if self.is_vector(type_name) { self.supported_type(token, self.vector_element(type_name)); return; }
        self.instantiate_type(type_name, token);
        if !self.numeric(type_name) && !type_name.equals("Bool") && !type_name.equals("String") && !type_name.equals("StringBuilder") && !type_name.equals("RawPtr") && !type_name.equals("IndexRange") && !self.structs.contains(type_name) {
            self.fail(token, "C backend supports only u8, i32, i64, Bool, RawPtr, String, StringBuilder, collections, or concrete declared structs/enums");
        }
    }
    pub def validate_subset() -> Void {
        var index = 0;
        for declaration in self.program.declarations {
            if declaration.kind == 1 && self.builtin_module(declaration.value) { index = index + 1; continue; }
            if declaration.kind == 3 { index = index + 1; continue; }
            if declaration.kind != 2 && declaration.kind != 4 { self.fail(declaration.token, "declaration unsupported by C backend"); }
            else {
                let name = declaration.token.text;
                if self.templates.contains(name) || primitive_type(name) || name.equals("String") || name.equals("Vec") || name.equals("Dict") || name.equals("StringBuilder") || name.equals("IndexRange") { self.fail(declaration.token, "duplicate or reserved struct name"); }
                self.templates.set(name, index); self.validate_parameters(declaration.generics, declaration.token);
                if declaration.generics.len() == 0 { self.structs.set(name, index); }
            }
            index = index + 1;
        }
        var declaration_index = 0;
        while declaration_index < self.program.declarations.len() {
            let declaration = self.program.declarations[declaration_index]; declaration_index = declaration_index + 1;
            if declaration.kind == 3 && !declaration.value.equals("Void") { self.supported_type(declaration.token, declaration.value); }
            let names = Dict<String, i32>.with_capacity(8, 1);
            if declaration.kind == 4 && declaration.variants.len() == 0 { self.fail(declaration.token, "enum must declare at least one case"); }
            for variant in declaration.variants {
                if names.contains(variant.token.text) { self.fail(variant.token, "duplicate enum case"); }
                names.set(variant.token.text, 1);
                let labels = Dict<String, Bool>.with_capacity(8, 1);
                for param in variant.payload {
                    self.template_type(param.type_name, declaration.generics, param.token);
                    if !param.token.text.is_empty() {
                        if labels.contains(param.token.text) { self.fail(param.token, "duplicate enum payload label"); }
                        labels.set(param.token.text, true);
                    }
                }
            }
            for field in declaration.fields {
                self.template_type(field.type_name, declaration.generics, field.token);
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
            if function.owner.is_empty() && function.modifiers.contains("static") { self.fail(function.token, "function unsupported by C backend"); }
            let parameter_names = Dict<String, Bool>.with_capacity(8, 1);
            if !function.owner.is_empty() && !function.modifiers.contains("static") { parameter_names.set("self", true); }
            for param in function.params {
                if parameter_names.contains(param.token.text) { self.fail(param.token, "duplicate parameter"); }
                parameter_names.set(param.token.text, true);
            }
            self.validate_parameters(function.generics, function.token);
            let parameters = self.function_parameters(function);
            self.validate_parameters(parameters, function.token);
            if !function.return_type.equals("Void") { self.template_type(function.return_type, parameters, function.token); }
            for param in function.params { self.template_type(param.type_name, parameters, param.token); }
            if function.owner.is_empty() && self.structs.contains(function.token.text) { self.fail(function.token, "function conflicts with struct name"); }
        }
        for expression in self.program.expressions {
            if expression.kind > 6 && expression.kind != 7 && expression.kind != 8 && expression.kind != 11 && expression.kind != 12 && expression.kind != 13 && expression.kind != 9 && expression.kind != 10 && expression.kind != 14 && expression.kind != 15 && expression.kind != 16 && expression.kind != 17 { self.fail(expression.token, "expression unsupported by C backend"); }
        }
        for statement in self.program.statements {
            if statement.expr < 0 && ((statement.kind == 2 && !self.is_optional(statement.annotation)) || statement.kind == 9 || statement.kind == 3 || statement.kind == 4 || statement.kind == 5 || statement.kind == 6 || statement.kind == 8 || statement.kind == 13) { self.fail(statement.token, "C backend requires an expression or initializer"); }

        }
    }
    pub def generate() -> Void {
        self.validate_subset();
        if !self.error.is_empty() { return; }
        var i = 0;
        while i < self.program.functions.len() {
            let function = self.program.functions.get(i);
            var key = function.token.text;
            if !function.owner.is_empty() { key = f"{function.owner}.{key}"; }
            if let previous = self.functions.get(key) { if previous != i { self.fail(function.token, "duplicate function"); } }
            self.functions.set(key, i);
            i = i + 1;
        }
        if let index = self.functions.get("main") {
            let main_function = self.program.functions.get(index);
            if main_function.generics.len() != 0 || main_function.params.len() != 0 || !main_function.return_type.equals("i32") { self.fail(main_function.token, "main must have signature def main() -> i32"); }
        } else { self.error = "1:1: missing main function"; }
        if !self.error.is_empty() { return; }
        i = 0;
        while i < self.program.functions.len() && self.error.is_empty() {
            let function = self.program.functions.get(i);
            if self.function_parameters(function).len() > 0 { i = i + 1; continue; }
            if !function.return_type.equals("Void") { self.supported_type(function.token, function.return_type); }
            for param in function.params { self.supported_type(param.token, param.type_name); }
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
                scope.set(param.token.text, Binding { code: f"rl_p{p}", type_name: param.type_name, mutable: false });
                p = p + 1;
            }
            self.output.append_line(f"{self.signature(i)} {{");
            if !self.block(function.body, false) && !function.return_type.equals("Void") { self.fail(function.token, "function must return on every path"); }
            self.output.append_line("}");
            self.scopes.pop();
            i = i + 1;
        }
        if !self.error.is_empty() { return; }
        let bodies = self.output.to_string(); self.output = StringBuilder.new();
        self.output.append(r"""#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700
#endif
#include <sys/stat.h>
#include <stdint.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef struct rl_string rl_string; typedef struct rl_vec rl_vec; typedef struct rl_optional rl_optional; typedef struct rl_dict rl_dict; typedef struct rl_builder rl_builder; typedef struct rl_range rl_range;
""");
        self.emit_structs();
        self.output.append(r"""static int32_t rl_bits(uint32_t x) { int32_t y; memcpy(&y, &x, 4); return y; }
static int32_t rl_add(int32_t a,int32_t b) { return rl_bits((uint32_t)a+(uint32_t)b); }
static int32_t rl_sub(int32_t a,int32_t b) { return rl_bits((uint32_t)a-(uint32_t)b); }
static int32_t rl_mul(int32_t a,int32_t b) { return rl_bits((uint32_t)a*(uint32_t)b); }
static int32_t rl_neg(int32_t a) { return rl_bits(0u-(uint32_t)a); }
static void rl_zero(void) { fputs("division by zero\n", stderr); exit(1); }
static int32_t rl_div(int32_t a,int32_t b) { if(!b) rl_zero(); if(a==INT32_MIN && b==-1) return INT32_MIN; return a/b; }
static int32_t rl_rem(int32_t a,int32_t b) { if(!b) rl_zero(); if(a==INT32_MIN && b==-1) return 0; return a%b; }
""");
        emit_string_runtime(self.output);
        emit_builder_runtime(self.output);
        emit_os_runtime(self.output);
        emit_vector_runtime(self.output);
        emit_range_runtime(self.output);
        self.output.append(r"""struct rl_optional { rl_slot value; };
static rl_optional *rl_some(rl_slot value) { rl_optional *p = rl_allocate(sizeof(*p)); p->value = value; return p; }
""");
        emit_dict_runtime(self.output);
        i = 0;
        while i < self.program.functions.len() { if self.function_parameters(self.program.functions[i]).len() == 0 { self.output.append_line(f"{self.signature(i)};"); } i = i + 1; }
        self.output.append(bodies);
        if let index = self.functions.get("main") { self.output.append_line(f"int main(int argc, char **argv) {{ rl_argc = argc; rl_argv = argv; if (atexit(rl_cleanup)) return 1; return (int)rl_f{index}(); }}"); }
    }

    pub def invalid() -> Value { return Value { code: "0", type_name: "i32" }; }
    pub def struct_index(name: String) -> i32 {
        if let index = self.structs.get(name) { return index; }
        return -1;
    }
    pub def c_type(name: String) -> String {
        if self.is_optional(name) { return "rl_optional*"; }
        if name.equals("IndexRange") { return "rl_range*"; }
        if name.equals("Void") { return "void"; }
        if name.equals("i64") { return "int64_t"; }
        if name.equals("u8") { return "uint8_t"; }
        if name.equals("StringBuilder") { return "rl_builder*"; }
        if name.equals("RawPtr") { return "void*"; }
        if name.equals("String") { return "rl_string*"; }
        if self.is_vector(name) { return "rl_vec*"; }
        if self.is_dictionary(name) { return "rl_dict*"; }
        let index = self.struct_index(name);
        if index >= 0 { return f"rl_s{index}*"; }
        return "int32_t";
    }
    pub def field_index(owner: String, token: Token) -> i32 {
        let index = self.struct_index(owner);
        if index < 0 { self.fail(token, "field receiver must be a struct"); return -1; }
        let fields = self.program.declarations.get(index).fields;
        for i in 0..<fields.len() {
            if fields.get(i).token.text.equals(token.text) {
                let field = fields.get(i);
                if !field.modifiers.contains("pub ") && !field.token.source.equals(token.source) { self.fail(token, "field is private to its module"); return -1; }
                return i;
            }
        }
        self.fail(token, f"unknown field '{token.text}'"); return -1;
    }
    pub def call(index: i32, values: Vec<i32>, receiver: String, token: Token, expected: String) -> Value {
        let function = self.program.functions.get(index);
        if !function.modifiers.contains("pub ") && !function.token.source.equals(token.source) { self.fail(token, "function or method is private to its module"); return self.invalid(); }
        if function.modifiers.contains("unsafe") && self.unsafe_depth == 0 { self.fail(token, "unsafe function call requires unsafe"); }
        if function.params.len() != values.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        if function.generics.len() > 0 { return self.generic_call(index, values, receiver, token, expected); }
        let args = StringBuilder.new(); args.append(receiver);
        for i in 0..<values.len() {
            var value = self.invalid();
            if i < function.params.len() { value = self.expression_as(values.get(i), function.params.get(i).type_name, token); }
            else { value = self.expression(values.get(i)); }
            if i > 0 || !receiver.is_empty() { args.append(", "); }
            args.append(value.code);
        }
        return self.value(function.return_type, f"rl_f{index}({args})");
    }
    pub def reference_root(id: i32) -> String {
        var current = id;
        while current >= 0 {
            let node = self.program.expressions.get(current);
            if node.kind == 3 { return node.token.text; }
            if node.kind != 9 { return ""; }
            current = node.left;
        }
        return "";
    }
    pub def method_call(expr: Expression, expected: String) -> Value {
        let member = self.program.expressions.get(expr.left);
        if member.kind == 17 { self.fail(member.token, "enum shorthand requires a contextual type"); return self.invalid(); }
        if member.kind != 9 { self.fail(expr.token, "call target unsupported by C backend"); return self.invalid(); }
        let enum_owner = self.static_owner(member.left);
        if self.is_enum(enum_owner) && self.variant_index(enum_owner, member.token.text) >= 0 {
            return self.enum_construct(enum_owner, member.token, expr.args, expr.labels, expected);
        }
        self.positional_arguments(expr);
        let root = self.reference_root(expr.left);
        var shadowed = false;
        if !root.is_empty() { if let local = self.lookup(root) { shadowed = true; } }
        if !shadowed && !expr.type_name.is_empty() {
            if expr.type_name.starts_with("$ambiguous$") { self.fail(expr.token, "ambiguous imported function"); return self.invalid(); }
            if expr.type_name.starts_with("$std$") { return self.stdlib_call(member.token, expr.args, true); }
            if let index = self.functions.get(expr.type_name) { return self.call(index, expr.args, "", member.token, expected); }
        }
        let receiver = self.program.expressions.get(member.left);
        if receiver.kind == 13 {
            self.supported_type(receiver.token, receiver.type_name);
            if self.is_dictionary(receiver.type_name) { return self.dict_constructor(receiver.type_name, member.token, expr.args); }
            if self.is_vector(receiver.type_name) { return self.vector_constructor(receiver.type_name, member.token, expr.args); }
            if !self.structs.contains(receiver.type_name) { self.fail(receiver.token, "generic receiver unsupported by C backend"); return self.invalid(); }
        }
        var owner = ""; var code = ""; var static_call = false;
        var receiver_type = receiver.token.text;
        if !receiver.type_name.is_empty() { receiver_type = receiver.type_name; }
        if (receiver.kind == 3 || receiver.kind == 13 || (receiver.kind == 9 && !receiver.type_name.is_empty())) && (self.structs.contains(receiver_type) || self.is_vector(receiver_type) || self.is_dictionary(receiver_type) || receiver_type.equals("StringBuilder") || receiver_type.starts_with("$ambiguous$")) {
            if shadowed { static_call = false; }
            else {
                if receiver_type.starts_with("$ambiguous$") { self.fail(receiver.token, "ambiguous imported type"); return self.invalid(); }
                static_call = true; owner = receiver_type;
            }
        }
        if !static_call {
            let object = self.expression(member.left); owner = object.type_name; code = object.code;
        }
        if static_call && self.is_vector(owner) { self.supported_type(member.token, owner); return self.vector_constructor(owner, member.token, expr.args); }
        if static_call && self.is_dictionary(owner) { self.supported_type(member.token, owner); return self.dict_constructor(owner, member.token, expr.args); }
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
        if !static_call && (owner.equals("String") || owner.equals("Bool") || self.numeric(owner)) { return self.builtin_method(owner, code, member.token, expr.args); }
        if let index = self.functions.get(f"{owner}.{member.token.text}") {
            let function = self.program.functions.get(index);
            if function.modifiers.contains("static") != static_call { self.fail(member.token, "static/instance method receiver mismatch"); return self.invalid(); }
            return self.call(index, expr.args, code, member.token, expected);
        }
        self.fail(member.token, f"unknown method '{member.token.text}'"); return self.invalid();
    }
    pub def construct(expr: Expression) -> Value {
        if type_arguments(expr.type_name).len() > 0 { self.supported_type(expr.token, expr.type_name); }
        let index = self.struct_index(expr.type_name);
        if index < 0 { self.fail(expr.token, f"unknown struct '{expr.type_name}'"); return self.invalid(); }
        let declaration = self.program.declarations.get(index);
        if declaration.kind != 2 { self.fail(expr.token, "enum requires a case constructor"); return self.invalid(); }
        let seen = Dict<String, i32>.with_capacity(8, 1);
        let result = self.value(expr.type_name, f"rl_allocate(sizeof(rl_s{index}))");
        var i = 0;
        while i < expr.args.len() && self.error.is_empty() {
            let label = expr.labels.get(i);
            let token = Token { text: label, kind: 1, line: expr.token.line, column: expr.token.column, source: expr.token.source };
            let field = self.field_index(expr.type_name, token);
            if field < 0 { return self.invalid(); }
            if seen.contains(label) { self.fail(token, "duplicate field initializer"); }
            seen.set(label, 1);
            let value = self.expression_as(expr.args.get(i), declaration.fields.get(field).type_name, token);
            self.output.append_line(f"{result.code}->rl_m{field} = {value.code};");
            i = i + 1;
        }
        if seen.len() != declaration.fields.len() { self.fail(expr.token, "missing field initializer"); }
        return result;
    }
    pub def emit_structs() -> Void {
        self.output.append(r"""typedef struct rl_allocation { void *object; struct rl_allocation *next; } rl_allocation;
static rl_allocation *rl_allocations;
static void rl_cleanup(void) { while (rl_allocations) { rl_allocation *p = rl_allocations; rl_allocations = p->next; free(p->object); free(p); } }
static void *rl_allocate(size_t size) { void *p = calloc(1, size); rl_allocation *a = malloc(sizeof(*a)); if (!p || !a) { free(p); free(a); fputs("allocation failed\n", stderr); exit(1); } a->object = p; a->next = rl_allocations; rl_allocations = a; return p; }
""");
        var i = 0;
        while i < self.program.declarations.len() {
            if self.program.declarations.get(i).generics.len() == 0 && (self.program.declarations.get(i).kind == 2 || self.program.declarations.get(i).kind == 4) { self.output.append_line(f"typedef struct rl_s{i} rl_s{i};"); } i = i + 1;
        }
        i = 0;
        for declaration in self.program.declarations {
            if declaration.generics.len() > 0 || (declaration.kind != 2 && declaration.kind != 4) { i = i + 1; continue; }
            self.output.append_line(f"struct rl_s{i} {{");
            if declaration.kind == 4 {
                self.output.append_line("int32_t rl_tag;");
                for tag in 0..<declaration.variants.len() {
                    let variant = declaration.variants.get(tag);
                    for payload in 0..<variant.payload.len() {
                        self.output.append_line(f"{self.c_type(variant.payload.get(payload).type_name)} rl_v{tag}_{payload};");
                    }
                }
            }
            var field = 0;
            for property in declaration.fields {
                self.output.append_line(f"{self.c_type(property.type_name)} rl_m{field};"); field = field + 1;
            }
            if field == 0 { self.output.append_line("unsigned char rl_empty;"); }
            self.output.append_line("};"); i = i + 1;
        }
    }

    pub def positional_arguments(expr: Expression) -> Void {
        for label in expr.labels { if !label.is_empty() { self.fail(expr.token, "named arguments are supported only for enum constructors"); } }
    }
    pub def is_enum(name: String) -> Bool {
        let index = self.enum_index(name);
        return index >= 0 && self.program.declarations.get(index).kind == 4;
    }
    pub def static_owner(id: i32) -> String {
        if id < 0 { return ""; }
        let root = self.reference_root(id);
        if !root.is_empty() { if let local = self.lookup(root) { return ""; } }
        let node = self.program.expressions.get(id);
        if node.kind != 3 && node.kind != 9 && node.kind != 13 { return ""; }
        if !node.type_name.is_empty() {
            if self.templates.contains(type_base(node.type_name)) && type_arguments(node.type_name).len() > 0 { self.supported_type(node.token, node.type_name); }
            return node.type_name;
        }
        if node.kind == 3 { return node.token.text; }
        return "";
    }
    pub def variant_index(owner: String, name: String) -> i32 {
        if !self.is_enum(owner) { return -1; }
        let variants = self.program.declarations.get(self.enum_index(owner)).variants;
        for i in 0..<variants.len() { if variants.get(i).token.text.equals(name) { return i; } }
        return -1;
    }
    pub def enum_construct(owner: String, token: Token, args: Vec<i32>, labels: Vec<String>, expected: String) -> Value {
        self.instantiate_type(owner, token);
        let tag = self.variant_index(owner, token.text);
        if tag < 0 { self.fail(token, "unknown enum case or enum type"); return self.invalid(); }
        let declaration = self.program.declarations[self.enum_index(owner)];
        let variant = declaration.variants[tag];
        if args.len() != variant.payload.len() { self.fail(token, "wrong enum payload count"); return self.invalid(); }
        let bindings = Dict<String, String>.with_capacity(8, 1); let parameters = declaration.generics;
        if parameters.len() > 0 && !expected.is_empty() {
            var context = expected; if self.is_optional(context) { context = self.optional_inner(context); }
            self.infer(type_apply(owner, parameters), context, parameters, bindings, token, true);
        }
        let seen = Dict<i32, Bool>.with_capacity(8, 0); let values = Vec<Value>.new(); let targets = Vec<i32>.new();
        for i in 0..<args.len() {
            var target = i; var label = "";
            if i < labels.len() { label = labels[i]; }
            if !label.is_empty() {
                target = -1;
                for j in 0..<variant.payload.len() { if variant.payload[j].token.text.equals(label) { target = j; } }
            }
            if target < 0 || target >= variant.payload.len() { self.fail(token, "unknown enum payload label"); return self.invalid(); }
            if seen.contains(target) { self.fail(token, "duplicate enum payload argument"); return self.invalid(); }
            seen.set(target, true); targets.push(target);
            let formal = variant.payload[target].type_name; let substituted = type_substitute(formal, bindings);
            var value = self.invalid();
            if type_unbound(substituted, parameters) { value = self.expression(args[i]); }
            else { value = self.expression_as(args[i], substituted, token); }
            if parameters.len() > 0 { self.infer(formal, value.type_name, parameters, bindings, token, false); }
            values.push(value);
        }
        var concrete = owner;
        if parameters.len() > 0 { concrete = type_apply(owner, self.arguments_for(parameters, bindings, token)); self.supported_type(token, concrete); }
        if !self.error.is_empty() { return self.invalid(); }
        let index = self.struct_index(concrete); let payload = self.program.declarations[index].variants[tag].payload;
        let result = self.value(concrete, f"rl_allocate(sizeof(rl_s{index}))"); self.output.append_line(f"{result.code}->rl_tag = {tag};");
        for i in 0..<values.len() {
            let target = targets[i]; let value = self.coerce(values[i], payload[target].type_name, token);
            self.output.append_line(f"{result.code}->rl_v{tag}_{target} = {value.code};");
        }
        return result;
    }
    // Each failed test jumps past this arm's scope. Payloads are loaded only
    // after testing the containing tag, including nested recursive patterns.
    pub def match_pattern(pattern: Pattern, value: Value, next: String) -> Void {
        if !self.error.is_empty() { return; }
        switch pattern {
        case .wildcard(_): return;
        case .binding(let token, let mutable):
            let scope = self.scopes.get(self.scopes.len() - 1); let name = token.text;
            if scope.contains(name) { self.fail(token, "duplicate pattern binding"); return; }
            let bound = self.value(value.type_name, value.code);
            scope.set(name, Binding { code: bound.code, type_name: value.type_name, mutable: mutable }); return;
        case .literal(let token, let literal_id):
            let expr = self.program.expressions.get(literal_id);
            if expr.kind == 8 && self.is_optional(value.type_name) {
                self.output.append_line(f"if ({value.code} != NULL) goto {next};"); return;
            }
            if expr.kind != 1 && expr.kind != 2 && expr.kind != 7 && !(expr.kind == 5 && expr.token.text.equals("-") && self.program.expressions.get(expr.left).kind == 1) {
                self.fail(token, "pattern requires a literal"); return;
            }
            if !self.numeric(value.type_name) && !value.type_name.equals("Bool") && !value.type_name.equals("String") {
                self.fail(token, "literal pattern incompatible with subject"); return;
            }
            let literal = self.expression_as(literal_id, value.type_name, token);
            if value.type_name.equals("String") { self.output.append_line(f"if (rl_string_compare({value.code}, {literal.code}) != 0) goto {next};"); }
            else { self.output.append_line(f"if ({value.code} != {literal.code}) goto {next};"); }
            return;
        case .variant(let token, let children):
        let tag = self.variant_index(value.type_name, token.text);
        if tag < 0 { self.fail(token, "unknown enum case in pattern"); return; }
        let variant = self.program.declarations.get(self.struct_index(value.type_name)).variants.get(tag);
        if children.len() != variant.payload.len() { self.fail(token, "wrong enum pattern payload count"); return; }
        self.output.append_line(f"if ({value.code}->rl_tag != {tag}) goto {next};");
        for i in 0..<children.len() {
            let payload = self.value(variant.payload.get(i).type_name, f"{value.code}->rl_v{tag}_{i}");
            self.match_pattern(children.get(i), payload, next);
        }
        }
    }
    pub def irrefutable(pattern: Pattern) -> Bool {
        return switch pattern {
            case .wildcard(_): true;
            case .binding(_, _): true;
            default: false;
        };
    }
    pub def coverage(pattern: Pattern) -> String {
        if self.irrefutable(pattern) { return "*"; }
        switch pattern {
            case .variant(let token, let children):
                for child in children { if !self.irrefutable(child) { return ""; } }
                return f"case:{token.text}";
            case .literal(_, let index):
                let literal = self.program.expressions.get(index);
                if literal.kind == 2 { return literal.token.text; }
            default: {}
        }
        return "";
    }
    pub def switch_value(subject: i32, arms: Vec<SwitchArm>, expected: String, values: Bool, token: Token) -> SwitchResult {
        let value = self.expression(subject); let outer = self.output; self.output = StringBuilder.new();
        let result = self.fresh(); let end = self.fresh(); var result_type = expected;
        let covered = Dict<String, Bool>.with_capacity(8, 1); var returned = arms.len() > 0;
        for arm in arms {
            let next = self.fresh(); self.output.append_line("{");
            self.scopes.push(Dict<String, Binding>.with_capacity(8, 1));
            self.match_pattern(arm.pattern, value, next);
            if arm.guard_expr >= 0 {
                let guard_value = self.expression_as(arm.guard_expr, "Bool", arm.token);
                self.output.append_line(f"if (!{guard_value.code}) goto {next};");
            } else { let key = self.coverage(arm.pattern); if !key.is_empty() { covered.set(key, true); } }
            if values {
                var branch = self.invalid();
                if result_type.is_empty() { branch = self.expression(arm.value); result_type = branch.type_name; }
                else { branch = self.expression_as(arm.value, result_type, arm.token); }
                if result_type.equals("Void") || result_type.equals("nil") { self.fail(arm.token, "switch branches must produce a concrete value"); }
                self.output.append_line(f"{result} = {branch.code};");
            } else { if !self.block(arm.body, false) { returned = false; } }
            self.output.append_line(f"goto {end};");
            self.scopes.pop(); self.output.append_line(f"}}
{next}:;");
        }
        var exhaustive = covered.contains("*");
        if value.type_name.equals("Bool") { exhaustive = exhaustive || (covered.contains("true") && covered.contains("false")); }
        if self.is_enum(value.type_name) {
            var complete = true;
            for variant in self.program.declarations.get(self.struct_index(value.type_name)).variants {
                if !covered.contains(f"case:{variant.token.text}") { complete = false; }
            }
            exhaustive = exhaustive || complete;
        }
        if !exhaustive && (values || self.is_enum(value.type_name) || value.type_name.equals("Bool")) { self.fail(token, "switch must be exhaustive; add an unguarded case or default"); }
        self.output.append_line(f"{end}:;");
        let body = self.output.to_string(); self.output = outer;
        if values { self.output.append_line(f"{self.c_type(result_type)} {result};"); }
        self.output.append(body);
        return SwitchResult { value: Value { code: result, type_name: result_type }, returned: returned && exhaustive };
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
        return self.value("String", f"rl_string_new((const unsigned char *){text}, {length})");
    }
    pub def builtin_method(owner: String, receiver: String, token: Token, args: Vec<i32>) -> Value {
        let name = token.text; let expected = Vec<String>.new();
        if name.equals("to_string") && args.len() == 0 {
            if owner.equals("String") { return Value { code: receiver, type_name: "String" }; }
            if owner.equals("Bool") { return self.value("String", f"rl_string_new((const unsigned char *)({receiver} ? \"true\" : \"false\"), {receiver} ? 4 : 5)"); }
            if self.numeric(owner) { return self.value("String", f"rl_integer_string({receiver})"); }
        }
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
                expected.push("String"); helper = f"rl_string_{name}"; result = "Bool";
            }
            if name.equals("byte_at") || name.equals("char_at") { expected.push("i32"); helper = "rl_string_byte"; }
            if name.equals("find_char") { expected.push("i32"); expected.push("i32"); helper = "rl_string_find_char"; }
            if name.equals("substring") { expected.push("i32"); expected.push("i32"); helper = "rl_string_substring"; result = "String"; }
        }
        if helper.is_empty() { self.fail(token, "method unsupported by C backend"); return self.invalid(); }
        if expected.len() != args.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        let code = StringBuilder.new(); code.append(f"{helper}({receiver}");
        for i in 0..<args.len() {
            let value = self.expression_as(args.get(i), expected.get(i), token);
            code.append(f", {value.code}");
        }
        code.append(")");
        if helper.equals("length") { return self.value("i64", f"{receiver}->length"); }
        if helper.equals("empty") { return self.value("Bool", f"{receiver}->length == 0"); }
        if name.equals("equals") { code.append(" == 0"); }
        return self.value(result, code.to_string());
    }

    pub def is_vector(name: String) -> Bool { return name.starts_with("Vec<") && name.ends_with(">"); }
    pub def vector_element(name: String) -> String { return name.substring(4, (name.len() as i32) - 5); }
    pub def slot(value: Value) -> String {
        if self.numeric(value.type_name) || value.type_name.equals("Bool") { return f"((rl_slot){{.number = {value.code}}})"; }
        return f"((rl_slot){{.reference = {value.code}}})";
    }
    pub def unpack(type_name: String, expression: String) -> Value {
        var field = "reference";
        if self.numeric(type_name) || type_name.equals("Bool") { field = "number"; }
        return self.value(type_name, f"({self.c_type(type_name)})({expression}).{field}");
    }
    pub def index_value(object: Value, id: i32, token: Token) -> Value {
        if self.is_dictionary(object.type_name) { return self.expression_as(id, self.dict_key(object.type_name), token); }
        if self.is_vector(object.type_name) { return self.expression(id); }
        return self.expression(id);
    }
    pub def vector_get(object: Value, index: Value, token: Token) -> Value {
        if !self.is_vector(object.type_name) { self.fail(token, "index receiver must be a Vec or Dict"); return self.invalid(); }
        self.require_type(token, index.type_name, "i32");
        return self.unpack(self.vector_element(object.type_name), f"rl_vec_get({object.code}, {index.code})");
    }
    pub def vector_constructor(type_name: String, token: Token, args: Vec<i32>) -> Value {
        var capacity = "8"; var count = 0;
        if token.text.equals("with_capacity") { count = 1; }
        else { if !token.text.equals("new") { self.fail(token, "unknown Vec constructor"); return self.invalid(); } }
        if args.len() != count { self.fail(token, "wrong argument count"); return self.invalid(); }
        if count == 1 { let value = self.expression(args.get(0)); self.require_type(token, value.type_name, "i32"); capacity = value.code; }
        return self.value(type_name, f"rl_vec_new({capacity})");
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
        let values = Vec<Value>.new(); for i in 0..<args.len() { let value = self.expression_as(args.get(i), expected.get(i), token); values.push(value); }
        if name.equals("len") { return self.value("i32", f"{receiver}->length"); }
        if name.equals("pop") { return self.unpack(element, f"rl_vec_pop({receiver})"); }
        if name.equals("get") { return self.unpack(element, f"rl_vec_get({receiver}, {values.get(0).code})"); }
        if name.equals("push") { return self.value("Void", f"rl_vec_push({receiver}, {self.slot(values.get(0))})"); }
        if name.equals("set") { return self.value("Void", f"rl_vec_set({receiver}, {values.get(0).code}, {self.slot(values.get(1))})"); }
        return self.value("Void", f"rl_vec_resize({receiver}, {values.get(0).code})");
    }
    pub def builtin_module(name: String) -> Bool {
        return name.equals("std.string_builder") || name.equals("std.process") || name.equals("std.fs") || name.equals("std.path") || name.equals("std.io");
    }
    pub def has_module(name: String, source: String) -> Bool {
        for declaration in self.program.declarations { if declaration.kind == 1 && declaration.value.equals(name) && declaration.token.text.equals("import") && declaration.token.source.equals(source) { return true; } }
        return false;
    }
    pub def stdlib_module(name: String) -> String { return native_stdlib_module(name); }
    pub def stdlib_call(token: Token, args: Vec<i32>, resolved: Bool) -> Value {
        let module = self.stdlib_module(token.text); let name = token.text;
        if !resolved && !self.has_module(module, token.source) { self.fail(token, f"{name} requires import {module}"); return self.invalid(); }
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
                expected.push("RawPtr"); helper = f"rl_file_{name.substring(3, (name.len() as i32) - 3)}";
                if name.equals("fs_close") { result = "Void"; }
                if name.equals("fs_read_all") || name.equals("fs_read_line") { result = "String"; }
                if name.equals("fs_write_str") { expected.push("String"); helper = "rl_file_write"; }
                if name.equals("fs_seek") { expected.push("i64"); expected.push("i32"); }
                if name.equals("fs_tell") { result = "i64"; }
            }
        }
        if module.equals("std.path") {
            expected.push("String"); result = "String"; helper = f"rl_{name}";
            if name.equals("path_join") { expected.push("String"); }
            if name.equals("path_exists") || name.equals("path_is_dir") || name.equals("path_is_file") { result = "Bool"; }
        }
        if expected.len() != args.len() { self.fail(token, "wrong argument count"); return self.invalid(); }
        let code = StringBuilder.new(); code.append(f"{helper}("); for i in 0..<args.len() {
            let value = self.expression_as(args.get(i), expected.get(i), token);
            if i > 0 { code.append(", "); }
            if module.equals("std.io") && !expected.get(i).equals("String") { code.append(f"rl_integer_string({value.code})"); }
            else { code.append(value.code); }
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
        if helper.equals("length") { return self.value("i64", f"{receiver}->length"); }
        var code = f"{helper}({receiver}";
        if count == 1 { let value = self.expression_as(args.get(0), expected, token); code = f"{code}, {value.code}"; }
        return self.value(result, f"{code})");
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
        return self.value(owner, f"rl_dict_new({values.get(0).code}, {values.get(1).code}, {reference}, {string})");
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
        let values = Vec<Value>.new(); for i in 0..<args.len() { values.push(self.expression_as(args.get(i), expected.get(i), token)); }
        if name.equals("len") { return self.value("i64", f"{receiver}->keys->length"); }
        if name.equals("clear") { return self.value("Void", f"rl_dict_clear({receiver})"); }
        if name.equals("keys") { return self.value(f"Vec<{key}>", f"rl_dict_snapshot({receiver}->keys)"); }
        if name.equals("values") { return self.value(f"Vec<{value}>", f"rl_dict_snapshot({receiver}->values)"); }
        if name.equals("value_at") { return self.unpack(value, f"rl_dict_value_at({receiver}, {values.get(0).code})"); }
        if name.equals("set_value_at") { return self.value("Void", f"rl_dict_set_at({receiver}, {values.get(0).code}, {self.slot(values.get(1))})"); }
        let first = f"{receiver}, {self.slot(values.get(0))}";
        if name.equals("set") { return self.value("Void", f"rl_dict_set({first}, {self.slot(values.get(1))})"); }
        if name.equals("entry_index") { return self.value("i64", f"rl_dict_entry({first}, {self.slot(values.get(1))})"); }
        if name.equals("contains") { return self.value("Bool", f"rl_dict_find({first}) >= 0"); }
        return self.value(f"{value}?", f"rl_dict_{name}({first})");
    }
    pub def leaves(body: Vec<i32>) -> Bool {
        for id in body {
            let node = self.program.statements.get(id);
            if node.kind == 1 || node.kind == 10 || node.kind == 11 { return true; }
            if (node.kind == 7 || node.kind == 12) && self.leaves(node.body) { return true; }
            if (node.kind == 4 || node.kind == 9) && self.leaves(node.body) && self.leaves(node.alternative) { return true; }
        }
        return false;
    }
    pub def guard_statement(statement: Statement) -> Bool {
        let value = self.expression(statement.expr);
        let binding = statement.kind == 14;
        if binding {
            if !self.is_optional(value.type_name) { self.fail(statement.token, "guard let requires an optional value"); return false; }
            self.output.append_line(f"if ({value.code} == NULL) {{");
        } else {
            self.require_type(statement.token, value.type_name, "Bool");
            self.output.append_line(f"if (!{value.code}) {{");
        }
        self.block(statement.alternative, true);
        if !self.leaves(statement.alternative) { self.fail(statement.token, "guard else must exit the current path"); }
        self.output.append_line("}");
        if binding {
            let inner = self.optional_inner(value.type_name);
            let unwrapped = self.unpack(inner, f"{value.code}->value");
            let scope = self.scopes.get(self.scopes.len() - 1);
            if scope.contains(statement.token.text) { self.fail(statement.token, "duplicate guard binding"); }
            scope.set(statement.token.text, Binding { code: unwrapped.code, type_name: inner, mutable: false });
        }
        return false;
    }
    pub def if_let(statement: Statement) -> Bool {
        let optional = self.expression(statement.expr);
        if !self.is_optional(optional.type_name) { self.fail(statement.token, "if let requires an optional value"); return false; }
        self.output.append_line(f"if ({optional.code} != NULL) {{");
        let inner = self.optional_inner(optional.type_name);
        let value = self.unpack(inner, f"{optional.code}->value");
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
        let range = object.type_name.equals("IndexRange");
        if !range && !self.is_vector(object.type_name) { self.fail(statement.token, "for-loop iterable must be a Vec or IndexRange"); return false; }
        var element = "i32"; let index = self.fresh(); var value = self.invalid();
        if range {
            self.output.append_line(f"for (int64_t {index} = {object.code}->start; {index} < (int64_t){object.code}->end + {object.code}->inclusive; ++{index}) {{");
            value = self.value("i32", f"(int32_t){index}");
        } else {
            element = self.vector_element(object.type_name);
            self.output.append_line(f"for (int32_t {index} = 0; {index} < {object.code}->length; ++{index}) {{");
            value = self.unpack(element, f"rl_vec_get({object.code}, {index})");
        }
        let scope = Dict<String, Binding>.with_capacity(8, 1);
        scope.set(statement.token.text, Binding { code: value.code, type_name: element, mutable: false }); self.scopes.push(scope);
        self.loop_depth = self.loop_depth + 1; self.block(statement.body, false); self.loop_depth = self.loop_depth - 1;
        self.scopes.pop(); self.output.append_line("}"); return false;
    }
}
