from __future__ import annotations

from pathlib import Path

from ..workflow import ResearchWorkflow, WorkflowResult
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .schemas import OperationResult, warning_from_text


class ResearchService:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.catalog = ArtifactCatalog(context)

    def start(self, ticker: str, offline: bool = False, refresh: bool = False) -> OperationResult:
        if not offline:
            self.context.require_sec_user_agent()
        workflow = self._workflow()
        result = workflow.start(ticker, offline=offline, refresh=refresh)
        return self._wrap_workflow_result(
            "research.start",
            result,
            next_actions=[] if not offline else ["Run online research ingest when provider access is available."],
        )

    def quickstart(self, ticker: str, offline: bool = False, refresh: bool = False) -> OperationResult:
        if not offline:
            self.context.require_sec_user_agent()
        workflow = self._workflow()
        result = workflow.start(ticker, offline=offline, refresh=refresh)
        next_actions = [
            f"Refresh local data for {result.ticker}, then summarize latest filing risks with citations.",
            f"Build a business quality memo for {result.ticker} from local filings and metrics.",
            f"Draft a bear case for {result.ticker} and separate evidence from interpretation.",
        ]
        if offline:
            next_actions.insert(0, "Offline mode only created the local workspace; run online ingest to fetch data.")
        return self._wrap_workflow_result("research.quickstart", result, next_actions=next_actions)

    def ingest(self, ticker: str, offline: bool = False, refresh: bool = False) -> OperationResult:
        if not offline:
            self.context.require_sec_user_agent()
        workflow = self._workflow()
        result = workflow.ingest(ticker, offline=offline, refresh=refresh)
        return self._wrap_workflow_result("research.ingest", result)

    def metrics(self, ticker: str) -> OperationResult:
        result = self._workflow().metrics(ticker)
        return self._wrap_workflow_result("research.metrics", result)

    def _workflow(self) -> ResearchWorkflow:
        return ResearchWorkflow(
            self.context.workspace_root,
            research_root=self.context.research_root,
        )

    def _wrap_workflow_result(
        self,
        operation: str,
        result: WorkflowResult,
        next_actions: list[str] | None = None,
    ) -> OperationResult:
        artifacts = [ref for ref in self.catalog.company_artifacts(result.ticker) if ref.exists]
        source_paths = [str(Path(ref.path)) for ref in artifacts]
        return OperationResult(
            operation=operation,
            status="ok",
            data={
                "ticker": result.ticker,
                "companyDir": str(result.company_dir),
                "messages": result.messages,
            },
            warnings=[warning_from_text(message) for message in result.warnings],
            sourcePaths=source_paths,
            artifacts=artifacts,
            nextActions=next_actions or [],
        )

