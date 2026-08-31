from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.quality_service import (
    _inspect_render_probe,
    _inspect_timeline,
    inspect_media_content,
    inspect_project_for_export,
)


class QualityServiceTests(unittest.TestCase):
    def _project(self, root: Path, duration: float = 12.0) -> tuple[Path, Path, Path, Path]:
        project = root / "project.json"
        source = root / "source.mp4"
        audio = root / "audio" / "narration.wav"
        srt = root / "audio" / "narration.srt"
        (root / "script").mkdir()
        (root / "timeline").mkdir()
        audio.parent.mkdir()
        source.write_bytes(b"video")
        audio.write_bytes(b"audio")
        project.write_text("{}", encoding="utf-8")
        (root / "script" / "story.json").write_text("{}", encoding="utf-8")
        (root / "timeline" / "matches.json").write_text("{}", encoding="utf-8")
        timeline = {
            "duration_sec": duration,
            "all_narration_covered": True,
            "narration": [{"covered": True}],
            "clips": [
                {
                    "event_id": 1,
                    "source_start": 0,
                    "source_end": duration,
                    "output_start": 0,
                    "output_end": duration,
                }
            ],
        }
        (root / "timeline" / "rough_cut.json").write_text(json.dumps(timeline), encoding="utf-8")
        srt.write_text(f"1\n00:00:00,000 --> 00:00:{duration:06.3f}\nHello.\n", encoding="utf-8")
        return project, source, audio, srt

    def test_complete_project_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project, source, audio, srt = self._project(root)
            report = inspect_project_for_export(
                project, source, audio, 12.0, srt, {"duration_sec": 20, "file_size": 5}
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["error_count"], 0)

    def test_overlong_and_missing_audio_are_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project, source, _audio, srt = self._project(root, 180.0)
            report = inspect_project_for_export(
                project, source, None, 0.0, srt, {"duration_sec": 200, "file_size": 5}
            )
            self.assertFalse(report["passed"])
            titles = {item["title"] for item in report["checks"] if item["level"] == "error"}
            self.assertIn("时间线超过 Shorts 上限", titles)
            self.assertIn("英文配音缺失", titles)

    def test_render_probe_accepts_shorts_compatible_output(self) -> None:
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        _inspect_render_probe(
            {
                "format": {"duration": "12.030"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1280,
                        "height": 720,
                        "pix_fmt": "yuv420p",
                    },
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            },
            12.0,
            1280,
            720,
            True,
            add,
        )
        self.assertFalse([item for item in checks if item["level"] == "error"])
        self.assertIn("成片时长", {item["title"] for item in checks})
        self.assertIn("音频轨道", {item["title"] for item in checks})

    def test_render_probe_blocks_missing_audio_and_wrong_dimensions(self) -> None:
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        _inspect_render_probe(
            {
                "format": {"duration": "180.0"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 640,
                        "height": 360,
                        "pix_fmt": "yuv420p",
                    }
                ],
            },
            120.0,
            1280,
            720,
            True,
            add,
        )
        titles = {item["title"] for item in checks if item["level"] == "error"}
        self.assertIn("输出分辨率异常", titles)
        self.assertIn("成片超过 Shorts 上限", titles)
        self.assertIn("输出缺少声音", titles)

    def test_non_adjacent_reused_shot_is_informational(self) -> None:
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        _inspect_timeline(
            {
                "duration_sec": 6.0,
                "all_narration_covered": True,
                "narration": [{"covered": True}],
                "clips": [
                    {"event_id": 1, "source_start": 0, "source_end": 2, "output_start": 0, "output_end": 2},
                    {"event_id": 2, "source_start": 5, "source_end": 7, "output_start": 2, "output_end": 4},
                    {"event_id": 1, "source_start": 0, "source_end": 2, "output_start": 4, "output_end": 6},
                ],
            },
            10.0,
            add,
        )
        reused = next(item for item in checks if item["title"] == "镜头复用说明")
        self.assertEqual(reused["level"], "info")
        self.assertIn("无需手动处理", reused["detail"])

    def test_adjacent_exact_replay_is_only_a_preview_warning(self) -> None:
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        _inspect_timeline(
            {
                "duration_sec": 4.0,
                "all_narration_covered": True,
                "narration": [{"covered": True}],
                "clips": [
                    {"event_id": 1, "source_start": 0, "source_end": 2, "output_start": 0, "output_end": 2},
                    {"event_id": 1, "source_start": 0, "source_end": 2, "output_start": 2, "output_end": 4},
                ],
            },
            10.0,
            add,
        )
        repeated = next(item for item in checks if item["title"] == "相邻镜头重复播放")
        self.assertEqual(repeated["level"], "warning")
        self.assertIn("只有画面跳回感明显", repeated["detail"])

    def test_deep_scan_reports_black_silence_and_low_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            media = Path(temporary_dir) / "preview.mp4"
            media.write_bytes(b"preview")
            processes = [
                SimpleNamespace(
                    returncode=0,
                    stderr="[blackdetect] black_start:2 black_end:3.2 black_duration:1.2",
                ),
                SimpleNamespace(
                    returncode=0,
                    stderr=(
                        "[silencedetect] silence_start:4\n"
                        "[silencedetect] silence_end:6.5 | silence_duration:2.5\n"
                        "[Parsed_volumedetect] mean_volume: -34.0 dB\n"
                        "[Parsed_volumedetect] max_volume: -4.0 dB\n"
                    ),
                ),
            ]
            with patch("src.quality_service._resolve_tool", return_value="ffmpeg"), patch(
                "src.quality_service.subprocess.run", side_effect=processes
            ):
                report = inspect_media_content(
                    media,
                    True,
                    True,
                    {"shared": {}, "quality": {}},
                    Path(temporary_dir),
                )

            warning_titles = {
                item["title"] for item in report["checks"] if item["level"] == "warning"
            }
            self.assertIn("检测到连续黑画面", warning_titles)
            self.assertIn("检测到较长静音", warning_titles)
            self.assertIn("整体音量偏低", warning_titles)
            self.assertTrue(report["passed"])

    def test_deep_scan_passes_clean_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            media = Path(temporary_dir) / "preview.mp4"
            media.write_bytes(b"preview")
            processes = [
                SimpleNamespace(returncode=0, stderr="no black frames"),
                SimpleNamespace(
                    returncode=0,
                    stderr=(
                        "[Parsed_volumedetect] mean_volume: -18.0 dB\n"
                        "[Parsed_volumedetect] max_volume: -1.2 dB\n"
                    ),
                ),
            ]
            with patch("src.quality_service._resolve_tool", return_value="ffmpeg"), patch(
                "src.quality_service.subprocess.run", side_effect=processes
            ):
                report = inspect_media_content(
                    media,
                    True,
                    True,
                    {"shared": {}, "quality": {}},
                    Path(temporary_dir),
                )

            self.assertEqual(report["warning_count"], 0)
            self.assertEqual(report["pass_count"], 3)


if __name__ == "__main__":
    unittest.main()
