"""Tests for Blackbear template rebuild failure handling."""

import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from scripts import build_ocr_templates


class TestBuildOCRTemplates(unittest.TestCase):
    def test_missing_source_does_not_replace_existing_templates(self):
        with tempfile.TemporaryDirectory() as output:
            sentinel = os.path.join(output, "period_1.png")
            with open(sentinel, "wb") as existing:
                existing.write(b"existing")
            with patch.object(
                build_ocr_templates, "SAMPLES", [("missing.mp4", 0, 1, "15:00")]
            ), patch.object(
                build_ocr_templates, "extract_frame_at_timestamp", return_value=None
            ):
                with self.assertRaisesRegex(ValueError, "Could not extract"):
                    build_ocr_templates.build(output, output)
            with open(sentinel, "rb") as existing:
                self.assertEqual(existing.read(), b"existing")

    def test_bad_segmentation_does_not_write_partial_output(self):
        with tempfile.TemporaryDirectory() as output:
            with patch.object(
                build_ocr_templates, "SAMPLES", [("frame.mp4", 0, 1, "15:00")]
            ), patch.object(
                build_ocr_templates,
                "extract_frame_at_timestamp",
                return_value=Image.new("RGB", (1280, 720)),
            ), patch.object(
                build_ocr_templates.OverlayTemplateMatcher,
                "clock_digit_cells",
                return_value=None,
            ):
                with self.assertRaisesRegex(ValueError, "segmentation mismatch"):
                    build_ocr_templates.build(output, output)
            self.assertEqual(os.listdir(output), [])

    def test_missing_labels_do_not_write_partial_output(self):
        with tempfile.TemporaryDirectory() as output:
            cells = [np.zeros((32, 26), dtype=np.float32) for _ in range(4)]
            with patch.object(
                build_ocr_templates, "SAMPLES", [("frame.mp4", 0, 1, "15:00")]
            ), patch.object(
                build_ocr_templates,
                "extract_frame_at_timestamp",
                return_value=Image.new("RGB", (1280, 720)),
            ), patch.object(
                build_ocr_templates.OverlayTemplateMatcher,
                "clock_digit_cells",
                return_value=cells,
            ):
                with self.assertRaisesRegex(ValueError, "Incomplete templates"):
                    build_ocr_templates.build(output, output)
            self.assertEqual(os.listdir(output), [])


if __name__ == "__main__":
    unittest.main()
