"""Workspace detection and multi-package build orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import WorkspaceError
from .manifest import Manifest


@dataclass
class Workspace:
    """
    A Rolang workspace — one or more packages sharing a root rolang.toml.

    If the root manifest has no [workspace] section this is treated as a
    trivial single-package workspace containing only the root manifest.
    """
    root: Path
    root_manifest: Manifest
    members: list[Manifest] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> "Workspace":
        root_manifest = Manifest.load(root)

        if root_manifest.workspace is None:
            # Single-package project
            return cls(root=root, root_manifest=root_manifest, members=[root_manifest])

        members: list[Manifest] = []
        for pattern in root_manifest.workspace.members:
            member_path = root / pattern
            if not member_path.exists():
                raise WorkspaceError(
                    f"Workspace member '{pattern}' not found at {member_path}"
                )
            members.append(Manifest.load(member_path))

        return cls(root=root, root_manifest=root_manifest, members=members)

    def find_member(self, name: str) -> Optional[Manifest]:
        """Return the member manifest whose package name matches *name*."""
        for m in self.members:
            if m.package and m.package.name == name:
                return m
        return None
