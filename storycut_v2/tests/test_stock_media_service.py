from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.stock_media_service import download_selected_stock_media, search_stock_media


class StockMediaServiceTests(unittest.TestCase):
    def test_search_uses_storyboard_queries_and_records_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            storyboard = root / "storyboard.json"
            output = root / "online_search.json"
            storyboard.write_text(
                json.dumps(
                    {
                        "beats": [
                            {
                                "id": 1,
                                "narration_id": 1,
                                "ideal_shot_zh": "象群交流",
                                "search_queries_en": ["elephant herd communication"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def fake_request(url: str, _headers: dict[str, str]):
                if "pexels" in url:
                    return {
                        "videos": [
                            {
                                "id": 10,
                                "url": "https://pexels.example/10",
                                "image": "https://images.example/10.jpg",
                                "duration": 12,
                                "user": {"name": "A", "url": "https://pexels.example/a"},
                                "video_files": [
                                    {"quality": "hd", "width": 1920, "height": 1080, "link": "https://cdn.example/10.mp4"}
                                ],
                            }
                        ]
                    }
                return {
                    "hits": [
                        {
                            "id": 20,
                            "pageURL": "https://pixabay.example/20",
                            "duration": 8,
                            "user": "B",
                            "user_id": 2,
                            "videos": {"medium": {"url": "https://cdn.example/20.mp4", "thumbnail": "https://cdn.example/20.jpg", "width": 1280, "height": 720}},
                        }
                    ]
                }

            with patch("src.stock_media_service._request_json", side_effect=fake_request):
                result = search_stock_media(
                    storyboard,
                    output,
                    "pexels-key",
                    "pixabay-key",
                    lambda _value, _status: None,
                )

            self.assertEqual(result["coverage_percent"], 100)
            self.assertEqual(result["results"][0]["query"], "elephant herd communication")
            self.assertEqual(len(result["results"][0]["candidates"]), 2)
            self.assertEqual(result["results"][0]["candidates"][0]["provider"], "Pexels")
            self.assertTrue(output.exists())

    def test_download_selected_candidate_and_updates_search_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            search_file = root / "online_search.json"
            search_file.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "narration_id": 1,
                                "selected_candidate_id": "pexels-10",
                                "candidates": [
                                    {
                                        "candidate_id": "pexels-10",
                                        "download_url": "https://cdn.example/10.mp4",
                                        "duration_sec": 12,
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            class FakeResponse:
                def __init__(self) -> None:
                    self._chunks = [b"video-data", b""]

                def __enter__(self):
                    return self

                def __exit__(self, *_args) -> None:
                    return None

                def read(self, _size: int) -> bytes:
                    return self._chunks.pop(0)

            with patch("src.stock_media_service.urlopen", return_value=FakeResponse()):
                result = download_selected_stock_media(
                    search_file,
                    root / "online",
                    lambda _value, _status: None,
                )

            downloaded = Path(result["results"][0]["local_path"])
            self.assertEqual(downloaded.read_bytes(), b"video-data")
            self.assertEqual(result["downloaded_count"], 1)
            self.assertEqual(result["results"][0]["download_status"], "downloaded")


if __name__ == "__main__":
    unittest.main()
