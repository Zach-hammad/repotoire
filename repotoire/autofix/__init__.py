"""Auto-fix functionality for Repotoire.

This module provides AI-powered automatic code fixes with human-in-the-loop approval.
Supports multiple programming languages including Python, TypeScript, Java, and Go.
Also includes template-based fixes for deterministic, fast code transformations.
"""

from repotoire.autofix.applicator import FixApplicator
from repotoire.autofix.best_of_n import (
    BestOfNConfig,
    BestOfNGenerator,
    BestOfNNotAvailableError,
    BestOfNResult,
    BestOfNUsageLimitError,
    generate_best_of_n,
)
from repotoire.autofix.engine import AutoFixEngine
from repotoire.autofix.entitlements import (
    TIER_BEST_OF_N_CONFIG,
    BestOfNEntitlement,
    BestOfNTierConfig,
    FeatureAccess,
    get_customer_entitlement,
    get_entitlement_sync,
    get_tier_config,
)
from repotoire.autofix.languages import (
    GoHandler,
    JavaHandler,
    LanguageHandler,
    PythonHandler,
    TypeScriptHandler,
    get_handler,
    get_handler_for_language,
    supported_extensions,
)
from repotoire.autofix.learning import (
    AdaptiveConfidence,
    DecisionStore,
    FixDecision,
    LearningStats,
    RejectionPattern,
    RejectionReason,
    UserDecision,
    create_decision_id,
)
from repotoire.autofix.models import (
    CodeChange,
    Evidence,
    FixBatch,
    FixConfidence,
    FixContext,
    FixProposal,
    FixStatus,
    FixType,
)
from repotoire.autofix.reviewer import InteractiveReviewer
from repotoire.autofix.scorer import (
    DimensionScore,
    FixScorer,
    RankedFix,
    ScoringConfig,
    ScoringDimension,
    VerificationResult,
    select_best_fix,
)
from repotoire.autofix.style import (
    StyleAnalyzer,
    StyleEnforcer,
    StyleProfile,
    StyleRule,
    classify_naming,
)
from repotoire.autofix.templates import (
    DEFAULT_TEMPLATE_DIRS,
    FixTemplate,
    PatternType,
    TemplateEvidence,
    TemplateFile,
    TemplateLoadError,
    TemplateMatch,
    TemplateRegistry,
    get_registry,
    reset_registry,
)
from repotoire.autofix.verifier import (
    ParallelVerifier,
    VerificationConfig,
    VerificationTask,
    verify_fixes_parallel,
)

__all__ = [
    # Core auto-fix
    "AutoFixEngine",
    "InteractiveReviewer",
    "FixApplicator",
    # Models
    "FixProposal",
    "FixContext",
    "CodeChange",
    "FixType",
    "FixConfidence",
    "FixStatus",
    "FixBatch",
    # Language handlers
    "LanguageHandler",
    "PythonHandler",
    "TypeScriptHandler",
    "JavaHandler",
    "GoHandler",
    "get_handler",
    "get_handler_for_language",
    "supported_extensions",
    # Templates
    "FixTemplate",
    "PatternType",
    "TemplateEvidence",
    "TemplateFile",
    "TemplateMatch",
    "TemplateRegistry",
    "TemplateLoadError",
    "get_registry",
    "reset_registry",
    "DEFAULT_TEMPLATE_DIRS",
    # Style analysis
    "StyleAnalyzer",
    "StyleEnforcer",
    "StyleProfile",
    "StyleRule",
    "classify_naming",
    # Learning feedback
    "UserDecision",
    "RejectionReason",
    "FixDecision",
    "LearningStats",
    "RejectionPattern",
    "DecisionStore",
    "AdaptiveConfidence",
    "create_decision_id",
    # Best-of-N entitlements
    "FeatureAccess",
    "BestOfNEntitlement",
    "BestOfNTierConfig",
    "TIER_BEST_OF_N_CONFIG",
    "get_customer_entitlement",
    "get_entitlement_sync",
    "get_tier_config",
    # Best-of-N generation
    "BestOfNConfig",
    "BestOfNGenerator",
    "BestOfNResult",
    "BestOfNNotAvailableError",
    "BestOfNUsageLimitError",
    "generate_best_of_n",
    # Scoring and ranking
    "FixScorer",
    "ScoringConfig",
    "ScoringDimension",
    "VerificationResult",
    "DimensionScore",
    "RankedFix",
    "select_best_fix",
    # Parallel verification
    "ParallelVerifier",
    "VerificationConfig",
    "VerificationTask",
    "verify_fixes_parallel",
]
