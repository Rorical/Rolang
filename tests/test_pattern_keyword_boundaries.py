from rolang import ast
from rolang.parser import parse


def test_binding_names_with_keyword_prefixes():
    program = parse('''def main() -> i32 {
        let variants = 1; let letter = 2; var variable = 3;
        switch variants { case let variant: return variant; }
        return letter + variable;
    }''')
    body = program.items[0].body.statements
    assert [s.pattern.name for s in body[:3]] == ['variants', 'letter', 'variable']
    pattern = body[3].cases[0].patterns[0][0]
    assert isinstance(pattern, ast.IdentifierPattern)
    assert (pattern.name, pattern.binding) == ('variant', 'let')
