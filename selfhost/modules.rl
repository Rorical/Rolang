import "lexer.rl"
import "ast.rl"
import "parser.rl"
import std.fs
import std.path

pub struct ModuleSymbol {
    pub var name: String;
    pub var source: String;
    pub var qualified: String;
    pub var public: Bool;
    pub var kind: i32; // 1 function, 2 struct
}

// Resolve source names before handing the merged syntax tree to the backend.
// Keep each module's syntax indices local until its dependencies are loaded.
pub struct Modules {
    pub var program: Program;
    pub var paths: Dict<String, i32>;
    pub var error: String;
    pub var root: String;
    pub var imports: Dict<String, Vec<String>>;
    pub var symbols: Dict<String, ModuleSymbol>;

    pub static def new() -> Modules {
        return Modules { program: Program { functions: Vec<Function>.new(), declarations: Vec<Declaration>.new(), expressions: Vec<Expression>.new(), statements: Vec<Statement>.new() }, paths: Dict<String, i32>.with_capacity(16, 1), error: "", root: "", imports: Dict<String, Vec<String>>.with_capacity(16, 1), symbols: Dict<String, ModuleSymbol>.with_capacity(64, 1) };
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
        self.paths.set(canonical, self.paths.len() as i32);
        let imports = Vec<String>.new(); self.imports.set(canonical, imports);
        // Tokens are shared by AST nodes and can be tagged after parsing.
        for expr in part.expressions { expr.token.source = canonical; }
        for stmt in part.statements { stmt.token.source = canonical; }
        for fn in part.functions { fn.token.source = canonical; for param in fn.params { param.token.source = canonical; } }
        for declaration in part.declarations {
            declaration.token.source = canonical;
            for field in declaration.fields { field.token.source = canonical; }
            if declaration.kind == 1 && declaration.modifiers.contains("pub ") { self.error = location(declaration.token, "public re-exports unsupported"); return; }
            if declaration.kind != 1 || !declaration.value.starts_with("\"") { continue; }
            if !declaration.token.text.equals("import") { self.error = location(declaration.token, "local import aliases unsupported"); return; }
            let name = declaration.value.substring(1, (declaration.value.len() as i32) - 2);
            if name.is_empty() || name.contains("\\") || name.contains("\0") { self.error = location(declaration.token, "empty or escaped import paths unsupported"); return; }
            let dependency = path_resolve(path_join(path_dirname(canonical), name));
            imports.push(dependency);
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
    pub def register(token: Token, modifiers: String, kind: i32) -> Void {
        let key = token.source + "#" + token.text;
        if self.symbols.contains(key) { self.error = location(token, "duplicate declaration"); return; }
        if kind == 2 && (primitive_type(token.text) || token.text.equals("String") || token.text.equals("Vec") || token.text.equals("Dict") || token.text.equals("StringBuilder")) {
            self.error = location(token, "duplicate or reserved struct name"); return;
        }
        var module = 0;
        if let id = self.paths.get(token.source) { module = id; }
        var qualified = "$m" + module.to_string() + "$" + token.text;
        if kind == 1 && token.text.equals("main") {
            if !token.source.equals(self.root) { self.error = location(token, "imported main unsupported"); return; }
            qualified = "main";
        }
        self.symbols.set(key, ModuleSymbol { name: token.text, source: token.source, qualified: qualified, public: modifiers.contains("pub "), kind: kind });
    }
    pub def resolve(name: String, token: Token, kind: i32) -> String {
        if let symbol = self.symbols.get(token.source + "#" + name) {
            if symbol.kind == kind { return symbol.qualified; }
            return "";
        }
        var found = "";
        if let imports = self.imports.get(token.source) {
            for source in imports {
                if let symbol = self.symbols.get(source + "#" + name) {
                    if symbol.public && symbol.kind == kind {
                        if !found.is_empty() && !found.equals(symbol.qualified) { return "$ambiguous$" + name; }
                        found = symbol.qualified;
                    }
                }
            }
        }
        return found;
    }
    pub def type_name(name: String, token: Token) -> String {
        // Replace identifiers inside nested Vec/Dict/optional type spellings.
        var result = ""; var i = 0;
        while i < (name.len() as i32) {
            let start = i;
            if identifier_start(name.byte_at(i)) {
                i = i + 1;
                while i < (name.len() as i32) && (identifier_start(name.byte_at(i)) || decimal_digit(name.byte_at(i))) { i = i + 1; }
                let word = name.substring(start, i - start);
                let resolved = self.resolve(word, token, 2);
                if resolved.starts_with("$ambiguous$") { self.error = location(token, "ambiguous imported type '" + word + "'"); }
                if resolved.is_empty() { result = result + word; } else { result = result + resolved; }
            } else { result = result + name.substring(i, 1); i = i + 1; }
        }
        return result;
    }
    pub def validate() -> Void {
        if self.paths.len() <= 1 { return; }
        for declaration in self.program.declarations { if declaration.kind == 2 { self.register(declaration.token, declaration.modifiers, 2); } }
        for fn in self.program.functions { if fn.owner.is_empty() { self.register(fn.token, fn.modifiers, 1); } }
        if !self.error.is_empty() { return; }
        for expr in self.program.expressions {
            expr.type_name = self.type_name(expr.type_name, expr.token);
            if expr.kind == 6 {
                expr.type_name = self.resolve(expr.token.text, expr.token, 1);
                if expr.type_name.is_empty() { expr.type_name = "$missing$" + expr.token.text; }
            }
            if expr.kind == 3 { expr.type_name = self.resolve(expr.token.text, expr.token, 2); }
        }
        for stmt in self.program.statements { stmt.annotation = self.type_name(stmt.annotation, stmt.token); }
        for fn in self.program.functions {
            fn.return_type = self.type_name(fn.return_type, fn.token);
            for param in fn.params { param.type_name = self.type_name(param.type_name, param.token); }
            if !fn.owner.is_empty() { fn.owner = self.type_name(fn.owner, fn.token); }
            else { fn.token.text = self.resolve(fn.token.text, fn.token, 1); }
        }
        for declaration in self.program.declarations {
            for field in declaration.fields { field.type_name = self.type_name(field.type_name, field.token); }
            if declaration.kind == 2 { declaration.token.text = self.resolve(declaration.token.text, declaration.token, 2); }
        }
    }
}
