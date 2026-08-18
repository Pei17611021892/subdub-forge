from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


UPDATER_PATH = Path(__file__).resolve().parents[2] / "repository_updater.py"


def load_updater():
    spec = importlib.util.spec_from_file_location("repository_updater_test", UPDATER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"subdub-forge-main/{name}", content)
    return stream.getvalue()


class RepositoryUpdaterTests(unittest.TestCase):
    def test_repository_sync_adds_replaces_deletes_and_protects_user_data(self) -> None:
        updater = load_updater()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            updater.REPOSITORY_ROOT = root
            updater.ROOT_MANIFEST = root / "update_manifest.json"

            (root / "storycut_v2").mkdir()
            (root / "storycut_v2" / "version.json").write_text(
                json.dumps({"version": "0.1.6", "repository": "owner/repo"}),
                encoding="utf-8",
            )
            (root / "commentary_studio" / "src").mkdir(parents=True)
            (root / "commentary_studio" / "src" / "old.py").write_text(
                "old", encoding="utf-8"
            )
            (root / "commentary_studio" / "projects" / "demo").mkdir(parents=True)
            (root / "commentary_studio" / "projects" / "demo" / "project.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / ".env").write_text("SECRET=keep", encoding="utf-8")
            (root / "update_manifest.json").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "files": [
                            "commentary_studio/src/old.py",
                            "storycut_v2/version.json",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            new_manifest = {
                "format": 2,
                "files": [
                    "repository_updater.py",
                    "storycut_v2/version.json",
                    "update_manifest.json",
                ],
                "remove": ["commentary_studio/src/old.py"],
            }
            archive_data = make_archive(
                {
                    "repository_updater.py": b"# new updater\n",
                    "storycut_v2/version.json": json.dumps(
                        {"version": "0.1.7", "repository": "owner/repo"}
                    ).encode(),
                    "update_manifest.json": json.dumps(new_manifest).encode(),
                }
            )

            updater.apply_repository_archive(
                "storycut_v2",
                {"version": "0.1.7", "repository": "owner/repo"},
                archive_data,
            )

            self.assertFalse((root / "commentary_studio" / "src" / "old.py").exists())
            self.assertTrue((root / "storycut_v2" / "projects" / "demo" / "project.json").exists())
            self.assertTrue((root / "commentary_studio" / "projects" / "demo" / "project.json").exists())
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "SECRET=keep")
            installed = json.loads(
                (root / "storycut_v2" / "version.json").read_text(encoding="utf-8")
            )
            self.assertEqual(installed["version"], "0.1.7")


if __name__ == "__main__":
    unittest.main()
