"""ORM models。Alembic 透過 `Base.metadata` 比對 schema，新增 model 時須在此匯入。"""

from app.models.ai import AIAnalysisRun, EventRelation, RiskAssessment
from app.models.alert import Alert, AlertDelivery
from app.models.anonymous_report import AnonymousReport, ReportReceipt
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.event import Event, EventEvidence, EventMerge, Location, LocationResolution
from app.models.publication import Publication, PublicationTakedown, PublicationVersion
from app.models.report import EventReport, ReportCauseDecision
from app.models.response_action import ResponseAction, ResponseActionTransition
from app.models.scenario import ScenarioRun
from app.models.series import (
    EventSeries,
    EventSeriesMember,
    EventSeriesRemoval,
    SeriesAnalysis,
    SeriesCauseDecision,
)
from app.models.source import (
    IngestionDeadLetter,
    IngestionRun,
    Source,
    SourceItem,
    SourceItemVersion,
)
from app.models.user import User, UserSession

__all__ = [
    "AIAnalysisRun",
    "Alert",
    "AlertDelivery",
    "AnonymousReport",
    "AuditLog",
    "Base",
    "Event",
    "EventEvidence",
    "EventMerge",
    "EventRelation",
    "EventReport",
    "EventSeries",
    "EventSeriesMember",
    "EventSeriesRemoval",
    "IngestionDeadLetter",
    "IngestionRun",
    "Location",
    "LocationResolution",
    "Publication",
    "PublicationTakedown",
    "PublicationVersion",
    "ReportCauseDecision",
    "ReportReceipt",
    "ResponseAction",
    "ResponseActionTransition",
    "RiskAssessment",
    "ScenarioRun",
    "SeriesAnalysis",
    "SeriesCauseDecision",
    "Source",
    "SourceItem",
    "SourceItemVersion",
    "User",
    "UserSession",
]
