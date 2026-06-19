from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..utils import utc_now_iso


SCHEMA_VERSION = "1.0"


@dataclass(slots=True)
class OperationWarning:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class ArtifactReference:
    uri: str
    name: str
    path: str
    kind: str
    mimeType: str = "application/json"
    description: str = ""
    exists: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OperationResult:
    operation: str
    status: str = "ok"
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[OperationWarning] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sourcePaths: list[str] = field(default_factory=list)
    artifacts: list[ArtifactReference] = field(default_factory=list)
    nextActions: list[str] = field(default_factory=list)
    generatedAt: str = field(default_factory=utc_now_iso)
    schemaVersion: str = SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schemaVersion,
            "operation": self.operation,
            "status": self.status,
            "generatedAt": self.generatedAt,
            "data": self.data,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "errors": list(self.errors),
            "sourcePaths": list(self.sourcePaths),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "nextActions": list(self.nextActions),
        }


def warning_from_text(message: str, code: str = "WARNING") -> OperationWarning:
    return OperationWarning(code=code, message=message)


def path_text(path: str | Path) -> str:
    return str(Path(path))

