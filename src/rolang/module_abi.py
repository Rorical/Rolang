"""Stable native identities and descriptor registration for separate modules."""
from __future__ import annotations

import dataclasses
import hashlib
import json

from llvmlite import binding as llvm, ir

from .symbols import SymbolId
from .types import TypeId


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def symbol_key(symbol_id, symbols, types):
    origin = symbols.specialization_origin.get(symbol_id)
    if origin:
        original, args = origin
        return symbol_key(original, symbols, types) + '<' + ','.join(type_key(t, symbols, types) for t in args) + '>'
    symbol = symbols.get_symbol(symbol_id)
    if symbol and symbol.decl_node is not None:
        key = getattr(symbol.decl_node, '_abi_key', None)
        if key:
            return key
    synthetic = getattr(symbols, 'module_type_keys', {}).get(symbol_id)
    if synthetic:
        return synthetic
    if symbol and symbol.name in symbols.builtins:
        return 'builtin:' + symbol.name
    raise ValueError(f'No stable module identity for symbol {symbol_id}')


def type_key(type_id, symbols, types):
    info = types.get_type(type_id)
    if info is None:
        raise ValueError(f'Unknown module ABI type {type_id}')

    def encode(value):
        if isinstance(value, TypeId):
            return type_key(value, symbols, types)
        if isinstance(value, SymbolId):
            return symbol_key(value, symbols, types)
        if dataclasses.is_dataclass(value):
            return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
        if isinstance(value, (tuple, list)):
            return [encode(v) for v in value]
        if hasattr(value, 'name'):
            return value.name
        return value

    return info.kind.name + ':' + json.dumps(encode(info.data), sort_keys=True, separators=(',', ':'))


def mark_declarations(node, module_key, source_hash, path='root'):
    """Declaration identities are independent of resolver traversal order."""
    from .ast import Node
    if isinstance(node, Node):
        node._abi_key = f'{module_key}@{source_hash}:{path}'
        node._abi_module = module_key
        for f in dataclasses.fields(node):
            if f.name == 'span':
                continue
            mark_declarations(getattr(node, f.name), module_key, source_hash, path + '.' + f.name)
    elif isinstance(node, (tuple, list)):
        for i, item in enumerate(node):
            mark_declarations(item, module_key, source_hash, path + f'[{i}]')


def symbol_owner(symbol_id, symbols):
    while symbol_id in symbols.specialization_origin:
        symbol_id = symbols.specialization_origin[symbol_id][0]
    symbol = symbols.get_symbol(symbol_id)
    return getattr(symbol.decl_node, '_abi_module', None) if symbol else None


def register_descriptors(module, cache, descriptors, fields, descriptor_ids):
    """Register private tables through an LLVM global constructor."""
    ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    keys = []
    for index, did in enumerate(descriptor_ids):
        data = cache._descriptor_keys[did].encode() + b'\0'
        var = ir.GlobalVariable(module, ir.ArrayType(ir.IntType(8), len(data)), name=f'__rl_type_key_{index}')
        var.linkage = 'private'
        var.global_constant = True
        var.initializer = ir.Constant(var.type.pointee, bytearray(data))
        keys.append(var.bitcast(ptr))
    key_array = ir.GlobalVariable(module, ir.ArrayType(ptr, len(keys)), name='__rl_type_keys')
    key_array.linkage = 'private'
    key_array.global_constant = True
    key_array.initializer = ir.Constant(key_array.type.pointee, keys)
    register = ir.Function(module, ir.FunctionType(ir.VoidType(), [ptr, i32, ptr, i32, ptr]), name='rt_register_module_types')
    ctor = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name='__rl_register_types')
    ctor.linkage = 'internal'
    b = ir.IRBuilder(ctor.append_basic_block('entry'))
    b.call(register, [descriptors.bitcast(ptr), ir.Constant(i32, len(keys)),
                     fields.bitcast(ptr) if fields else ir.Constant(ptr, None),
                     module.globals['RT_TYPE_FIELD_DESCRIPTOR_COUNT'].initializer,
                     key_array.bitcast(ptr)])
    b.ret_void()
    ct = ir.LiteralStructType([i32, ctor.type, ptr])
    constructors = ir.GlobalVariable(module, ir.ArrayType(ct, 1), name='llvm.global_ctors')
    constructors.linkage = 'appending'
    constructors.initializer = ir.Constant(constructors.type.pointee, [ir.Constant(ct, [ir.Constant(i32, 65535), ctor, ir.Constant(ptr, None)])])
    for name in ('RT_TYPE_DESCRIPTORS', 'RT_TYPE_DESCRIPTOR_COUNT', 'RT_TYPE_FIELD_DESCRIPTORS', 'RT_TYPE_FIELD_DESCRIPTOR_COUNT'):
        if name in module.globals:
            module.globals[name].linkage = 'internal'


def finish_module(module, program, symbols, types, owner):
    """Give definitions stable names and omit imported non-template bodies."""
    native = llvm.parse_assembly(str(module))
    by_name = {f.name: f for f in program.functions}
    remove = set()
    for function in native.functions:
        if function.is_declaration or function.linkage == llvm.Linkage.internal:
            continue
        mir = by_name.get(function.name)
        suffix = ''
        if mir and mir.symbol_id is None and function.name.endswith('_resume'):
            mir = by_name.get(function.name[:-7])
            suffix = ':resume'
        if mir is None or mir.symbol_id is None:
            function.linkage = llvm.Linkage.internal
            continue
        sid = mir.symbol_id
        origin = symbol_owner(sid, symbols)
        template = bool(symbols.specialization_origin.get(sid, (None, ()))[1])
        if function.name != '__rolang_user_main':
            function.name = '__rl_' + digest(symbol_key(sid, symbols, types) + suffix)
        if origin != owner and not template and not (origin or '').startswith('std:'):
            remove.add(function.name)
        elif template or (origin or '').startswith('std:'):
            function.linkage = llvm.Linkage.weak_odr
    # Witness tables are immutable and local to their translation unit.
    for global_value in native.global_variables:
        if global_value.name != 'llvm.global_ctors' and not global_value.is_declaration:
            global_value.linkage = llvm.Linkage.internal
    text = str(native)
    for function in native.functions:
        if function.name not in remove:
            continue
        args = ', '.join(str(arg.type) for arg in function.arguments)
        return_type = str(function.global_value_type.get_function_return())
        declaration = f'declare {return_type} @{function.name}({args})\n'
        text = text.replace(str(function), declaration)
    native.close()
    result = llvm.parse_assembly(text)
    result.verify()
    return result
