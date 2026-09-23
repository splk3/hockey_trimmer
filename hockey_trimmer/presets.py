"""
Data-driven scoreboard preset loading and validation.
"""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import yaml

    HAS_YAML = True
except ImportError:
    yaml = None
    HAS_YAML = False


ROI = Tuple[float, float, float, float]


class PresetError(ValueError):
    """Raised when a scoreboard preset cannot be loaded or validated."""


@dataclass
class ScoreboardPreset:
    """Validated scoreboard layout, detection, and OCR settings."""

    name: str
    full_roi: ROI
    period_roi: ROI
    clock_roi: ROI
    score_roi: ROI
    detection: Dict[str, Any] = field(default_factory=dict)
    ocr: Dict[str, Any] = field(default_factory=dict)
    candidate_rois: List[ROI] = field(default_factory=list)

    def copy(self) -> "ScoreboardPreset":
        return ScoreboardPreset(
            name=self.name,
            full_roi=self.full_roi,
            period_roi=self.period_roi,
            clock_roi=self.clock_roi,
            score_roi=self.score_roi,
            detection=deepcopy(self.detection),
            ocr=deepcopy(self.ocr),
            candidate_rois=list(self.candidate_rois),
        )


BUILTIN_PRESET_NAMES = ("blackbear", "top_left", "top_center")


BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "blackbear": {
        "name": "blackbear",
        "layout": {
            "full_roi": [0.045, 0.020, 0.255, 0.140],
            "period_roi": [0.34, 0.02, 0.65, 0.22],
            "clock_roi": [0.29, 0.56, 0.70, 0.90],
            "score_roi": [0.30, 0.20, 0.70, 0.58],
        },
        "detection": {
            "mode": "blackbear",
            "white_threshold": 220,
            "white_min_ratio": 0.08,
            "white_conf_ratio": 0.15,
            "blue_min_ratio": 0.005,
            "blue_conf_ratio": 0.02,
            "blue_min_b": 160,
            "blue_max_r": 70,
            "blue_max_g": 160,
        },
        "ocr": {
            "clock_invert_if_dark": False,
            "period_invert_if_dark": True,
            "score_invert_if_dark": True,
            "clock_slot_centers": [35, 45, 61, 71],
            "clock_slot_tolerance": 8,
        },
        "candidate_rois": [
            [0.045, 0.020, 0.255, 0.140],
            [0.02, 0.02, 0.30, 0.22],
            [0.35, 0.02, 0.65, 0.18],
            [0.70, 0.02, 0.98, 0.22],
            [0.02, 0.75, 0.30, 0.98],
            [0.35, 0.75, 0.65, 0.98],
            [0.70, 0.75, 0.98, 0.98],
        ],
    },
    "top_left": {
        "name": "top_left",
        "layout": {
            "full_roi": [0.02, 0.02, 0.30, 0.22],
            "period_roi": [0.30, 0.0, 0.70, 0.25],
            "clock_roi": [0.25, 0.50, 0.75, 0.90],
            "score_roi": [0.25, 0.20, 0.75, 0.55],
        },
        "detection": {
            "mode": "generic",
            "white_threshold": 220,
            "white_min_ratio": 0.10,
            "edge_threshold": 80,
            "edge_min_ratio": 0.05,
            "edge_conf_scale": 10,
        },
        "ocr": {
            "clock_invert_if_dark": False,
            "period_invert_if_dark": True,
            "score_invert_if_dark": True,
            "clock_slot_centers": [35, 45, 61, 71],
            "clock_slot_tolerance": 8,
        },
        "candidate_rois": [
            [0.02, 0.02, 0.30, 0.22],
            [0.35, 0.02, 0.65, 0.18],
            [0.70, 0.02, 0.98, 0.22],
            [0.02, 0.75, 0.30, 0.98],
            [0.35, 0.75, 0.65, 0.98],
            [0.70, 0.75, 0.98, 0.98],
        ],
    },
    "top_center": {
        "name": "top_center",
        "layout": {
            "full_roi": [0.35, 0.02, 0.65, 0.18],
            "period_roi": [0.05, 0.10, 0.30, 0.90],
            "clock_roi": [0.35, 0.10, 0.65, 0.90],
            "score_roi": [0.70, 0.10, 0.95, 0.90],
        },
        "detection": {
            "mode": "generic",
            "white_threshold": 220,
            "white_min_ratio": 0.10,
            "edge_threshold": 80,
            "edge_min_ratio": 0.05,
            "edge_conf_scale": 10,
        },
        "ocr": {
            "clock_invert_if_dark": False,
            "period_invert_if_dark": True,
            "score_invert_if_dark": True,
            "clock_slot_centers": [35, 45, 61, 71],
            "clock_slot_tolerance": 8,
        },
        "candidate_rois": [
            [0.35, 0.02, 0.65, 0.18],
            [0.02, 0.02, 0.30, 0.22],
            [0.70, 0.02, 0.98, 0.22],
            [0.02, 0.75, 0.30, 0.98],
            [0.35, 0.75, 0.65, 0.98],
            [0.70, 0.75, 0.98, 0.98],
        ],
    },
}


