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
    ('def main() -> i32 { return ; }', 'requires an expression'),
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
    ('def main() -> i64 { return 0; }', 'only i32 and Bool'),
    ('def main() -> i32 { return "text"; }', 'unsupported by C backend'),
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
    'let x = Vec<i32>.new(); return 0;', 'return 1 as i32;', 'return a[0];',
    'x.field = 1; return 0;', 'for x in xs { break; } return 0;',
    'if let x = y { return x; } return 0;', 'unsafe { return 0; }',
    'var x: i32; return 0;', 'return;',
    'return 0; let unreachable = "still unsupported";',
])
def test_extended_syntax_c_gate(bootstrap, tmp_path, body):
    result, path, output = emit(bootstrap, tmp_path, 'def main() -> i32 {' + body + '}')
    assert result.returncode == 1, result.stdout
    assert 'C backend' in result.stdout
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
