"""Native Rolang-written bootstrap compiler: differential and diagnostic tests."""
from pathlib import Path
import json
import random
import subprocess

import pytest

from rolang.driver import CompileOptions, OptLevel, compile_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module', params=[OptLevel.O0, OptLevel.O3], ids=['bootstrap-O0', 'bootstrap-O3'])
def bootstrap(request, tmp_path_factory):
    folder = tmp_path_factory.mktemp('bootstrap')
    compiler = folder / 'rolang-stage0'
    result = compile_source(ROOT / 'selfhost' / 'main.rl', CompileOptions(
        opt_level=request.param, output_path=compiler))
    assert result.success, [d.message for d in result.diagnostics.diagnostics]
    return compiler


def emit(bootstrap, tmp_path, source):
    path = tmp_path / 'program.rl'
    output = tmp_path / 'program.c'
    path.write_text(source)
    result = subprocess.run([str(bootstrap), str(path), str(output)],
                            capture_output=True, text=True, timeout=15)
    return result, path, output


def execute_c(path, level, flags=()):
    exe = path.with_suffix('.exe')
    result = subprocess.run(['cc', '-std=c11', f'-O{level}', *flags, str(path), '-o', str(exe)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)


PROGRAMS = [
    ('def main() -> i32 { return 42; }', 42),
    ('''// forward call and recursion
    def main() -> i32 {
        var sum = 0; var i = 0;
        while i < 10 { sum = sum + fib(i); i = i + 1; }
        return sum;
    }
    def fib(n: i32) -> i32 { if n < 2 { return n; } return fib(n-1) + fib(n-2); }
    ''', 88),
    ('''def even(n: i32) -> Bool { if n == 0 { return true; } return odd(n-1); }
    def odd(n: i32) -> Bool { if n == 0 { return false; } return even(n-1); }
    def main() -> i32 {
        let x = 9;
        if even(8) && !odd(8) { let x: i32 = 17; if x == 17 { return 3; } }
        return x;
    }''', 3),
    ('''def explode() -> Bool { return 1 / 0 == 0; }
    def main() -> i32 {
        if false && explode() { return 1; }
        if true || explode() { return 11; }
        return 2;
    }''', 11),
    ('''def main() -> i32 {
        let max: i32 = 2147483647;
        let min: i32 = -2147483648;
        if max + 1 != min { return 1; }
        if min - 1 != max { return 2; }
        if min / -1 != min { return 3; }
        if min % -1 != 0 { return 4; }
        if -min != min { return 5; }
        if 65536 * 65536 != 0 { return 6; }
        if -7 / 3 != -2 || -7 % 3 != -1 { return 7; }
        return 0;
    }''', 0),
    ('''def main() -> i32 {
        var done: Bool = false; var n = 0;
        while !done { n = n + 1; if n >= 5 { done = true; } }
        if (2 + 3 * 4 == 14) && (20 / 2 / 2 == 5) { return n; } else { return 99; }
    }''', 5),
    ('def main() -> i32 { let switch = 7; let int32_t = 5; return switch + int32_t; }', 12),
]


@pytest.mark.parametrize('source, expected', PROGRAMS)
def test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected):
    result, path, output = emit(bootstrap, tmp_path, source)
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')
    # The emitted program has no dependency on the Rolang compiler/runtime.
    for level in (0, 3):
        native = execute_c(output, level)
        assert (native.returncode, native.stdout, native.stderr) == (expected, '', '')
    reference = tmp_path / 'reference'
    compiled = compile_source(path, CompileOptions(opt_level=OptLevel.O2, output_path=reference))
    assert compiled.success, [d.message for d in compiled.diagnostics.diagnostics]
    native = subprocess.run([str(reference)], capture_output=True, text=True, timeout=10)
    assert (native.returncode, native.stdout, native.stderr) == (expected, '', '')


@pytest.mark.parametrize('source, diagnostic', [
    ('', 'missing main'),
    ('def main() -> i32 { return ; }', 'expected i32'),
    ('def main() -> i32 { return 1;', "expected '}'"),
    ('def main() -> i32 { return unknown; }', 'unknown variable'),
    ('def main() -> i32 { return missing(); }', 'unknown function'),
    ('def main() -> i32 { let n = 1; n = 2; return n; }', 'cannot assign to let'),
    ('def main() -> i32 { let n = 1; let n = 2; return n; }', 'duplicate local'),
    ('def f(n: i32, n: i32) -> i32 { return n; } def main() -> i32 { return 0; }', 'duplicate parameter'),
    ('def main() -> i32 { return 0; } def main() -> i32 { return 1; }', 'duplicate function'),
    ('def main(n: i32) -> i32 { return n; }', 'main must have signature'),
    ('def main() -> Bool { return true; }', 'main must have signature'),
    ('def main() -> i32 { if true { return 1; } }', 'return on every path'),
    ('def main() -> i32 { if 1 { return 1; } return 0; }', 'expected Bool'),
    ('def main() -> i32 { return true; }', 'expected i32'),
    ('def main() -> i32 { let x: Bool = 3; return 0; }', 'expected Bool'),
    ('def f(n: Bool) -> i32 { return 0; } def main() -> i32 { return f(3); }', 'expected Bool'),
    ('def f(n: i32) -> i32 { return n; } def main() -> i32 { return f(); }', 'wrong argument count'),
    ('def main() -> i32 { return 2147483648; }', 'out of i32 range'),
    ('def main() -> i32 { return 999999999999999999999999999; }', 'out of i32 range'),
    ('def main() -> i64 { return 0; }', 'main must have signature'),
    ('def main() -> i32 { return "text"; }', 'expected i32'),
    ('import std.io\ndef main() -> i32 { return 0; }', 'unsupported by C backend'),
    ('def main() -> i32 { return ' + '(' * 150 + '0' + ')' * 150 + '; }', 'nesting limit'),
    ('def main() -> i32 { return ' + '+'.join(['1'] * 150) + '; }', 'nesting limit'),
])
def test_errors_preserve_output(bootstrap, tmp_path, source, diagnostic):
    output = tmp_path / 'program.c'
    output.write_text('existing output')
    result, _, _ = emit(bootstrap, tmp_path, source)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert diagnostic in result.stdout
    assert ':1:' in result.stdout or ':2:' in result.stdout
    assert output.read_text() == 'existing output'


def test_random_expression_differential(bootstrap, tmp_path):
    rng = random.Random(2741)
    def expression(depth):
        if depth == 0:
            value = rng.randrange(0, 50000)
            return str(value), value
        left, a = expression(depth - 1)
        right, b = expression(depth - 1)
        op = rng.choice(['+', '-', '*'])
        value = {'+': a + b, '-': a - b, '*': a * b}[op] & 0xffffffff
        if value >= 0x80000000:
            value -= 0x100000000
        return '(' + left + op + right + ')', value
    # Check full signed values rather than only the low byte of exit status.
    values = [expression(3) for _ in range(30)]
    source = 'def main() -> i32 {' + ''.join(
        f'if {text} != {value} {{ return {index + 1}; }}'
        for index, (text, value) in enumerate(values)) + 'return 0; }'
    result, path, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0, result.stdout
    reference = tmp_path / 'reference'
    compiled = compile_source(path, CompileOptions(opt_level=OptLevel.O3, output_path=reference))
    assert compiled.success, [d.message for d in compiled.diagnostics.diagnostics]
    expected = subprocess.run([str(reference)], capture_output=True, timeout=10).returncode
    for level in (0, 3):
        assert execute_c(output, level).returncode == expected


