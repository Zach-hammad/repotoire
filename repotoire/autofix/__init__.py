"""Auto-fix functionality for Repotoire.

This module provides AI-powered automatic code fixes with human-in-the-loop approval.
Supports multiple programming languages including Python, TypeScript, Java, and Go.
Also includes template-based fixes for deterministic, fast code transformations.
"""

from repotoire.autofix.engine import AutoFixEngine
from repotoire.autofix.reviewer import InteractiveReviewer
from repotoire.autofix.applicator import FixApplicator
from repotoire.autofix.models import (
    FixProposal,
    FixContext,
    CodeChange,
    Evidence,
    FixType,
    FixConfidence,
    FixStatus,
    FixBatch,
)
from repotoire.autofix.languages import (
    LanguageHandler,
    PythonHandler,
    TypeScriptHandler,
    JavaHandler,
    GoHandler,
    get_handler,
    get_handler_for_language,
    supported_extensions,
)
from repotoire.autofix.templates import (
    FixTemplate,
    PatternType,
    TemplateEvidence,
    TemplateFile,
    TemplateMatch,
    TemplateRegistry,
    TemplateLoadError,
    get_registry,
    reset_registry,
    DEFAULT_TEMPLATE_DIRS,
)
from repotoire.autofix.style import (
    StyleAnalyzer,
    StyleEnforcer,
    StyleProfile,
    StyleRule,
    classify_naming,
)
from repotoire.autofix.learning import (
    UserDecision,
    RejectionReason,
    FixDecision,
    LearningStats,
    RejectionPattern,
    DecisionStore,
    AdaptiveConfidence,
    create_decision_id,
)
from repotoire.autofix.entitlements import (
    FeatureAccess,
    BestOfNEntitlement,
    BestOfNTierConfig,
    TIER_BEST_OF_N_CONFIG,
    get_customer_entitlement,
    get_entitlement_sync,
    get_tier_config,
)
from repotoire.autofix.best_of_n import (
    BestOfNConfig,
    BestOfNGenerator,
    BestOfNResult,
    BestOfNNotAvailableError,
    BestOfNUsageLimitError,
    generate_best_of_n,
)
from repotoire.autofix.scorer import (
    FixScorer,
    ScoringConfig,
    ScoringDimension,
    VerificationResult,
    DimensionScore,
    RankedFix,
    select_best_fix,
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
