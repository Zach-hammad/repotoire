"""Organization detector settings model.

This module defines the DetectorSettings model for storing
detector threshold configurations at the organization level.
"""

from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .organization import Organization


class DetectorPreset(str, Enum):
    """Preset detector configuration profiles."""

    STRICT = "strict"  # Catch more issues, more false positives
    BALANCED = "balanced"  # Default balanced thresholds
    PERMISSIVE = "permissive"  # Fewer findings, only critical issues
    CUSTOM = "custom"  # User-defined thresholds


# Default thresholds for each preset
PRESET_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    DetectorPreset.STRICT.value: {
        # God Class - stricter thresholds
        "god_class_high_method_count": 15,
        "god_class_medium_method_count": 10,
        "god_class_high_complexity": 75,
        "god_class_medium_complexity": 40,
        "god_class_high_loc": 400,
        "god_class_medium_loc": 250,
        "god_class_high_lcom": 0.7,
        "god_class_medium_lcom": 0.5,
        # Feature Envy - stricter thresholds
        "feature_envy_threshold_ratio": 2.0,
        "feature_envy_min_external_uses": 10,
        # Radon - stricter thresholds
        "radon_complexity_threshold": 8,
        "radon_maintainability_threshold": 70,
        # Global settings
        "max_findings_per_detector": 150,
        "confidence_threshold": 0.6,
    },
    DetectorPreset.BALANCED.value: {
        # God Class - default thresholds
        "god_class_high_method_count": 20,
        "god_class_medium_method_count": 15,
        "god_class_high_complexity": 100,
        "god_class_medium_complexity": 50,
        "god_class_high_loc": 500,
        "god_class_medium_loc": 300,
        "god_class_high_lcom": 0.8,
        "god_class_medium_lcom": 0.6,
        # Feature Envy - default thresholds
        "feature_envy_threshold_ratio": 3.0,
        "feature_envy_min_external_uses": 15,
        # Radon - default thresholds
        "radon_complexity_threshold": 10,
        "radon_maintainability_threshold": 65,
        # Global settings
        "max_findings_per_detector": 100,
        "confidence_threshold": 0.7,
    },
    DetectorPreset.PERMISSIVE.value: {
        # God Class - permissive thresholds
        "god_class_high_method_count": 30,
        "god_class_medium_method_count": 25,
        "god_class_high_complexity": 150,
        "god_class_medium_complexity": 100,
        "god_class_high_loc": 750,
        "god_class_medium_loc": 500,
        "god_class_high_lcom": 0.9,
        "god_class_medium_lcom": 0.75,
        # Feature Envy - permissive thresholds
        "feature_envy_threshold_ratio": 5.0,
        "feature_envy_min_external_uses": 25,
        # Radon - permissive thresholds
        "radon_complexity_threshold": 15,
        "radon_maintainability_threshold": 55,
        # Global settings
        "max_findings_per_detector": 50,
        "confidence_threshold": 0.8,
    },
}


def get_default_thresholds() -> Dict[str, Any]:
    """Get default (balanced) detector thresholds."""
    return PRESET_THRESHOLDS[DetectorPreset.BALANCED.value].copy()


class DetectorSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Organization-level detector configuration settings.

    Stores custom detector thresholds for an organization. When an analysis
    runs, these settings override the default thresholds.

    Attributes:
        id: UUID primary key.
        organization_id: Foreign key to the organization.
        preset: Current preset profile (strict/balanced/permissive/custom).
        thresholds: JSON object with all threshold values.
        enabled_detectors: List of enabled detector names (null = all enabled).
        disabled_detectors: List of disabled detector names.
    """

    __tablename__ = "detector_settings"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    preset: Mapped[str] = mapped_column(
        String(20),
        default=DetectorPreset.BALANCED.value,
        nullable=False,
    )

    thresholds: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        default=get_default_thresholds,
        nullable=False,
    )

    # Optional: control which detectors are enabled/disabled
    enabled_detectors: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="List of enabled detector names. Null means all enabled.",
    )

    disabled_detectors: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
        comment="List of disabled detector names.",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="detector_settings",
    )

    def apply_preset(self, preset: DetectorPreset) -> None:
        """Apply a preset profile to the thresholds.

        Args:
            preset: The preset to apply.
        """
        if preset == DetectorPreset.CUSTOM:
            # Custom preset keeps current thresholds
            self.preset = preset.value
            return

        self.preset = preset.value
        self.thresholds = PRESET_THRESHOLDS[preset.value].copy()

    def get_threshold(self, key: str, default: Any = None) -> Any:
        """Get a specific threshold value.

        Args:
            key: The threshold key.
            default: Default value if not found.

        Returns:
            The threshold value or default.
        """
        return self.thresholds.get(key, default)

    def update_threshold(self, key: str, value: Any) -> None:
        """Update a specific threshold value.

        Also sets preset to CUSTOM if changed from a preset value.

        Args:
            key: The threshold key.
            value: The new value.
        """
        if self.preset != DetectorPreset.CUSTOM.value:
            preset_value = PRESET_THRESHOLDS.get(self.preset, {}).get(key)
            if preset_value != value:
                self.preset = DetectorPreset.CUSTOM.value

        # Create a new dict to trigger SQLAlchemy change detection
        new_thresholds = dict(self.thresholds)
        new_thresholds[key] = value
        self.thresholds = new_thresholds

    def is_detector_enabled(self, detector_name: str) -> bool:
        """Check if a detector is enabled.

        Args:
            detector_name: The detector class name.

        Returns:
            True if enabled, False if disabled.
        """
        # Check disabled list first
        if self.disabled_detectors and detector_name in self.disabled_detectors:
            return False

        # If enabled list is specified, check it
        if self.enabled_detectors is not None:
            return detector_name in self.enabled_detectors

        # Default: all enabled
        return True

    def __repr__(self) -> str:
        return f"<DetectorSettings org_id={self.organization_id} preset={self.preset}>"