def test_usage_and_io_failures(bootstrap, tmp_path):
    result = subprocess.run([str(bootstrap)], capture_output=True, text=True)
    assert result.returncode == 2 and 'usage:' in result.stdout
    result = subprocess.run([str(bootstrap), str(tmp_path / 'missing.rl'), str(tmp_path / 'out.c')], capture_output=True, text=True)
    assert result.returncode == 2 and 'cannot open input' in result.stdout
    path = tmp_path / 'main.rl'
    source = 'def main() -> i32 { return 0; }'
    path.write_text(source)
    result = subprocess.run([str(bootstrap), str(path), str(path)], capture_output=True, text=True)
    assert result.returncode == 2 and path.read_text() == source
    result = subprocess.run([str(bootstrap), str(path), str(tmp_path / 'missing' / 'out.c')], capture_output=True, text=True)
    assert result.returncode == 2 and 'cannot open output' in result.stdout


def test_emission_is_deterministic(bootstrap, tmp_path):
    source = PROGRAMS[1][0]
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0
    first = output.read_bytes()
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0 and output.read_bytes() == first


def test_runs_without_python_or_backend_tools_on_path(bootstrap, tmp_path):
    import os
    source = tmp_path / 'main.rl'
    source.write_text('def main() -> i32 { return 17; }')
    output = tmp_path / 'out.c'
    env = dict(os.environ, PATH=str(tmp_path / 'no-tools'), PYTHONPATH='')
    result = subprocess.run([str(bootstrap), str(source), str(output)], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert execute_c(output, 3).returncode == 17
    own_source = ROOT / 'selfhost' / 'parser.rl'
    parsed = subprocess.run([str(bootstrap), '--parse', str(own_source)], env=env,
                            capture_output=True, text=True, timeout=20)
    assert parsed.returncode == 0, parsed.stdout
    assert json.loads(parsed.stdout)['functions']
    assert parsed.stdout == parse_native(bootstrap, own_source).stdout


def test_large_frontend_workload(bootstrap, tmp_path):
    source = '\n'.join(f'def f{i}(n: i32) -> i32 {{ let x = n + {i}; if x < 0 {{ return 0; }} return x; }}' for i in range(300))
    source += '\ndef main() -> i32 { return f299(1) - 201; }'
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert execute_c(output, 3).returncode == 99


def test_symlink_output_cannot_overwrite_input(bootstrap, tmp_path):
    source = tmp_path / 'main.rl'
    original = 'def main() -> i32 { return 0; }'
    source.write_text(original)
    alias = tmp_path / 'alias.c'
    alias.symlink_to(source)
    result = subprocess.run([str(bootstrap), str(source), str(alias)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert source.read_text() == original


def test_divide_by_zero_is_runtime_error(bootstrap, tmp_path):
    result, _, output = emit(bootstrap, tmp_path, 'def main() -> i32 { return 1 / 0; }')
    assert result.returncode == 0
    native = execute_c(output, 3)
    assert native.returncode != 0 and 'division by zero' in native.stderr


def test_generated_arithmetic_has_no_c_undefined_behavior(bootstrap, tmp_path):
    result, _, output = emit(bootstrap, tmp_path, PROGRAMS[4][0])
    assert result.returncode == 0
    native = execute_c(output, 3, ('-fsanitize=undefined', '-fno-sanitize-recover=all'))
    assert (native.returncode, native.stderr) == (0, '')


# Normalize independently shaped trees to compare semantics, not node numbering.
def reference_type(node):
    from rolang import ast
    if node is None:
        return ""
    if isinstance(node, ast.BuiltinType):
        return node.name
    if isinstance(node, ast.NamedType):
        name = ".".join([*node.module_path, node.name])
        return name + ("<" + ",".join(map(reference_type, node.generic_args)) + ">" if node.generic_args else "")
    if isinstance(node, ast.OptionalType):
        return reference_type(node.inner) + "?"
    if isinstance(node, ast.ArrayType):
        return "[" + reference_type(node.element) + "]"
    if isinstance(node, ast.DictType):
        return "[" + reference_type(node.key) + ":" + reference_type(node.value) + "]"
    if isinstance(node, ast.FunctionType):
        return "(" + ",".join(map(reference_type, node.params)) + ")->" + reference_type(node.return_type)
    raise AssertionError(type(node))


def reference_expr(node):
    from rolang import ast
    if node is None:
        return None
    if isinstance(node, ast.Literal):
        return (node.kind, node.value)
    if isinstance(node, ast.Identifier):
        return ('name', node.name)
    if isinstance(node, ast.TypeReference):
        return ('type', reference_type(node.type_name))
    if isinstance(node, ast.BinaryOp):
        return ('binary', node.op, reference_expr(node.left), reference_expr(node.right))
    if isinstance(node, ast.UnaryOp):
        return ('unary', node.op, reference_expr(node.operand))
    if isinstance(node, ast.Call):
        return ('call', reference_expr(node.callee), [reference_expr(a.value) for a in node.arguments])
    if isinstance(node, ast.MemberAccess):
        return ('member', reference_expr(node.object), node.member)
    if isinstance(node, ast.StructLiteral):
        return ('struct', reference_type(node.type_name), [(a.label, reference_expr(a.value)) for a in node.arguments])
    if isinstance(node, ast.Cast):
        return ('cast', reference_expr(node.expr), reference_type(node.target_type))
    if isinstance(node, ast.Subscript):
        assert len(node.indices) == 1
        return ('index', reference_expr(node.object), reference_expr(node.indices[0]))
    raise AssertionError(type(node))


def native_expr(tree, index):
    if index < 0:
        return None
    node = tree['expressions'][index]
    kind, text = node['kind'], node['token']['text']
    child = lambda key: native_expr(tree, node[key])
    args = [native_expr(tree, i) for i in node['args']]
    if kind == 1: return ('int', int(text))
    if kind == 2: return ('bool', text == 'true')
    if kind == 3: return ('name', text)
    if kind == 4: return ('binary', text, child('left'), child('right'))
    if kind == 5: return ('unary', text, child('left'))
    if kind == 6: return ('call', ('name', text), args)
    if kind == 7:
        # The frontend deliberately retains raw spelling; decode for comparison.
        from rolang.parser import RoLangTransformer
        return ('string', RoLangTransformer()._unescape_string(text[1:-1]))
    if kind == 8: return ('nil', None)
    if kind == 9: return ('member', child('left'), text)
    if kind == 10: return ('struct', node['type_name'], list(zip(node['labels'], args)))
    if kind == 11: return ('cast', child('left'), node['type_name'])
    if kind == 12: return ('index', child('left'), child('right'))
    if kind == 13: return ('type', node['type_name'])
    if kind == 14: return ('call', child('left'), args)
    raise AssertionError(kind)


def reference_statement(node):
    from rolang import ast
    block = lambda b: [reference_statement(s) for s in b.statements] if b else []
    if isinstance(node, ast.ReturnStmt): return ('return', reference_expr(node.value))
    if isinstance(node, ast.VarDecl): return ('var', node.pattern.name, node.is_mutable, reference_type(node.type_annotation), reference_expr(node.initializer))
    if isinstance(node, ast.Assignment): return ('assign', reference_expr(node.target), reference_expr(node.value))
    if isinstance(node, ast.ExprStmt): return ('expr', reference_expr(node.expr))
    if isinstance(node, ast.IfStmt):
        if isinstance(node.condition, tuple):
            pattern, value = node.condition
            return ('iflet', pattern.name, reference_expr(value), block(node.then_block), block(node.else_block))
        return ('if', reference_expr(node.condition), block(node.then_block), block(node.else_block))
    if isinstance(node, ast.WhileStmt): return ('while', reference_expr(node.condition), block(node.body))
    if isinstance(node, ast.ForStmt): return ('for', node.pattern.name, reference_expr(node.iterable), block(node.body))
    if isinstance(node, ast.BreakStmt): return ('break',)
    if isinstance(node, ast.ContinueStmt): return ('continue',)
    if isinstance(node, ast.Block): return ('unsafe' if node.is_unsafe else 'block', block(node))
    raise AssertionError(type(node))


def native_statement(tree, index):
    node = tree['statements'][index]
    kind, name = node['kind'], node['token']['text']
    expr = native_expr(tree, node['expr'])
    body = [native_statement(tree, i) for i in node['body']]
    alternative = [native_statement(tree, i) for i in node['alternative']]
    if kind == 1: return ('return', expr)
    if kind == 2: return ('var', name, node['mutable'], node['annotation'], expr)
    if kind in (3, 13): return ('assign', native_expr(tree, node['target']), expr)
    if kind == 4: return ('if', expr, body, alternative)
    if kind == 5: return ('while', expr, body)
    if kind == 6: return ('expr', expr)
    if kind == 7: return ('block', body)
    if kind == 8: return ('for', name, expr, body)
    if kind == 9: return ('iflet', name, expr, body, alternative)
    if kind == 10: return ('break',)
    if kind == 11: return ('continue',)
    if kind == 12: return ('unsafe', body)
    raise AssertionError(kind)


def assert_tree_matches_reference(tree, source):
    from rolang import ast
    from rolang.parser import parse
    reference = parse(source)
    functions = []
    declarations = []
    for item in reference.items:
        if isinstance(item, ast.FuncDecl):
            functions.append(('', item))
        elif isinstance(item, ast.StructDecl):
            declarations.append((2, item.name))
            actual = next(d for d in tree['declarations'] if d['kind'] == 2 and d['token']['text'] == item.name)
            fields = [m for m in item.members if isinstance(m, ast.PropertyDecl)]
            assert [(f['token']['text'], f['type_name'], f['mutable'], native_expr(tree, f['expr'])) for f in actual['fields']] == [(f.name, reference_type(f.type_annotation), f.is_mutable, reference_expr(f.initializer)) for f in fields]
            methods = [m for m in item.members if isinstance(m, ast.FuncDecl)]
            assert [tree['functions'][i]['token']['text'] for i in actual['methods']] == [m.name for m in methods]
            assert actual['generics'] == [g.name for g in item.generic_params]
            functions.extend((item.name, m) for m in methods)
        elif isinstance(item, ast.ImportDecl):
            declarations.append((1, item.alias or 'import'))
            actual = tree['declarations'][len(declarations) - 1]
            value = actual['value']
            assert (json.loads(value) if value.startswith('"') else value) == ('.'.join(item.module) if item.module else item.path)
        elif isinstance(item, ast.TypeAliasDecl):
            declarations.append((3, item.name))
            assert tree['declarations'][len(declarations) - 1]['value'] == reference_type(item.aliased_type)
        else:
            raise AssertionError(type(item))
    assert [(d['kind'], d['token']['text']) for d in tree['declarations']] == declarations
    assert len(tree['functions']) == len(functions)
    for actual, (owner, function) in zip(tree['functions'], functions):
        assert (actual['owner'], actual['token']['text']) == (owner, function.name)
        assert actual['return_type'] == (reference_type(function.return_type) or 'Void')
        assert actual['generics'] == [g.name for g in function.generic_params]
        assert ('pub ' in actual['modifiers']) == (function.visibility == 'pub')
        assert ('static ' in actual['modifiers']) == function.is_static
        assert [(p['token']['text'], p['type_name']) for p in actual['params']] == [(p.internal_name, reference_type(p.type_annotation)) for p in function.params]
        assert [native_statement(tree, i) for i in actual['body']] == [reference_statement(s) for s in function.body.statements], (owner, function.name)


def parse_native(bootstrap, path):
    return subprocess.run([str(bootstrap), '--parse', str(path)], capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize('path', sorted((ROOT / 'selfhost').glob('*.rl')), ids=lambda p: p.name)
def test_frontend_parses_own_source(bootstrap, path):
    result = parse_native(bootstrap, path)
    assert (result.returncode, result.stderr) == (0, ''), result.stdout
    assert_tree_matches_reference(json.loads(result.stdout), path.read_text())


EXTENDED_SOURCE = r'''/* declarations and compound types */
import "library.rl" as Library
pub typealias Callback = (i32, String)->Bool;
pub struct Box<T> {
    pub var value: T;
    pub static def make(value: T) -> Box<T> { return Box<T> { value: value }; }
    pub def get() -> T { return self.value; }
}
pub def exercise<T>(table: [String:Vec<T>], callback: Callback) -> Void {
    var optional: Box<T>?;
    let text = "quote: \" slash: \\ tab: \t UTF8: λ";
    let other = Box<T>.make(table["key"][0]);
    other.value = table["key"][1];
    if let box = optional { box.get(); } else { return; }
    for item in table { if true { continue; } break; }
    unsafe { let x = (1 + 2 * 3) as i64; }
    { let y: [i32]; }
    return;
}
'''


def test_extended_tree_matches_reference(bootstrap, tmp_path):
    # Uninitialized declarations must be mutable.
    source = EXTENDED_SOURCE.replace('let y: [i32]', 'var y: [i32]')
    path = tmp_path / 'extended.rl'
    path.write_text(source)
    result = parse_native(bootstrap, path)
    assert result.returncode == 0, result.stdout
    assert_tree_matches_reference(json.loads(result.stdout), source)


@pytest.mark.parametrize('source, diagnostic', [
    ('/* missing end', 'unterminated block comment'),
    ('def f() { let x = "missing end', 'unterminated string'),
    ('struct S { var x: Vec<>; }', 'expected identifier'),
    ('def f() { (1 + 2) = 3; }', 'invalid assignment target'),
    ('def f() { let x; }', 'expected initializer'),
    ('def f() { var x; }', 'expected initializer'),
    ('struct S { var x: i32;', "expected 'let'"),
    ('def f() { let x = S { field: }; }', 'expected expression'),
    ('def f() { if let = nil {} }', 'expected identifier'),
    ('typealias T = ' + 'Vec<' * 150 + 'i32' + '>' * 150 + ';', 'type nesting limit'),
    ('def f() {' + 'unsafe {' * 150 + '}' * 151, 'block nesting limit'),
    ('def f() { let text = "/* not a comment */"; @; }', 'unsupported character'),
])
def test_extended_parse_errors(bootstrap, tmp_path, source, diagnostic):
    path = tmp_path / 'invalid.rl'
    path.write_text(source)
    result = parse_native(bootstrap, path)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert diagnostic in result.stdout
    assert result.stderr == ''


@pytest.mark.parametrize('body', [
    'return "text";', 'let x = nil; return 0;', 'let x = S { field: 1 }; return 0;',
    'return a[0];',
    'x.field = 1; return 0;', 'for x in xs { break; } return 0;',
    'if let x = y { return x; } return 0;',
    'var x: i32; return 0;', 'return;',
])
def test_extended_syntax_c_gate(bootstrap, tmp_path, body):
    result, path, output = emit(bootstrap, tmp_path, 'def main() -> i32 {' + body + '}')
    assert result.returncode == 1, result.stdout
    assert result.stdout.strip()
    assert not output.exists()
    parsed = parse_native(bootstrap, path)
    assert parsed.returncode == 0, parsed.stdout


def test_string_positions_and_json_escaping(bootstrap, tmp_path):
    source = '/* first\nsecond */\ndef f() { let x = "λ\n\t\x01"; return; }'
    path = tmp_path / 'positions.rl'
    path.write_text(source)
    result = parse_native(bootstrap, path)
    assert result.returncode == 0, result.stdout
    tree = json.loads(result.stdout)
    token = next(e['token'] for e in tree['expressions'] if e['kind'] == 7)
    assert token == {'text': '"λ\n\t\x01"', 'line': 3, 'column': 19}
    statement = next(s for s in tree['statements'] if s['kind'] == 1)
    assert (statement['token']['line'], statement['token']['column']) == (4, 6)


@pytest.mark.parametrize('body', [
    'let x = a < b == c > d;',
    'let x = a == b < c;',
    'let x = a as i32 < b as Bool && c as Bool || d;',
    'let x = (a as i32) + b * -c;',
    'let x = a + b as i64;',
    'if Ready {} while Ready {} for Item in Items {}',
    'let x = empty {}; let y = Module.Type { x: 1 };',
    'if check(empty {}) {} if (empty {}) {}',
    'let x = a < b && c > d; let y = Vec<Vec<i32>>.new();',
])
def test_expression_tree_precedence(bootstrap, tmp_path, body):
    source = 'def f() {' + body + '}'
    path = tmp_path / 'precedence.rl'
    path.write_text(source)
    result = parse_native(bootstrap, path)
    assert result.returncode == 0, result.stdout
    assert_tree_matches_reference(json.loads(result.stdout), source)


STRUCT_PROGRAMS = [
    ('''struct Point { pub var x: i32; pub let y: i32; }
    def main() -> i32 { let p = Point { y: 4, x: 2 }; p.x = 9; return p.x + p.y; }''', 13),
    ('''struct Counter {
        pub var n: i32;
        pub static def new(n: i32) -> Counter { return Counter { n: n }; }
        pub def bump() -> i32 { self.n = self.n + 1; return self.n; }
        pub def reset() -> Void { self.n = 0; }
        pub def same() -> Counter { return self; }
    }
    def sum(a: i32, b: i32) -> i32 { return a * 10 + b; }
    def main() -> i32 {
        let c = Counter.new(5); let alias = c.same(); alias.reset();
        let result = sum(c.bump(), alias.bump());
        return result + c.n;
    }''', 14),
    ('''struct Leaf { pub var n: i32; }
    struct Tree { pub var left: Leaf; pub var right: Leaf; }
    def make() -> Tree { let leaf = Leaf { n: 3 }; return Tree { left: leaf, right: leaf }; }
    def main() -> i32 {
        let t = make(); t.left.n = 9;
        let old = t.right; t.left = Leaf { n: 4 };
        return old.n + t.right.n + t.left.n;
    }''', 22),
    ('''struct Empty {}
    def consume(value: Empty) -> Void { return; }
    struct Holder { pub let item: Empty; }
    def main() -> i32 {
        let a = Empty {}; let b = a;
        let h = Holder { item: b }; let item: Empty = h.item;
        consume(item); consume(a); consume(Empty {}); return 0;
    }''', 0),
    ('''struct Counter {
        pub var n: i32;
        pub def down() -> i32 { if self.n == 0 { return 0; } self.n = self.n - 1; return 1 + self.down(); }
    }
    def main() -> i32 { return Counter { n: 17 }.down(); }''', 17),
    ('''struct A { pub var b: B; }
    struct B { pub var n: i32; }
    def make(b: B) -> A { return A { b: b }; }
    def main() -> i32 { var x: A = make(B { n: 1 }); let saved = x; x = make(B { n: 2 }); return saved.b.n * 10 + x.b.n; }''', 12),
    ('''struct C {
        pub var n: i32;
        pub def next() -> i32 { self.n = self.n + 1; return self.n; }
    }
    struct Pair { pub var first: i32; pub var second: i32; }
    def main() -> i32 {
        let c = C { n: 0 }; let p = Pair { second: c.next(), first: c.next() };
        return p.first * 10 + p.second;
    }''', 21),
    ('''struct switch {
        pub var int32_t: Bool;
        pub def read() -> Bool { return self.int32_t; }
    }
    def noop() -> Void { return; }
    def main() -> i32 {
        noop(); let x = switch { int32_t: true };
        if x.read() { return 19; } return 0;
    }''', 19),
    ('''struct C { pub var n: i32; }
    def main() -> i32 {
        var c = C { n: 0 }; var i = 0;
        while i < 10000 { c = C { n: c.n + 1 }; i = i + 1; }
        return c.n - 9900;
    }''', 100),
    ('''struct Leaf { pub var n: i32; }
    struct Box {
        pub var leaf: Leaf;
        pub def replace() -> i32 { self.leaf = Leaf { n: 90 }; return 7; }
    }
    def main() -> i32 {
        let box = Box { leaf: Leaf { n: 3 } }; let old = box.leaf;
        box.leaf.n = box.replace(); return old.n * 10 + box.leaf.n;
    }''', 37),
    ('''struct S { pub let x: i32; }
    def main() -> i32 { let s = S { x: 1 }; s.x = 2; return s.x; }''', 2),

    ((ROOT / 'selfhost' / 'examples' / 'structs.rl').read_text(), 42),

]


@pytest.mark.parametrize('source, expected', STRUCT_PROGRAMS)
def test_struct_program_matches_reference(bootstrap, tmp_path, source, expected):
    test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected)


@pytest.mark.parametrize('source, diagnostic', [
    ('struct S {} struct S {}', 'duplicate or reserved struct'),
    ('struct i32 {}', 'duplicate or reserved struct'),
    ('struct S { var x: i32; var x: Bool; }', 'duplicate field'),
    ('struct S { var x: i32; def x() {} }', 'duplicate member'),
    ('struct S { def x() {} def x() {} }', 'duplicate member'),
    ('struct S { var x: Missing; }', 'supports only'),
    ('struct S { var x: Void; }', 'supports only'),
    ('struct S { var x: i32 = 1; }', 'field defaults unsupported'),
    ('struct S { def __release__() {} }', 'lifecycle hooks unsupported'),
    ('struct S<T> { var x: T; }', 'declaration unsupported'),
    ('struct S {} def S() {}', 'conflicts with struct'),
    ('struct S { var x: i32; } def main() -> i32 { let s = S {}; return 0; }', 'missing field'),
    ('struct S { var x: i32; } def main() -> i32 { let s = S { x: 1, x: 2 }; return 0; }', 'duplicate field initializer'),
    ('struct S {} def main() -> i32 { let s = S { x: 1 }; return 0; }', 'unknown field'),
    ('struct S { var x: i32; } def main() -> i32 { let s = S { x: true }; return 0; }', 'expected i32'),
    ('struct S { var x: i32; } def main() -> i32 { let s = S { x: 1 }; s.x = true; return 0; }', 'expected i32'),
    ('struct S {} def main() -> i32 { let s = S {}; return s.missing; }', 'unknown field'),
    ('def main() -> i32 { return 1.field; }', 'receiver must be a struct'),
    ('struct S { def f() -> i32 { return 0; } } def main() -> i32 { return S.f(); }', 'receiver mismatch'),
    ('struct S { static def f() -> i32 { return 0; } } def main() -> i32 { return S {}.f(); }', 'receiver mismatch'),
    ('struct S {} def main() -> i32 { return S {}.missing(); }', 'unknown method'),
    ('struct S { def f(x: i32) -> i32 { return x; } } def main() -> i32 { return S {}.f(); }', 'wrong argument count'),
    ('struct S { def f(x: i32) -> i32 { return x; } } def main() -> i32 { return S {}.f(true); }', 'expected i32'),
    ('struct S { def f() { self = S {}; } }', 'cannot assign to let'),
    ('struct S { def f(self: i32) {} }', 'duplicate parameter'),
    ('def f() {} def main() -> i32 { let x = f(); return 0; }', 'Void is not a value'),
    ('def f() {} def main() -> i32 { if f() == f() { return 0; } return 1; }', 'Void is not a value'),
    ('def f() -> Void { return 1; }', 'expected Void'),
    ('def f() -> i32 { return; }', 'expected i32'),
    ('struct S {} def main() -> i32 { let eq = S {} == S {}; return 0; }', 'cannot compare struct values'),
    ('struct S {} struct T {} def main() -> i32 { let s: S = T {}; return 0; }', 'expected S'),
])
def test_struct_diagnostics_preserve_output(bootstrap, tmp_path, source, diagnostic):
    if 'def main' not in source:
        source += ' def main() -> i32 { return 0; }'
    output = tmp_path / 'program.c'
    output.write_text('existing output')
    result, _, _ = emit(bootstrap, tmp_path, source)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert diagnostic in result.stdout
    assert output.read_text() == 'existing output'


def test_struct_c_sanitizers(bootstrap, tmp_path):
    for source, expected in (STRUCT_PROGRAMS[2], STRUCT_PROGRAMS[8]):
        result, _, output = emit(bootstrap, tmp_path, source)
        assert result.returncode == 0, result.stdout
        native = execute_c(output, 3, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
        assert (native.returncode, native.stderr) == (expected, '')


STRING_PROGRAMS = [
    ('''def message(n: i32) -> String { return "value=" + n.to_string(); }
    def main() -> i32 { let a = message(-42); if !a.equals("value=-42") { return 1; }
        if !a.concat("!").equals("value=-42!") { return 2; }
        if a.len() != 9 || "".len() != 0 || !"".is_empty() { return 3; } return 0; }''', 0),
    (r'''def main() -> i32 {
        let s = "A\0λ😀\n\t\r\"\\\q";
        if s.len() != 14 { return 1; }
        if s.byte_at(1) != 0 || s.byte_at(2) != 206 || s.char_at(3) != 187 { return 2; }
        if s.byte_at(-1) != -1 || s.byte_at(99) != -1 { return 3; }
        if !s.contains("\0λ") || !s.starts_with("A\0") || !s.ends_with("\\q") { return 4; }
        if s.find_char(0, -9) != 1 || s.find_char(999, 0) != -1 { return 5; }
        if !s.substring(1, 3).equals("\0λ") { return 6; } return 0;
    }''', 0),
    ('''def main() -> i32 { let s = "abc";
        if !s.substring(-2, 2).equals("ab") { return 1; }
        if !s.substring(1, 2147483647).equals("bc") { return 2; }
        if !s.substring(99, 1).is_empty() || !s.substring(0, -1).is_empty() { return 3; }
        if !s.contains("") || !s.starts_with("") || !s.ends_with("") { return 4; }
        if s.contains("abcd") || s.starts_with("abcd") || s.ends_with("abcd") { return 5; }
        if s.compare_to("abd") != -1 || s.compare_to("ab") != 1 || s.compare_to("abc") != 0 { return 6; }
        return s.find_char(98, 0);
    }''', 1),
    ('''struct Token { pub var text: String; pub var offset: i64;
        pub def append(text: String) -> Void { self.text = self.text + text; }
        pub def read() -> String { return self.text; }
    }
    def main() -> i32 { let token = Token { text: "x", offset: 4 }; let alias = token;
        alias.append("y"); if !token.read().equals("xy") { return 1; }
        token.offset = token.offset + token.text.len(); return token.offset as i32;
    }''', 6),
    ('''def widen(x: i64) -> i64 { return x + 1; }
    def main() -> i32 {
        var n: i64 = 2147483647; n = widen(n);
        if !n.to_string().equals("2147483648") { return 1; }
        if (n as i32) != -2147483648 { return 2; }
        let min = n * (n * 2);
        if !min.to_string().equals("-9223372036854775808") { return 3; }
        if min / -1 != min || min % -1 != 0 || -min != min { return 4; }
        if min - 1 + 1 != min || min + min != 0 { return 5; }
        if !(-2147483648).to_string().equals("-2147483648") { return 6; }
        return 0;
    }''', 0),
    ('''struct State { pub var n: i32;
        pub def next() -> String { self.n = self.n + 1; return self.n.to_string(); }
    }
    def main() -> i32 { let s = State { n: 0 }; let text = s.next() + s.next();
        if !text.equals("12") { return 1; }
        if !s.next().concat(s.next()).equals("34") { return 2; }
        var i = 0; var many = ""; while i < 100 { many = many + "x"; i = i + 1; }
        return many.len() as i32;
    }''', 100),
    ('def main() -> i32 { let s = "first\nsecond"; return s.len() as i32; }', 12),
]


@pytest.mark.parametrize('source, expected', STRING_PROGRAMS)
def test_string_program_matches_reference(bootstrap, tmp_path, source, expected):
    test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected)


@pytest.mark.parametrize('body, diagnostic', [
    ('let s: String = 1; return 0;', 'expected String'),
    ('return "abc".len();', 'expected i32'),
    ('let s = "abc" + 1; return 0;', 'expected String'),
    ('let b = "a" == "a"; return 0;', 'String.equals'),
    ('let b = "a" < "b"; return 0;', 'integer operands'),
    ('return "a".byte_at();', 'wrong argument count'),
    ('return "a".byte_at("b");', 'expected i32'),
    ('return "a".byte_at(1 as i64);', 'expected i32'),
    ('return "a".len(1) as i32;', 'wrong argument count'),
    ('let s = "a".substring(0); return 0;', 'wrong argument count'),
    ('let s = "a".replace("a", "b"); return 0;', 'method unsupported'),
    ('let s = (1).to_string(2); return 0;', 'wrong argument count'),
    ('return "a" as i32;', 'numeric i32/i64 casts'),
    ('return 1 as Bool;', 'numeric i32/i64 casts'),
    ('let x: i32 = 1 as i64; return 0;', 'expected i32'),
])
def test_string_diagnostics_preserve_output(bootstrap, tmp_path, body, diagnostic):
    output = tmp_path / 'program.c'; output.write_text('existing output')
    result, _, _ = emit(bootstrap, tmp_path, 'def main() -> i32 {' + body + '}')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert diagnostic in result.stdout
    assert output.read_text() == 'existing output'


def test_string_c_sanitizers(bootstrap, tmp_path):
    for source, expected in (STRING_PROGRAMS[1], STRING_PROGRAMS[2], STRING_PROGRAMS[4], STRING_PROGRAMS[5]):
        result, _, output = emit(bootstrap, tmp_path, source)
        assert result.returncode == 0, result.stdout
        native = execute_c(output, 3, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
        assert (native.returncode, native.stderr) == (expected, '')


VECTOR_PROGRAMS = [
    ('''def main() -> i32 {
        let xs = Vec<i32>.with_capacity(-1); var i = 0;
        while i < 1000 { xs.push(i); i = i + 1; }
        let alias = xs; alias.set(3, 42); xs[4] = 8;
        xs.resize(2048); xs.resize(2);
        if xs.len() != 1000 || xs.get(3) != 42 || alias[4] != 8 { return 1; }
        if xs.pop() != 999 || xs.len() != 999 { return 2; }
        return 0;
    }''', 0),
    ('''struct Token { pub var text: String; pub var start: i32; }
    def scan(text: String) -> Vec<Token> {
        let out = Vec<Token>.new(); var start = 0; var i = 0;
        while i <= (text.len() as i32) {
            if i == (text.len() as i32) || text.byte_at(i) == 32 {
                if i > start { out.push(Token { text: text.substring(start, i - start), start: start }); }
                start = i + 1;
            }
            i = i + 1;
        }
        return out;
    }
    def main() -> i32 {
        let tokens = scan("let answer = 42 ;");
        if tokens.len() != 5 || !tokens.get(3).text.equals("42") { return 1; }
        let token = tokens.get(1); token.text = "result";
        if !tokens[1].text.equals("result") { return 2; }
        var length = 0; for token in tokens { length = length + (token.text.len() as i32); }
        return length;
    }''', 13),
    ('''def main() -> i32 {
        let outer = Vec<Vec<i64>>.new(); let inner = Vec<i64>.new();
        inner.push(2147483647); inner.push((2147483647 as i64) + 1);
        outer.push(inner); outer[0][0] = -5;
        if inner.get(0) != -5 || outer.get(0).pop() != ((2147483647 as i64) + 1) { return 1; }
        let flags = Vec<Bool>.new(); flags.push(true); flags.push(false);
        if flags.pop() || !flags.pop() || flags.pop() { return 2; }
        let texts = Vec<String>.new(); texts.push("λ"); texts.push("a" + "b");
        if !texts.pop().equals("ab") || !texts.pop().equals("λ") { return 3; }
        return 0;
    }''', 0),
    ('''def main() -> i32 {
        let xs = Vec<i32>.new(); xs.push(1); var sum = 0;
        for x in xs {
            if x < 5 { xs.push(x + 1); }
            if x == 2 { continue; }
            if x == 4 { break; }
            sum = sum + x;
        }
        var i = 0;
        while true { i = i + 1; if i < 3 { continue; } break; }
        { let sum = 99; if sum != 99 { return 1; } }
        unsafe { if i != 3 { return 2; } }
        return sum + xs.len();
    }''', 9),
    ('''def main() -> i32 {
        let xs = Vec<i32>.new(); xs.push(2); xs.push(3); var sum = 0;
        for x in xs { for y in xs { if y == 3 { break; } sum = sum + x * y; } }
        let empty = Vec<i32>.new(); for x in empty { return 1; }
        if empty.pop() != 0 { return 2; }
        return sum;
    }''', 10),
    ('''struct State {
        pub var items: Vec<i32>;
        pub def index() -> i32 { self.items.push(2); return 0; }
        pub def value() -> i32 { self.items = Vec<i32>.new(); self.items.push(9); return 7; }
    }
    def main() -> i32 {
        let original = Vec<i32>.new(); original.push(1); let state = State { items: original };
        state.items[state.index()] = state.value();
        return original[0] * 10 + state.items[0];
    }''', 79),
    ('''def same(xs: Vec<i32>) -> Vec<i32> { xs.push(8); return xs; }
    def main() -> i32 {
        let xs = Vec<i32>.new(); let ys = same(xs); ys.push(9);
        var total = 0; var current = xs;
        for x in current { current = Vec<i32>.new(); total = total + x; }
        return total;
    }''', 17),
]


@pytest.mark.parametrize('source, expected', VECTOR_PROGRAMS)
def test_vector_program_matches_reference(bootstrap, tmp_path, source, expected):
    test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected)


@pytest.mark.parametrize('body, diagnostic', [
    ('let xs = Vec<Void>.new(); return 0;', 'supports only'),
    ('let xs = Vec<Missing>.new(); return 0;', 'supports only'),
    ('let xs = Vec<i32, Bool>.new(); return 0;', 'supports only'),
    ('let xs = Vec<Vec<Missing>>.new(); return 0;', 'supports only'),
    ('let xs: Vec<i32> = Vec<i64>.new(); return 0;', 'expected Vec<i32>'),
    ('let xs = Vec<i32>.new(); xs.push(true); return 0;', 'expected i32'),
    ('let xs = Vec<String>.new(); xs.push(1); return 0;', 'expected String'),
    ('let xs = Vec<i32>.new(); return xs[true];', 'expected i32'),
    ('let xs = Vec<i32>.new(); xs[0] = "bad"; return 0;', 'expected i32'),
    ('let xs = Vec<i32>.new(); xs.set(0, false); return 0;', 'expected i32'),
    ('let xs = Vec<i32>.new(1); return 0;', 'wrong argument count'),
    ('let xs = Vec<i32>.with_capacity(); return 0;', 'wrong argument count'),
    ('let xs = Vec<i32>.with_capacity(true); return 0;', 'expected i32'),
    ('let xs = Vec<i32>.missing(); return 0;', 'unknown Vec constructor'),
    ('let xs = Vec<i32>.new(); xs.free(); return 0;', 'Vec method unsupported'),
    ('let xs = Vec<i32>.new(); xs.pop(0); return 0;', 'wrong argument count'),
    ('let xs = Vec<i32>.new(); xs.get(); return 0;', 'wrong argument count'),
    ('let xs = Vec<i32>.new(); let same = xs == xs; return 0;', 'cannot compare struct'),
    ('let x = Vec<i32>; return 0;', "expected expression"),
    ('break; return 0;', 'loop control outside a loop'),
    ('continue; return 0;', 'loop control outside a loop'),
    ('for x in 1 {} return 0;', 'iterable must be a Vec'),
    ('let xs = Vec<i32>.new(); for x in xs { x = 1; } return 0;', 'cannot assign to let'),
    ('let xs = Vec<i32>.new(); for x in xs { let x = 1; } return 0;', 'duplicate local'),
    ('let xs = Vec<i32>.new(); for x in xs {} return x;', 'unknown variable'),
])
def test_vector_diagnostics_preserve_output(bootstrap, tmp_path, body, diagnostic):
    output = tmp_path / 'program.c'; output.write_text('existing output')
    result, _, _ = emit(bootstrap, tmp_path, 'def main() -> i32 {' + body + '}')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert diagnostic in result.stdout
    assert output.read_text() == 'existing output'


@pytest.mark.parametrize('action', ['xs.get(0);', 'xs.set(0, 1);', 'xs[-1];', 'xs[1] = 2;'])
def test_vector_bounds_fail_at_runtime(bootstrap, tmp_path, action):
    result, _, output = emit(bootstrap, tmp_path, 'def main() -> i32 { let xs = Vec<i32>.new(); ' + action + ' return 0; }')
    assert result.returncode == 0, result.stdout
    native = execute_c(output, 3, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
    assert native.returncode == 1
    assert native.stderr == 'Vec index out of bounds\n'


def test_vector_c_sanitizers(bootstrap, tmp_path):
    for source, expected in VECTOR_PROGRAMS[:4]:
        result, _, output = emit(bootstrap, tmp_path, source)
        assert result.returncode == 0, result.stdout
        native = execute_c(output, 3, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
        assert (native.returncode, native.stderr) == (expected, '')


OPTIONAL_PROGRAMS = [
    ('''def choose(flag: Bool) -> i32? { if flag { return 0; } return nil; }
    def main() -> i32 {
        var value: i32?;
        if let x = value { return 1; }
        value = choose(true);
        if let x = value { if x != 0 { return 2; } } else { return 3; }
        value = nil;
        if let x = value { return 4; }
        if let x = choose(false) { return 5; } else { return 42; }
    }''', 42),
    ('''def read(value: i64?) -> i32 { if let x = value { return x as i32; } else { return 9; } }
    def main() -> i32 {
        let flag: Bool? = false;
        if let x = flag { if x { return 1; } } else { return 2; }
        let text: String? = "";
        if let x = text { if !x.is_empty() { return 3; } } else { return 4; }
        return read(33) + read(nil);
    }''', 42),
    ('''struct Box { var value: i32?; }
    def read(box: Box?) -> i32 {
        if let b = box { if let v = b.value { return v; } return 3; } return 4;
    }
    def main() -> i32 {
        let box = Box { value: nil };
        if read(box) != 3 { return 1; }
        box.value = 42;
        if read(nil) != 4 { return 2; }
        return read(box);
    }''', 42),
    ('''def main() -> i32 {
        let values = Vec<i32?>.new(); values.push(nil); values.push(10);
        values[0] = 20; values.set(1, 22);
        var total = 0;
        for value in values { if let x = value { total = total + x; } }
        values[0] = nil;
        if let x = values.get(0) { return 1; }
        if let x = values.pop() { if x != 22 { return 2; } } else { return 3; }
        return total;
    }''', 42),
    ('''def main() -> i32 {
        let values: Vec<i32>? = Vec<i32>.new();
        if let v = values { v.push(42); }
        if let v = values { return v[0]; } else { return 1; }
    }''', 42),
    ('''struct Counter { var n: i32; }
    def next(c: Counter) -> i32? { c.n = c.n + 1; return c.n; }
    def main() -> i32 {
        let c = Counter { n: 0 }; let x = 40;
        if let x = next(c) { if x != 1 { return 1; } }
        if c.n != 1 { return 2; }
        return x + 2;
    }''', 42),
]


@pytest.mark.parametrize('source, expected', OPTIONAL_PROGRAMS[:3] + OPTIONAL_PROGRAMS[4:])
def test_optional_values_match_reference(bootstrap, tmp_path, source, expected):
    test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected)


@pytest.mark.parametrize('source, diagnostic', [
    ('def main() -> i32 { if let x = 3 { return x; } return 0; }', 'requires an optional'),
    ('def main() -> i32 { let x: i32? = true; return 0; }', 'expected i32?'),
    ('def main() -> i32 { let x: i32? = 1; return x; }', 'expected i32'),
    ('def main() -> i32 { let x: i32? = 1; if let y = x { y = 2; } return 0; }', 'cannot assign'),
    ('def main() -> i32 { let x: i32? = nil; if let y = x {} return y; }', 'unknown variable'),
    ('def main() -> i32 { let x: i32? = nil; if let y = x { return y; } }', 'return on every path'),
    ('def main() -> i32 { let x: Void? = nil; return 0; }', 'supports only'),
])
def test_optional_errors_preserve_output(bootstrap, tmp_path, source, diagnostic):
    output = tmp_path / 'program.c'
    output.write_text('preserved')
    result, _, _ = emit(bootstrap, tmp_path, source)
    assert result.returncode == 1
    assert diagnostic in result.stdout
    assert output.read_text() == 'preserved'


def test_optional_generated_c_sanitizers(bootstrap, tmp_path):
    for source, expected in OPTIONAL_PROGRAMS:
        result, _, output = emit(bootstrap, tmp_path, source)
        assert result.returncode == 0, result.stdout
        run = execute_c(output, 1, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
        assert run.returncode == expected
        assert run.stderr == ''


def test_optional_vector_values(bootstrap, tmp_path):
    # The reference LLVM backend currently fails to compile Vec<i32?> calls.
    # Exercise the native backend against explicit results until that is fixed.
    source, expected = OPTIONAL_PROGRAMS[3]
    result, _, output = emit(bootstrap, tmp_path, source)
    assert result.returncode == 0, result.stdout
    for level in (0, 3):
        run = execute_c(output, level)
        assert (run.returncode, run.stdout, run.stderr) == (expected, '', '')


@pytest.mark.parametrize('body', [
    'let x: i32? = 1; if x == x { return 1; } return 0;',
    'if nil == nil { return 1; } return 0;',
])
def test_optional_comparison_rejected(bootstrap, tmp_path, body):
    test_optional_errors_preserve_output(
        bootstrap, tmp_path, 'def main() -> i32 {' + body + '}',
        'optional comparison unsupported')


DICT_PROGRAMS = [
    ('''def main() -> i32 {
        let d = Dict<String, i32>.with_capacity(1, 1);
        d.set("zero", 0); d["answer"] = 42;
        if d.len() != 2 || !d.contains("zero") || d.contains("missing") { return 1; }
        if let n = d["zero"] { if n != 0 { return 2; } } else { return 3; }
        if let n = d.get("missing") { return 4; }
        if let n = d.get("answer") { return n; } return 5;
    }''', 42),
    ('''def main() -> i32 {
        let d = Dict<i32, i64>.with_capacity(0, 0); var i = 0;
        while i < 1000 { d.set(i - 500, i); i = i + 1; }
        i = 0;
        while i < 1000 {
            if let n = d.get(i - 500) { if n != i { return 1; } } else { return 2; }
            i = i + 1;
        }
        d.set(-500, 42); if d.len() != 1000 { return 3; }
        i = 0;
        while i < 1000 { if i % 3 == 0 { d.remove(i - 500); } i = i + 1; }
        i = 0;
        while i < 1000 {
            if d.contains(i - 500) != (i % 3 != 0) { return 4; }
            i = i + 1;
        }
        let keys = d.keys(); if keys[0] != -499 || keys[1] != -498 { return 5; }
        if d.len() != 666 { return 6; }
        d.clear(); if d.len() != 0 || d.contains(1) { return 7; }
        d.set(7, 42); if let n = d.remove(7) { return n as i32; } return 8;
    }''', 42),
    ('''def main() -> i32 {
        let d = Dict<String, String>.with_capacity(4, 1);
        d.set("λ\\0中", "value"); d.set("λ", "prefix");
        let same = "λ" + "\\0中";
        if let text = d.get(same) { if !text.equals("value") { return 1; } } else { return 2; }
        d.set(same, "updated");
        if d.len() != 2 { return 3; }
        let keys = d.keys(); let values = d.values();
        d.remove("λ\\0中"); d.set("new", "last");
        if !keys[0].equals("λ\\0中") || !values[0].equals("updated") { return 4; }
        if !d.keys()[0].equals("λ") || !d.keys()[1].equals("new") { return 5; }
        if let removed = d.remove("absent") { return 6; }
        return 42;
    }''', 42),
    ('''struct Binding { var number: i32; }
    def lookup(scopes: Vec<Dict<String, Binding>>, name: String) -> Binding? {
        var i = scopes.len() - 1;
        while i >= 0 { if let binding = scopes[i].get(name) { return binding; } i = i - 1; }
        return nil;
    }
    def main() -> i32 {
        let scopes = Vec<Dict<String, Binding>>.new();
        let outer = Dict<String, Binding>.with_capacity(8, 1);
        let inner = Dict<String, Binding>.with_capacity(8, 1);
        outer.set("x", Binding { number: 5 }); inner.set("x", Binding { number: 20 });
        scopes.push(outer); scopes.push(inner);
        if let binding = lookup(scopes, "x") { binding.number = 42; } else { return 1; }
        let snapshot = inner.values(); inner.clear();
        if snapshot[0].number != 42 { return 2; }
        if let binding = lookup(scopes, "x") { if binding.number != 5 { return 3; } } else { return 4; }
        if let binding = lookup(scopes, "missing") { return 5; }
        return snapshot[0].number;
    }''', 42),
    ('''def main() -> i32 {
        let d = Dict<i64, Bool>.new(2, 0, 0, 0);
        let wide: i64 = 2147483647; let key = wide + 100;
        d.set(key, false); d.set(-key, true);
        if let flag = d.get(key) { if flag { return 1; } } else { return 2; }
        if let flag = d.get(-key) { if !flag { return 3; } } else { return 4; }
        let flags = Dict<Bool, i32>.with_capacity(1, 0);
        flags.set(false, 17); flags.set(true, 25);
        if let a = flags.get(false) { if let b = flags.get(true) { return a + b; } }
        return 5;
    }''', 42),
    ('''def main() -> i32 {
        let d = Dict<String, i32>.with_capacity(0, 1);
        let first = d.entry_index("a", 1); let again = d.entry_index("a", 99);
        if first != again || d.value_at(first) != 1 { return 1; }
        let second = d.entry_index("b", 20); d.set_value_at(first, 22);
        if d.value_at(-1) != 0 || d.value_at(100) != 0 { return 2; }
        d.set_value_at(-1, 7); d.set_value_at(100, 7);
        return d.value_at(first) + d.value_at(second);
    }''', 42),
    ('''struct Key { var n: i32; }
    def main() -> i32 {
        let d = Dict<Key, i32>.with_capacity(2, 0);
        let key = Key { n: 1 }; let alias = key; let other = Key { n: 1 };
        d.set(key, 42); key.n = 2;
        if d.contains(other) { return 1; }
        if let n = d.get(alias) { return n; } return 2;
    }''', 42),
    ('''struct State { var n: i32; var dict: Dict<i32, i32>; }
    def receiver(s: State) -> Dict<i32, i32> { s.n = s.n * 10 + 1; return s.dict; }
    def key(s: State) -> i32 { s.n = s.n * 10 + 2; return 7; }
    def value(s: State) -> i32 { s.n = s.n * 10 + 3; return 42; }
    def main() -> i32 {
        let s = State { n: 0, dict: Dict<i32, i32>.with_capacity(2, 0) };
        s.dict[key(s)] = value(s);
        if s.n != 23 { return 1; }
        s.n = 0; receiver(s).set(key(s), value(s));
        if s.n != 123 { return 2; }
        if let n = s.dict[7] { return n; } return 3;
    }''', 42),
    ('''def main() -> i32 {
        let outer = Dict<String, Dict<String, Vec<i32>>>.with_capacity(2, 1);
        let inner = Dict<String, Vec<i32>>.with_capacity(2, 1);
        let values = Vec<i32>.new(); values.push(42);
        inner.set("values", values); outer.set("scope", inner);
        if let scope = outer.get("scope") { if let list = scope["values"] { return list[0]; } }
        return 1;
    }''', 42),
]


@pytest.mark.parametrize('source, expected', DICT_PROGRAMS)
def test_dict_matches_reference(bootstrap, tmp_path, source, expected):
    test_bootstrap_matches_reference(bootstrap, tmp_path, source, expected)


DICT_OPTIONAL_SOURCE = '''def main() -> i32 {
    let d = Dict<String, i32?>.with_capacity(1, 1);
    d.set("nil", nil); d.set("zero", 0); d.set("answer", 42);
    if let outer = d.get("absent") { return 1; }
    if let outer = d.get("nil") { if let inner = outer { return 2; } } else { return 3; }
    if let outer = d.get("zero") { if let inner = outer { if inner != 0 { return 4; } } else { return 5; } } else { return 6; }
    if let outer = d.remove("nil") { if let inner = outer { return 7; } } else { return 8; }
    let snapshot = d.values(); d.clear();
    if let n = snapshot[1] { return n; } return 9;
}'''


def test_dict_optional_values(bootstrap, tmp_path):
    # The reference compiler cannot currently lower optional-valued collections.
    result, _, output = emit(bootstrap, tmp_path, DICT_OPTIONAL_SOURCE)
    assert result.returncode == 0, result.stdout
    for level in (0, 3):
        run = execute_c(output, level)
        assert (run.returncode, run.stdout, run.stderr) == (42, '', '')


@pytest.mark.parametrize('source, diagnostic', [
    ('let d = Dict<String, i32>.with_capacity(1, 1); d.set(1, 2);', 'expected String'),
    ('let d = Dict<String, i32>.with_capacity(1, 1); d["a"] = true;', 'expected i32'),
    ('let d = Dict<i32, i32>.with_capacity(1, 0); d.get("x");', 'expected i32'),
    ('let d = Dict<i32, i32>.with_capacity(1);', 'wrong argument count'),
    ('let d = Dict<i32, i32>.new();', 'wrong argument count'),
    ('let d = Dict<i32, i32>.with_capacity(1, 0); d.remove();', 'wrong argument count'),
    ('let d = Dict<i32, i32>.with_capacity(1, 0); d.set_value_at(0, false);', 'expected i32'),
    ('let d = Dict<i32, i32>.with_capacity(1, 0); d.unknown();', 'Dict method unsupported'),
    ('let d = Dict<i32?, i32>.with_capacity(1, 0);', 'optional dictionary keys unsupported'),
    ('let d = Dict<i32>.with_capacity(1, 0);', 'supports only'),
    ('let d = Dict<i32, i32, i32>.with_capacity(1, 0);', 'supports only'),
    ('let d = Dict<i32, Void>.with_capacity(1, 0);', 'supports only'),
    ('let d = Dict<i32, i32>.with_capacity(1, 0); let x = d == d;', 'cannot compare struct values'),
])
def test_dict_errors_preserve_output(bootstrap, tmp_path, source, diagnostic):
    test_optional_errors_preserve_output(
        bootstrap, tmp_path, 'def main() -> i32 {' + source + ' return 0; }', diagnostic)


def test_dict_generated_c_sanitizers(bootstrap, tmp_path):
    for source, expected in DICT_PROGRAMS + [(DICT_OPTIONAL_SOURCE, 42)]:
        result, _, output = emit(bootstrap, tmp_path, source)
        assert result.returncode == 0, result.stdout
        run = execute_c(output, 1, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
        assert (run.returncode, run.stdout, run.stderr) == (expected, '', '')


@pytest.mark.parametrize('capacity, key_kind, diagnostic', [
    (-1, 0, 'negative Dict capacity\n'),
    (1, 1, 'Dict string key kind requires String keys\n'),
])
def test_dict_constructor_runtime_errors(bootstrap, tmp_path, capacity, key_kind, diagnostic):
    result, _, output = emit(bootstrap, tmp_path,
        f'def main() -> i32 {{ let d = Dict<i32, i32>.with_capacity({capacity}, {key_kind}); return 0; }}')
    assert result.returncode == 0, result.stdout
    run = execute_c(output, 1, ('-fsanitize=address,undefined', '-fno-sanitize-recover=all'))
    assert (run.returncode, run.stdout, run.stderr) == (1, '', diagnostic)


def test_native_backend_compiles_its_frontend(bootstrap, tmp_path):
    # Until module loading exists, assemble the actual frontend source files.
    # No source implementation is substituted: only import declarations are removed.
    source = '\n'.join('\n'.join(
        line for line in (ROOT / 'selfhost' / name).read_text().splitlines()
        if not line.startswith('import ')) for name in ('lexer.rl', 'ast.rl', 'parser.rl'))
    input_path = tmp_path / 'frontend.rl'
    input_path.write_text(source)
    reference = parse_native(bootstrap, input_path)
    assert reference.returncode == 0, reference.stdout
    tree = json.loads(reference.stdout)
    counts = {key: len(tree[key]) for key in ('functions', 'declarations', 'expressions', 'statements')}
    driver = f'''def main() -> i32 {{
        let scanned = lex({json.dumps(source, ensure_ascii=False)});
        if !scanned.error.is_empty() {{ return 1; }}
        let parser = Parser.new(scanned.tokens); parser.parse();
        if !parser.error.is_empty() {{ return 2; }}
        if parser.program.functions.len() != {counts['functions']} {{ return 3; }}
        if parser.program.declarations.len() != {counts['declarations']} {{ return 4; }}
        if parser.program.expressions.len() != {counts['expressions']} {{ return 5; }}
        if parser.program.statements.len() != {counts['statements']} {{ return 6; }}
        let bad = Parser.new(lex("def main( {{").tokens); bad.parse();
        if bad.error.is_empty() {{ return 7; }}
        if lex("/* unclosed").error.is_empty() {{ return 8; }}
        return 42;
    }}'''
    result, _, output = emit(bootstrap, tmp_path, source + '\n' + driver)
    assert result.returncode == 0, result.stdout
    for level in (0, 3):
        run = execute_c(output, level)
        assert (run.returncode, run.stdout, run.stderr) == (42, '', '')
