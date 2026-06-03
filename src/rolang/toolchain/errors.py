"""Toolchain-specific error types."""


class ToolchainError(Exception):
    """Base class for all Rolang toolchain errors."""


class ManifestError(ToolchainError):
    """Error reading or validating rolang.toml."""


class DependencyError(ToolchainError):
    """Error resolving or fetching a dependency."""


class BuildError(ToolchainError):
    """Error during the build process."""


class WorkspaceError(ToolchainError):
    """Error in workspace configuration."""
