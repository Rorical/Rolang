import "lexer.rl"
import "ast.rl"
import "parser.rl"
import std.fs
import std.path

// Public, globally unique exports are the first native module subset.
// Keep each module's syntax indices local until its dependencies are loaded.
pub struct Modules {
    pub var program: Program;
    pub var paths: Dict<String, Bool>;
    pub var error: String;
    pub var root: String;

    pub static def new() -> Modules {
        return Modules { program: Program { functions: Vec<Function>.new(), declarations: Vec<Declaration>.new(), expressions: Vec<Expression>.new(), statements: Vec<Statement>.new() }, paths: Dict<String, Bool>.with_capacity(16, 1), error: "", root: "" };
    }
    pub def offset(ids: Vec<i32>, amount: i32) -> Void {
        var i = 0;
        while i < ids.len() { ids.set(i, ids.get(i) + amount); i = i + 1; }
    }
    pub def merge(part: Program) -> Void {
        let expressions = self.program.expressions.len();
        let statements = self.program.statements.len();
        let functions = self.program.functions.len();
        for expr in part.expressions {
            if expr.left >= 0 { expr.left = expr.left + expressions; }
            if expr.right >= 0 { expr.right = expr.right + expressions; }
            self.offset(expr.args, expressions); self.program.expressions.push(expr);
        }
        for stmt in part.statements {
            if stmt.expr >= 0 { stmt.expr = stmt.expr + expressions; }
            if stmt.target >= 0 { stmt.target = stmt.target + expressions; }
            self.offset(stmt.body, statements); self.offset(stmt.alternative, statements);
            self.program.statements.push(stmt);
        }
        for fn in part.functions { self.offset(fn.body, statements); self.program.functions.push(fn); }
        for declaration in part.declarations {
            if declaration.kind == 1 && declaration.value.starts_with("\"") { continue; }
            for field in declaration.fields { if field.expr >= 0 { field.expr = field.expr + expressions; } }
            self.offset(declaration.methods, functions); self.program.declarations.push(declaration);
        }
    }
    pub def load(path: String, part: Program, depth: i32) -> Void {
        if !self.error.is_empty() { return; }
        let canonical = path_resolve(path);
        if self.paths.contains(canonical) { return; }
        if depth > 128 { self.error = path + ":1:1: module nesting limit exceeded"; return; }
        if depth == 0 { self.root = canonical; }
        self.paths.set(canonical, true);
        // Tokens are shared by AST nodes and can be tagged after parsing.
        for expr in part.expressions { expr.token.source = canonical; }
        for stmt in part.statements { stmt.token.source = canonical; }
        for fn in part.functions { fn.token.source = canonical; for param in fn.params { param.token.source = canonical; } }
        for declaration in part.declarations {
            declaration.token.source = canonical;
            for field in declaration.fields { field.token.source = canonical; }
            if declaration.kind != 1 || !declaration.value.starts_with("\"") { continue; }
            if !declaration.token.text.equals("import") { self.error = location(declaration.token, "local import aliases unsupported"); return; }
            let name = declaration.value.substring(1, (declaration.value.len() as i32) - 2);
            if name.is_empty() || name.contains("\\") || name.contains("\0") { self.error = location(declaration.token, "empty or escaped import paths unsupported"); return; }
            let dependency = path_resolve(path_join(path_dirname(canonical), name));
            if self.paths.contains(dependency) { continue; }
            let file = fs_open(dependency, 0);
            unsafe { if (file as i64) == 0 { self.error = location(declaration.token, "cannot open import: " + dependency); return; } }
            let source = fs_read_all(file); fs_close(file);
            let scanned = lex(source);
            if !scanned.error.is_empty() { self.error = dependency + ":" + scanned.error; return; }
            for token in scanned.tokens { token.source = dependency; }
            let parser = Parser.new(scanned.tokens); parser.parse();
            if !parser.error.is_empty() { self.error = parser.error; return; }
            self.load(dependency, parser.program, depth + 1);
            if !self.error.is_empty() { return; }
        }
        self.merge(part);
    }
    pub def validate() -> Void {
        if self.paths.len() <= 1 { return; }
        // Reject unsupported visibility rather than exposing private declarations.
        for fn in self.program.functions {
            if fn.owner.is_empty() && fn.token.text.equals("main") {
                if !fn.token.source.equals(self.root) { self.error = location(fn.token, "imported main unsupported"); return; }
            } else {
                if !fn.modifiers.contains("pub ") { self.error = location(fn.token, "native modules require public functions and methods"); return; }
            }
        }
        for declaration in self.program.declarations {
            if declaration.kind != 2 { continue; }
            if !declaration.modifiers.contains("pub ") { self.error = location(declaration.token, "native modules require public structs"); return; }
            for field in declaration.fields {
                if !field.modifiers.contains("pub ") { self.error = location(field.token, "native modules require public fields"); return; }
            }
        }
    }
}
