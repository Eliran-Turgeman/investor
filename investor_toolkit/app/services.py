from __future__ import annotations

from .agent_harness_service import AgentHarnessService
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .discovery_service import DiscoveryService
from .portfolio_service import PortfolioService
from .profile_service import ProfileService
from .research_service import ResearchService
from .valuation_service import ValuationService


class InvestorApplication:
    def __init__(self, context: AppContext | None = None) -> None:
        self.context = context or AppContext.from_env()
        self.artifacts = ArtifactCatalog(self.context)
        self.research = ResearchService(self.context)
        self.valuation = ValuationService(self.context)
        self.portfolio = PortfolioService(self.context)
        self.profile = ProfileService(self.context)
        self.discovery = DiscoveryService(self.context)
        self.agents = AgentHarnessService(self.context)
