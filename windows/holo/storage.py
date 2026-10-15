# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Profile persistence in %APPDATA%\\Holo. Only feature vectors are stored, never audio."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .actions import ZoneAction
from .core import LAYOUTS, LabeledTap, MAX_PATTERN_TAPS, PASSIVE_FEATURE_NAMES

PROFILE_VERSION = 3


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    path = Path(base) / "Holo"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Profile:
    name: str = "My desk"
    description: str = ""
    input_device: Optional[str] = None
    sample_rate: float = 48000.0
    positives: list[LabeledTap] = field(default_factory=list)
    negatives: list[LabeledTap] = field(default_factory=list)
    zone_count: int = 4
    # actions[zone][pattern]: pattern 0 = single tap, 1 = double, 2 = triple
    actions: list[list[ZoneAction]] = field(default_factory=lambda: empty_actions(4))
    loo_accuracy: Optional[float] = None

    @property
    def layout(self):
        return LAYOUTS[self.zone_count]

    def resize_actions(self, zone_count: int) -> None:
        """Keeps existing zone actions where the zone still exists."""
        self.actions = (self.actions + empty_actions(zone_count))[:zone_count]

    def to_dict(self) -> dict:
        return {
            "version": PROFILE_VERSION,
            "name": self.name,
            "description": self.description,
            "input_device": self.input_device,
            "sample_rate": self.sample_rate,
            "positives": [t.to_dict() for t in self.positives],
            "negatives": [t.to_dict() for t in self.negatives],
            "zone_count": self.zone_count,
            "actions": [[a.to_dict() for a in zone] for zone in self.actions],
            "loo_accuracy": self.loo_accuracy,
        }

    @staticmethod
    def from_dict(d: dict) -> "Profile":
        version = d.get("version")
        if version not in (1, 2, PROFILE_VERSION):
            raise ValueError("Unsupported profile version.")
        positives = [LabeledTap.from_dict(t) for t in d.get("positives", [])]
        negatives = [LabeledTap.from_dict(t) for t in d.get("negatives", [])]
        if any(t.feature.names != PASSIVE_FEATURE_NAMES for t in positives + negatives):
            raise ValueError("Profile uses an incompatible feature schema; recalibrate.")
        zone_count = int(d.get("zone_count", 4))
        if version == 2 and zone_count == 3:
            # The 3-zone layout changed from (left, right rear, right front) to
            # (left, right, front): keep the actions, but the taps must be recalibrated.
            positives, negatives = [], []
        if zone_count not in LAYOUTS or any(t.zone is None or not 0 <= t.zone < zone_count
                                            for t in positives):
            raise ValueError("Profile zones are inconsistent; recalibrate.")
        raw = d.get("actions", [])
        if version == 1:  # one action per zone -> single-tap action
            raw = [[a] for a in raw]
        actions = [[ZoneAction.from_dict(a) for a in zone][:MAX_PATTERN_TAPS] for zone in raw]
        actions = [zone + [ZoneAction() for _ in range(MAX_PATTERN_TAPS - len(zone))] for zone in actions]
        actions = (actions + empty_actions(zone_count))[:zone_count]
        return Profile(
            name=d.get("name", "My desk"),
            description=d.get("description", ""),
            input_device=d.get("input_device"),
            sample_rate=float(d.get("sample_rate", 48000.0)),
            positives=positives,
            negatives=negatives,
            zone_count=zone_count,
            actions=actions,
            loo_accuracy=d.get("loo_accuracy"),
        )


def empty_actions(zone_count: int) -> list[list[ZoneAction]]:
    return [[ZoneAction() for _ in range(MAX_PATTERN_TAPS)] for _ in range(zone_count)]


def profile_path() -> Path:
    return data_dir() / "profile.json"


def load_profile() -> Optional[Profile]:
    path = profile_path()
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return Profile.from_dict(json.load(fh))


def save_profile(profile: Profile) -> None:
    path = profile_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(profile.to_dict(), fh, indent=1)
    os.replace(tmp, path)
