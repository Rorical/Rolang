import "lexer.rl"

// Indices keep the syntax tree independent of recursive object layouts.
pub struct Expression {
    pub var token: Token;
    pub var kind: i32; // integer, boolean, name, binary, unary, call
    pub var left: i32;
    pub var right: i32;
    pub var args: Vec<i32>;
}
pub struct Statement {
    pub var token: Token;
    pub var kind: i32; // return, let/var, assign, if, while, expression
    pub var expr: i32;
    pub var annotation: String;
    pub var mutable: Bool;
    pub var body: Vec<i32>;
    pub var alternative: Vec<i32>;
}
pub struct Parameter { pub var token: Token; pub var type_name: String; }
pub struct Function {
    pub var token: Token;
    pub var params: Vec<Parameter>;
    pub var return_type: String;
    pub var body: Vec<i32>;
}
pub struct Program {
    pub var functions: Vec<Function>;
    pub var expressions: Vec<Expression>;
    pub var statements: Vec<Statement>;
}
