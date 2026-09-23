"""
Tests for data-driven scoreboard preset loading and validation.
"""

import json
import os
import tempfile
import unittest

from hockey_trimmer.presets import (
    HAS_YAML,
    PresetError,
    load_preset,
    parse_roi_string,
    save_preset,
    validate_preset,
)


class TestPresets(unittest.TestCase):

    def test_load_builtin_preset(self):
        preset = load_preset("blackbear")
        self.assertEqual(preset.name, "blackbear")
        self.assertEqual(preset.full_roi, (0.045, 0.02, 0.255, 0.14))
        self.assertEqual(preset.detection["mode"], "blackbear")

    def test_unknown_builtin_rejected(self):
        with self.assertRaises(PresetError):
            load_preset("missing")

    def test_parse_roi_string(self):
        self.assertEqual(parse_roi_string("0.1,0.2,0.3,0.4"), (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(
            parse_roi_string("0,0,320,180"),
            (0.0, 0.0, 320.0, 180.0),
        )
        with self.assertRaises(PresetError):
            parse_roi_string("0.3,0.2,0.1,0.4")

    def test_mixed_unit_roi_rejected(self):
        with self.assertRaises(PresetError):
            parse_roi_string("0.1,0.2,320,180")
        with self.assertRaises(PresetError):
            parse_roi_string("0,0,320.5,180")

    def test_validate_missing_layout_rejected(self):
        with self.assertRaises(PresetError):
            validate_preset({"name": "bad", "layout": {"full_roi": [0, 0, 1, 1]}})

    def test_load_json_preset_file(self):
        data = {
            "name": "custom",
            "layout": {
                "full_roi": [0.1, 0.1, 0.4, 0.3],
                "period_roi": [0.0, 0.0, 0.2, 0.2],
                "clock_roi": [0.2, 0.2, 0.8, 0.5],
                "score_roi": [0.2, 0.5, 0.8, 0.9],
            },
            "detection": {"mode": "generic"},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            path = fh.name
        try:
            preset = load_preset(preset_file=path)
            self.assertEqual(preset.name, "custom")
            self.assertEqual(preset.clock_roi, (0.2, 0.2, 0.8, 0.5))
        finally:
            os.unlink(path)

    @unittest.skipUnless(HAS_YAML, "PyYAML is not installed")
    def test_load_yaml_preset_file(self):
        data = """
name: custom-yaml
layout:
  full_roi: [0.1, 0.1, 0.4, 0.3]
  period_roi: [0.0, 0.0, 0.2, 0.2]
  clock_roi: [0.2, 0.2, 0.8, 0.5]
  score_roi: [0.2, 0.5, 0.8, 0.9]
detection:
  mode: generic
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(data)
            path = fh.name
        try:
            preset = load_preset(preset_file=path)
            self.assertEqual(preset.name, "custom-yaml")
            self.assertEqual(preset.score_roi, (0.2, 0.5, 0.8, 0.9))
        finally:
            os.unlink(path)

    def test_save_preset_json(self):
        preset = load_preset("top_left")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
            path = fh.name
        try:
            save_preset(preset, path)
            reloaded = load_preset(preset_file=path)
            self.assertEqual(reloaded.full_roi, preset.full_roi)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
