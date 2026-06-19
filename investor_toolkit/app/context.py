from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping


@dataclass(slots=True)
class AppContext:
    """Resolved runtime paths and provider settings shared by adapters."""

    workspace_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    research_root: Path | None = None
    portfolio_dir: Path = Path("portfolio")
    assumptions_dir: Path = Path("assumptions")
    valuations_dir: Path = Path("valuations")
    today: date = field(default_factory=date.today)
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).resolve()
        self.research_root = self.resolve_path(
            self.research_root or self.env.get("RESEARCH_HOME") or "research"
        )
        self.portfolio_dir = self.resolve_path(self.portfolio_dir)
        self.assumptions_dir = self.resolve_path(self.assumptions_dir)
        self.valuations_dir = self.resolve_path(self.valuations_dir)

    @classmethod
    def from_env(
        cls,
        cwd: str | Path | None = None,
        research_root: str | Path | None = None,
        portfolio_dir: str | Path | None = None,
        assumptions_dir: str | Path | None = None,
        valuations_dir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "AppContext":
        resolved_env = dict(os.environ if env is None else env)
        return cls(
            workspace_root=Path(cwd or Path.cwd()).resolve(),
            research_root=Path(research_root) if research_root is not None else None,
            portfolio_dir=Path(portfolio_dir) if portfolio_dir is not None else Path("portfolio"),
            assumptions_dir=Path(assumptions_dir) if assumptions_dir is not None else Path("assumptions"),
            valuations_dir=Path(valuations_dir) if valuations_dir is not None else Path("valuations"),
            env=resolved_env,
        )

    def resolve_path(self, path: str | Path) -> Path:
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = self.workspace_root / resolved
        return resolved.resolve()

    def relative_or_absolute(self, path: str | Path) -> str:
        resolved = self.resolve_path(path)
        try:
            return resolved.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return str(resolved)

    @property
    def sec_user_agent(self) -> str:
        return str(self.env.get("SEC_USER_AGENT", "")).strip()

    def require_sec_user_agent(self) -> None:
        user_agent = self.sec_user_agent
        if user_agent and "set sec_user_agent" not in user_agent.lower():
            return
        raise ValueError(
            "SEC_USER_AGENT is required for online quickstart or research. "
            'Set it first, for example: $env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"'
        )
