from __future__ import annotations

import base64
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


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
    def test_update_check_falls_back_to_github_api(self) -> None:
        updater = load_updater()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            updater.REPOSITORY_ROOT = root
            (root / "storycut_v2").mkdir()
            (root / "storycut_v2" / "version.json").write_text(
                json.dumps({"version": "0.2.0", "repository": "owner/repo"}),
                encoding="utf-8",
            )
            remote_version = json.dumps(
                {"version": "0.2.1", "repository": "owner/repo"}
            ).encode()
            api_response = json.dumps(
                {"content": base64.b64encode(remote_version).decode()}
            ).encode()
            calls: list[str] = []

            def fake_request(url: str, **_kwargs) -> bytes:
                calls.append(url)
                if "raw.githubusercontent.com" in url:
                    raise OSError("simulated 404")
                return api_response

            with patch.object(updater, "_request_bytes", side_effect=fake_request):
                local, remote, newer = updater.check_for_update("storycut_v2")

            self.assertEqual(local["version"], "0.2.0")
            self.assertEqual(remote["version"], "0.2.1")
            self.assertTrue(newer)
            self.assertEqual(len(calls), 2)
            self.assertIn("api.github.com", calls[1])

    def test_git_commands_inherit_windows_system_proxy(self) -> None:
        updater = load_updater()
        completed = CompletedProcess(["git"], 0, "", "")
        with patch.object(
            updater.urllib.request,
            "getproxies",
            return_value={"http": "http://127.0.0.1:7890"},
        ), patch.object(updater.subprocess, "run", return_value=completed) as run:
            updater._run_git("git.exe", "status")

        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")

    @unittest.skipUnless(shutil.which("git"), "Git is required for this integration test")
    def test_real_fast_forward_preserves_ignored_and_untracked_files(self) -> None:
        updater = load_updater()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            remote = root / "storycut-test-remote.git"
            source = root / "source"
            consumer = root / "consumer"

            def git(cwd: Path, *arguments: str) -> None:
                result = subprocess.run(
                    [shutil.which("git") or "git", *arguments],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            remote.mkdir()
            git(remote, "init", "--bare")
            source.mkdir()
            git(source, "init", "-b", "main")
            git(source, "config", "user.email", "storycut-test@example.invalid")
            git(source, "config", "user.name", "StoryCut Test")
            (source / ".gitignore").write_text(".env\n", encoding="utf-8")
            (source / "storycut_v2").mkdir()
            (source / "storycut_v2" / "version.json").write_text(
                json.dumps({"version": "0.1.7", "repository": "owner/repo"}),
                encoding="utf-8",
            )
            (source / "old_program.py").write_text("old\n", encoding="utf-8")
            git(source, "add", ".")
            git(source, "commit", "-m", "old release")
            git(source, "remote", "add", "origin", str(remote))
            git(source, "push", "-u", "origin", "main")
            git(root, "clone", "--branch", "main", str(remote), str(consumer))

            (consumer / ".env").write_text("SECRET=keep\n", encoding="utf-8")
            (consumer / "customer_notes.txt").write_text("keep me\n", encoding="utf-8")
            (source / "storycut_v2" / "version.json").write_text(
                json.dumps({"version": "0.1.8", "repository": "owner/repo"}),
                encoding="utf-8",
            )
            (source / "old_program.py").unlink()
            (source / "new_program.py").write_text("new\n", encoding="utf-8")
            git(source, "add", "-A")
            git(source, "commit", "-m", "new release")
            git(source, "push", "origin", "main")

            updater.REPOSITORY_ROOT = consumer
            result = updater._try_git_fast_forward(
                "storycut_v2",
                remote.name,
                "0.1.8",
                lambda _message: None,
            )

            self.assertEqual(result, consumer / ".git")
            self.assertFalse((consumer / "old_program.py").exists())
            self.assertTrue((consumer / "new_program.py").exists())
            self.assertEqual((consumer / ".env").read_text(encoding="utf-8"), "SECRET=keep\n")
            self.assertEqual(
                (consumer / "customer_notes.txt").read_text(encoding="utf-8"),
                "keep me\n",
            )

    def test_git_clone_prefers_fast_forward_pull(self) -> None:
        updater = load_updater()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            updater.REPOSITORY_ROOT = root
            (root / ".git").mkdir()
            (root / "storycut_v2").mkdir()
            (root / "storycut_v2" / "version.json").write_text(
                json.dumps({"version": "0.1.8", "repository": "owner/repo"}),
                encoding="utf-8",
            )
            calls: list[tuple[str, ...]] = []

            def fake_git(_executable: str, *arguments: str, **_kwargs):
                calls.append(arguments)
                outputs = {
                    ("remote", "get-url", "origin"): "https://github.com/owner/repo.git\n",
                    ("branch", "--show-current"): "main\n",
                    ("status", "--porcelain", "--untracked-files=no"): "",
                    ("pull", "--ff-only", "origin", "main"): "Already up to date.\n",
                }
                return CompletedProcess(arguments, 0, outputs[arguments], "")

            with patch.object(updater.shutil, "which", return_value="git.exe"), patch.object(
                updater, "_run_git", side_effect=fake_git
            ):
                result = updater._try_git_fast_forward(
                    "storycut_v2", "owner/repo", "0.1.8", lambda _message: None
                )

            self.assertEqual(result, root / ".git")
            self.assertIn(("pull", "--ff-only", "origin", "main"), calls)

    def test_git_with_tracked_local_changes_falls_back_without_pull(self) -> None:
        updater = load_updater()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            updater.REPOSITORY_ROOT = root
            (root / ".git").mkdir()
            calls: list[tuple[str, ...]] = []

            def fake_git(_executable: str, *arguments: str, **_kwargs):
                calls.append(arguments)
                outputs = {
                    ("remote", "get-url", "origin"): "git@github.com:owner/repo.git\n",
                    ("branch", "--show-current"): "main\n",
                    ("status", "--porcelain", "--untracked-files=no"): " M storycut_v2/main.py\n",
                }
                return CompletedProcess(arguments, 0, outputs[arguments], "")

            with patch.object(updater.shutil, "which", return_value="git.exe"), patch.object(
                updater, "_run_git", side_effect=fake_git
            ):
                result = updater._try_git_fast_forward(
                    "storycut_v2", "owner/repo", "0.1.8", lambda _message: None
                )

            self.assertIsNone(result)
            self.assertNotIn(("pull", "--ff-only", "origin", "main"), calls)

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
            (root / "storycut_v2" / "src").mkdir()
            (root / "storycut_v2" / "src" / "old.py").write_text(
                "old", encoding="utf-8"
            )
            (root / "storycut_v2" / "projects" / "demo").mkdir(parents=True)
            (root / "storycut_v2" / "projects" / "demo" / "project.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / "storycut_v2" / "config.user.yaml").write_text(
                "user: keep", encoding="utf-8"
            )
            (root / ".env").write_text("SECRET=keep", encoding="utf-8")
            (root / "update_manifest.json").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "files": [
                            "storycut_v2/src/old.py",
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
                "remove": [
                    "storycut_v2/src/old.py",
                ],
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

            self.assertFalse((root / "storycut_v2" / "src" / "old.py").exists())
            self.assertTrue((root / "storycut_v2" / "projects" / "demo" / "project.json").exists())
            self.assertEqual(
                (root / "storycut_v2" / "config.user.yaml").read_text(encoding="utf-8"),
                "user: keep",
            )
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "SECRET=keep")
            installed = json.loads(
                (root / "storycut_v2" / "version.json").read_text(encoding="utf-8")
            )
            self.assertEqual(installed["version"], "0.1.7")


if __name__ == "__main__":
    unittest.main()
