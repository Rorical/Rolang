import "lexer.rl"

// Indices keep the syntax tree independent of recursive object layouts.
pub struct Variant { pub var token: Token; pub var payload: Vec<Parameter>; }
// The compiler now uses its own recursive enum and pattern matching support.
// Only literal patterns carry an expression-array index.
pub enum Pattern {
    case wildcard(Token);
    case binding(Token, Bool);
    case literal(Token, i32);
    case variant(Token, Vec<Pattern>);

    pub def position() -> Token {
        return switch self {
            case .wildcard(let token): token;
            case .binding(let token, _): token;
            case .literal(let token, _): token;
            case .variant(let token, _): token;
        };
    }
}
pub struct SwitchArm {
    pub var token: Token;
    pub var pattern: Pattern;
    pub var guard_expr: i32;
    pub var value: i32;
    pub var body: Vec<i32>;
}
pub struct Expression {
    pub var token: Token;
    pub var kind: i32; // 1 integer, 2 boolean, 3 name, 4 binary, 5 unary, 6 named call
    // 7 string, 8 nil, 9 member, 10 struct literal, 11 cast, 12 index,
    // 13 generic type reference, 14 call through an expression, 15 index range,
    // 16 switch expression, 17 inferred enum case
    pub var left: i32;
    pub var right: i32;
    pub var args: Vec<i32>;
    pub var labels: Vec<String>;
    pub var arms: Vec<SwitchArm>;
    pub var type_name: String;
}
pub struct Statement {
    pub var token: Token;
    pub var kind: i32; // 1 return, 2 let/var, 3 name assignment, 4 if, 5 while, 6 expression
    // 7 block, 8 for, 9 if-let, 10 break, 11 continue, 12 unsafe,
    // 13 member/index assignment, 14 guard-let, 15 boolean guard, 16 switch
    pub var expr: i32;
    pub var target: i32;
    pub var annotation: String;
    pub var mutable: Bool;
    pub var body: Vec<i32>;
    pub var alternative: Vec<i32>;
    pub var arms: Vec<SwitchArm>;
}
pub struct Parameter { pub var token: Token; pub var type_name: String; }
pub struct Function {
    pub var token: Token;
    pub var params: Vec<Parameter>;
    pub var owner: String;
    pub var modifiers: String;
    pub var generics: Vec<String>;
    pub var return_type: String;
    pub var body: Vec<i32>;
}
pub struct Program {
    pub var functions: Vec<Function>;
    pub var declarations: Vec<Declaration>;
    pub var expressions: Vec<Expression>;
    pub var statements: Vec<Statement>;
}

// Declaration kinds: 1 import, 2 struct, 3 typealias, 4 enum. Methods live in Program.functions.
pub struct Field {
    pub var token: Token;
    pub var type_name: String;
    pub var mutable: Bool;
    pub var modifiers: String;
    pub var expr: i32;
}
pub struct Declaration {
    pub var token: Token;
    pub var kind: i32;
    pub var value: String;
    pub var modifiers: String;
    pub var generics: Vec<String>;
    pub var fields: Vec<Field>;
    pub var variants: Vec<Variant>;
    pub var methods: Vec<i32>;
}
