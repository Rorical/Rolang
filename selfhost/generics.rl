import "ast.rl"
import "lexer.rl"
import std.string_builder

// Canonical type spellings remain module-qualified throughout specialization.
pub def type_base(name: String) -> String {
    let opening = name.find_char(60, 0);
    if opening < 0 { return name; }
    return name[0..<opening];
}
pub def type_arguments(name: String) -> Vec<String> {
    let result = Vec<String>.new(); let opening = name.find_char(60, 0);
    if opening < 0 || !name.ends_with(">") { return result; }
    var start = opening + 1; var depth = 0;
    for i in start..<(name.len() as i32) - 1 {
        let c = name.byte_at(i);
        if c == 60 || c == 40 || c == 91 { depth = depth + 1; }
        if c == 62 || c == 41 || c == 93 { depth = depth - 1; }
        if c == 44 && depth == 0 { result.push(name[start..<i]); start = i + 1; }
    }
    result.push(name[start..<(name.len() as i32) - 1]); return result;
}
pub def type_apply(base: String, args: Vec<String>) -> String {
    let text = StringBuilder.new(); text.append(base); text.append("<");
    for i in 0..<args.len() { if i > 0 { text.append(","); } text.append(args[i]); }
    text.append(">"); return text.to_string();
}
pub def type_substitute(name: String, bindings: Dict<String, String>) -> String {
    let text = StringBuilder.new(); var i = 0;
    while i < (name.len() as i32) {
        let start = i; let c = name.byte_at(i);
        if identifier_start(c) || c == 36 {
            i = i + 1;
            while i < (name.len() as i32) && (identifier_start(name.byte_at(i)) || decimal_digit(name.byte_at(i)) || name.byte_at(i) == 36 || name.byte_at(i) == 46) { i = i + 1; }
            let word = name[start..<i];
            if let replacement = bindings.get(word) { text.append(replacement); } else { text.append(word); }
        } else { text.append(name[i..<i + 1]); i = i + 1; }
    }
    return text.to_string();
}
pub def type_unbound(name: String, parameters: Vec<String>) -> Bool {
    let bindings = Dict<String, String>.with_capacity(8, 1);
    for param in parameters { bindings.set(param, "$unbound$"); }
    return type_substitute(name, bindings).contains("$unbound$");
}
pub def copy_items<T>(items: Vec<T>) -> Vec<T> {
    let result = Vec<T>.new(); for item in items { result.push(item); } return result;
}
pub def copy_token(token: Token, text: String) -> Token {
    return Token { text: text, kind: token.kind, line: token.line, column: token.column, source: token.source };
}

// A fresh tree per instance keeps generic bodies immutable. Cloning also
// remaps literal patterns, nested switch arms, locals, casts and type receivers.
pub struct Specializer {
    pub var program: Program;
    pub var bindings: Dict<String, String>;
    pub var expressions: Dict<i32, i32>;
    pub var statements: Dict<i32, i32>;
    pub static def new(program: Program, bindings: Dict<String, String>) -> Specializer {
        return Specializer { program: program, bindings: bindings,
            expressions: Dict<i32, i32>.with_capacity(32, 0), statements: Dict<i32, i32>.with_capacity(32, 0) };
    }
    pub def pattern(value: Pattern) -> Pattern {
        switch value {
            case .literal(let token, let index): return Pattern.literal(token, self.expression(index));
            case .variant(let token, let children):
                let result = Vec<Pattern>.new(); for child in children { result.push(self.pattern(child)); }
                return Pattern.variant(token, result);
            default: return value;
        }
    }
    pub def arms(values: Vec<SwitchArm>) -> Vec<SwitchArm> {
        let result = Vec<SwitchArm>.new();
        for arm in values {
            result.push(SwitchArm { token: arm.token, pattern: self.pattern(arm.pattern),
                guard_expr: self.expression(arm.guard_expr), value: self.expression(arm.value), body: self.body(arm.body) });
        }
        return result;
    }
    pub def expression(index: i32) -> i32 {
        if index < 0 { return index; }
        if let existing = self.expressions.get(index) { return existing; }
        let node = self.program.expressions[index]; let args = Vec<i32>.new();
        for arg in node.args { args.push(self.expression(arg)); }
        var type_name = type_substitute(node.type_name, self.bindings);
        if node.kind == 3 { if let replacement = self.bindings.get(node.token.text) { type_name = replacement; } }
        let copy = Expression { token: node.token, kind: node.kind, left: self.expression(node.left), right: self.expression(node.right),
            args: args, labels: copy_items(node.labels), arms: self.arms(node.arms), type_name: type_name };
        let result = self.program.expressions.len(); self.program.expressions.push(copy); self.expressions.set(index, result); return result;
    }
    pub def statement(index: i32) -> i32 {
        if let existing = self.statements.get(index) { return existing; }
        let node = self.program.statements[index];
        let copy = Statement { token: node.token, kind: node.kind, expr: self.expression(node.expr), target: self.expression(node.target),
            annotation: type_substitute(node.annotation, self.bindings), mutable: node.mutable,
            body: self.body(node.body), alternative: self.body(node.alternative), arms: self.arms(node.arms) };
        let result = self.program.statements.len(); self.program.statements.push(copy); self.statements.set(index, result); return result;
    }
    pub def body(values: Vec<i32>) -> Vec<i32> {
        let result = Vec<i32>.new(); for index in values { result.push(self.statement(index)); } return result;
    }
    pub def parameters(values: Vec<Parameter>) -> Vec<Parameter> {
        let result = Vec<Parameter>.new();
        for param in values { result.push(Parameter { token: param.token, type_name: type_substitute(param.type_name, self.bindings) }); }
        return result;
    }
    pub def function(value: Function, name: String, owner: String, generics: Vec<String>) -> Function {
        return Function { token: copy_token(value.token, name), params: self.parameters(value.params), owner: owner,
            modifiers: value.modifiers, generics: generics, return_type: type_substitute(value.return_type, self.bindings), body: self.body(value.body) };
    }
    pub def declaration(value: Declaration, name: String) -> Declaration {
        let fields = Vec<Field>.new(); let variants = Vec<Variant>.new();
        for field in value.fields { fields.push(Field { token: field.token, type_name: type_substitute(field.type_name, self.bindings), mutable: field.mutable, modifiers: field.modifiers, expr: self.expression(field.expr) }); }
        for variant in value.variants { variants.push(Variant { token: variant.token, payload: self.parameters(variant.payload) }); }
        return Declaration { token: copy_token(value.token, name), kind: value.kind, value: value.value, modifiers: value.modifiers,
            generics: Vec<String>.new(), fields: fields, variants: variants, methods: Vec<i32>.new() };
    }
}
