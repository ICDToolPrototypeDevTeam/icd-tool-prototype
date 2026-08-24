"""Controller profile registry.

Each profile is a directory under profiles/ containing config.yaml.
The registry scans these directories at startup and provides lookup
by profile_id.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from app.v4.profiles.base import (
    ControllerProfile,
    ProfileLoadError,
    load_profile_from_yaml,
)

if TYPE_CHECKING:
    from app.v4.models import HLROutput


class ProfileRegistry:
    """In-memory registry of loaded ControllerProfile objects."""

    def __init__(self) -> None:
        self._profiles: dict[str, ControllerProfile] = {}

    def load_all(self, profiles_dir: Path) -> None:
        """Scan profiles_dir for */config.yaml and load each."""
        if not profiles_dir.exists():
            raise ProfileLoadError(f"Profiles directory not found: {profiles_dir}")
        for child in sorted(profiles_dir.iterdir()):
            if not child.is_dir():
                continue
            config_path = child / "config.yaml"
            if not config_path.exists():
                continue
            profile = load_profile_from_yaml(config_path)
            self._profiles[profile.profile_id] = profile

    def get(self, profile_id: str) -> ControllerProfile:
        """Return profile; raises ProfileLoadError if missing."""
        return self.get_or_raise(profile_id)

    def get_or_raise(self, profile_id: str) -> ControllerProfile:
        """Return profile or raise ProfileLoadError with available ids."""
        if profile_id not in self._profiles:
            available = sorted(self._profiles.keys())
            raise ProfileLoadError(
                f"Unknown profile '{profile_id}'; available: {available}"
            )
        return self._profiles[profile_id]

    def list_ids(self) -> list[str]:
        return sorted(self._profiles.keys())


# Module-level singleton, populated at app startup by `init_registry()`.
_registry = ProfileRegistry()


def init_registry(profiles_dir: Path) -> ProfileRegistry:
    """Initialize the singleton registry from a profiles directory."""
    _registry.load_all(profiles_dir)
    return _registry


def get_registry() -> ProfileRegistry:
    """Return the module-level singleton registry."""
    return _registry


def apply_hlr_preprocess_hook(
    profile: ControllerProfile,
    hlr_out: "HLROutput",
) -> int:
    """Dispatch profile-specific HLR preprocessing if declared.

    No-op when ``profile.hlr_preprocess.enabled`` is False (default for
    profiles that don't declare the section — e.g. AMS, FGMC).

    When enabled, dynamically imports ``app.v4.profiles.<profile_id>.hooks``
    and calls ``preprocess_hlr_requirements(profile, hlr_out)``. The hook
    is expected to mutate ``hlr_out.requirements[i].content`` in-place and
    return the number of requirements whose content was rewritten.

    Returns the rewrite count (0 when no-op). A profile whose hooks module
    is missing or whose module lacks the expected function is silently
    skipped (returns 0) — this keeps the contract "profile declares
    preprocessing in YAML = hook is required" explicit without crashing
    profiles that haven't migrated yet.
    """
    if not profile.hlr_preprocess.enabled:
        return 0

    module_name = f"app.v4.profiles.{profile.profile_id}.hooks"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return 0

    fn = getattr(module, "preprocess_hlr_requirements", None)
    if fn is None:
        return 0

    result = fn(profile, hlr_out)
    return int(result) if result is not None else 0
