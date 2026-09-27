import "lexer.rl"
import "parser.rl"
import "backend.rl"
import "ast_json.rl"
import "modules.rl"
import std.fs
import std.process
import std.io
import std.path

// Emit only after lexing, parsing, and type checking have all succeeded.
// The caller chooses whether/how to invoke a C compiler on the result.
def main() -> i32 {
    if argc() != 3 {
        println("usage: rolang-stage0 <input.rl> <output.c> | --parse <input.rl>");
        return 2;
    }
    let parse_only = argv(1).equals("--parse");
    var input = argv(1);
    if parse_only { input = argv(2); }
    let output = argv(2);
    if !parse_only && path_resolve(input).equals(path_resolve(output)) { println("input and output paths must differ"); return 2; }
    let file = fs_open(input, 0);
    unsafe { if (file as i64) == 0 { println(f"cannot open input: {input}"); return 2; } }
    let source = fs_read_all(file);
    fs_close(file);
    let scanned = lex(source);
    if !scanned.error.is_empty() { println(f"{input}:{scanned.error}"); return 1; }
    let parser = Parser.new(scanned.tokens);
    parser.parse();
    if !parser.error.is_empty() { println(f"{input}:{parser.error}"); return 1; }
    if parse_only { let json = AstJson.new(); json.program(parser.program); println(json.out.to_string()); return 0; }
    let modules = Modules.new();
    modules.load(input, parser.program, 0);
    if !modules.error.is_empty() { println(modules.error); return 1; }
    if modules.paths.contains(path_resolve(output)) { println("input and output paths must differ"); return 2; }
    modules.validate();
    if !modules.error.is_empty() { println(modules.error); return 1; }
    let backend = Backend.new(modules.program);
    backend.generate();
    if !backend.error.is_empty() {
        if backend.error.equals("1:1: missing main function") { println(f"{input}:{backend.error}"); }
        else { println(backend.error); }
        return 1;
    }
    let text = backend.output.to_string();
    let destination = fs_open(output, 1);
    unsafe { if (destination as i64) == 0 { println(f"cannot open output: {output}"); return 2; } }
    let count = fs_write_str(destination, text);
    let flushed = fs_flush(destination);
    fs_close(destination);
    if (count as i64) != text.len() || flushed != 0 { println(f"failed to write output: {output}"); return 2; }
    return 0;
}
