from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.matching_service import (
    _score_candidate,
    apply_voice_timing,
    build_rough_cut,
    select_shot_match,
)


def _candidate(event_id: int, start: float, end: float, score: float) -> dict[str, object]:
    return {
        "event_id": event_id,
        "start": start,
        "end": end,
        "duration_sec": end - start,
        "score": score,
        "reason": "test",
    }


def _item(
    narration_id: int,
    selected_event_id: int,
    candidates: list[dict[str, object]],
    duration: float = 3.0,
) -> dict[str, object]:
    selected = next(item for item in candidates if item["event_id"] == selected_event_id)
    return {
        "narration_id": narration_id,
        "text_en": f"Line {narration_id}",
        "visual_query": "测试画面",
        "narration_duration_sec": duration,
        "selected_event_id": selected_event_id,
        "selected_start": selected["start"],
        "selected_end": selected["end"],
        "selected_clips": [],
        "coverage_sec": 0,
        "selection_mode": "automatic",
        "candidates": candidates,
    }


class MatchingServiceTests(unittest.TestCase):
    def test_voice_timing_continues_same_event_instead_of_restarting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            matches_file = Path(temporary_dir) / "matches.json"
            candidate = _candidate(7, 10.0, 20.0, 0.95)
            payload = {
                "schema_version": 1,
                "items": [
                    _item(1, 7, [candidate]),
                    _item(2, 7, [candidate]),
                ],
            }
            matches_file.write_text(json.dumps(payload), encoding="utf-8")

            result = apply_voice_timing(matches_file, 6.0)

            first = result["items"][0]["selected_clips"][0]
            second = result["items"][1]["selected_clips"][0]
            self.assertEqual((first["start"], first["end"]), (10.0, 13.0))
            self.assertEqual((second["start"], second["end"]), (13.0, 16.0))
            self.assertEqual(result["allocation_strategy"], "global_non_repeating_v2")

    def test_exhausted_event_switches_candidate_without_reusing_source_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            matches_file = Path(temporary_dir) / "matches.json"
            short = _candidate(1, 0.0, 2.0, 0.95)
            fallback = _candidate(2, 10.0, 20.0, 0.72)
            payload = {
                "schema_version": 1,
                "items": [
                    _item(1, 1, [short, fallback]),
                    _item(2, 1, [short, fallback]),
                ],
            }
            matches_file.write_text(json.dumps(payload), encoding="utf-8")

            result = apply_voice_timing(matches_file, 6.0)
            all_clips = [
                clip for item in result["items"] for clip in item["selected_clips"]
            ]

            event_one_ranges = [
                (clip["start"], clip["end"])
                for clip in all_clips
                if clip["event_id"] == 1
            ]
            self.assertEqual(event_one_ranges, [(0.0, 2.0)])
            self.assertTrue(all(item["coverage_sec"] == 3.0 for item in result["items"]))

    def test_manual_selection_reallocates_following_items_globally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            matches_file = Path(temporary_dir) / "matches.json"
            first = _candidate(1, 0.0, 10.0, 0.9)
            second = _candidate(2, 20.0, 30.0, 0.8)
            payload = {
                "schema_version": 1,
                "items": [
                    _item(1, 1, [first, second]),
                    _item(2, 1, [first, second]),
                ],
            }
            matches_file.write_text(json.dumps(payload), encoding="utf-8")

            result = select_shot_match(matches_file, 1, 2)

            self.assertEqual(result["items"][0]["selection_mode"], "manual")
            self.assertEqual(result["items"][0]["selected_event_id"], 2)
            ranges = [
                (clip["event_id"], clip["start"], clip["end"])
                for item in result["items"]
                for clip in item["selected_clips"]
            ]
            self.assertEqual(len(ranges), len(set(ranges)))

    def test_same_source_start_is_not_a_chronology_bonus(self) -> None:
        event = {"id": 2, "start": 5.0, "end": 8.0, "transcript": "扶梯"}

        first = _score_candidate(event, "扶梯", True)
        repeated = _score_candidate(
            event,
            "扶梯",
            True,
            used_count=1,
            last_selected_start=5.0,
        )

        self.assertLess(repeated["score"], first["score"] - 0.2)

    def test_rough_cut_duration_matches_retimed_voice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            matches_file = root / "matches.json"
            rough_cut_file = root / "rough_cut.json"
            candidate = _candidate(1, 0.0, 30.0, 0.9)
            payload = {
                "items": [
                    _item(1, 1, [candidate], 1.0),
                    _item(2, 1, [candidate], 2.0),
                    _item(3, 1, [candidate], 3.0),
                ]
            }
            matches_file.write_text(json.dumps(payload), encoding="utf-8")

            apply_voice_timing(matches_file, 11.23)
            rough_cut = build_rough_cut(matches_file, rough_cut_file)

            self.assertAlmostEqual(rough_cut["duration_sec"], 11.23, places=2)
            source_ranges = [
                (clip["event_id"], clip["source_start"], clip["source_end"])
                for clip in rough_cut["clips"]
            ]
            self.assertEqual(len(source_ranges), len(set(source_ranges)))


if __name__ == "__main__":
    unittest.main()
