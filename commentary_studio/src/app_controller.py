from __future__ import annotations

from copy import deepcopy
import json
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from .config_manager import load_config
from .analysis_service import (
    build_timeline_events,
    detect_scenes_and_keyframes,
    extract_analysis_audio,
    transcribe_analysis_audio,
)
from .media_service import analyze_media, extract_preview_frame, render_subtitle_effect_preview
from .vision_service import api_configuration, describe_event_keyframes
from .story_service import generate_story_script
from .matching_service import (
    apply_voice_timing,
    adjust_shot_boundary,
    build_rough_cut,
    generate_shot_matches,
    select_shot_match,
)
from .export_service import render_rough_preview
from .voice_service import import_narration_audio, import_synced_srt, prepare_tts_package
from .update_manager import check_for_update, download_and_apply, read_version


class AppController(QObject):
    projectChanged = Signal()
    noticeChanged = Signal()
    recentProjectsChanged = Signal()
    mediaChanged = Signal()
    previewChanged = Signal()
    analysisChanged = Signal()
    eventsChanged = Signal()
    storyChanged = Signal()
    matchingChanged = Signal()
    exportChanged = Signal()
    voiceChanged = Signal()
    subtitleStyleChanged = Signal()
    subtitleEffectPreviewChanged = Signal()
    updateChanged = Signal()
    updateDialogRequested = Signal()
    _mediaReady = Signal(object, str, str, int)
    _previewReady = Signal(str, str, int, float)
    _subtitleEffectPreviewReady = Signal(str, int)
    _analysisProgressReady = Signal(float, str, float, int)
    _analysisFinished = Signal(bool, str, int)
    _storyProgressReady = Signal(float, str, int)
    _storyFinished = Signal(bool, str, object, int)
    _exportProgressReady = Signal(float, str, int)
    _exportFinished = Signal(bool, str, object, int)
    _updateCheckFinished = Signal(bool, str, object)
    _updateApplyFinished = Signal(bool, str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._config = load_config(root)
        self._project_name = "尚未创建项目"
        self._video_path = ""
        self._notice = "导入一个长视频，开始生成精简解说。"
        self._projects_dir = (root / self._config.get("projects_dir", "projects")).resolve()
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._recent_projects: list[dict[str, str]] = []
        self._media: dict[str, object] = {}
        self._cover_url = ""
        self._media_busy = False
        self._media_job_id = 0
        self._preview_job_id = 0
        self._preview_url = ""
        self._preview_busy = False
        self._preview_position = 0.0
        self._current_project_file: Path | None = None
        self._analysis_job_id = 0
        self._analysis_busy = False
        self._analysis_progress = 0.0
        self._analysis_status = "等待开始"
        self._analysis_started_at = 0.0
        self._analysis_eta_seconds = -1.0
        self._analysis_eta_updated_at = 0.0
        self._analysis_estimated_total = -1.0
        self._events: list[dict[str, object]] = []
        self._story_job_id = 0
        self._story_busy = False
        self._story_progress = 0.0
        self._story_status = "等待组织故事"
        self._story: dict[str, object] = {}
        self._story_outline: list[dict[str, object]] = []
        self._story_narration: list[dict[str, object]] = []
        self._matching_busy = False
        self._matching_status = "等待匹配镜头"
        self._matches: list[dict[str, object]] = []
        self._export_job_id = 0
        self._export_busy = False
        self._export_progress = 0.0
        self._export_status = "等待生成成片预览"
        self._export_path = ""
        self._voice_status = "等待准备 GPT-SoVITS 文案"
        self._narration_audio_path = ""
        self._narration_duration_sec = 0.0
        self._synced_srt_path = ""
        self._subtitle_style = self._default_subtitle_style()
        self._subtitle_effect_preview_url = ""
        self._subtitle_effect_preview_busy = False
        self._subtitle_effect_preview_job_id = 0
        try:
            self._app_version = str(read_version().get("version", "0.1.1"))
        except Exception:
            self._app_version = "0.1.1"
        self._update_busy = False
        self._update_available = False
        self._update_installed = False
        self._update_status = f"当前版本 v{self._app_version}"
        self._remote_update: dict[str, object] = {}
        self._mediaReady.connect(self._apply_media_result)
        self._previewReady.connect(self._apply_preview_result)
        self._subtitleEffectPreviewReady.connect(self._apply_subtitle_effect_preview)
        self._analysisProgressReady.connect(self._apply_analysis_progress)
        self._analysisFinished.connect(self._apply_analysis_finished)
        self._storyProgressReady.connect(self._apply_story_progress)
        self._storyFinished.connect(self._apply_story_finished)
        self._exportProgressReady.connect(self._apply_export_progress)
        self._exportFinished.connect(self._apply_export_finished)
        self._updateCheckFinished.connect(self._apply_update_check)
        self._updateApplyFinished.connect(self._apply_update_install)
        self._analysis_clock = QTimer(self)
        self._analysis_clock.setInterval(1000)
        self._analysis_clock.timeout.connect(self._tick_analysis_clock)
        self._refresh_recent_projects()

    @Property(str, notify=projectChanged)
    def projectName(self) -> str:
        return self._project_name

    @Property(str, notify=projectChanged)
    def videoPath(self) -> str:
        return self._video_path

    @Property(str, notify=noticeChanged)
    def notice(self) -> str:
        return self._notice

    @Property("QVariantList", notify=recentProjectsChanged)
    def recentProjects(self) -> list[dict[str, str]]:
        return self._recent_projects

    @Property(str, notify=mediaChanged)
    def coverUrl(self) -> str:
        return self._cover_url

    @Property(bool, notify=mediaChanged)
    def mediaBusy(self) -> bool:
        return self._media_busy

    @Property(str, notify=mediaChanged)
    def durationText(self) -> str:
        seconds = float(self._media.get("duration_sec", 0) or 0)
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"

    @Property(str, notify=mediaChanged)
    def resolutionText(self) -> str:
        width = int(self._media.get("width", 0) or 0)
        height = int(self._media.get("height", 0) or 0)
        return f"{width}×{height}" if width and height else "待分析"

    @Property(str, notify=mediaChanged)
    def fpsText(self) -> str:
        fps = float(self._media.get("fps", 0) or 0)
        return f"{fps:.2f} fps" if fps else ""

    @Property(str, notify=mediaChanged)
    def codecText(self) -> str:
        codec = str(self._media.get("video_codec", "") or "")
        backend = str(self._media.get("probe_backend", "") or "")
        return f"{codec.upper()} · {backend}" if codec and backend else ""

    @Property(float, notify=mediaChanged)
    def durationSeconds(self) -> float:
        return float(self._media.get("duration_sec", 0) or 0)

    @Property(str, notify=previewChanged)
    def previewUrl(self) -> str:
        return self._preview_url or self._cover_url

    @Property(bool, notify=previewChanged)
    def previewBusy(self) -> bool:
        return self._preview_busy

    @Property(float, notify=previewChanged)
    def previewPosition(self) -> float:
        return self._preview_position

    @Property(str, notify=previewChanged)
    def previewPositionText(self) -> str:
        return self._format_time(self._preview_position)

    @Property(bool, notify=analysisChanged)
    def analysisBusy(self) -> bool:
        return self._analysis_busy

    @Property(float, notify=analysisChanged)
    def analysisProgress(self) -> float:
        return self._analysis_progress

    @Property(str, notify=analysisChanged)
    def analysisStatus(self) -> str:
        return self._analysis_status

    @Property(str, notify=analysisChanged)
    def analysisElapsedText(self) -> str:
        elapsed = time.monotonic() - self._analysis_started_at if self._analysis_started_at else 0.0
        return f"已用 {self._format_time(elapsed)}"

    @Property(str, notify=analysisChanged)
    def analysisEtaText(self) -> str:
        if self._analysis_eta_seconds < 0:
            return "预计剩余：正在根据当前算力计算"
        remaining = max(0.0, self._analysis_eta_seconds - (time.monotonic() - self._analysis_eta_updated_at))
        return f"预计剩余约 {self._format_time(remaining)}"

    @Property(str, notify=analysisChanged)
    def analysisEstimatedTotalText(self) -> str:
        if self._analysis_estimated_total < 0:
            return "预计总用时：计算中"
        return f"预计总用时约 {self._format_time(self._analysis_estimated_total)}"

    @Property("QVariantList", notify=eventsChanged)
    def events(self) -> list[dict[str, object]]:
        return self._events

    @Property(bool, notify=storyChanged)
    def storyBusy(self) -> bool:
        return self._story_busy

    @Property(float, notify=storyChanged)
    def storyProgress(self) -> float:
        return self._story_progress

    @Property(str, notify=storyChanged)
    def storyStatus(self) -> str:
        return self._story_status

    @Property(str, notify=storyChanged)
    def storyTitle(self) -> str:
        return str(self._story.get("title", ""))

    @Property(str, notify=storyChanged)
    def storyAngle(self) -> str:
        return str(self._story.get("angle", ""))

    @Property(str, notify=storyChanged)
    def storyStats(self) -> str:
        if not self._story:
            return ""
        return f"{self._story.get('word_count', 0)} 词 · 约 {self._story.get('estimated_duration_sec', 0)} 秒"

    @Property("QVariantList", notify=storyChanged)
    def storyOutline(self) -> list[dict[str, object]]:
        return self._story_outline

    @Property("QVariantList", notify=storyChanged)
    def storyNarration(self) -> list[dict[str, object]]:
        return self._story_narration

    @Property(bool, notify=matchingChanged)
    def matchingBusy(self) -> bool:
        return self._matching_busy

    @Property(str, notify=matchingChanged)
    def matchingStatus(self) -> str:
        return self._matching_status

    @Property("QVariantList", notify=matchingChanged)
    def matches(self) -> list[dict[str, object]]:
        return self._matches

    @Property(str, notify=matchingChanged)
    def roughCutSummary(self) -> str:
        if not self._matches:
            return ""
        total = sum(float(item.get("coverage_sec", 0) or 0) for item in self._matches)
        clips = sum(len(item.get("selected_clips", [])) for item in self._matches)
        covered = sum(1 for item in self._matches if bool(item.get("isCovered", False)))
        return f"{len(self._matches)} 句解说 · {clips} 个镜头 · 粗剪约 {total:.1f} 秒 · 覆盖 {covered}/{len(self._matches)}"

    @Property(bool, notify=exportChanged)
    def exportBusy(self) -> bool:
        return self._export_busy

    @Property(float, notify=exportChanged)
    def exportProgress(self) -> float:
        return self._export_progress

    @Property(str, notify=exportChanged)
    def exportStatus(self) -> str:
        return self._export_status

    @Property(bool, notify=exportChanged)
    def previewVideoReady(self) -> bool:
        return bool(self._export_path and Path(self._export_path).exists())

    @Property(str, notify=exportChanged)
    def previewVideoPath(self) -> str:
        return self._export_path

    @Property(str, notify=voiceChanged)
    def voiceStatus(self) -> str:
        return self._voice_status

    @Property(bool, notify=voiceChanged)
    def ttsPackageReady(self) -> bool:
        if not self._current_project_file:
            return False
        return (self._current_project_file.parent / "script" / "tts" / "gpt_sovits_input.txt").exists()

    @Property(bool, notify=voiceChanged)
    def narrationAudioReady(self) -> bool:
        return bool(self._narration_audio_path and Path(self._narration_audio_path).exists())

    @Property(bool, notify=voiceChanged)
    def syncedSrtReady(self) -> bool:
        return bool(self._synced_srt_path and Path(self._synced_srt_path).exists())

    @Property(str, notify=voiceChanged)
    def narrationDurationText(self) -> str:
        return self._format_time(self._narration_duration_sec) if self._narration_duration_sec else ""

    @Property("QVariantMap", notify=subtitleStyleChanged)
    def subtitleStyle(self) -> dict[str, object]:
        return dict(self._subtitle_style)

    @Property(str, notify=subtitleEffectPreviewChanged)
    def subtitleEffectPreviewUrl(self) -> str:
        return self._subtitle_effect_preview_url or self._cover_url

    @Property(bool, notify=subtitleEffectPreviewChanged)
    def subtitleEffectPreviewBusy(self) -> bool:
        return self._subtitle_effect_preview_busy

    @Property(bool, notify=subtitleEffectPreviewChanged)
    def subtitleEffectPreviewReady(self) -> bool:
        return bool(self._subtitle_effect_preview_url)

    @Property(int, notify=mediaChanged)
    def sourceVideoHeight(self) -> int:
        return int(self._media.get("height", 1080) or 1080)

    @Property(bool, notify=noticeChanged)
    def apiConfigured(self) -> bool:
        return bool(api_configuration(self._config, self._root, "story")["configured"])

    @Property(str, notify=noticeChanged)
    def apiConfigurationHint(self) -> str:
        api = api_configuration(self._config, self._root, "story")
        if api["configured"]:
            endpoint = str(api["base_url"] or "OpenAI 官方接口")
            return f"API 已配置：{api['model']} · {endpoint}"
        return "根目录 .env 中未配置 OPENAI_API_KEY"

    @Property(str, notify=updateChanged)
    def appVersion(self) -> str:
        return self._app_version

    @Property(bool, notify=updateChanged)
    def updateBusy(self) -> bool:
        return self._update_busy

    @Property(bool, notify=updateChanged)
    def updateAvailable(self) -> bool:
        return self._update_available

    @Property(bool, notify=updateChanged)
    def updateInstalled(self) -> bool:
        return self._update_installed

    @Property(str, notify=updateChanged)
    def updateStatus(self) -> str:
        return self._update_status

    @Property(str, notify=updateChanged)
    def remoteVersion(self) -> str:
        return str(self._remote_update.get("version", ""))

    @Property(str, notify=updateChanged)
    def remoteNotes(self) -> str:
        return str(self._remote_update.get("notes", ""))

    @Slot(result=bool)
    def refreshApiConfiguration(self) -> bool:
        configured = self.apiConfigured
        self.noticeChanged.emit()
        return configured

    @Slot()
    def openApiConfigFolder(self) -> None:
        env_file = self._root.parent / ".env"
        target = env_file if env_file.exists() else self._root.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def checkForUpdates(self) -> None:
        if self._update_busy:
            return
        self._update_busy = True
        self._update_installed = False
        self._update_status = "正在连接 GitHub 检查 StoryCut 更新…"
        self.updateChanged.emit()
        self.updateDialogRequested.emit()

        def worker() -> None:
            try:
                _local, remote, newer = check_for_update()
                message = (
                    f"发现 StoryCut v{remote.get('version')} 新版本"
                    if newer
                    else f"当前已是最新版 v{self._app_version}"
                )
                self._updateCheckFinished.emit(bool(newer), message, remote)
            except Exception as exc:
                self._updateCheckFinished.emit(False, f"检查更新失败：{exc}", {})

        threading.Thread(target=worker, name="storycut-update-check", daemon=True).start()

    @Slot()
    def installUpdate(self) -> None:
        if self._update_busy or not self._update_available or not self._remote_update:
            return
        self._update_busy = True
        self._update_status = f"正在安装 StoryCut v{self.remoteVersion}…"
        self.updateChanged.emit()
        remote = dict(self._remote_update)

        def worker() -> None:
            try:
                download_and_apply(remote)
                self._updateApplyFinished.emit(
                    True,
                    f"StoryCut v{remote.get('version')} 已安装。请关闭并重新启动程序",
                )
            except Exception as exc:
                self._updateApplyFinished.emit(False, f"安装更新失败：{exc}")

        threading.Thread(target=worker, name="storycut-update-install", daemon=True).start()

    @Property(str, notify=storyChanged)
    def subtitlePreviewText(self) -> str:
        if self._story_narration:
            return str(self._story_narration[0].get("text_en", ""))
        return "Clear subtitles make every story easier to follow."

    @Slot(str)
    def importVideo(self, url: str) -> None:
        if not url:
            return
        path = Path(QUrlHelper.to_local_path(url))
        self._video_path = str(path)
        self._project_name = path.stem
        project_dir = self._projects_dir / self._safe_name(path.stem)
        project_dir.mkdir(parents=True, exist_ok=True)
        for child in ("source", "analysis", "script", "timeline", "cache", "exports"):
            (project_dir / child).mkdir(exist_ok=True)
        project_file = project_dir / "project.json"
        self._current_project_file = project_file
        created_at = datetime.now().isoformat(timespec="seconds")
        existing_payload: dict[str, object] = {}
        if project_file.exists():
            try:
                existing_payload = json.loads(project_file.read_text(encoding="utf-8"))
                created_at = str(existing_payload.get("created_at", created_at))
            except (OSError, ValueError, TypeError):
                pass
        payload = dict(existing_payload)
        payload.update(
            {
                "schema_version": 1,
                "name": path.stem,
                "source_video": str(path),
                "created_at": created_at,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        payload.setdefault("stage", "imported")
        project_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._notice = "视频已导入。下一步将接入语音识别和场景分析。"
        self._media = dict(payload.get("media") or {})
        cover_path = project_dir / "cache" / "cover.jpg"
        self._cover_url = cover_path.as_uri() if cover_path.exists() else ""
        self._preview_url = ""
        self._preview_position = 0.0
        self._refresh_recent_projects()
        self.projectChanged.emit()
        self.noticeChanged.emit()
        self.mediaChanged.emit()
        self.previewChanged.emit()
        self._load_events(project_file)
        self._load_story(project_file)
        self._load_matches(project_file)
        self._load_export(project_file)
        self._load_voice(project_file)
        self._load_subtitle_style(project_file)
        if not self._media:
            self._start_media_analysis(path, project_file)

    @Slot(str)
    def openProject(self, url: str) -> None:
        if not url:
            return
        project_file = Path(QUrlHelper.to_local_path(url))
        if project_file.is_dir():
            project_file = project_file / "project.json"
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            self._current_project_file = project_file
            self._project_name = str(payload.get("name") or project_file.parent.name)
            self._video_path = str(payload.get("source_video") or "")
            self._media = dict(payload.get("media") or {})
            cover_path = project_file.parent / "cache" / "cover.jpg"
            self._cover_url = cover_path.as_uri() if cover_path.exists() else ""
            self._preview_url = ""
            self._preview_position = 0.0
            stage = str(payload.get("stage") or "imported")
            stage_names = {
                "imported": "已导入",
                "analyzed": "已完成原片分析",
                "transcribed": "已完成语音转录",
                "understood": "已完成原片理解",
                "scripted": "已生成解说文案",
                "matched": "已完成镜头匹配",
                "previewed": "已生成粗剪预览",
                "exported": "已导出成片",
            }
            self._notice = f"项目已恢复：{stage_names.get(stage, stage)}。"
            self.projectChanged.emit()
            self.noticeChanged.emit()
            self.mediaChanged.emit()
            self.previewChanged.emit()
            self._load_events(project_file)
            self._load_story(project_file)
            self._load_matches(project_file)
            self._load_export(project_file)
            self._load_voice(project_file)
            self._load_subtitle_style(project_file)
            if not self._media and self._video_path and Path(self._video_path).exists():
                self._start_media_analysis(Path(self._video_path), project_file)
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法打开项目：{exc}"
            self.noticeChanged.emit()

    @Slot(str)
    def setNotice(self, message: str) -> None:
        self._notice = message
        self.noticeChanged.emit()

    @Slot(float)
    def requestPreviewFrame(self, seconds: float) -> None:
        if not self._video_path or not self._current_project_file:
            return
        duration = self.durationSeconds
        timestamp = min(max(float(seconds), 0.0), duration if duration > 0 else float(seconds))
        self._preview_job_id += 1
        job_id = self._preview_job_id
        self._preview_busy = True
        self._preview_position = timestamp
        self.previewChanged.emit()
        video = Path(self._video_path)
        output = self._current_project_file.parent / "cache" / f"preview_{job_id % 2}.jpg"

        def worker() -> None:
            try:
                backend = extract_preview_frame(video, output, timestamp, self._config, self._root)
                self._previewReady.emit(output.as_uri() + f"?v={job_id}", backend, job_id, timestamp)
            except Exception as exc:
                self._previewReady.emit("", str(exc), job_id, timestamp)

        threading.Thread(target=worker, name="storycut-preview-frame", daemon=True).start()

    @Slot()
    def startUnderstanding(self) -> None:
        if bool(self._config.get("vision", {}).get("enabled", True)) and not self.apiConfigured:
            self._notice = (
                "未配置 AI 接口：视觉描述不会生成，且第 2 步无法组织故事。"
                "请配置根目录 .env，或选择仅执行本地分析。"
            )
            self.noticeChanged.emit()
            return
        self._start_understanding(skip_vision=False)

    @Slot()
    def startUnderstandingLocalOnly(self) -> None:
        self._start_understanding(skip_vision=True)

    def _start_understanding(self, skip_vision: bool) -> None:
        if self._analysis_busy or not self._video_path or not self._current_project_file:
            return
        self._analysis_job_id += 1
        job_id = self._analysis_job_id
        self._analysis_busy = True
        self._analysis_progress = 0.01
        self._analysis_status = "准备理解原片…"
        self._analysis_started_at = time.monotonic()
        self._analysis_estimated_total = self._estimate_analysis_total(self.durationSeconds)
        self._analysis_eta_seconds = self._analysis_estimated_total
        self._analysis_eta_updated_at = self._analysis_started_at
        self._analysis_clock.start()
        self.analysisChanged.emit()
        video = Path(self._video_path)
        project_file = self._current_project_file
        analysis_dir = project_file.parent / "analysis"
        audio = analysis_dir / "audio_16k_mono.wav"

        status_file = analysis_dir / "status.json"

        def report(value: float, status: str, eta_seconds: float = -1.0) -> None:
            status_payload = {
                "state": "running",
                "progress": round(value, 4),
                "status": status,
                "elapsed_sec": round(time.monotonic() - self._analysis_started_at, 1),
                "eta_sec": round(eta_seconds, 1) if eta_seconds >= 0 else None,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._analysisProgressReady.emit(value, status, eta_seconds, job_id)

        def worker() -> None:
            try:
                report(0.03, "正在提取 16 kHz 单声道分析音频…")
                extract_analysis_audio(video, audio, self._config, self._root)
                report(0.10, "音频提取完成，准备加载语音模型…")
                transcribe_started = time.monotonic()
                def transcribe_report(value: float, status: str) -> None:
                    media_ratio = max(0.0, min(1.0, (value - 0.16) / 0.82))
                    eta = -1.0
                    if media_ratio > 0.015:
                        elapsed = time.monotonic() - transcribe_started
                        eta = elapsed * (1.0 - media_ratio) / media_ratio
                        eta += self.durationSeconds * 0.12
                    report(0.10 + value * 0.50, status, eta)

                transcribe_analysis_audio(
                    audio,
                    analysis_dir / "transcript.json",
                    analysis_dir / "transcript.srt",
                    self.durationSeconds,
                    self._config,
                    self._root,
                    transcribe_report,
                )
                report(0.62, "语音转录完成，开始检测场景变化…")
                scene_started = time.monotonic()

                def scene_report(value: float, status: str) -> None:
                    eta = -1.0
                    if value > 0.02:
                        elapsed = time.monotonic() - scene_started
                        eta = elapsed * (1.0 - value) / value
                    report(0.62 + value * 0.18, status, eta)

                detect_scenes_and_keyframes(
                    video,
                    analysis_dir / "scenes.json",
                    analysis_dir / "keyframes",
                    self.durationSeconds,
                    self._config,
                    self._root,
                    scene_report,
                )
                report(0.81, "正在组合字幕、场景和关键帧…")
                build_timeline_events(
                    analysis_dir / "transcript.json",
                    analysis_dir / "scenes.json",
                    analysis_dir / "events.json",
                )
                vision_warning = "用户选择仅执行本地分析，未调用视觉模型" if skip_vision else ""
                if bool(self._config.get("vision", {}).get("enabled", True)) and not skip_vision:
                    try:
                        def vision_report(value: float, status: str) -> None:
                            report(0.82 + value * 0.17, status)

                        describe_event_keyframes(
                            analysis_dir / "events.json",
                            self._config,
                            self._root,
                            vision_report,
                        )
                    except Exception as exc:
                        vision_warning = str(exc)
                        report(0.99, f"视觉描述已跳过：{vision_warning}")
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["stage"] = "understood"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                payload.setdefault("artifacts", {})["transcript"] = "analysis/transcript.json"
                payload["artifacts"]["transcript_srt"] = "analysis/transcript.srt"
                payload["artifacts"]["analysis_audio"] = "analysis/audio_16k_mono.wav"
                payload["artifacts"]["scenes"] = "analysis/scenes.json"
                payload["artifacts"]["keyframes"] = "analysis/keyframes"
                payload["artifacts"]["events"] = "analysis/events.json"
                if vision_warning:
                    payload.setdefault("warnings", {})["vision"] = vision_warning
                else:
                    payload.get("warnings", {}).pop("vision", None)
                project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                if skip_vision:
                    message = "本地结构化完成；未生成视觉描述。配置 API 后建议重新理解原片，再组织故事"
                elif vision_warning:
                    message = f"原片结构化完成；视觉描述生成失败：{vision_warning}"
                else:
                    message = "原片理解完成，可以开始组织故事"
                status_file.write_text(
                    json.dumps(
                        {
                            "state": "completed",
                            "progress": 1.0,
                            "status": message,
                            "elapsed_sec": round(time.monotonic() - self._analysis_started_at, 1),
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                self._analysisFinished.emit(True, message, job_id)
            except Exception as exc:
                analysis_dir.mkdir(parents=True, exist_ok=True)
                (analysis_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
                status_file.write_text(
                    json.dumps(
                        {
                            "state": "failed",
                            "progress": round(self._analysis_progress, 4),
                            "status": str(exc),
                            "elapsed_sec": round(time.monotonic() - self._analysis_started_at, 1),
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                self._analysisFinished.emit(False, str(exc), job_id)

        threading.Thread(target=worker, name="storycut-understanding", daemon=True).start()

    @Slot(int)
    def generateStory(self, target_duration_sec: int) -> None:
        if self._story_busy or not self._current_project_file:
            return
        if not self.apiConfigured:
            message = "未配置 OPENAI_API_KEY，无法生成故事。请在仓库根目录 .env 中配置后重试"
            self._story_status = f"故事生成失败：{message}"
            self._notice = self._story_status
            self.storyChanged.emit()
            self.noticeChanged.emit()
            return
        events_file = self._current_project_file.parent / "analysis" / "events.json"
        if not events_file.exists():
            self._notice = "请先完成第 1 步：理解原片"
            self.noticeChanged.emit()
            return
        self._story_job_id += 1
        job_id = self._story_job_id
        self._story_busy = True
        self._story_progress = 0.02
        self._story_status = "正在准备原片事件…"
        self.storyChanged.emit()
        project_file = self._current_project_file
        story_file = project_file.parent / "script" / "story.json"

        def report(value: float, status: str) -> None:
            self._storyProgressReady.emit(value, status, job_id)

        def worker() -> None:
            try:
                story = generate_story_script(
                    events_file,
                    story_file,
                    max(15, int(target_duration_sec)),
                    self._config,
                    self._root,
                    report,
                )
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["stage"] = "scripted"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                payload.setdefault("artifacts", {})["story"] = "script/story.json"
                project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._storyFinished.emit(True, "故事与英文解说已生成", story, job_id)
            except Exception as exc:
                (project_file.parent / "script").mkdir(parents=True, exist_ok=True)
                (project_file.parent / "script" / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
                self._storyFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-story-writer", daemon=True).start()

    @Slot(int, str)
    def updateNarration(self, index: int, text: str) -> None:
        if index < 0 or index >= len(self._story_narration) or not self._current_project_file:
            return
        cleaned = text.strip()
        self._story_narration[index]["text_en"] = cleaned
        self._story["narration"] = self._story_narration
        story_file = self._current_project_file.parent / "script" / "story.json"
        story_file.write_text(json.dumps(self._story, ensure_ascii=False, indent=2), encoding="utf-8")

    @Slot()
    def generateMatches(self) -> None:
        if self._matching_busy or not self._current_project_file:
            return
        project_file = self._current_project_file
        story_file = project_file.parent / "script" / "story.json"
        events_file = project_file.parent / "analysis" / "events.json"
        if not story_file.exists() or not events_file.exists():
            self._notice = "请先完成原片理解和故事组织"
            self.noticeChanged.emit()
            return
        self._matching_busy = True
        self._matching_status = "正在为每句解说生成候选镜头…"
        self.matchingChanged.emit()
        try:
            matches_file = project_file.parent / "timeline" / "matches.json"
            generate_shot_matches(story_file, events_file, matches_file)
            build_rough_cut(matches_file, project_file.parent / "timeline" / "rough_cut.json")
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            payload["stage"] = "matched"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            payload.setdefault("artifacts", {})["matches"] = "timeline/matches.json"
            payload["artifacts"]["rough_cut"] = "timeline/rough_cut.json"
            project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._matching_status = "镜头匹配完成，可点击候选画面进行替换"
            self._notice = self._matching_status
            self._load_matches(project_file)
            self._refresh_recent_projects()
            self.noticeChanged.emit()
        except Exception as exc:
            self._matching_status = f"镜头匹配失败：{exc}"
            self._notice = self._matching_status
            self.noticeChanged.emit()
        finally:
            self._matching_busy = False
            self.matchingChanged.emit()

    @Slot(int, int)
    def selectMatch(self, narration_id: int, event_id: int) -> None:
        if not self._current_project_file:
            return
        matches_file = self._current_project_file.parent / "timeline" / "matches.json"
        if not matches_file.exists():
            return
        try:
            select_shot_match(matches_file, narration_id, event_id)
            build_rough_cut(matches_file, self._current_project_file.parent / "timeline" / "rough_cut.json")
            self._matching_status = f"第 {narration_id} 句已改用场景 {event_id}"
            self._load_matches(self._current_project_file)
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法替换镜头：{exc}"
            self.noticeChanged.emit()

    @Slot(int, str, float)
    def adjustMatchBoundary(self, narration_id: int, boundary: str, delta_sec: float) -> None:
        if not self._current_project_file:
            return
        matches_file = self._current_project_file.parent / "timeline" / "matches.json"
        if not matches_file.exists():
            return
        try:
            adjust_shot_boundary(matches_file, narration_id, boundary, delta_sec)
            build_rough_cut(matches_file, self._current_project_file.parent / "timeline" / "rough_cut.json")
            self._matching_status = f"第 {narration_id} 句镜头范围已调整并保存"
            self._load_matches(self._current_project_file)
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法调整镜头范围：{exc}"
            self.noticeChanged.emit()

    @Slot()
    def generateRoughPreview(self) -> None:
        if self._export_busy or not self._current_project_file or not self._video_path:
            return
        project_file = self._current_project_file
        rough_cut_file = project_file.parent / "timeline" / "rough_cut.json"
        if not rough_cut_file.exists():
            self._notice = "请先完成第 3 步镜头匹配"
            self.noticeChanged.emit()
            return
        if not self.narrationAudioReady:
            self._notice = "请先导入 GPT-SoVITS 生成的英文配音"
            self.noticeChanged.emit()
            return
        self._export_job_id += 1
        job_id = self._export_job_id
        self._export_busy = True
        self._export_progress = 0.01
        self._export_status = "正在准备成片预览…"
        self.exportChanged.emit()
        output = project_file.parent / "exports" / "rough_preview.mp4"
        source = Path(self._video_path)

        def report(value: float, status: str) -> None:
            self._exportProgressReady.emit(value, status, job_id)

        def worker() -> None:
            try:
                result = render_rough_preview(
                    source,
                    rough_cut_file,
                    output,
                    Path(self._narration_audio_path),
                    Path(self._synced_srt_path) if self.syncedSrtReady else None,
                    int(self._media.get("width", 0) or 0),
                    int(self._media.get("height", 0) or 0),
                    self._config_with_project_style(),
                    self._root,
                    report,
                )
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["stage"] = "previewed"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                payload.setdefault("artifacts", {})["rough_preview"] = "exports/rough_preview.mp4"
                project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._exportFinished.emit(True, "成片预览已生成，可使用系统播放器查看", result, job_id)
            except Exception as exc:
                (project_file.parent / "exports").mkdir(parents=True, exist_ok=True)
                (project_file.parent / "exports" / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
                self._exportFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-rough-preview", daemon=True).start()

    @Slot()
    def openRoughPreview(self) -> None:
        if self._export_path and Path(self._export_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._export_path))

    @Slot()
    def prepareTtsPackage(self) -> None:
        if not self._current_project_file:
            return
        story_file = self._current_project_file.parent / "script" / "story.json"
        if not story_file.exists():
            self._notice = "请先完成第 2 步故事组织"
            self.noticeChanged.emit()
            return
        try:
            output_dir = self._current_project_file.parent / "script" / "tts"
            result = prepare_tts_package(story_file, output_dir)
            self._voice_status = (
                f"GPT-SoVITS 文案已准备：{result['sentence_count']} 句，"
                "请生成整段英文音频后导入"
            )
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {})["tts_input"] = "script/tts/gpt_sovits_input.txt"
            payload["artifacts"]["tts_reference_srt"] = "script/tts/gpt_sovits_reference.srt"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.voiceChanged.emit()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir)))
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法准备 GPT-SoVITS 文案：{exc}"
            self.noticeChanged.emit()

    @Slot(str)
    def saveTtsSrt(self, url: str) -> None:
        if not url or not self._current_project_file:
            return
        try:
            source = self._current_project_file.parent / "script" / "tts" / "gpt_sovits_reference.srt"
            if not source.exists():
                story_file = self._current_project_file.parent / "script" / "story.json"
                prepare_tts_package(story_file, source.parent)
            destination = Path(QUrlHelper.to_local_path(url))
            if destination.suffix.lower() != ".srt":
                destination = destination.with_suffix(".srt")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            self._voice_status = f"英文 SRT 已另存为：{destination}"
            self._notice = "英文 SRT 已保存，可在 GPT-SoVITS 中选择该文件生成配音"
            self.voiceChanged.emit()
            self.noticeChanged.emit()
        except Exception as exc:
            self._notice = f"英文 SRT 另存失败：{exc}"
            self.noticeChanged.emit()

    @Slot(str, str)
    @Slot(str, float)
    @Slot(str, bool)
    def updateSubtitleStyle(self, key: str, value: object) -> None:
        allowed = {
            "fontFamily",
            "fontSize",
            "bottomMargin",
            "horizontalMargin",
            "bold",
            "backgroundEnabled",
            "backgroundOpacity",
            "outlineWidth",
            "boxPadding",
            "cleanupMode",
            "cleanupX",
            "cleanupY",
            "cleanupWidth",
            "cleanupHeight",
            "cleanupOpacity",
            "blurRadius",
            "blurPower",
            "regionPadding",
            "feather",
        }
        if key not in allowed:
            return
        if key in {
            "fontSize", "bottomMargin", "horizontalMargin", "outlineWidth",
            "boxPadding", "blurRadius", "blurPower", "regionPadding", "feather",
        }:
            value = int(float(value))
        elif key in {"cleanupWidth", "cleanupOpacity"}:
            value = min(1.0, max(0.02, float(value)))
        elif key in {
            "backgroundOpacity", "cleanupX", "cleanupY", "cleanupHeight",
        }:
            value = min(0.95, max(0.0, float(value)))
        elif key in {"bold", "backgroundEnabled"}:
            value = bool(value)
        else:
            value = str(value)
        self._subtitle_style[key] = value
        if key == "cleanupMode":
            # The cleanup layer is also the visible subtitle backdrop.  Keep the
            # ASS renderer from drawing a second box immediately around the text.
            self._subtitle_style["backgroundEnabled"] = False
        self._subtitle_effect_preview_url = ""
        self.subtitleEffectPreviewChanged.emit()
        self._save_subtitle_style()

    @Slot()
    def generateSubtitleEffectPreview(self) -> None:
        if not self._video_path or not self._current_project_file:
            self._notice = "请先创建并分析视频项目"
            self.noticeChanged.emit()
            return
        width = int(self._media.get("width", 0) or 0)
        height = int(self._media.get("height", 0) or 0)
        if width <= 0 or height <= 0:
            self._notice = "缺少视频尺寸信息，无法生成真实预览"
            self.noticeChanged.emit()
            return

        self._subtitle_effect_preview_job_id += 1
        job_id = self._subtitle_effect_preview_job_id
        self._subtitle_effect_preview_busy = True
        self.subtitleEffectPreviewChanged.emit()
        video = Path(self._video_path)
        output = self._current_project_file.parent / "cache" / f"subtitle_effect_{job_id % 2}.jpg"
        style = dict(self._subtitle_style)
        duration = float(self._media.get("duration_sec", 0) or 0)
        timestamp = min(max(0.0, self._preview_position), max(0.0, duration - 0.1))
        if timestamp <= 0 and duration > 0:
            timestamp = min(duration * 0.2, max(0.0, duration - 0.1))

        def worker() -> None:
            try:
                render_subtitle_effect_preview(
                    video, output, timestamp, style, width, height, self._config, self._root
                )
                self._subtitleEffectPreviewReady.emit(output.as_uri() + f"?v={job_id}", job_id)
            except Exception:
                self._subtitleEffectPreviewReady.emit("", job_id)

        threading.Thread(target=worker, name="storycut-subtitle-effect-preview", daemon=True).start()

    @Slot(str)
    def applySubtitlePreset(self, preset: str) -> None:
        presets: dict[str, dict[str, object]] = {
            "box": {
                "fontFamily": "Arial",
                "fontSize": 48,
                "bottomMargin": 150,
                "horizontalMargin": 72,
                "bold": True,
                "backgroundEnabled": False,
                "outlineWidth": 3,
                "boxPadding": 12,
            },
            "outline": {
                "fontFamily": "Arial",
                "fontSize": 52,
                "bottomMargin": 80,
                "horizontalMargin": 72,
                "bold": True,
                "backgroundEnabled": False,
                "backgroundOpacity": 0.0,
                "outlineWidth": 4,
                "boxPadding": 10,
            },
            "shorts": {
                "fontFamily": "Arial",
                "fontSize": 60,
                "bottomMargin": 105,
                "horizontalMargin": 90,
                "bold": True,
                "backgroundEnabled": False,
                "outlineWidth": 3,
                "boxPadding": 15,
            },
        }
        selected = presets.get(preset)
        if selected is None:
            self._subtitle_style = self._default_subtitle_style()
        else:
            self._subtitle_style.update(selected)
        self._save_subtitle_style()

    @Slot(str)
    def importNarrationAudio(self, url: str) -> None:
        if not url or not self._current_project_file:
            return
        try:
            source = Path(QUrlHelper.to_local_path(url))
            destination = self._current_project_file.parent / "audio" / "narration.wav"
            result = import_narration_audio(source, destination, self._config, self._root)
            self._narration_audio_path = str(destination)
            self._narration_duration_sec = float(result["duration_sec"])
            self._apply_voice_timing_to_matches()
            self._voice_status = (
                f"英文配音已导入，实际时长 {self._format_time(self._narration_duration_sec)}；"
                + ("已使用同步 SRT 校准" if self.syncedSrtReady else "未导入同步 SRT，暂按句子比例分配")
            )
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {})["narration_audio"] = "audio/narration.wav"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.voiceChanged.emit()
        except Exception as exc:
            self._notice = f"英文配音导入失败：{exc}"
            self.noticeChanged.emit()

    @Slot(str)
    def importNarrationSrt(self, url: str) -> None:
        if not url or not self._current_project_file:
            return
        try:
            source = Path(QUrlHelper.to_local_path(url))
            destination = self._current_project_file.parent / "audio" / "narration.srt"
            result = import_synced_srt(source, destination)
            self._synced_srt_path = str(destination)
            if self.narrationAudioReady:
                self._apply_voice_timing_to_matches(list(result["segments"]))
            self._voice_status = (
                f"同步字幕已导入：{result['segment_count']} 段"
                + ("，镜头时间线已按真实配音校准" if self.narrationAudioReady else "，请继续导入英文音频")
            )
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {})["narration_srt"] = "audio/narration.srt"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.voiceChanged.emit()
        except Exception as exc:
            self._notice = f"同步字幕导入失败：{exc}"
            self.noticeChanged.emit()

    @staticmethod
    def _safe_name(value: str) -> str:
        forbidden = '<>:"/\\|?*'
        cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
        return cleaned or "未命名项目"

    def _refresh_recent_projects(self) -> None:
        projects: list[dict[str, str]] = []
        for project_file in self._projects_dir.glob("*/project.json"):
            try:
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                projects.append(
                    {
                        "name": str(payload.get("name") or project_file.parent.name),
                        "video": str(payload.get("source_video") or ""),
                        "stage": str(payload.get("stage") or "imported"),
                        "updated": str(payload.get("updated_at") or payload.get("created_at") or ""),
                        "projectFile": project_file.as_uri(),
                    }
                )
            except (OSError, ValueError, TypeError):
                continue
        projects.sort(key=lambda item: item["updated"], reverse=True)
        self._recent_projects = projects[:8]
        self.recentProjectsChanged.emit()

    def _start_media_analysis(self, video: Path, project_file: Path) -> None:
        self._media_job_id += 1
        job_id = self._media_job_id
        self._media_busy = True
        self._notice = "正在读取视频信息并生成封面…"
        self.mediaChanged.emit()
        self.noticeChanged.emit()

        def worker() -> None:
            try:
                cover = project_file.parent / "cache" / "cover.jpg"
                media = analyze_media(video, cover, self._config, self._root)
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["media"] = media
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._mediaReady.emit(media, cover.as_uri(), "", job_id)
            except Exception as exc:
                self._mediaReady.emit({}, "", str(exc), job_id)

        threading.Thread(target=worker, name="storycut-media-probe", daemon=True).start()

    @Slot(object, str, str, int)
    def _apply_media_result(self, media: object, cover_url: str, error: str, job_id: int) -> None:
        if job_id != self._media_job_id:
            return
        self._media_busy = False
        if error:
            self._notice = f"视频信息读取失败：{error}"
        else:
            self._media = dict(media) if isinstance(media, dict) else {}
            self._cover_url = cover_url
            backend = str(self._media.get("probe_backend", "unknown"))
            self._notice = f"视频信息读取完成（{backend}）。可以进入原片分析。"
            self._refresh_recent_projects()
        self.mediaChanged.emit()
        self.noticeChanged.emit()

    @Slot(str, str, int, float)
    def _apply_preview_result(self, preview_url: str, status: str, job_id: int, timestamp: float) -> None:
        if job_id != self._preview_job_id:
            return
        self._preview_busy = False
        self._preview_position = timestamp
        if preview_url:
            self._preview_url = preview_url
        else:
            self._notice = f"预览帧读取失败：{status}"
            self.noticeChanged.emit()
        self.previewChanged.emit()

    @Slot(str, int)
    def _apply_subtitle_effect_preview(self, preview_url: str, job_id: int) -> None:
        if job_id != self._subtitle_effect_preview_job_id:
            return
        self._subtitle_effect_preview_busy = False
        if preview_url:
            self._subtitle_effect_preview_url = preview_url
            self._notice = "真实字幕底板预览已生成"
        else:
            self._notice = "真实预览生成失败，请确认 FFmpeg 可用"
        self.subtitleEffectPreviewChanged.emit()
        self.noticeChanged.emit()

    @Slot(float, str, float, int)
    def _apply_analysis_progress(self, value: float, status: str, eta_seconds: float, job_id: int) -> None:
        if job_id != self._analysis_job_id:
            return
        self._analysis_progress = min(max(value, 0.0), 1.0)
        self._analysis_status = status
        if eta_seconds >= 0:
            self._analysis_eta_seconds = eta_seconds
            self._analysis_eta_updated_at = time.monotonic()
        self.analysisChanged.emit()

    @Slot(bool, str, int)
    def _apply_analysis_finished(self, success: bool, message: str, job_id: int) -> None:
        if job_id != self._analysis_job_id:
            return
        self._analysis_busy = False
        self._analysis_clock.stop()
        if success:
            self._analysis_eta_seconds = 0.0
            self._analysis_eta_updated_at = time.monotonic()
        self._analysis_progress = 1.0 if success else self._analysis_progress
        self._analysis_status = message if success else f"分析失败：{message}"
        self._notice = self._analysis_status
        if success:
            self._refresh_recent_projects()
            if self._current_project_file:
                self._load_events(self._current_project_file)
        self.analysisChanged.emit()
        self.noticeChanged.emit()

    @Slot(float, str, int)
    def _apply_story_progress(self, value: float, status: str, job_id: int) -> None:
        if job_id != self._story_job_id:
            return
        self._story_progress = min(max(value, 0.0), 1.0)
        self._story_status = status
        self.storyChanged.emit()

    @Slot(bool, str, object, int)
    def _apply_story_finished(self, success: bool, message: str, story: object, job_id: int) -> None:
        if job_id != self._story_job_id:
            return
        self._story_busy = False
        self._story_progress = 1.0 if success else self._story_progress
        self._story_status = message if success else f"故事生成失败：{message}"
        self._notice = self._story_status
        if success and isinstance(story, dict):
            self._set_story(story)
            self._refresh_recent_projects()
        self.storyChanged.emit()
        self.noticeChanged.emit()

    @Slot(float, str, int)
    def _apply_export_progress(self, value: float, status: str, job_id: int) -> None:
        if job_id != self._export_job_id:
            return
        self._export_progress = min(max(value, 0.0), 1.0)
        self._export_status = status
        self.exportChanged.emit()

    @Slot(bool, str, object, int)
    def _apply_export_finished(self, success: bool, message: str, result: object, job_id: int) -> None:
        if job_id != self._export_job_id:
            return
        self._export_busy = False
        self._export_progress = 1.0 if success else self._export_progress
        self._export_status = message if success else f"Shorts 预览生成失败：{message}"
        self._notice = self._export_status
        if success and isinstance(result, dict):
            self._export_path = str(result.get("path", ""))
            self._refresh_recent_projects()
        self.exportChanged.emit()
        self.noticeChanged.emit()

    @Slot(bool, str, object)
    def _apply_update_check(self, available: bool, message: str, remote: object) -> None:
        self._update_busy = False
        self._update_available = bool(available)
        if isinstance(remote, dict) and remote:
            self._remote_update = dict(remote)
        self._update_status = message
        self.updateChanged.emit()
        self.updateDialogRequested.emit()

    @Slot(bool, str)
    def _apply_update_install(self, success: bool, message: str) -> None:
        self._update_busy = False
        self._update_installed = bool(success)
        if success:
            self._update_available = False
        self._update_status = message
        self.updateChanged.emit()
        self.updateDialogRequested.emit()

    @Slot()
    def _tick_analysis_clock(self) -> None:
        if self._analysis_busy:
            self.analysisChanged.emit()

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"

    def _load_events(self, project_file: Path) -> None:
        events_file = project_file.parent / "analysis" / "events.json"
        loaded: list[dict[str, object]] = []
        if events_file.exists():
            try:
                payload = json.loads(events_file.read_text(encoding="utf-8"))
                for event in payload.get("events", []):
                    item = dict(event)
                    keyframe = project_file.parent / "analysis" / str(event.get("keyframe", ""))
                    item["keyframeUrl"] = keyframe.as_uri() if keyframe.exists() else ""
                    item["timeRange"] = f"{self._format_time(float(event.get('start', 0)))} – {self._format_time(float(event.get('end', 0)))}"
                    item["visualDescription"] = str(event.get("visual_description", "") or "尚无视觉描述")
                    loaded.append(item)
            except (OSError, ValueError, TypeError):
                loaded = []
        self._events = loaded
        self.eventsChanged.emit()

    def _load_story(self, project_file: Path) -> None:
        story_file = project_file.parent / "script" / "story.json"
        if not story_file.exists():
            self._set_story({})
            return
        try:
            self._set_story(json.loads(story_file.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            self._set_story({})

    def _load_matches(self, project_file: Path) -> None:
        matches_file = project_file.parent / "timeline" / "matches.json"
        loaded: list[dict[str, object]] = []
        if matches_file.exists():
            try:
                payload = json.loads(matches_file.read_text(encoding="utf-8"))
                for raw_item in payload.get("items", []):
                    item = dict(raw_item)
                    selected_clips = [dict(clip) for clip in raw_item.get("selected_clips", []) if isinstance(clip, dict)]
                    if not selected_clips and item.get("selected_event_id"):
                        selected_clips = [
                            {
                                "event_id": int(item.get("selected_event_id", 0)),
                                "start": float(item.get("selected_start", 0)),
                                "end": float(item.get("selected_end", 0)),
                            }
                        ]
                    coverage = sum(
                        max(0.0, float(clip.get("end", 0)) - float(clip.get("start", 0)))
                        for clip in selected_clips
                    )
                    required = float(item.get("narration_duration_sec", 0) or 0)
                    item["selected_clips"] = selected_clips
                    item["coverage_sec"] = round(coverage, 3)
                    item["isCovered"] = coverage + 0.05 >= required
                    item["coverageText"] = f"镜头 {coverage:.1f} 秒 / 解说 {required:.1f} 秒"
                    item["selectedRangeText"] = (
                        f"{self._format_time(float(item.get('selected_start', 0)))} – "
                        f"{self._format_time(float(item.get('selected_end', 0)))}"
                    )
                    candidates = []
                    for raw_candidate in raw_item.get("candidates", []):
                        candidate = dict(raw_candidate)
                        keyframe = project_file.parent / "analysis" / str(candidate.get("keyframe", ""))
                        candidate["keyframeUrl"] = keyframe.as_uri() if keyframe.exists() else ""
                        candidate["timeRange"] = (
                            f"{self._format_time(float(candidate.get('start', 0)))} – "
                            f"{self._format_time(float(candidate.get('end', 0)))}"
                        )
                        candidate["scorePercent"] = round(float(candidate.get("score", 0)) * 100)
                        candidates.append(candidate)
                    item["candidates"] = candidates
                    loaded.append(item)
            except (OSError, ValueError, TypeError):
                loaded = []
        self._matches = loaded
        self.matchingChanged.emit()

    def _load_export(self, project_file: Path) -> None:
        output = project_file.parent / "exports" / "rough_preview.mp4"
        self._export_path = str(output) if output.exists() else ""
        self._export_progress = 1.0 if output.exists() else 0.0
        self._export_status = "成片预览已生成，可使用系统播放器查看" if output.exists() else "等待生成成片预览"
        self.exportChanged.emit()

    def _load_voice(self, project_file: Path) -> None:
        audio = project_file.parent / "audio" / "narration.wav"
        srt = project_file.parent / "audio" / "narration.srt"
        self._narration_audio_path = str(audio) if audio.exists() else ""
        self._synced_srt_path = str(srt) if srt.exists() else ""
        self._narration_duration_sec = 0.0
        if audio.exists():
            try:
                from .voice_service import probe_audio_duration

                self._narration_duration_sec = probe_audio_duration(audio, self._config, self._root)
            except Exception:
                self._narration_duration_sec = 0.0
        if audio.exists() and srt.exists():
            self._voice_status = f"英文配音与同步字幕已就绪，时长 {self._format_time(self._narration_duration_sec)}"
        elif audio.exists():
            self._voice_status = f"英文配音已就绪，时长 {self._format_time(self._narration_duration_sec)}；建议导入同步 SRT"
        elif (project_file.parent / "script" / "tts" / "gpt_sovits_input.txt").exists():
            self._voice_status = "GPT-SoVITS 文案已准备，请生成并导入英文配音"
        else:
            self._voice_status = "等待准备 GPT-SoVITS 文案"
        self.voiceChanged.emit()

    def _default_subtitle_style(self) -> dict[str, object]:
        export = self._config.get("export", {})
        return {
            "fontFamily": str(export.get("subtitle_font", "Arial")),
            "fontSize": int(export.get("subtitle_font_size", 48) or 48),
            "bottomMargin": int(export.get("subtitle_margin_v", 72) or 72),
            "horizontalMargin": int(export.get("subtitle_margin_h", 72) or 72),
            "bold": bool(export.get("subtitle_bold", True)),
            "backgroundEnabled": False,
            "backgroundOpacity": float(export.get("subtitle_background_opacity", 0.62) or 0.62),
            "outlineWidth": int(export.get("subtitle_outline_width", 3) or 3),
            "boxPadding": int(export.get("subtitle_box_padding", 12) or 12),
            "cleanupMode": str(export.get("original_subtitle_cleanup_mode", "mask")),
            "cleanupX": float(export.get("original_subtitle_cleanup_x", 0.08) or 0.08),
            "cleanupY": float(export.get("original_subtitle_cleanup_y", 0.82) or 0.82),
            "cleanupWidth": float(export.get("original_subtitle_cleanup_width", 0.84) or 0.84),
            "cleanupHeight": float(export.get("original_subtitle_cleanup_height", 0.14) or 0.14),
            "cleanupOpacity": float(export.get("original_subtitle_cleanup_opacity", 0.78) or 0.78),
            "blurRadius": int(export.get("original_subtitle_blur_radius", 12) or 12),
            "blurPower": int(export.get("original_subtitle_blur_power", 2) or 2),
            "regionPadding": int(export.get("original_subtitle_region_padding", 4) or 4),
            "feather": int(export.get("original_subtitle_feather", 12) or 12),
        }

    def _load_subtitle_style(self, project_file: Path) -> None:
        self._subtitle_style = self._default_subtitle_style()
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            saved = payload.get("settings", {}).get("subtitle", {})
            if isinstance(saved, dict):
                self._subtitle_style.update(saved)
            if self._subtitle_style.get("cleanupMode") not in {"mask", "blur", "delogo"}:
                self._subtitle_style["cleanupMode"] = "mask"
            self._subtitle_style["backgroundEnabled"] = False
        except (OSError, ValueError, TypeError):
            pass
        self.subtitleStyleChanged.emit()

    def _save_subtitle_style(self) -> None:
        if self._current_project_file:
            try:
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                payload.setdefault("settings", {})["subtitle"] = dict(self._subtitle_style)
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError, TypeError):
                pass
        self._export_status = "字幕样式已修改，请重新生成预览"
        self.subtitleStyleChanged.emit()
        self.exportChanged.emit()

    def _config_with_project_style(self) -> dict[str, object]:
        config = deepcopy(self._config)
        export = config.setdefault("export", {})
        style = self._subtitle_style
        export["subtitle_font"] = style["fontFamily"]
        export["subtitle_font_size"] = style["fontSize"]
        export["subtitle_margin_v"] = style["bottomMargin"]
        export["subtitle_margin_h"] = style["horizontalMargin"]
        export["subtitle_bold"] = style["bold"]
        export["subtitle_background_enabled"] = style["backgroundEnabled"]
        export["subtitle_background_opacity"] = style["backgroundOpacity"]
        export["subtitle_outline_width"] = style["outlineWidth"]
        export["subtitle_box_padding"] = style["boxPadding"]
        export["original_subtitle_cleanup_mode"] = style["cleanupMode"]
        export["original_subtitle_cleanup_x"] = style["cleanupX"]
        export["original_subtitle_cleanup_y"] = style["cleanupY"]
        export["original_subtitle_cleanup_width"] = style["cleanupWidth"]
        export["original_subtitle_cleanup_height"] = style["cleanupHeight"]
        export["original_subtitle_cleanup_opacity"] = style["cleanupOpacity"]
        export["original_subtitle_blur_radius"] = style["blurRadius"]
        export["original_subtitle_blur_power"] = style["blurPower"]
        export["original_subtitle_region_padding"] = style["regionPadding"]
        export["original_subtitle_feather"] = style["feather"]
        return config

    def _apply_voice_timing_to_matches(self, segments: list[dict[str, object]] | None = None) -> None:
        if not self._current_project_file:
            return
        matches_file = self._current_project_file.parent / "timeline" / "matches.json"
        if not matches_file.exists():
            return
        if segments is None and self._synced_srt_path and Path(self._synced_srt_path).exists():
            from .voice_service import parse_srt_timings

            segments = parse_srt_timings(Path(self._synced_srt_path).read_text(encoding="utf-8-sig"))
        apply_voice_timing(matches_file, self._narration_duration_sec, segments)
        build_rough_cut(matches_file, self._current_project_file.parent / "timeline" / "rough_cut.json")
        self._load_matches(self._current_project_file)

    def _set_story(self, story: dict[str, object]) -> None:
        self._story = dict(story)
        self._story_outline = [dict(item) for item in story.get("outline", []) if isinstance(item, dict)]
        self._story_narration = [dict(item) for item in story.get("narration", []) if isinstance(item, dict)]
        self.storyChanged.emit()

    def _estimate_analysis_total(self, duration: float) -> float:
        samples: list[tuple[float, float]] = []
        for project_file in self._projects_dir.glob("*/project.json"):
            status_file = project_file.parent / "analysis" / "status.json"
            if not status_file.exists():
                continue
            try:
                project = json.loads(project_file.read_text(encoding="utf-8"))
                status = json.loads(status_file.read_text(encoding="utf-8"))
                media_duration = float(project.get("media", {}).get("duration_sec", 0) or 0)
                elapsed = float(status.get("elapsed_sec", 0) or 0)
                state = str(status.get("state", ""))
                eta = status.get("eta_sec")
                total = elapsed if state == "completed" else elapsed + float(eta or 0)
                if media_duration > 0 and total > 2 and (state == "completed" or eta is not None):
                    samples.append((media_duration, total))
            except (OSError, ValueError, TypeError):
                continue

        if len(samples) >= 2:
            count = float(len(samples))
            sum_x = sum(item[0] for item in samples)
            sum_y = sum(item[1] for item in samples)
            sum_xx = sum(item[0] * item[0] for item in samples)
            sum_xy = sum(item[0] * item[1] for item in samples)
            denominator = count * sum_xx - sum_x * sum_x
            slope = (count * sum_xy - sum_x * sum_y) / denominator if abs(denominator) > 1e-9 else 1.5
            intercept = (sum_y - slope * sum_x) / count
            return max(8.0, max(5.0, intercept) + max(0.1, slope) * duration)
        if samples:
            sample_duration, sample_total = samples[-1]
            fixed = min(35.0, sample_total * 0.55)
            rate = max(0.1, (sample_total - fixed) / sample_duration)
            return max(8.0, fixed + rate * duration)
        return max(15.0, 25.0 + duration * 2.0)



class QUrlHelper:
    @staticmethod
    def to_local_path(value: str) -> str:
        from PySide6.QtCore import QUrl

        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value
