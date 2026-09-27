import "lexer.rl"
import "ast.rl"
import "parser.rl"
import "os_codegen.rl"
import std.fs
import std.path

pub struct ModuleImport {
    pub var target: String;
    pub var alias: String;
    pub var public: Bool;
    pub var token: Token;
}

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
    pub var imports: Dict<String, Vec<ModuleImport>>;
    pub var symbols: Dict<String, ModuleSymbol>;

    pub static def new() -> Modules {
        return Modules { program: Program { functions: Vec<Function>.new(), declarations: Vec<Declaration>.new(), expressions: Vec<Expression>.new(), statements: Vec<Statement>.new() }, paths: Dict<String, i32>.with_capacity(16, 1), error: "", root: "", imports: Dict<String, Vec<ModuleImport>>.with_capacity(16, 1), symbols: Dict<String, ModuleSymbol>.with_capacity(64, 1) };
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
        let imports = Vec<ModuleImport>.new(); self.imports.set(canonical, imports);
        // Tokens are shared by AST nodes and can be tagged after parsing.
        for expr in part.expressions { expr.token.source = canonical; }
        for stmt in part.statements { stmt.token.source = canonical; }
        for fn in part.functions { fn.token.source = canonical; for param in fn.params { param.token.source = canonical; } }
        for declaration in part.declarations {
            declaration.token.source = canonical;
            for field in declaration.fields { field.token.source = canonical; }
            if declaration.kind != 1 { continue; }
            var alias = declaration.token.text;
            if alias.equals("import") { alias = ""; }
            if primitive_type(alias) || alias.equals("Self") || alias.equals("Iterator") || alias.equals("Iterable") { self.error = location(declaration.token, "import alias shadows a built-in type"); return; }
            var dependency = declaration.value;
            let local = dependency.starts_with("\"");
            if local {
                let name = dependency.substring(1, (dependency.len() as i32) - 2);
                if name.is_empty() || name.contains("\\") || name.contains("\0") { self.error = location(declaration.token, "empty or escaped import paths unsupported"); return; }
                dependency = path_resolve(path_join(path_dirname(canonical), name));
            }
            for previous in imports {
                if !alias.is_empty() && previous.alias.equals(alias) && !previous.target.equals(dependency) { self.error = location(declaration.token, "duplicate import alias"); return; }
            }
            imports.push(ModuleImport { target: dependency, alias: alias, public: declaration.modifiers.contains("pub "), token: declaration.token });
            if !local { continue; }
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
    pub def import_name(edge: ModuleImport, name: String) -> String {
        if edge.alias.is_empty() { return name; }
        let prefix = edge.alias + ".";
        if name.starts_with(prefix) { return name.substring(prefix.len() as i32, (name.len() - prefix.len()) as i32); }
        return "";
    }
    pub def combine(found: String, candidate: String, name: String) -> String {
        if candidate.is_empty() { return found; }
        if found.is_empty() || found.equals(candidate) { return candidate; }
        return "$ambiguous$" + name;
    }
    pub def exported(source: String, name: String, kind: i32, seen: Dict<String, Bool>, depth: i32, token: Token) -> String {
        let key = source + "#" + name;
        if seen.contains(key) { return ""; }
        seen.set(key, true);
        if depth > 128 { self.error = location(token, "re-export nesting limit exceeded"); return ""; }
        if source.starts_with("std.") {
            if kind == 1 && native_stdlib_module(name).equals(source) { return "$std$" + name; }
            if kind == 2 && source.equals("std.string_builder") && name.equals("StringBuilder") { return "StringBuilder"; }
            return "";
        }
        if let symbol = self.symbols.get(key) {
            if symbol.public && symbol.kind == kind { return symbol.qualified; }
            return "";
        }
        // A locally declared root name hides an imported namespace of that name.
        let dot = name.find_char(46, 0);
        if dot >= 0 && self.symbols.contains(source + "#" + name.substring(0, dot)) { return ""; }
        var found = "";
        if let edges = self.imports.get(source) {
            for edge in edges {
                if !edge.public { continue; }
                let imported = self.import_name(edge, name);
                if imported.is_empty() { continue; }
                found = self.combine(found, self.exported(edge.target, imported, kind, seen, depth + 1, token), name);
            }
        }
        return found;
    }
    pub def resolve(name: String, token: Token, kind: i32) -> String {
        if name.is_empty() { return ""; }
        if let symbol = self.symbols.get(token.source + "#" + name) {
            if symbol.kind == kind { return symbol.qualified; }
            return "";
        }
        let dot = name.find_char(46, 0);
        if dot >= 0 && self.symbols.contains(token.source + "#" + name.substring(0, dot)) { return ""; }
        var found = "";
        let seen = Dict<String, Bool>.with_capacity(16, 1);
        if let edges = self.imports.get(token.source) {
            for edge in edges {
                let imported = self.import_name(edge, name);
                if imported.is_empty() { continue; }
                found = self.combine(found, self.exported(edge.target, imported, kind, seen, 1, token), name);
            }
        }
        return found;
    }
    pub def reference_name(id: i32) -> String {
        var current = id; var suffix = "";
        while current >= 0 {
            let node = self.program.expressions.get(current);
            if node.kind == 3 { return node.token.text + suffix; }
            if node.kind != 9 { return ""; }
            suffix = "." + node.token.text + suffix; current = node.left;
        }
        return "";
    }
    pub def type_name(name: String, token: Token) -> String {
        // Replace identifiers inside nested Vec/Dict/optional type spellings.
        var result = ""; var i = 0;
        while i < (name.len() as i32) {
            let start = i;
            if identifier_start(name.byte_at(i)) {
                i = i + 1;
                while i < (name.len() as i32) && (identifier_start(name.byte_at(i)) || decimal_digit(name.byte_at(i)) || name.byte_at(i) == 46) { i = i + 1; }
                let word = name.substring(start, i - start);
                let resolved = self.resolve(word, token, 2);
                if resolved.starts_with("$ambiguous$") { self.error = location(token, "ambiguous imported type '" + word + "'"); }
                if resolved.is_empty() { result = result + word; } else { result = result + resolved; }
            } else { result = result + name.substring(i, 1); i = i + 1; }
        }
        return result;
    }
    pub def validate() -> Void {
        if self.paths.len() <= 1 {
            var needed = false;
            if let edges = self.imports.get(self.root) { for edge in edges { if !edge.alias.is_empty() || edge.public { needed = true; } } }
            if !needed { return; }
        }
        for declaration in self.program.declarations { if declaration.kind == 2 { self.register(declaration.token, declaration.modifiers, 2); } }
        for fn in self.program.functions { if fn.owner.is_empty() { self.register(fn.token, fn.modifiers, 1); } }
        if !self.error.is_empty() { return; }
        for source in self.imports.keys() {
            if let edges = self.imports.get(source) {
                for edge in edges {
                    if !edge.alias.is_empty() && self.symbols.contains(source + "#" + edge.alias) { self.error = location(edge.token, "import alias is already defined in this scope"); return; }
                }
            }
        }
        var expression_id = 0;
        for expr in self.program.expressions {
            expr.type_name = self.type_name(expr.type_name, expr.token);
            if expr.kind == 6 {
                expr.type_name = self.resolve(expr.token.text, expr.token, 1);
                if expr.type_name.is_empty() { expr.type_name = "$missing$" + expr.token.text; }
            }
            if expr.kind == 3 || expr.kind == 9 { expr.type_name = self.resolve(self.reference_name(expression_id), expr.token, 2); }
            if expr.kind == 14 { expr.type_name = self.resolve(self.reference_name(expr.left), expr.token, 1); }
            expression_id = expression_id + 1;
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
