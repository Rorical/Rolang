import "lexer.rl"

// Indices keep the syntax tree independent of recursive object layouts.
pub struct Expression {
    pub var token: Token;
    pub var kind: i32; // 1 integer, 2 boolean, 3 name, 4 binary, 5 unary, 6 named call
    // 7 string, 8 nil, 9 member, 10 struct literal, 11 cast, 12 index,
    // 13 generic type reference, 14 call through an expression, 15 index range
    pub var left: i32;
    pub var right: i32;
    pub var args: Vec<i32>;
    pub var labels: Vec<String>;
    pub var type_name: String;
}
pub struct Statement {
    pub var token: Token;
    pub var kind: i32; // 1 return, 2 let/var, 3 name assignment, 4 if, 5 while, 6 expression
    // 7 block, 8 for, 9 if-let, 10 break, 11 continue, 12 unsafe,
    // 13 member/index assignment, 14 guard-let, 15 boolean guard
    pub var expr: i32;
    pub var target: i32;
    pub var annotation: String;
    pub var mutable: Bool;
    pub var body: Vec<i32>;
    pub var alternative: Vec<i32>;
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

// Declaration kinds: 1 import, 2 struct, 3 typealias. Methods live in Program.functions.
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
    pub var methods: Vec<i32>;
}
