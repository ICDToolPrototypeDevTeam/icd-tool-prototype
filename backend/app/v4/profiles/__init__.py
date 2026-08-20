"""Controller profile registry.

Each profile is a directory under profiles/ containing config.yaml.
The registry scans these directories at startup and provides lookup
by profile_id.
"""
from __future__ import annotations

from pathlib import Path

from app.v4.profiles.base import (
    ControllerProfile,
    ProfileLoadError,
    load_profile_from_yaml,
)


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
