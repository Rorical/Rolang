"""
Witness Table Builder.

Generates witness tables for protocol conformances at compile time.
Witness tables map protocol requirements to concrete implementations.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .types import TypeId, TypeTable, TypeKind, ProtocolTypeData
from .symbols import SymbolId, SymbolTable
from .conformance import ConformanceChecker, ConformanceResult, WitnessEntry


@dataclass
class WitnessTable:
    """
    A witness table for a (ConcreteType, Protocol) pair.

    Contains function pointers/offsets for each protocol requirement.
    """
    concrete_type: TypeId
    protocol_type: TypeId
    entries: List[WitnessEntry]
    # Unique name for the witness table global variable
    global_name: str


class WitnessTableBuilder:
    """
    Builds witness tables for protocol conformances.

    Witness tables are created lazily when needed and cached.
    """

    def __init__(
        self,
        type_table: TypeTable,
        symbol_table: SymbolTable,
        conformance_checker: Optional[ConformanceChecker] = None,
    ) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.conformance_checker = conformance_checker or ConformanceChecker(
            type_table, symbol_table
        )

        # Cache of built witness tables
        self._tables: Dict[Tuple[TypeId, TypeId], WitnessTable] = {}

        # Counter for generating unique table names
        self._table_counter = 0

    def get_or_create_table(
        self,
        concrete_type: TypeId,
        protocol_type: TypeId,
    ) -> Optional[WitnessTable]:
        """
        Get or create a witness table for the given type and protocol.

        Returns None if the type doesn't conform to the protocol.
        """
        key = (concrete_type, protocol_type)

        # Check cache
        if key in self._tables:
            return self._tables[key]

        # Check conformance
        result = self.conformance_checker.check_conformance(
            concrete_type, protocol_type
        )

        if not result.conforms:
            return None

        # Build the table
        table = self._build_table(concrete_type, protocol_type, result)
        self._tables[key] = table
        return table

    def _build_table(
        self,
        concrete_type: TypeId,
        protocol_type: TypeId,
        conformance: ConformanceResult,
    ) -> WitnessTable:
        """Build a witness table from conformance result."""
        # Generate unique name
        concrete_name = self._get_type_name(concrete_type)
        protocol_name = self._get_type_name(protocol_type)
        global_name = f"__witness_{concrete_name}_{protocol_name}_{self._table_counter}"
        self._table_counter += 1

        return WitnessTable(
            concrete_type=concrete_type,
            protocol_type=protocol_type,
            entries=conformance.witnesses,
            global_name=global_name,
        )

    def _get_type_name(self, type_id: TypeId) -> str:
        """Get a mangled name for a type."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return f"type{type_id.id}"

        if info.kind == TypeKind.STRUCT:
            from .types import StructTypeData
            data = info.data
            if isinstance(data, StructTypeData):
                sym = self.symbol_table.get_symbol(data.symbol_id)
                if sym:
                    return sym.name
        elif info.kind == TypeKind.ENUM:
            from .types import EnumTypeData
            data = info.data
            if isinstance(data, EnumTypeData):
                sym = self.symbol_table.get_symbol(data.symbol_id)
                if sym:
                    return sym.name
        elif info.kind == TypeKind.PROTOCOL:
            data = info.data
            if isinstance(data, ProtocolTypeData):
                sym = self.symbol_table.get_symbol(data.symbol_id)
                if sym:
                    return sym.name

        return f"type{type_id.id}"

    def get_all_tables(self) -> List[WitnessTable]:
        """Get all built witness tables."""
        return list(self._tables.values())

    def get_method_index(
        self,
        protocol_type: TypeId,
        method_name: str,
    ) -> Optional[int]:
        """
        Get the index of a method in the protocol's witness table layout.

        This is used to determine the offset when making vtable calls.
        """
        protocol_info = self.type_table.get_type(protocol_type)
        if protocol_info is None or protocol_info.kind != TypeKind.PROTOCOL:
            return None

        data = protocol_info.data
        if not isinstance(data, ProtocolTypeData):
            return None

        # Index is the position in func_requirements
        for i, req in enumerate(data.func_requirements):
            if req.name == method_name:
                return i

        # Also check property requirements (after functions)
        func_count = len(data.func_requirements)
        for i, req in enumerate(data.prop_requirements):
            if req.name == method_name:
                return func_count + i

        return None


@dataclass
class WitnessTableRegistry:
    """
    Global registry of all witness tables in a program.

    Used during code generation to emit witness table globals.
    """
    tables: List[WitnessTable] = field(default_factory=list)

    def add_table(self, table: WitnessTable) -> None:
        """Add a witness table to the registry."""
        # Avoid duplicates
        for existing in self.tables:
            if (existing.concrete_type == table.concrete_type and
                existing.protocol_type == table.protocol_type):
                return
        self.tables.append(table)

    def get_table(
        self,
        concrete_type: TypeId,
        protocol_type: TypeId,
    ) -> Optional[WitnessTable]:
        """Look up a witness table."""
        for table in self.tables:
            if (table.concrete_type == concrete_type and
                table.protocol_type == protocol_type):
                return table
        return None
