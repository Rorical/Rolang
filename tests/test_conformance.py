"""Tests for protocol conformance checking."""

from rolang.conformance import ConformanceChecker
from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck
from rolang.symbols import SymbolKind


STRING_PRELUDE = "\nstruct String { var handle: RawPtr; }\n"


def with_string_prelude(source: str) -> str:
    if "String" in source or '"' in source:
        return source + STRING_PRELUDE
    return source


def check_source(source: str):
    source = with_string_prelude(source)
    program = parse(source)
    resolution = resolve(program)
    assert not resolution.has_errors(), resolution.errors
    type_result = typecheck(program, resolution)
    assert not type_result.has_errors(), type_result.errors
    return resolution, type_result


def find_type_id(resolution, type_result, kind, name: str):
    for symbol in resolution.symbol_table.symbols.values():
        if symbol.kind is kind and symbol.name == name:
            if kind is SymbolKind.STRUCT:
                return type_result.type_table.make_struct(symbol.id)
            if kind is SymbolKind.PROTOCOL:
                protocol_type = type_result.type_table.get_protocol_type(symbol.id)
                assert protocol_type is not None
                return protocol_type
    raise AssertionError(f"symbol not found: {name}")


def test_protocol_conformance_accepts_matching_method_and_property():
    source = """
protocol NamedSize {
    def size() -> i32;
    var name: String { get set };
}

struct Box {
    var name: String;

    def size() -> i32 {
        return 1;
    }
}
"""
    resolution, type_result = check_source(source)
    concrete = find_type_id(resolution, type_result, SymbolKind.STRUCT, "Box")
    protocol = find_type_id(resolution, type_result, SymbolKind.PROTOCOL, "NamedSize")

    checker = ConformanceChecker(type_result.type_table, resolution.symbol_table)
    result = checker.check_conformance(concrete, protocol)

    assert result.conforms
    assert result.missing_requirements == []
    assert result.errors == []
    assert {w.requirement_name for w in result.witnesses} == {"size", "name"}


def test_protocol_conformance_rejects_method_return_mismatch():
    source = """
protocol Sized {
    def size() -> i32;
}

struct Box {
    def size() -> i64 {
        return 1;
    }
}
"""
    resolution, type_result = check_source(source)
    concrete = find_type_id(resolution, type_result, SymbolKind.STRUCT, "Box")
    protocol = find_type_id(resolution, type_result, SymbolKind.PROTOCOL, "Sized")

    checker = ConformanceChecker(type_result.type_table, resolution.symbol_table)
    result = checker.check_conformance(concrete, protocol)

    assert not result.conforms
    assert result.missing_requirements == []
    assert any("return type mismatch" in error for error in result.errors)


def test_protocol_conformance_rejects_property_type_mismatch():
    source = """
protocol Named {
    let name: String { get };
}

struct Box {
    var name: i32;
}
"""
    resolution, type_result = check_source(source)
    concrete = find_type_id(resolution, type_result, SymbolKind.STRUCT, "Box")
    protocol = find_type_id(resolution, type_result, SymbolKind.PROTOCOL, "Named")

    checker = ConformanceChecker(type_result.type_table, resolution.symbol_table)
    result = checker.check_conformance(concrete, protocol)

    assert not result.conforms
    assert result.missing_requirements == []
    assert any("has type i32, expected String" in error for error in result.errors)


def test_protocol_conformance_rejects_immutable_setter_requirement():
    source = """
protocol Named {
    var name: String { get set };
}

struct Box {
    let name: String;
}
"""
    resolution, type_result = check_source(source)
    concrete = find_type_id(resolution, type_result, SymbolKind.STRUCT, "Box")
    protocol = find_type_id(resolution, type_result, SymbolKind.PROTOCOL, "Named")

    checker = ConformanceChecker(type_result.type_table, resolution.symbol_table)
    result = checker.check_conformance(concrete, protocol)

    assert not result.conforms
    assert result.missing_requirements == []
    assert any("must be mutable" in error for error in result.errors)


def test_any_protocol_type_resolves_to_existential():
    source = """
protocol Printable {
    def print() -> Void;
}

def use(p: any Printable) -> Void {
}
"""
    _, type_result = check_source(source)

    existential_types = [
        type_info
        for type_info in type_result.type_table.types.values()
        if type_info.kind.name == "EXISTENTIAL"
    ]
    assert len(existential_types) == 1


def test_conforming_value_assigns_to_existential():
    source = """
protocol Printable {
    def print() -> Void;
}

struct Box {
    def print() -> Void {
    }
}

def use() -> Void {
    let p: any Printable = Box {};
}
"""
    program = parse(source)
    resolution = resolve(program)
    assert not resolution.has_errors(), resolution.errors
    type_result = typecheck(program, resolution)

    assert not type_result.has_errors(), type_result.errors


def test_nonconforming_value_cannot_assign_to_existential():
    source = """
protocol Printable {
    def print() -> Void;
}

struct Box {
}

def use() -> Void {
    let p: any Printable = Box {};
}
"""
    program = parse(source)
    resolution = resolve(program)
    assert not resolution.has_errors(), resolution.errors
    type_result = typecheck(program, resolution)

    assert type_result.has_errors()
    assert any(
        "missing requirements: print" in error.message
        for error in type_result.errors
    )
