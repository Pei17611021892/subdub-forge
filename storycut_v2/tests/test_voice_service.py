from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.voice_service import (
    MIN_TTS_UNIT_DURATION_SEC,
    estimate_tts_unit_duration,
    parse_srt_timings,
    prepare_tts_srt,
    recommended_shorts_speed,
    scale_srt_timeline,
)


class VoiceServiceTests(unittest.TestCase):
    def test_short_phrase_is_not_forced_to_two_seconds(self) -> None:
        duration = estimate_tts_unit_duration("All right.")
        self.assertGreaterEqual(duration, 0.9)
        self.assertLess(duration, 1.5)
        self.assertEqual(MIN_TTS_UNIT_DURATION_SEC, 0.75)

    def test_longer_and_syllable_heavy_sentences_take_longer(self) -> None:
        short = estimate_tts_unit_duration("She tries again.")
        long = estimate_tts_unit_duration(
            "She carefully steadies the complicated machinery and slowly tries again."
        )
        self.assertGreater(long, short * 2)

    def test_reference_srt_recalculates_old_one_second_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            story_path = root / "story.json"
            story_path.write_text(
                json.dumps(
                    {
                        "narration": [
                            {
                                "id": 1,
                                "text_en": "She lifts the seat.",
                                "estimated_duration_sec": 1.0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = prepare_tts_srt(story_path, root / "tts")
            segments = parse_srt_timings(
                Path(result["reference_srt_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(len(segments), 1)
            self.assertGreaterEqual(segments[0]["end"] - segments[0]["start"], 1.8)

    def test_recommended_shorts_speed_uses_smallest_hundredth(self) -> None:
        self.assertEqual(recommended_shorts_speed(170), 1.0)
        self.assertEqual(recommended_shorts_speed(180), 1.01)
        self.assertEqual(recommended_shorts_speed(200), 1.13)
        self.assertIsNone(recommended_shorts_speed(230))

    def test_scale_srt_timeline_matches_audio_speed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "original.srt"
            destination = root / "adjusted.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:04,000\nFirst line.\n\n"
                "2\n00:00:04,500 --> 00:00:09,000\nSecond line.\n",
                encoding="utf-8",
            )
            segments = scale_srt_timeline(source, destination, 1.25)
            self.assertAlmostEqual(segments[0]["end"], 3.2)
            self.assertAlmostEqual(segments[1]["start"], 3.6)
            self.assertAlmostEqual(segments[1]["end"], 7.2)
            self.assertIn("00:00:07,200", destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
