import "lexer.rl"
import "ast.rl"
import std.string_builder

// Dump flat nodes so even long expression chains do not recurse while printing.
pub struct AstJson {
    pub var out: StringBuilder;
    pub static def new() -> AstJson { return AstJson { out: StringBuilder.new() }; }
    pub def quoted(text: String) -> Void {
        self.out.append_byte(34 as u8);
        var i = 0;
        while i < (text.len() as i32) {
            let byte = text.byte_at(i);
            if byte == 34 || byte == 92 { self.out.append_byte(92 as u8); self.out.append_byte(byte as u8); }
            else {
                if byte < 32 {
                    self.out.append("\\u00");
                    self.out.append_byte("0123456789abcdef".byte_at(byte / 16) as u8);
                    self.out.append_byte("0123456789abcdef".byte_at(byte % 16) as u8);
                } else { self.out.append_byte(byte as u8); }
            }
            i = i + 1;
        }
        self.out.append_byte(34 as u8);
    }
    pub def key(name: String) -> Void { self.quoted(name); self.out.append(":"); }
    pub def number(value: i32) -> Void { self.out.append(value.to_string()); }
    pub def boolean(value: Bool) -> Void { if value { self.out.append("true"); } else { self.out.append("false"); } }
    pub def token(value: Token) -> Void {
        self.out.append("{"); self.key("text"); self.quoted(value.text);
        self.out.append(","); self.key("line"); self.number(value.line);
        self.out.append(","); self.key("column"); self.number(value.column); self.out.append("}");
    }
    pub def indices(values: Vec<i32>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.number(value); }
        self.out.append("]");
    }
    pub def strings(values: Vec<String>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.quoted(value); }
        self.out.append("]");
    }
    pub def parameter(value: Parameter) -> Void {
        self.out.append("{");
        self.key("token"); self.token(value.token);
        self.out.append(",");
        self.key("type_name"); self.quoted(value.type_name);
        self.out.append("}");
    }
    pub def parameters(values: Vec<Parameter>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.parameter(value); }
        self.out.append("]");
    }
    pub def field(value: Field) -> Void {
        self.out.append("{");
        self.key("token"); self.token(value.token);
        self.out.append(",");
        self.key("type_name"); self.quoted(value.type_name);
        self.out.append(",");
        self.key("mutable"); self.boolean(value.mutable);
        self.out.append(",");
        self.key("modifiers"); self.quoted(value.modifiers);
        self.out.append(",");
        self.key("expr"); self.number(value.expr);
        self.out.append("}");
    }
    pub def fields(values: Vec<Field>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.field(value); }
        self.out.append("]");
    }
    pub def expression(value: Expression) -> Void {
        self.out.append("{");
        self.key("token"); self.token(value.token);
        self.out.append(",");
        self.key("kind"); self.number(value.kind);
        self.out.append(",");
        self.key("left"); self.number(value.left);
        self.out.append(",");
        self.key("right"); self.number(value.right);
        self.out.append(",");
        self.key("args"); self.indices(value.args);
        self.out.append(",");
        self.key("labels"); self.strings(value.labels);
        self.out.append(",");
        self.key("type_name"); self.quoted(value.type_name);
        self.out.append("}");
    }
    pub def expressions(values: Vec<Expression>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.expression(value); }
        self.out.append("]");
    }
    pub def statement(value: Statement) -> Void {
        self.out.append("{");
        self.key("token"); self.token(value.token);
        self.out.append(",");
        self.key("kind"); self.number(value.kind);
        self.out.append(",");
        self.key("expr"); self.number(value.expr);
        self.out.append(",");
        self.key("target"); self.number(value.target);
        self.out.append(",");
        self.key("annotation"); self.quoted(value.annotation);
        self.out.append(",");
        self.key("mutable"); self.boolean(value.mutable);
        self.out.append(",");
        self.key("body"); self.indices(value.body);
        self.out.append(",");
        self.key("alternative"); self.indices(value.alternative);
        self.out.append("}");
    }
    pub def statements(values: Vec<Statement>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.statement(value); }
        self.out.append("]");
    }
    pub def function(value: Function) -> Void {
        self.out.append("{");
        self.key("token"); self.token(value.token);
        self.out.append(",");
        self.key("params"); self.parameters(value.params);
        self.out.append(",");
        self.key("owner"); self.quoted(value.owner);
        self.out.append(",");
        self.key("modifiers"); self.quoted(value.modifiers);
        self.out.append(",");
        self.key("generics"); self.strings(value.generics);
        self.out.append(",");
        self.key("return_type"); self.quoted(value.return_type);
        self.out.append(",");
        self.key("body"); self.indices(value.body);
        self.out.append("}");
    }
    pub def functions(values: Vec<Function>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.function(value); }
        self.out.append("]");
    }
    pub def declaration(value: Declaration) -> Void {
        self.out.append("{");
        self.key("token"); self.token(value.token);
        self.out.append(",");
        self.key("kind"); self.number(value.kind);
        self.out.append(",");
        self.key("value"); self.quoted(value.value);
        self.out.append(",");
        self.key("modifiers"); self.quoted(value.modifiers);
        self.out.append(",");
        self.key("generics"); self.strings(value.generics);
        self.out.append(",");
        self.key("fields"); self.fields(value.fields);
        self.out.append(",");
        self.key("methods"); self.indices(value.methods);
        self.out.append("}");
    }
    pub def declarations(values: Vec<Declaration>) -> Void {
        self.out.append("["); var first = true;
        for value in values { if !first { self.out.append(","); } first = false; self.declaration(value); }
        self.out.append("]");
    }
    pub def program(value: Program) -> Void {
        self.out.append("{");
        self.key("functions"); self.functions(value.functions);
        self.out.append(",");
        self.key("declarations"); self.declarations(value.declarations);
        self.out.append(",");
        self.key("expressions"); self.expressions(value.expressions);
        self.out.append(",");
        self.key("statements"); self.statements(value.statements);
        self.out.append("}");
    }
}