def _as_mapping(data: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise PresetError(f"Preset {source} must contain a JSON/YAML object.")
    return data


def _parse_roi(value: Any, field_name: str) -> ROI:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PresetError(f"{field_name} must be a list of four numbers.")
    try:
        x1, y1, x2, y2 = tuple(float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise PresetError(f"{field_name} must contain only numbers.") from exc

    if x2 <= x1 or y2 <= y1:
        raise PresetError(f"{field_name} must be ordered as x1,y1,x2,y2.")
    if min(x1, y1, x2, y2) < 0.0:
        raise PresetError(f"{field_name} values must be non-negative.")

    values = (x1, y1, x2, y2)
    is_normalized = all(v <= 1.0 for v in values)
    is_pixel = max(values) > 1.0 and all(float(v).is_integer() for v in values)
    if not is_normalized and not is_pixel:
        raise PresetError(
            f"{field_name} must use consistent units: all normalized values "
            "in 0..1 or all integer pixel values."
        )
    return (x1, y1, x2, y2)


def parse_roi_string(value: str, field_name: str = "ROI") -> ROI:
    """Parse a comma-separated ROI string."""
    try:
        parts = [float(p.strip()) for p in value.split(",")]
    except ValueError as exc:
        raise PresetError(f"{field_name} must be 4 comma-separated numbers.") from exc
    return _parse_roi(parts, field_name)


def _load_file(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise PresetError(f"Preset file not found: {path}")
    suffix = path.suffix.lower()
    try:
        with path.open("r", encoding="utf-8") as fh:
            if suffix == ".json":
                return _as_mapping(json.load(fh), str(path))
            if suffix in (".yaml", ".yml"):
                if not HAS_YAML:
                    raise PresetError(
                        "YAML presets require PyYAML. Install dependencies from requirements.txt."
                    )
                return _as_mapping(yaml.safe_load(fh), str(path))
    except json.JSONDecodeError as exc:
        raise PresetError(f"Invalid JSON preset {path}: {exc}") from exc
    except OSError as exc:
        raise PresetError(f"Could not read preset file {path}: {exc}") from exc
    raise PresetError("Preset files must end in .json, .yaml, or .yml.")


def _load_builtin_data(name: str) -> Mapping[str, Any]:
    normalized = name.lower()
    if normalized not in BUILTIN_PRESET_NAMES:
        choices = ", ".join(BUILTIN_PRESET_NAMES)
        raise PresetError(f"Unknown preset '{name}'. Built-in presets: {choices}.")
    return deepcopy(BUILTIN_PRESETS[normalized])


def validate_preset(
    data: Mapping[str, Any], source: str = "preset"
) -> ScoreboardPreset:
    """Validate raw preset data and return a typed config."""
    name = str(data.get("name") or source).strip().lower()
    if not name:
        raise PresetError("Preset name cannot be empty.")

    layout = _as_mapping(data.get("layout", {}), f"{source}.layout")
    required = ("full_roi", "period_roi", "clock_roi", "score_roi")
    missing = [key for key in required if key not in layout]
    if missing:
        raise PresetError(
            f"Preset {source} missing layout fields: {', '.join(missing)}"
        )

    detection = dict(_as_mapping(data.get("detection", {}), f"{source}.detection"))
    mode = detection.get("mode", "generic")
    if mode not in ("blackbear", "generic"):
        raise PresetError("detection.mode must be 'blackbear' or 'generic'.")
    detection["mode"] = mode

    ocr = dict(_as_mapping(data.get("ocr", {}), f"{source}.ocr"))
    candidate_rois = [
        _parse_roi(roi, f"candidate_rois[{idx}]")
        for idx, roi in enumerate(data.get("candidate_rois", []))
    ]

    return ScoreboardPreset(
        name=name,
        full_roi=_parse_roi(layout["full_roi"], "layout.full_roi"),
        period_roi=_parse_roi(layout["period_roi"], "layout.period_roi"),
        clock_roi=_parse_roi(layout["clock_roi"], "layout.clock_roi"),
        score_roi=_parse_roi(layout["score_roi"], "layout.score_roi"),
        detection=detection,
        ocr=ocr,
        candidate_rois=candidate_rois,
    )


def load_preset(
    name: str = "blackbear", preset_file: Optional[str] = None
) -> ScoreboardPreset:
    """Load a built-in preset by name or a user-supplied JSON/YAML preset file."""
    if preset_file:
        return validate_preset(_load_file(Path(preset_file)), source=preset_file)
    return validate_preset(_load_builtin_data(name), source=name)


def preset_to_dict(preset: ScoreboardPreset) -> Dict[str, Any]:
    """Convert a preset to a serializable dictionary."""
    data = {
        "name": preset.name,
        "layout": {
            "full_roi": list(preset.full_roi),
            "period_roi": list(preset.period_roi),
            "clock_roi": list(preset.clock_roi),
            "score_roi": list(preset.score_roi),
        },
        "detection": deepcopy(preset.detection),
        "ocr": deepcopy(preset.ocr),
    }
    if preset.candidate_rois:
        data["candidate_rois"] = [list(roi) for roi in preset.candidate_rois]
    return data


def save_preset(preset: ScoreboardPreset, output_path: str) -> None:
    """Save a preset to JSON or YAML based on the output extension."""
    path = Path(output_path)
    data = preset_to_dict(preset)
    suffix = path.suffix.lower()
    try:
        with path.open("w", encoding="utf-8") as fh:
            if suffix == ".json":
                json.dump(data, fh, indent=2)
                fh.write("\n")
            elif suffix in (".yaml", ".yml"):
                if not HAS_YAML:
                    raise PresetError(
                        "YAML presets require PyYAML. Install dependencies from requirements.txt."
                    )
                yaml.safe_dump(data, fh, sort_keys=False)
            else:
                raise PresetError("Preset output must end in .json, .yaml, or .yml.")
    except OSError as exc:
        raise PresetError(f"Could not write preset file {path}: {exc}") from exc
