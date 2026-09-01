from __future__ import annotations

from copy import deepcopy
import json
import os
import re
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from .config_manager import load_config, update_user_config
from .analysis_service import (
    attach_visual_samples,
    build_timeline_events,
    detect_scenes_and_keyframes,
    extract_analysis_audio,
    extract_visual_sample_frames,
    transcribe_analysis_audio,
)
from .media_service import analyze_media, extract_preview_frame, render_subtitle_effect_preview
from .vision_service import api_configuration, describe_event_keyframes
from .story_service import (
    generate_story_script,
    narrative_strategy_label,
    narrative_strategy_options,
    normalize_narrative_strategy,
    normalize_story_after_text_edit,
    refresh_story_timing,
)
from .duration_revision_service import propose_duration_revision
from .content_review_service import review_story_content
from .layered_analysis_service import analyze_layered_structure
from .matching_service import (
    apply_voice_timing,
    adjust_shot_boundary,
    build_rough_cut,
    generate_shot_matches,
    select_shot_match,
)
from .export_service import render_rough_preview
from .quality_service import (
    combine_quality_reports,
    inspect_media_content,
    inspect_project_for_export,
    inspect_rendered_video,
)
from .voice_service import (
    SHORTS_MAX_DURATION_SEC,
    import_narration_audio,
    import_synced_srt,
    parse_srt_timings,
    process_narration_speed,
    prepare_tts_srt,
    probe_audio_duration,
    recommended_shorts_speed,
    scale_srt_timeline,
    split_gpt_sovits_units,
    estimate_tts_unit_duration,
)
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
    factReviewChanged = Signal()
    terminologyReviewChanged = Signal()
    matchingChanged = Signal()
    exportChanged = Signal()
    voiceChanged = Signal()
    subtitleStyleChanged = Signal()
    subtitleEffectPreviewChanged = Signal()
    updateChanged = Signal()
    apiModelsChanged = Signal()
    qualityChanged = Signal()
    durationRevisionChanged = Signal()
    updateDialogRequested = Signal()
    sourceVideoRelinkRequested = Signal()
    qualityDialogRequested = Signal()
    durationRevisionDialogRequested = Signal()
    _mediaReady = Signal(object, str, str, int)
    _previewReady = Signal(str, str, int, float)
    _subtitleEffectPreviewReady = Signal(str, int)
    _analysisProgressReady = Signal(float, str, float, int)
    _modelDownloadProgressReady = Signal(float, str, bool, int)
    _analysisFinished = Signal(bool, str, int)
    _storyProgressReady = Signal(float, str, int)
    _storyFinished = Signal(bool, str, object, int)
    _factReviewFinished = Signal(bool, str, object, int)
    _exportProgressReady = Signal(float, str, int)
    _exportFinished = Signal(bool, str, object, int)
    _updateCheckFinished = Signal(bool, str, object)
    _updateApplyFinished = Signal(bool, str)
    _apiModelsFinished = Signal(bool, str, object)
    _voiceProcessingFinished = Signal(bool, str, object, int)
    _voiceDurationReady = Signal(float, str, int)
    _voiceSrtProgressReady = Signal(str, int)
    _voiceSrtFinished = Signal(bool, str, object, int)
    _qualityCheckFinished = Signal(object, int)
    _durationRevisionProgressReady = Signal(str, int)
    _durationRevisionFinished = Signal(bool, str, object, int)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._config = load_config(root)
        self._project_name = "尚未创建项目"
        self._video_path = ""
        self._notice = "导入一个长视频，开始生成精简解说。"
        self._projects_dir = (root / self._config.get("projects_dir", "projects")).resolve()
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._export_dir = (root / self._config.get("export_dir", "../export")).resolve()
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
        self._analysis_content_mode = str(
            self._config.get("analysis", {}).get("content_mode", "speech")
        )
        self._layered_analysis_enabled = bool(
            self._config.get("layered_analysis", {}).get("enabled", True)
        )
        self._analysis_busy = False
        self._analysis_progress = 0.0
        self._analysis_status = "等待开始"
        self._analysis_started_at = 0.0
        self._analysis_eta_seconds = -1.0
        self._analysis_eta_updated_at = 0.0
        self._analysis_estimated_total = -1.0
        self._analysis_eta_reliable = False
        self._analysis_eta_observations: list[tuple[float, float]] = []
        self._model_download_progress = 0.0
        self._model_download_status = ""
        self._model_download_visible = False
        self._events: list[dict[str, object]] = []
        self._story_job_id = 0
        self._story_busy = False
        self._story_progress = 0.0
        self._story_status = "等待组织故事"
        self._narrative_strategy = normalize_narrative_strategy(
            self._config.get("story", {}).get("narrative_strategy", "auto")
        )
        self._story: dict[str, object] = {}
        self._story_outline: list[dict[str, object]] = []
        self._story_narration: list[dict[str, object]] = []
        self._fact_review_job_id = 0
        self._fact_review_busy = False
        self._fact_review_status = "可选功能，尚未进行事实审查"
        self._fact_review: dict[str, object] = {}
        self._fact_review_issues: list[dict[str, object]] = []
        self._fact_review_auto = bool(
            self._config.get("fact_review", {}).get("auto_after_story", False)
        )
        self._terminology_review_job_id = 0
        self._terminology_review_busy = False
        self._terminology_review_status = "可选功能，尚未检查术语一致性"
        self._terminology_review: dict[str, object] = {}
        self._terminology_review_issues: list[dict[str, object]] = []
        self._matching_busy = False
        self._matching_status = "等待匹配镜头"
        self._matches: list[dict[str, object]] = []
        self._export_job_id = 0
        self._export_busy = False
        self._export_progress = 0.0
        self._export_status = "等待生成成片预览"
        self._export_path = ""
        self._preserve_original_audio = bool(
            self._config.get("export", {}).get("preserve_original_audio", False)
        )
        self._voice_status = "等待导出 SRT 到 GPT-SoVITS"
        self._voice_busy = False
        self._voice_job_id = 0
        self._narration_speed = 1.0
        self._narration_audio_path = ""
        self._narration_duration_sec = 0.0
        self._synced_srt_path = ""
        self._duration_revision_busy = False
        self._duration_revision_job_id = 0
        self._duration_revision_status = ""
        self._duration_revision_proposal: dict[str, object] = {}
        self._quality_report: dict[str, object] = {}
        self._quality_busy = False
        self._quality_job_id = 0
        self._subtitle_style = self._default_subtitle_style()
        self._subtitle_effect_preview_url = ""
        self._subtitle_effect_preview_busy = False
        self._subtitle_effect_preview_job_id = 0
        try:
            self._app_version = str(read_version().get("version", "0.2.6"))
        except Exception:
            self._app_version = "0.2.6"
        self._update_busy = False
        self._update_available = False
        self._update_installed = False
        self._update_status = f"当前版本 v{self._app_version}"
        self._remote_update: dict[str, object] = {}
        self._show_update_dialog_after_check = True
        self._api_models_busy = False
        self._api_models_status = "尚未获取模型列表"
        self._api_models: list[str] = []
        self._mediaReady.connect(self._apply_media_result)
        self._previewReady.connect(self._apply_preview_result)
        self._subtitleEffectPreviewReady.connect(self._apply_subtitle_effect_preview)
        self._analysisProgressReady.connect(self._apply_analysis_progress)
        self._modelDownloadProgressReady.connect(self._apply_model_download_progress)
        self._analysisFinished.connect(self._apply_analysis_finished)
        self._storyProgressReady.connect(self._apply_story_progress)
        self._storyFinished.connect(self._apply_story_finished)
        self._factReviewFinished.connect(self._apply_fact_review_finished)
        self._exportProgressReady.connect(self._apply_export_progress)
        self._exportFinished.connect(self._apply_export_finished)
        self._updateCheckFinished.connect(self._apply_update_check)
        self._updateApplyFinished.connect(self._apply_update_install)
        self._apiModelsFinished.connect(self._apply_api_models)
        self._voiceProcessingFinished.connect(self._apply_voice_processing_finished)
        self._voiceDurationReady.connect(self._apply_voice_duration)
        self._voiceSrtProgressReady.connect(self._apply_voice_srt_progress)
        self._voiceSrtFinished.connect(self._apply_voice_srt_finished)
        self._qualityCheckFinished.connect(self._apply_quality_check_finished)
        self._durationRevisionProgressReady.connect(self._apply_duration_revision_progress)
        self._durationRevisionFinished.connect(self._apply_duration_revision_finished)
        self._analysis_clock = QTimer(self)
        self._analysis_clock.setInterval(1000)
        self._analysis_clock.timeout.connect(self._tick_analysis_clock)
        self._refresh_recent_projects()
        QTimer.singleShot(1200, self.checkForUpdatesSilently)

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
    def analysisContentMode(self) -> str:
        return self._analysis_content_mode

    @Property(str, notify=analysisChanged)
    def analysisContentModeHint(self) -> str:
        if self._analysis_content_mode == "visual":
            return "纯画面叙事：跳过语音识别，连续采样画面并详细分析动作、环境和事件变化。"
        return "语音与画面：转写人声，并用关键画面补充上下文（现有默认流程）。"

    @Property(bool, notify=analysisChanged)
    def layeredAnalysisEnabled(self) -> bool:
        return self._layered_analysis_enabled

    @Property(bool, notify=analysisChanged)
    def layeredAnalysisSuggested(self) -> bool:
        threshold = max(
            60.0,
            float(
                self._config.get("layered_analysis", {}).get(
                    "suggestion_threshold_sec", 300
                )
                or 300
            ),
        )
        return self.durationSeconds >= threshold and not self._layered_analysis_enabled

    @Property(str, notify=analysisChanged)
    def layeredAnalysisHint(self) -> str:
        threshold = max(
            60.0,
            float(
                self._config.get("layered_analysis", {}).get(
                    "suggestion_threshold_sec", 300
                )
                or 300
            ),
        )
        if self._layered_analysis_enabled:
            layered_file = (
                self._current_project_file.parent / "analysis" / "layered_structure.json"
                if self._current_project_file
                else None
            )
            if layered_file and layered_file.exists():
                return "分层理解已生成；下次组织故事会引用全片章节、跨场景联系和关键转折。"
            return "已开启；重新理解原片时会增加分段分析与全片综合 API 请求。"
        if self.durationSeconds >= threshold:
            return (
                f"视频达到 {self._format_time(threshold)}，建议开启。"
                "它能连接远距离场景并区分关键转折与重复内容。"
            )
        return "可选增强，默认关闭；不开启时完全沿用当前理解与故事生成流程。"

    @Property(str, notify=analysisChanged)
    def analysisElapsedText(self) -> str:
        elapsed = time.monotonic() - self._analysis_started_at if self._analysis_started_at else 0.0
        return f"已用 {self._format_time(elapsed)}"

    @Property(str, notify=analysisChanged)
    def analysisEtaText(self) -> str:
        if not self._analysis_busy:
            return "处理完成" if self._analysis_progress >= 1.0 else "等待开始"
        if not self._analysis_eta_reliable or self._analysis_eta_seconds < 0:
            return "正在处理"
        remaining = self._analysis_eta_seconds - (time.monotonic() - self._analysis_eta_updated_at)
        if remaining <= 0:
            return "正在处理"
        return f"预计剩余约 {self._format_time(remaining)}"

    @Property(bool, notify=analysisChanged)
    def analysisEtaReliable(self) -> bool:
        return self._analysis_eta_reliable and self._analysis_eta_seconds >= 0

    @Property(str, notify=analysisChanged)
    def analysisEstimatedTotalText(self) -> str:
        return ""

    @Property(float, notify=analysisChanged)
    def modelDownloadProgress(self) -> float:
        return self._model_download_progress

    @Property(str, notify=analysisChanged)
    def modelDownloadStatus(self) -> str:
        return self._model_download_status

    @Property(bool, notify=analysisChanged)
    def modelDownloadVisible(self) -> bool:
        return self._model_download_visible

    @Property(str, notify=analysisChanged)
    def modelDownloadHint(self) -> str:
        analysis = self._config.get("analysis", {})
        model_name = str(analysis.get("asr_model", "large-v3"))
        if bool(analysis.get("auto_download_model", True)):
            return f"本地缺少 {model_name} 时会自动下载；下载完成后自动继续，无需重新点击。"
        return f"模型自动下载已关闭；开始前请把 {model_name} 放入 models/faster-whisper。"

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

    @Property(str, notify=storyChanged)
    def narrativeStrategy(self) -> str:
        return self._narrative_strategy

    @Property("QVariantList", notify=storyChanged)
    def narrativeStrategyOptions(self) -> list[dict[str, str]]:
        return narrative_strategy_options()

    @Property(str, notify=storyChanged)
    def narrativeStrategyHint(self) -> str:
        options = narrative_strategy_options()
        selected = next(
            item for item in options if item["value"] == self._narrative_strategy
        )
        if self._story and self._story.get("narrative_strategy_requested") == self._narrative_strategy:
            resolved = str(self._story.get("narrative_strategy", ""))
            if self._narrative_strategy == "auto" and resolved:
                reason = str(self._story.get("narrative_strategy_reason_zh", "")).strip()
                suffix = f"：{reason}" if reason else ""
                return f"本稿自动采用“{narrative_strategy_label(resolved)}”{suffix}"
        return selected["description"]

    @Property(bool, notify=factReviewChanged)
    def factReviewBusy(self) -> bool:
        return self._fact_review_busy

    @Property(bool, notify=factReviewChanged)
    def factReviewAuto(self) -> bool:
        return self._fact_review_auto

    @Property(str, notify=factReviewChanged)
    def factReviewStatus(self) -> str:
        return self._fact_review_status

    @Property(str, notify=factReviewChanged)
    def factReviewSummary(self) -> str:
        return str(self._fact_review.get("summary_zh", ""))

    @Property(str, notify=factReviewChanged)
    def factReviewDisclaimer(self) -> str:
        return str(
            self._fact_review.get(
                "disclaimer", "AI 辅助审查，未联网检索权威来源；高风险内容仍建议人工核对。"
            )
        )

    @Property("QVariantList", notify=factReviewChanged)
    def factReviewIssues(self) -> list[dict[str, object]]:
        return self._fact_review_issues

    @Property(bool, notify=terminologyReviewChanged)
    def terminologyReviewBusy(self) -> bool:
        return self._terminology_review_busy

    @Property(str, notify=terminologyReviewChanged)
    def terminologyReviewStatus(self) -> str:
        return self._terminology_review_status

    @Property(str, notify=terminologyReviewChanged)
    def terminologyReviewSummary(self) -> str:
        return str(self._terminology_review.get("summary_zh", ""))

    @Property(str, notify=terminologyReviewChanged)
    def terminologyReviewDisclaimer(self) -> str:
        return str(
            self._terminology_review.get(
                "disclaimer",
                "只检查文案中的术语、单位、名称和数字一致性；具体发音由配音工具处理。",
            )
        )

    @Property("QVariantList", notify=terminologyReviewChanged)
    def terminologyReviewIssues(self) -> list[dict[str, object]]:
        return self._terminology_review_issues

    @Property("QVariantList", notify=terminologyReviewChanged)
    def terminologyCanonicalTerms(self) -> list[dict[str, object]]:
        return [
            dict(item)
            for item in self._terminology_review.get("canonical_terms", [])
            if isinstance(item, dict)
        ]

    @Property(bool, notify=terminologyReviewChanged)
    def contentReviewBusy(self) -> bool:
        return self._fact_review_busy or self._terminology_review_busy

    @Property(str, notify=terminologyReviewChanged)
    def contentReviewStatus(self) -> str:
        if self.contentReviewBusy:
            return "正在一次完成事实、证据与术语综合审查…"
        if not self._fact_review and not self._terminology_review:
            return "可选功能，尚未进行文案审查"
        if bool(self._fact_review.get("stale", False)) or bool(
            self._terminology_review.get("stale", False)
        ):
            applied_count = sum(
                bool(item.get("applied", False)) for item in self.contentReviewIssues
            )
            if applied_count:
                return f"已应用 {applied_count} 条建议；文案已更新，建议重新审查"
            return "英文解说已修改，旧文案审查结果需要重新检查"
        all_issues = self.contentReviewIssues
        applied_count = sum(bool(item.get("applied", False)) for item in all_issues)
        pending_count = self.contentReviewPendingCount
        if all_issues and pending_count == 0 and applied_count:
            return f"文案审查完成：全部 {applied_count} 条可应用建议已应用"
        if applied_count:
            return f"已应用 {applied_count} 条建议，剩余 {pending_count} 条待处理"
        fact_count = int(self._fact_review.get("issue_count", 0) or 0)
        term_count = int(self._terminology_review.get("issue_count", 0) or 0)
        if fact_count + term_count == 0:
            return "文案审查完成：未发现明显事实或术语问题"
        return f"文案审查完成：发现 {fact_count + term_count} 项建议"

    @Property(str, notify=terminologyReviewChanged)
    def contentReviewSummary(self) -> str:
        summaries = [
            str(self._fact_review.get("summary_zh", "")).strip(),
            str(self._terminology_review.get("summary_zh", "")).strip(),
        ]
        return "；".join(item for item in summaries if item)

    @Property(str, notify=terminologyReviewChanged)
    def contentReviewBreakdown(self) -> str:
        if not self._fact_review and not self._terminology_review:
            return ""
        fact_count = int(self._fact_review.get("issue_count", 0) or 0)
        term_count = int(self._terminology_review.get("issue_count", 0) or 0)
        canonical_count = len(self._terminology_review.get("canonical_terms", []))
        fact_text = f"事实与证据：{fact_count} 项建议" if fact_count else "事实与证据：已检查通过"
        term_text = f"术语一致性：{term_count} 项建议" if term_count else "术语一致性：已检查通过"
        if canonical_count:
            term_text += f"，已整理 {canonical_count} 个标准术语"
        return f"{fact_text}　｜　{term_text}"

    @Property("QVariantList", notify=terminologyReviewChanged)
    def contentReviewIssues(self) -> list[dict[str, object]]:
        combined: list[dict[str, object]] = []
        for item in self._fact_review_issues:
            value = dict(item)
            value.update(
                {
                    "reviewType": "fact",
                    "reviewTypeText": "事实与证据",
                    "titleText": f"{value.get('severityText', '建议')} · {value.get('categoryText', '事实')}",
                    "subjectText": str(value.get("claim_en", "")),
                }
            )
            combined.append(value)
        for item in self._terminology_review_issues:
            value = dict(item)
            variants = str(value.get("variantsText", "")).strip()
            term = str(value.get("term", "")).strip()
            value.update(
                {
                    "reviewType": "terminology",
                    "reviewTypeText": "术语一致性",
                    "titleText": str(value.get("categoryText", "术语")),
                    "subjectText": variants or term,
                }
            )
            combined.append(value)
        return combined

    @Property(int, notify=terminologyReviewChanged)
    def contentReviewPendingCount(self) -> int:
        return sum(
            1
            for item in self.contentReviewIssues
            if not bool(item.get("applied", False))
            and len(item.get("narration_ids", [])) == 1
            and bool(str(item.get("suggestion_en", "")).strip())
            and len(
                split_gpt_sovits_units(str(item.get("suggestion_en", "")))
            )
            == 1
        )

    @Property("QVariantList", notify=terminologyReviewChanged)
    def contentReviewCanonicalTerms(self) -> list[dict[str, object]]:
        return self.terminologyCanonicalTerms

    @Property(str, notify=terminologyReviewChanged)
    def contentReviewDisclaimer(self) -> str:
        return (
            "一次 API 请求同时检查事实、原片证据和术语一致性；"
            "未联网检索权威来源，具体发音仍由配音工具处理。"
        )

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

    @Slot(result=str)
    def prepareTtsSrtExportUrl(self) -> str:
        if not self._current_project_file:
            return ""
        self._export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_name(self._project_name)}_gpt_sovits.srt"
        return QUrl.fromLocalFile(
            str(self._export_dir / filename)
        ).toString()

    @Property(bool, notify=exportChanged)
    def preserveOriginalAudio(self) -> bool:
        return self._preserve_original_audio

    @Property(str, notify=voiceChanged)
    def voiceStatus(self) -> str:
        return self._voice_status

    @Property(bool, notify=voiceChanged)
    def narrationAudioReady(self) -> bool:
        return bool(self._narration_audio_path and Path(self._narration_audio_path).exists())

    @Property(bool, notify=voiceChanged)
    def syncedSrtReady(self) -> bool:
        return bool(self._synced_srt_path and Path(self._synced_srt_path).exists())

    @Property(str, notify=voiceChanged)
    def narrationDurationText(self) -> str:
        return self._format_time(self._narration_duration_sec) if self._narration_duration_sec else ""

    @Property(bool, notify=voiceChanged)
    def voiceBusy(self) -> bool:
        return self._voice_busy

    @Property(float, notify=voiceChanged)
    def narrationSpeed(self) -> float:
        return self._narration_speed

    @Property(bool, notify=voiceChanged)
    def narrationOverShortsLimit(self) -> bool:
        return self._narration_duration_sec >= SHORTS_MAX_DURATION_SEC

    @Property(bool, notify=voiceChanged)
    def canAutoFitNarration(self) -> bool:
        if not self.narrationAudioReady or not self.narrationOverShortsLimit:
            return False
        original_duration = self._narration_duration_sec * max(1.0, self._narration_speed)
        return recommended_shorts_speed(original_duration) is not None

    @Property(str, notify=voiceChanged)
    def narrationSpeedHint(self) -> str:
        if not self.narrationAudioReady:
            return "导入真实配音后，程序会检查是否超过 Shorts 三分钟上限。"
        speed_text = f"当前 {self._narration_speed:.2f}x"
        if not self.narrationOverShortsLimit:
            return f"{speed_text} · 实际配音在 179 秒安全线内。"
        original_duration = self._narration_duration_sec * max(1.0, self._narration_speed)
        suggested = recommended_shorts_speed(original_duration)
        if suggested is None:
            return f"{speed_text} · 超时较多，最高 1.25x 仍不够，需要删减或重写文案。"
        return f"{speed_text} · 可自动调整为 {suggested:.2f}x，并同步缩放 SRT 时间轴。"

    @Property(bool, notify=voiceChanged)
    def canReviseNarrationDuration(self) -> bool:
        if not self.narrationAudioReady or not self.narrationOverShortsLimit:
            return False
        original_duration = self._narration_duration_sec * max(1.0, self._narration_speed)
        return recommended_shorts_speed(original_duration) is None

    @Property(bool, notify=durationRevisionChanged)
    def durationRevisionBusy(self) -> bool:
        return self._duration_revision_busy

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionStatus(self) -> str:
        return self._duration_revision_status

    @Property(bool, notify=durationRevisionChanged)
    def durationRevisionReady(self) -> bool:
        return bool(self._duration_revision_proposal.get("revised_story"))

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionSummary(self) -> str:
        return str(self._duration_revision_proposal.get("summary_zh", ""))

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionBeforeStats(self) -> str:
        if not self._duration_revision_proposal:
            return ""
        return (
            f"{int(self._duration_revision_proposal.get('current_word_count', 0))} 词 · "
            f"真实配音 {self._format_time(float(self._duration_revision_proposal.get('actual_duration_sec', 0)))}"
        )

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionAfterStats(self) -> str:
        if not self._duration_revision_proposal:
            return ""
        return (
            f"{int(self._duration_revision_proposal.get('revised_word_count', 0))} 词 · "
            f"按本次语速预计 {self._format_time(float(self._duration_revision_proposal.get('projected_duration_sec', 0)))}"
        )

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionBeforeText(self) -> str:
        return "\n\n".join(
            str(item.get("text_en", "")).strip()
            for item in self._story_narration
            if str(item.get("text_en", "")).strip()
        )

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionAfterText(self) -> str:
        revised = self._duration_revision_proposal.get("revised_story", {})
        if not isinstance(revised, dict):
            return ""
        return "\n\n".join(
            str(item.get("text_en", "")).strip()
            for item in revised.get("narration", [])
            if isinstance(item, dict) and str(item.get("text_en", "")).strip()
        )

    @Property("QVariantList", notify=durationRevisionChanged)
    def durationRevisionChanges(self) -> list[str]:
        changes = self._duration_revision_proposal.get("removed_or_merged", [])
        return [str(item) for item in changes] if isinstance(changes, list) else []

    @Property(bool, notify=durationRevisionChanged)
    def canRestoreDurationRevision(self) -> bool:
        return self._duration_revision_archive_path() is not None

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionArchiveText(self) -> str:
        archive = self._duration_revision_archive_path()
        if not archive:
            return ""
        manifest_file = archive / "manifest.json"
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            created = str(manifest.get("created_at", "")).replace("T", " ")[:19]
            return f"可恢复应用前版本 · {created}" if created else "可恢复应用前版本"
        except (OSError, ValueError, TypeError):
            return "可恢复应用前版本"

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionRestoreCurrentStats(self) -> str:
        words = int(self._story.get("word_count", 0) or 0)
        duration = float(self._story.get("estimated_duration_sec", 0) or 0)
        if self.narrationAudioReady and self._narration_duration_sec > 0:
            return f"{words} 词 · 实际配音 {self._format_time(self._narration_duration_sec)}"
        return f"{words} 词 · 预计 {self._format_time(duration)}"

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionRestoreCurrentText(self) -> str:
        return "\n\n".join(
            str(item.get("text_en", "")).strip()
            for item in self._story_narration
            if str(item.get("text_en", "")).strip()
        )

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionRestoreArchivedStats(self) -> str:
        bundle = self._duration_revision_archive_bundle()
        story = bundle.get("story", {})
        project = bundle.get("project", {})
        words = int(story.get("word_count", 0) or 0) if isinstance(story, dict) else 0
        voice = project.get("settings", {}).get("voice", {}) if isinstance(project, dict) else {}
        voice = voice if isinstance(voice, dict) else {}
        actual_duration = float(voice.get("duration_sec", 0) or 0)
        if actual_duration > 0:
            return f"{words} 词 · 实际配音 {self._format_time(actual_duration)}"
        estimate = float(story.get("estimated_duration_sec", 0) or 0) if isinstance(story, dict) else 0
        return f"{words} 词 · 预计 {self._format_time(estimate)}"

    @Property(str, notify=durationRevisionChanged)
    def durationRevisionRestoreArchivedText(self) -> str:
        story = self._duration_revision_archive_bundle().get("story", {})
        if not isinstance(story, dict):
            return ""
        return "\n\n".join(
            str(item.get("text_en", "")).strip()
            for item in story.get("narration", [])
            if isinstance(item, dict) and str(item.get("text_en", "")).strip()
        )

    @Property(str, notify=qualityChanged)
    def qualityCheckText(self) -> str:
        if not self._quality_report:
            return "尚未执行成片检查。"
        lines = [
            (
                f"检查完成：{self._quality_report.get('pass_count', 0)} 项正常，"
                f"{self._quality_report.get('info_count', 0)} 项说明，"
                f"{self._quality_report.get('warning_count', 0)} 项提醒，"
                f"{self._quality_report.get('error_count', 0)} 项必须处理。"
            ),
            "",
        ]
        icons = {"pass": "✓", "info": "i", "warning": "!", "error": "×"}
        for item in self._quality_report.get("checks", []):
            if not isinstance(item, dict):
                continue
            level = str(item.get("level", "warning"))
            lines.append(f"{icons.get(level, '•')} {item.get('title', '')}")
            lines.append(f"   {item.get('detail', '')}")
            lines.append("")
        return "\n".join(lines).rstrip()

    @Property(bool, notify=qualityChanged)
    def qualityCheckPassed(self) -> bool:
        return bool(self._quality_report.get("passed", False))

    @Property(bool, notify=qualityChanged)
    def qualityCheckBusy(self) -> bool:
        return self._quality_busy

    @Property(int, notify=qualityChanged)
    def qualityPassCount(self) -> int:
        return int(self._quality_report.get("pass_count", 0) or 0)

    @Property(int, notify=qualityChanged)
    def qualityInfoCount(self) -> int:
        return int(self._quality_report.get("info_count", 0) or 0)

    @Property(int, notify=qualityChanged)
    def qualityWarningCount(self) -> int:
        return int(self._quality_report.get("warning_count", 0) or 0)

    @Property(int, notify=qualityChanged)
    def qualityErrorCount(self) -> int:
        return int(self._quality_report.get("error_count", 0) or 0)

    @Property("QVariantList", notify=qualityChanged)
    def qualityCheckItems(self) -> list[dict[str, object]]:
        return [
            dict(item)
            for item in self._quality_report.get("checks", [])
            if isinstance(item, dict)
        ]

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
    def sourceVideoWidth(self) -> int:
        return int(self._media.get("width", 1920) or 1920)

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

    @Property(str, notify=noticeChanged)
    def apiBaseUrl(self) -> str:
        return str(api_configuration(self._config, self._root, "story")["base_url"])

    @Property(str, notify=noticeChanged)
    def apiKey(self) -> str:
        return str(api_configuration(self._config, self._root, "story")["api_key"])

    @Property(str, notify=noticeChanged)
    def apiKeyMasked(self) -> str:
        key = self.apiKey
        if not key:
            return "未配置"
        if len(key) <= 8:
            return "••••••••"
        return f"{key[:3]}••••••{key[-4:]}"

    @Property(str, notify=noticeChanged)
    def storyApiModel(self) -> str:
        return str(self._config.get("story", {}).get("model", "gpt-4o-mini"))

    @Property(str, notify=noticeChanged)
    def storyEditorApiModel(self) -> str:
        return str(self._config.get("story", {}).get("editor_model", ""))

    @Property(str, notify=noticeChanged)
    def visionApiModel(self) -> str:
        return str(self._config.get("vision", {}).get("model", "gpt-4o-mini"))

    @Property(bool, notify=apiModelsChanged)
    def apiModelsBusy(self) -> bool:
        return self._api_models_busy

    @Property(str, notify=apiModelsChanged)
    def apiModelsStatus(self) -> str:
        return self._api_models_status

    @Property("QStringList", notify=apiModelsChanged)
    def apiModels(self) -> list[str]:
        return self._api_models

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

    @Slot(str, str, result=bool)
    @Slot(str, str, str, str, result=bool)
    @Slot(str, str, str, str, str, result=bool)
    def saveApiConfiguration(
        self,
        api_key: str,
        base_url: str,
        story_model: str = "",
        vision_model: str = "",
        editor_model: str = "",
    ) -> bool:
        cleaned_key = api_key.strip()
        cleaned_url = base_url.strip()
        cleaned_story_model = story_model.strip() or self.storyApiModel
        cleaned_vision_model = vision_model.strip() or self.visionApiModel
        cleaned_editor_model = editor_model.strip()
        if not cleaned_key:
            self._notice = "API Key 不能为空"
            self.noticeChanged.emit()
            return False
        if "\n" in cleaned_key or "\r" in cleaned_key or "\n" in cleaned_url or "\r" in cleaned_url:
            self._notice = "API 配置不能包含换行符"
            self.noticeChanged.emit()
            return False
        if any(
            char in cleaned_story_model + cleaned_vision_model + cleaned_editor_model
            for char in "\r\n"
        ):
            self._notice = "模型名称不能包含换行符"
            self.noticeChanged.emit()
            return False
        try:
            cleaned_url = self._normalize_api_base_url(cleaned_url)
        except ValueError as exc:
            self._notice = str(exc)
            self.noticeChanged.emit()
            return False

        env_file = self._root.parent / ".env"
        try:
            self._update_env_file(
                env_file,
                {
                    "OPENAI_API_KEY": cleaned_key,
                    "OPENAI_BASE_URL": cleaned_url,
                },
            )
            update_user_config(
                self._root,
                {
                    "story": {
                        "model": cleaned_story_model,
                        "editor_model": cleaned_editor_model,
                    },
                    "vision": {"model": cleaned_vision_model},
                },
            )
            # 当前进程可能已经加载过旧值，保存后立即同步，避免必须重启应用。
            os.environ["OPENAI_API_KEY"] = cleaned_key
            if cleaned_url:
                os.environ["OPENAI_BASE_URL"] = cleaned_url
            else:
                os.environ.pop("OPENAI_BASE_URL", None)
            self._config = load_config(self._root)
            self._notice = "API 配置已保存并立即生效"
            self.noticeChanged.emit()
            return True
        except Exception as exc:
            self._notice = f"API 配置保存失败：{exc}"
            self.noticeChanged.emit()
            return False

    @Slot(str, str)
    def fetchApiModels(self, api_key: str, base_url: str) -> None:
        if self._api_models_busy:
            return
        cleaned_key = api_key.strip()
        if not cleaned_key:
            self._api_models_status = "请先填写 API Key"
            self.apiModelsChanged.emit()
            return
        try:
            cleaned_url = self._normalize_api_base_url(base_url.strip())
        except ValueError as exc:
            self._api_models_status = str(exc)
            self.apiModelsChanged.emit()
            return

        self._api_models_busy = True
        self._api_models_status = "正在从接口获取可用模型…"
        self._api_models = []
        self.apiModelsChanged.emit()

        def worker() -> None:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=cleaned_key, base_url=cleaned_url or None)
                response = client.models.list()
                models = sorted(
                    {
                        str(getattr(item, "id", "")).strip()
                        for item in response.data
                        if str(getattr(item, "id", "")).strip()
                    },
                    key=str.casefold,
                )
                if not models:
                    raise RuntimeError("接口返回了空模型列表")
                self._apiModelsFinished.emit(
                    True,
                    f"已获取 {len(models)} 个模型",
                    models,
                )
            except Exception as exc:
                message = str(exc).strip() or type(exc).__name__
                status_code = getattr(exc, "status_code", None)
                if status_code in {404, 405} or "404" in message or "405" in message:
                    message = (
                        "该中转站没有开放 /models 列表接口，或接口地址不正确。"
                        "你仍可关闭列表并手动填写模型名称。"
                    )
                else:
                    message = f"获取模型列表失败：{message}"
                self._apiModelsFinished.emit(False, message, [])

        threading.Thread(target=worker, name="storycut-api-models", daemon=True).start()

    @Slot()
    def checkForUpdates(self) -> None:
        self._start_update_check(show_dialog=True)

    @Slot()
    def checkForUpdatesSilently(self) -> None:
        self._start_update_check(show_dialog=False)

    def _start_update_check(self, show_dialog: bool) -> None:
        if self._update_busy:
            return
        self._show_update_dialog_after_check = show_dialog
        self._update_busy = True
        self._update_installed = False
        self._update_status = "正在连接 GitHub 检查仓库更新…"
        self.updateChanged.emit()
        if show_dialog:
            self.updateDialogRequested.emit()

        def worker() -> None:
            try:
                _local, remote, newer = check_for_update()
                message = (
                    f"发现仓库新版本 v{remote.get('version')}"
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
        self._update_status = f"正在同步 GitHub 仓库 v{self.remoteVersion}…"
        self.updateChanged.emit()
        remote = dict(self._remote_update)

        def worker() -> None:
            try:
                download_and_apply(remote)
                self._updateApplyFinished.emit(
                    True,
                    f"仓库程序文件已同步到 v{remote.get('version')}。请关闭并重新启动程序",
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
        self._project_name = self._next_project_name()
        project_dir = self._projects_dir / self._project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        for child in ("source", "analysis", "script", "timeline", "cache"):
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
                "name": self._project_name,
                "source_video": str(path),
                "created_at": created_at,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        payload.setdefault("stage", "imported")
        settings = payload.setdefault("settings", {})
        if isinstance(settings, dict):
            saved_mode = str(settings.get("content_mode", self._analysis_content_mode))
            self._analysis_content_mode = saved_mode if saved_mode in {"speech", "visual"} else "speech"
            settings["content_mode"] = self._analysis_content_mode
            settings.setdefault("fact_review_auto", self._fact_review_auto)
            saved_strategy = normalize_narrative_strategy(
                settings.get("narrative_strategy", self._narrative_strategy)
            )
            self._narrative_strategy = saved_strategy
            settings["narrative_strategy"] = saved_strategy
            self._layered_analysis_enabled = bool(
                self._config.get("layered_analysis", {}).get("enabled", True)
            )
            settings["layered_analysis_enabled"] = self._layered_analysis_enabled
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
    def renameProject(self, value: str) -> None:
        if not self._current_project_file:
            return
        if any(
            (
                self._media_busy,
                self._preview_busy,
                self._analysis_busy,
                self._story_busy,
                self._fact_review_busy,
                self._quality_busy,
                self._matching_busy,
                self._export_busy,
                self._voice_busy,
                self._subtitle_effect_preview_busy,
            )
        ):
            self._notice = "当前有任务正在运行，请等待任务完成后再修改项目名。"
            self.noticeChanged.emit()
            self.projectChanged.emit()
            return

        requested = value.strip()
        if not requested:
            self._notice = "项目名不能为空。"
            self.noticeChanged.emit()
            self.projectChanged.emit()
            return
        new_name = self._safe_name(requested[:64])
        if new_name == self._project_name:
            self.projectChanged.emit()
            return

        try:
            current_file = self._current_project_file.resolve()
            current_dir = current_file.parent
            if current_dir.parent != self._projects_dir.resolve():
                raise ValueError("当前项目目录不在 StoryCut 项目目录中")
            target_dir = self._projects_dir / new_name
            if target_dir.exists() and target_dir.resolve() != current_dir:
                raise ValueError(f"项目“{new_name}”已存在，请换一个名称")

            if target_dir.resolve() != current_dir:
                current_dir.rename(target_dir)
            renamed_file = target_dir / "project.json"
            payload = json.loads(renamed_file.read_text(encoding="utf-8"))
            payload["name"] = new_name
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            renamed_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._current_project_file = renamed_file
            self._project_name = new_name
            cover_path = target_dir / "cache" / "cover.jpg"
            self._cover_url = cover_path.as_uri() if cover_path.exists() else ""
            self._preview_url = ""
            self._subtitle_effect_preview_url = ""
            self._refresh_recent_projects()
            self.projectChanged.emit()
            self.mediaChanged.emit()
            self.previewChanged.emit()
            self.subtitleEffectPreviewChanged.emit()
            self._load_events(renamed_file)
            self._load_story(renamed_file)
            self._load_matches(renamed_file)
            self._load_export(renamed_file)
            self._load_voice(renamed_file)
            self._load_subtitle_style(renamed_file)
            self._notice = f"项目已重命名为“{new_name}”。之后导出的文件将使用新项目名。"
            self.noticeChanged.emit()
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法修改项目名：{exc}"
            self.noticeChanged.emit()
            self.projectChanged.emit()

    @Slot(str)
    def relinkSourceVideo(self, url: str) -> None:
        if not url or not self._current_project_file:
            return
        path = Path(QUrlHelper.to_local_path(url))
        if not path.exists() or not path.is_file():
            self._notice = "选择的原视频不存在"
            self.noticeChanged.emit()
            return
        candidate_cover = self._current_project_file.parent / "cache" / "relink_candidate.jpg"
        try:
            metadata = analyze_media(path, candidate_cover, self._config, self._root)
            expected_duration = float(self._media.get("duration_sec", 0) or 0)
            expected_width = int(self._media.get("width", 0) or 0)
            expected_height = int(self._media.get("height", 0) or 0)
            actual_duration = float(metadata.get("duration_sec", 0) or 0)
            actual_width = int(metadata.get("width", 0) or 0)
            actual_height = int(metadata.get("height", 0) or 0)
            duration_tolerance = max(1.0, expected_duration * 0.005)
            if expected_duration and abs(actual_duration - expected_duration) > duration_tolerance:
                raise ValueError("所选视频时长与当前项目不一致，请选择原来分析的那一个视频")
            if expected_width and expected_height and (
                actual_width != expected_width or actual_height != expected_height
            ):
                raise ValueError("所选视频分辨率与当前项目不一致，请选择原来分析的那一个视频")
            cover = self._current_project_file.parent / "cache" / "cover.jpg"
            if candidate_cover.exists():
                shutil.copy2(candidate_cover, cover)
            self._media = metadata
            self._update_source_video_path(path, "原视频已重新关联，可以继续生成预览")
            self.mediaChanged.emit()
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法重新关联原视频：{exc}"
            self.noticeChanged.emit()
        finally:
            try:
                candidate_cover.unlink(missing_ok=True)
            except OSError:
                pass

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
            saved_mode = str(payload.get("settings", {}).get("content_mode", "speech"))
            self._analysis_content_mode = saved_mode if saved_mode in {"speech", "visual"} else "speech"
            self._fact_review_auto = bool(
                payload.get("settings", {}).get(
                    "fact_review_auto",
                    self._config.get("fact_review", {}).get("auto_after_story", False),
                )
            )
            self._narrative_strategy = normalize_narrative_strategy(
                payload.get("settings", {}).get(
                    "narrative_strategy",
                    self._config.get("story", {}).get("narrative_strategy", "auto"),
                )
            )
            self._layered_analysis_enabled = bool(
                self._config.get("layered_analysis", {}).get("enabled", True)
            )
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
        except Exception as exc:
            self._notice = f"无法打开项目：{exc}"
            self.noticeChanged.emit()

    @Slot(str)
    def deleteProject(self, url: str) -> None:
        if not url:
            return
        if (
            self._media_busy
            or self._preview_busy
            or self._analysis_busy
            or self._story_busy
            or self._fact_review_busy
            or self._quality_busy
            or self._matching_busy
            or self._export_busy
            or self._subtitle_effect_preview_busy
        ):
            self._notice = "当前有任务正在运行，请等待任务完成后再删除项目。"
            self.noticeChanged.emit()
            return

        try:
            project_file = Path(QUrlHelper.to_local_path(url)).resolve()
            projects_dir = self._projects_dir.resolve()
            if project_file.name.lower() != "project.json":
                raise ValueError("目标不是 StoryCut 项目文件")
            project_dir = project_file.parent
            if project_dir.parent != projects_dir or not project_file.is_file():
                raise ValueError("只能删除 StoryCut 项目目录内的项目")

            deleting_current = (
                self._current_project_file is not None
                and self._current_project_file.resolve() == project_file
            )
            project_name = project_dir.name
            shutil.rmtree(project_dir)
            if deleting_current:
                self._clear_current_project()
            self._refresh_recent_projects()
            self._notice = f"项目“{project_name}”及其缓存文件已删除，项目目录外的原始视频未删除。"
            self.noticeChanged.emit()
        except (OSError, ValueError) as exc:
            self._notice = f"删除项目失败：{exc}"
            self.noticeChanged.emit()

    @Slot(str)
    def setNotice(self, message: str) -> None:
        self._notice = message
        self.noticeChanged.emit()

    @Slot(float)
    def requestPreviewFrame(self, seconds: float) -> None:
        if not self._video_path or not self._current_project_file:
            return
        if not self._ensure_source_video():
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

    @Slot(str)
    def setAnalysisContentMode(self, mode: str) -> None:
        normalized = mode if mode in {"speech", "visual"} else "speech"
        if normalized == self._analysis_content_mode:
            return
        self._analysis_content_mode = normalized
        if self._current_project_file and self._current_project_file.exists():
            try:
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                payload.setdefault("settings", {})["content_mode"] = normalized
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        self._notice = (
            "已切换为纯画面叙事，请重新理解原片"
            if normalized == "visual"
            else "已切换为语音与画面模式，请重新理解原片"
        )
        self.analysisChanged.emit()
        self.noticeChanged.emit()

    @Slot(bool)
    def setLayeredAnalysisEnabled(self, enabled: bool) -> None:
        if not bool(
            self._config.get("layered_analysis", {}).get("manual_control", False)
        ):
            self._notice = "分层理解已由 StoryCut 自动管理，无需手动设置"
            self.noticeChanged.emit()
            return
        value = bool(enabled)
        if value == self._layered_analysis_enabled:
            return
        self._layered_analysis_enabled = value
        if self._current_project_file and self._current_project_file.exists():
            try:
                payload = json.loads(
                    self._current_project_file.read_text(encoding="utf-8")
                )
                payload.setdefault("settings", {})[
                    "layered_analysis_enabled"
                ] = value
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        self._notice = (
            "已开启分层理解；请重新理解原片以生成全片结构"
            if value
            else "已关闭分层理解；后续将完全沿用当前默认流程"
        )
        self.analysisChanged.emit()
        self.noticeChanged.emit()

    @Slot(str)
    def setNarrativeStrategy(self, strategy: str) -> None:
        normalized = normalize_narrative_strategy(strategy)
        if normalized == self._narrative_strategy:
            return
        self._narrative_strategy = normalized
        if self._current_project_file and self._current_project_file.exists():
            try:
                payload = json.loads(
                    self._current_project_file.read_text(encoding="utf-8")
                )
                payload.setdefault("settings", {})["narrative_strategy"] = normalized
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        label = narrative_strategy_label(normalized)
        self._notice = f"叙事策略已设为“{label}”，将在下次生成故事时生效"
        self.storyChanged.emit()
        self.noticeChanged.emit()

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
        self._analysis_estimated_total = -1.0
        self._model_download_progress = 0.0
        self._model_download_status = ""
        self._model_download_visible = False
        self._analysis_eta_seconds = -1.0
        self._analysis_eta_updated_at = 0.0
        self._analysis_eta_reliable = False
        self._analysis_eta_observations = []
        self._analysis_clock.start()
        self.analysisChanged.emit()
        video = Path(self._video_path)
        project_file = self._current_project_file
        analysis_dir = project_file.parent / "analysis"
        audio = analysis_dir / "audio_16k_mono.wav"
        content_mode = self._analysis_content_mode
        layered_enabled = self._layered_analysis_enabled

        status_file = analysis_dir / "status.json"

        def report(value: float, status: str, eta_seconds: float = -1.0) -> None:
            if eta_seconds >= 0 and value < 1.0:
                eta_seconds = max(3.0, eta_seconds)
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
                if content_mode == "visual":
                    report(0.10, "纯画面叙事模式：跳过语音识别…")
                    transcript_payload = {
                        "schema_version": 1,
                        "language": "",
                        "language_probability": 0.0,
                        "duration_sec": self.durationSeconds,
                        "model": "skipped-visual-mode",
                        "device": "",
                        "compute_type": "",
                        "segments": [],
                    }
                    analysis_dir.mkdir(parents=True, exist_ok=True)
                    (analysis_dir / "transcript.json").write_text(
                        json.dumps(transcript_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    (analysis_dir / "transcript.srt").write_text("", encoding="utf-8")
                    report(0.60, "已跳过人声转写，准备分析全部画面场景…")
                else:
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

                    def model_download_report(value: float, status: str, visible: bool) -> None:
                        self._modelDownloadProgressReady.emit(value, status, visible, job_id)

                    transcribe_analysis_audio(
                        audio,
                        analysis_dir / "transcript.json",
                        analysis_dir / "transcript.srt",
                        self.durationSeconds,
                        self._config,
                        self._root,
                        transcribe_report,
                        model_download_report,
                    )
                report(0.62, "语音转录完成，开始检测场景变化…")
                scene_started = time.monotonic()
                vision_reserve = (
                    0.0
                    if skip_vision or not bool(self._config.get("vision", {}).get("enabled", True))
                    else max(15.0, self.durationSeconds * 0.08)
                )

                def scene_report(value: float, status: str) -> None:
                    eta = vision_reserve
                    if value > 0.02:
                        elapsed = time.monotonic() - scene_started
                        eta += elapsed * (1.0 - value) / value
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
                report(0.81, "正在组合字幕、场景和关键帧…", vision_reserve)
                events_payload = build_timeline_events(
                    analysis_dir / "transcript.json",
                    analysis_dir / "scenes.json",
                    analysis_dir / "events.json",
                )
                events_payload["content_mode"] = content_mode
                if content_mode == "visual":
                    report(0.815, "正在按时间序列抽取动作与环境变化画面…", vision_reserve)
                    samples = extract_visual_sample_frames(
                        video,
                        analysis_dir / "visual_samples",
                        self._config,
                        self._root,
                    )
                    events_payload = attach_visual_samples(
                        analysis_dir / "events.json",
                        samples,
                    )
                    events_payload["content_mode"] = content_mode
                (analysis_dir / "events.json").write_text(
                    json.dumps(events_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                vision_warning = "用户选择仅执行本地分析，未调用视觉模型" if skip_vision else ""
                if bool(self._config.get("vision", {}).get("enabled", True)) and not skip_vision:
                    try:
                        target_count = sum(
                            1
                            for event in events_payload.get("events", [])
                            if str(event.get("keyframe", "")).strip()
                        )
                        batch_size = max(
                            1,
                            min(8, int(self._config.get("vision", {}).get("batch_size", 4))),
                        )
                        request_batches = max(1, (target_count + batch_size - 1) // batch_size)
                        vision_reserve = max(vision_reserve, request_batches * 8.0)
                        report(0.82, f"准备分 {request_batches} 批理解关键帧…", vision_reserve)
                        vision_started = time.monotonic()

                        def vision_report(value: float, status: str) -> None:
                            eta = vision_reserve
                            if value > 0.02:
                                elapsed = time.monotonic() - vision_started
                                eta = elapsed * (1.0 - value) / value
                            span = 0.10 if layered_enabled else 0.17
                            report(0.82 + value * span, status, max(3.0, eta))

                        describe_event_keyframes(
                            analysis_dir / "events.json",
                            self._config,
                            self._root,
                            vision_report,
                            video,
                        )
                    except Exception as exc:
                        if content_mode == "visual":
                            raise RuntimeError(
                                f"纯画面叙事必须完成视觉描述，当前无法继续：{exc}"
                            ) from exc
                        vision_warning = str(exc)
                        report(
                            0.92 if layered_enabled else 0.99,
                            f"视觉描述已跳过：{vision_warning}",
                        )
                layered_file = analysis_dir / "layered_structure.json"
                layered_warning = ""
                if layered_enabled and not skip_vision:
                    try:
                        def layered_report(value: float, status: str) -> None:
                            report(0.92 + min(max(value, 0.0), 1.0) * 0.07, status)

                        analyze_layered_structure(
                            analysis_dir / "events.json",
                            layered_file,
                            self._config,
                            self._root,
                            layered_report,
                        )
                    except Exception as exc:
                        layered_file.unlink(missing_ok=True)
                        layered_warning = str(exc)
                        report(0.99, f"基础理解已完成；分层理解未生成：{layered_warning}")
                else:
                    layered_file.unlink(missing_ok=True)
                    if layered_enabled and skip_vision:
                        layered_warning = "仅本地预处理不会调用分层理解 API"
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["stage"] = "understood"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                payload.setdefault("artifacts", {})["transcript"] = "analysis/transcript.json"
                payload["artifacts"]["transcript_srt"] = "analysis/transcript.srt"
                if content_mode != "visual" and audio.exists():
                    payload["artifacts"]["analysis_audio"] = "analysis/audio_16k_mono.wav"
                else:
                    payload["artifacts"].pop("analysis_audio", None)
                payload["artifacts"]["scenes"] = "analysis/scenes.json"
                payload["artifacts"]["keyframes"] = "analysis/keyframes"
                payload["artifacts"]["events"] = "analysis/events.json"
                if layered_file.exists():
                    payload["artifacts"]["layered_structure"] = "analysis/layered_structure.json"
                else:
                    payload["artifacts"].pop("layered_structure", None)
                if vision_warning:
                    payload.setdefault("warnings", {})["vision"] = vision_warning
                else:
                    payload.get("warnings", {}).pop("vision", None)
                if layered_warning:
                    payload.setdefault("warnings", {})["layered_analysis"] = layered_warning
                else:
                    payload.get("warnings", {}).pop("layered_analysis", None)
                project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                if skip_vision:
                    message = "本地结构化完成；未生成视觉描述。配置 API 后建议重新理解原片，再组织故事"
                elif vision_warning:
                    message = f"原片结构化完成；视觉描述生成失败：{vision_warning}"
                elif layered_file.exists():
                    message = "原片与分层结构理解完成，可以开始组织故事"
                elif layered_warning:
                    message = f"原片理解完成；分层理解未生成：{layered_warning}"
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
        if (
            self._story_busy
            or self._fact_review_busy
            or self._terminology_review_busy
            or not self._current_project_file
        ):
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
        try:
            event_payload = json.loads(events_file.read_text(encoding="utf-8"))
            if str(event_payload.get("content_mode", "speech")) == "visual":
                descriptions = [
                    str(event.get("visual_description", "")).strip()
                    for event in event_payload.get("events", [])
                    if isinstance(event, dict)
                ]
                if not any(descriptions):
                    self._notice = "纯画面叙事尚未生成视觉描述，请配置支持图片的模型并重新理解原片"
                    self.noticeChanged.emit()
                    return
        except (OSError, ValueError, TypeError):
            pass
        self._story_job_id += 1
        self._duration_revision_job_id += 1
        self._duration_revision_proposal = {}
        self._duration_revision_status = ""
        self.durationRevisionChanged.emit()
        self._fact_review_job_id += 1
        self._terminology_review_job_id += 1
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
                    narrative_strategy=self._narrative_strategy,
                    layered_structure_json=(
                        project_file.parent / "analysis" / "layered_structure.json"
                        if self._layered_analysis_enabled
                        else None
                    ),
                    planning_words_per_second=self._planning_words_per_second(),
                )
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["stage"] = "scripted"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                artifacts = payload.setdefault("artifacts", {})
                artifacts["story"] = "script/story.json"
                story_plan_file = project_file.parent / "script" / "story_plan.json"
                if story_plan_file.exists():
                    artifacts["story_plan"] = "script/story_plan.json"
                else:
                    artifacts.pop("story_plan", None)
                artifacts.pop("matches", None)
                artifacts.pop("rough_cut", None)
                artifacts.pop("rough_preview", None)
                artifacts.pop("fact_review", None)
                artifacts.pop("terminology_review", None)
                artifacts.pop("content_review", None)
                artifacts.pop("duration_revision_proposal", None)
                (project_file.parent / "script" / "fact_review.json").unlink(missing_ok=True)
                (project_file.parent / "script" / "terminology_review.json").unlink(
                    missing_ok=True
                )
                (project_file.parent / "script" / "content_review.json").unlink(
                    missing_ok=True
                )
                (project_file.parent / "script" / "duration_revision_proposal.json").unlink(missing_ok=True)
                (project_file.parent / "timeline" / "matches.json").unlink(missing_ok=True)
                (project_file.parent / "timeline" / "rough_cut.json").unlink(missing_ok=True)
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
        narration_id = int(self._story_narration[index].get("id", index + 1) or index + 1)
        self._apply_narration_replacements({narration_id: cleaned})

    def _apply_narration_replacements(
        self,
        replacements: dict[int, str],
        invalidate_reviews: bool = True,
    ) -> int:
        if not self._current_project_file or not replacements:
            return 0
        measured_scale: float | None = None
        if str(self._story.get("timing_model", "")).startswith("measured_voice"):
            natural_total = sum(
                estimate_tts_unit_duration(unit)
                for item in self._story_narration
                for unit in split_gpt_sovits_units(str(item.get("text_en", "")))
            )
            saved_total = float(self._story.get("estimated_duration_sec", 0) or 0)
            if natural_total > 0 and saved_total > 0:
                measured_scale = saved_total / natural_total

        applied = 0
        for item in self._story_narration:
            narration_id = int(item.get("id", 0) or 0)
            if narration_id in replacements:
                item["text_en"] = str(replacements[narration_id]).strip()
                applied += 1
        if applied == 0:
            return 0
        self._story["narration"] = self._story_narration
        self._story = normalize_story_after_text_edit(
            self._story, measured_timing_scale=measured_scale
        )
        self._story_narration = [
            dict(item)
            for item in self._story.get("narration", [])
            if isinstance(item, dict)
        ]
        story_file = self._current_project_file.parent / "script" / "story.json"
        story_file.write_text(json.dumps(self._story, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            prepare_tts_srt(
                story_file, self._current_project_file.parent / "script" / "tts"
            )
        except (OSError, ValueError, TypeError):
            pass
        if self._matches:
            try:
                events_file = self._current_project_file.parent / "analysis" / "events.json"
                matches_file = self._current_project_file.parent / "timeline" / "matches.json"
                if events_file.exists():
                    generate_shot_matches(story_file, events_file, matches_file)
                    build_rough_cut(
                        matches_file,
                        self._current_project_file.parent / "timeline" / "rough_cut.json",
                    )
                    self._load_matches(self._current_project_file)
                    self._matching_status = "文案已重新断句，镜头已自动重新匹配"
                    self.matchingChanged.emit()
            except (OSError, ValueError, TypeError):
                self._matches = []
                self._matching_status = "文案断句已变化，请重新匹配镜头"
                self.matchingChanged.emit()
        self._invalidate_duration_revision()
        if invalidate_reviews and self._fact_review:
            self._fact_review["stale"] = True
            self._fact_review_status = "英文解说已修改，旧审查结果需要重新检查"
            review_file = self._current_project_file.parent / "script" / "fact_review.json"
            try:
                review_file.write_text(
                    json.dumps(self._fact_review, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError:
                pass
            self.factReviewChanged.emit()
        if invalidate_reviews and self._terminology_review:
            self._terminology_review["stale"] = True
            self._terminology_review_status = (
                "英文解说已修改，旧术语检查结果需要重新检查"
            )
            review_file = (
                self._current_project_file.parent / "script" / "terminology_review.json"
            )
            try:
                review_file.write_text(
                    json.dumps(
                        self._terminology_review, ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            self.terminologyReviewChanged.emit()
        if invalidate_reviews and (self._fact_review or self._terminology_review):
            content_file = (
                self._current_project_file.parent / "script" / "content_review.json"
            )
            try:
                content_file.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "fact_review": self._fact_review,
                            "terminology_review": self._terminology_review,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                (content_file.parent / "fact_review.json").unlink(missing_ok=True)
                (content_file.parent / "terminology_review.json").unlink(
                    missing_ok=True
                )
            except OSError:
                pass
        self.storyChanged.emit()
        return applied

    @Slot(bool)
    def setFactReviewAuto(self, enabled: bool) -> None:
        self._fact_review_auto = bool(enabled)
        if self._current_project_file and self._current_project_file.exists():
            try:
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                payload.setdefault("settings", {})["fact_review_auto"] = self._fact_review_auto
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        self.factReviewChanged.emit()

    @Slot(int)
    def applyFactReviewSuggestion(self, issue_id: int) -> None:
        if not self._current_project_file:
            return
        issue = next(
            (item for item in self._fact_review_issues if int(item.get("id", 0) or 0) == issue_id),
            None,
        )
        if not issue:
            self._notice = "找不到这条事实审查建议，请重新审查后再试"
            self.noticeChanged.emit()
            return
        if bool(issue.get("applied", False)):
            self._notice = "这条事实审查建议已经应用"
            self.noticeChanged.emit()
            return
        suggestion = str(issue.get("suggestion_en", "")).strip()
        narration_ids = issue.get("narration_ids", [])
        narration_ids = narration_ids if isinstance(narration_ids, list) else []
        if (
            not suggestion
            or len(narration_ids) != 1
            or len(split_gpt_sovits_units(suggestion)) != 1
        ):
            self._notice = "这条建议不能安全地自动应用，请根据原因手动修改对应解说"
            self.noticeChanged.emit()
            return
        narration_id = int(narration_ids[0])
        index = next(
            (
                item_index
                for item_index, item in enumerate(self._story_narration)
                if int(item.get("id", 0) or 0) == narration_id
            ),
            -1,
        )
        if index < 0:
            self._notice = "对应解说句已经变化，请重新进行事实审查"
            self.noticeChanged.emit()
            return
        applied = self._apply_narration_replacements(
            {narration_id: suggestion}, invalidate_reviews=False
        )
        if applied:
            self._mark_content_review_issues_applied([("fact", issue_id)])
        self._notice = f"已将建议应用到解说句 {narration_id}"
        self.storyChanged.emit()
        self.noticeChanged.emit()

    @Slot()
    def runFactReview(self) -> None:
        if (
            self._fact_review_busy
            or self._terminology_review_busy
            or self._story_busy
            or not self._current_project_file
        ):
            return
        if not self.apiConfigured:
            self._fact_review_status = "文案审查失败：请先配置 API Key"
            self._terminology_review_status = self._fact_review_status
            self._notice = self._fact_review_status
            self.factReviewChanged.emit()
            self.terminologyReviewChanged.emit()
            self.noticeChanged.emit()
            return
        project_file = self._current_project_file
        events_file = project_file.parent / "analysis" / "events.json"
        story_file = project_file.parent / "script" / "story.json"
        if not events_file.exists() or not story_file.exists():
            self._notice = "请先完成原片理解和故事生成，再进行文案审查"
            self.noticeChanged.emit()
            return

        self._fact_review_job_id += 1
        self._terminology_review_job_id += 1
        job_id = self._fact_review_job_id
        self._fact_review_busy = True
        self._terminology_review_busy = True
        self._fact_review_status = "正在一次完成事实、证据与术语综合审查…"
        self._terminology_review_status = self._fact_review_status
        self.factReviewChanged.emit()
        self.terminologyReviewChanged.emit()

        def worker() -> None:
            try:
                report = review_story_content(
                    events_file,
                    story_file,
                    project_file.parent / "script" / "content_review.json",
                    self._config,
                    self._root,
                )
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                artifacts = payload.setdefault("artifacts", {})
                artifacts["content_review"] = "script/content_review.json"
                artifacts.pop("fact_review", None)
                artifacts.pop("terminology_review", None)
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (project_file.parent / "script" / "fact_review.json").unlink(
                    missing_ok=True
                )
                (project_file.parent / "script" / "terminology_review.json").unlink(
                    missing_ok=True
                )
                self._factReviewFinished.emit(True, "", report, job_id)
            except Exception as exc:
                self._factReviewFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-content-review", daemon=True).start()

    @Slot(int)
    def applyTerminologySuggestion(self, issue_id: int) -> None:
        if not self._current_project_file:
            return
        issue = next(
            (
                item
                for item in self._terminology_review_issues
                if int(item.get("id", 0) or 0) == issue_id
            ),
            None,
        )
        if not issue:
            self._notice = "找不到这条术语建议，请重新检查后再试"
            self.noticeChanged.emit()
            return
        if bool(issue.get("applied", False)):
            self._notice = "这条术语建议已经应用"
            self.noticeChanged.emit()
            return
        narration_ids = issue.get("narration_ids", [])
        narration_ids = narration_ids if isinstance(narration_ids, list) else []
        suggestion = str(issue.get("suggestion_en", "")).strip()
        if (
            len(narration_ids) != 1
            or not suggestion
            or len(split_gpt_sovits_units(suggestion)) != 1
        ):
            self._notice = "这条术语建议不能安全自动应用，请手动修改"
            self.noticeChanged.emit()
            return
        narration_id = int(narration_ids[0])
        index = next(
            (
                item_index
                for item_index, item in enumerate(self._story_narration)
                if int(item.get("id", 0) or 0) == narration_id
            ),
            -1,
        )
        if index < 0:
            self._notice = "对应解说句已经变化，请重新检查术语"
            self.noticeChanged.emit()
            return
        applied = self._apply_narration_replacements(
            {narration_id: suggestion}, invalidate_reviews=False
        )
        if applied:
            self._mark_content_review_issues_applied([("terminology", issue_id)])
        self._notice = f"已统一解说句 {narration_id} 的术语"
        self.storyChanged.emit()
        self.noticeChanged.emit()

    @Slot()
    def applyAllTerminologySuggestions(self) -> None:
        applicable = [
            dict(item)
            for item in self._terminology_review_issues
            if len(item.get("narration_ids", [])) == 1
            and str(item.get("suggestion_en", "")).strip()
            and not bool(item.get("applied", False))
            and len(split_gpt_sovits_units(str(item.get("suggestion_en", "")))) == 1
        ]
        if not applicable:
            self._notice = "当前没有可以安全批量应用的术语建议"
            self.noticeChanged.emit()
            return
        replacements = {
            int(issue["narration_ids"][0]): str(issue["suggestion_en"])
            for issue in applicable
        }
        applied = self._apply_narration_replacements(
            replacements, invalidate_reviews=False
        )
        if applied:
            self._mark_content_review_issues_applied(
                [("terminology", int(issue.get("id", 0) or 0)) for issue in applicable]
            )
        self._notice = f"已应用 {applied} 条术语统一建议"
        self.noticeChanged.emit()

    @Slot()
    def applyAllContentReviewSuggestions(self) -> None:
        suggestions: dict[int, str] = {}
        selected: list[tuple[str, int]] = []
        for issue in self.contentReviewIssues:
            narration_ids = issue.get("narration_ids", [])
            narration_ids = narration_ids if isinstance(narration_ids, list) else []
            suggestion = str(issue.get("suggestion_en", "")).strip()
            if (
                len(narration_ids) == 1
                and suggestion
                and not bool(issue.get("applied", False))
                and len(split_gpt_sovits_units(suggestion)) == 1
            ):
                narration_id = int(narration_ids[0])
                if narration_id not in suggestions:
                    suggestions[narration_id] = suggestion
                    selected.append(
                        (str(issue.get("reviewType", "fact")), int(issue.get("id", 0) or 0))
                    )
        if not suggestions:
            self._notice = "当前没有可以安全批量应用的文案建议"
            self.noticeChanged.emit()
            return
        applied = self._apply_narration_replacements(
            suggestions, invalidate_reviews=False
        )
        if applied:
            self._mark_content_review_issues_applied(selected)
        self._notice = f"已应用 {applied} 条文案审查建议"
        self.noticeChanged.emit()

    def _mark_content_review_issues_applied(
        self, entries: list[tuple[str, int]]
    ) -> None:
        targets = {(review_type, int(issue_id)) for review_type, issue_id in entries}
        for review_type, report, ui_issues in (
            ("fact", self._fact_review, self._fact_review_issues),
            ("terminology", self._terminology_review, self._terminology_review_issues),
        ):
            target_ids = {
                issue_id for item_type, issue_id in targets if item_type == review_type
            }
            if not target_ids:
                continue
            for item in report.get("issues", []):
                if isinstance(item, dict) and int(item.get("id", 0) or 0) in target_ids:
                    item["applied"] = True
            for item in ui_issues:
                if int(item.get("id", 0) or 0) in target_ids:
                    item["applied"] = True
        if self._current_project_file:
            content_file = self._current_project_file.parent / "script" / "content_review.json"
            try:
                content_file.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "fact_review": self._fact_review,
                            "terminology_review": self._terminology_review,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
        self.factReviewChanged.emit()
        self.terminologyReviewChanged.emit()

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
        self._matching_status = "正在从全部场景中自动挑选并排列镜头…"
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
            self._matching_status = "镜头已自动匹配完成；可直接进入预览导出"
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
        if self._export_busy or self._quality_busy or not self._current_project_file or not self._video_path:
            return
        if not self._ensure_source_video():
            return
        if not self._run_quality_check():
            self.qualityDialogRequested.emit()
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
        if self._narration_duration_sec >= SHORTS_MAX_DURATION_SEC:
            self._notice = (
                f"英文配音时长为 {self._format_time(self._narration_duration_sec)}，"
                "超过 Shorts 三分钟上限；请缩短文案并重新生成配音"
            )
            self.noticeChanged.emit()
            return
        self._start_rough_preview(
            Path(self._narration_audio_path),
            Path(self._synced_srt_path) if self.syncedSrtReady else None,
            "成片预览已生成，可使用系统播放器查看",
            "storycut_final_preview.mp4",
        )

    @Slot()
    def runQualityCheck(self) -> None:
        if self._quality_busy:
            return
        self._quality_job_id += 1
        job_id = self._quality_job_id
        self._quality_busy = True
        self._quality_report = {}
        self._notice = "正在检查项目，并扫描黑帧、静音与异常音量…"
        self.qualityChanged.emit()
        self.noticeChanged.emit()
        self.qualityDialogRequested.emit()

        def worker() -> None:
            try:
                report = self._collect_quality_report()
            except Exception as exc:
                report = {
                    "passed": False,
                    "error_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "pass_count": 0,
                    "checks": [
                        {"level": "error", "title": "成片检查失败", "detail": str(exc)}
                    ],
                }
            self._qualityCheckFinished.emit(report, job_id)

        threading.Thread(target=worker, name="storycut-quality-check", daemon=True).start()

    def _run_quality_check(self) -> bool:
        # Preview generation performs this preflight on the UI thread, so keep
        # the full FFmpeg decode scan in the explicit background check only.
        self._quality_report = self._collect_quality_report(deep_scan=False)
        self.qualityChanged.emit()
        passed = bool(self._quality_report.get("passed", False))
        self._notice = (
            "成片检查通过，可以生成预览。"
            if passed
            else f"成片检查发现 {self._quality_report.get('error_count', 0)} 项必须处理的问题。"
        )
        self.noticeChanged.emit()
        return passed

    def _collect_quality_report(self, deep_scan: bool = True) -> dict[str, object]:
        if not self._current_project_file:
            return {
                "passed": False,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
                "pass_count": 0,
                "checks": [
                    {"level": "error", "title": "尚未创建项目", "detail": "请先选择视频创建项目。"}
                ],
            }
        else:
            project_report = inspect_project_for_export(
                self._current_project_file,
                Path(self._video_path) if self._video_path else None,
                Path(self._narration_audio_path) if self._narration_audio_path else None,
                self._narration_duration_sec,
                Path(self._synced_srt_path) if self._synced_srt_path else None,
                self._media,
            )
            render_report: dict[str, object] = {}
            deep_report: dict[str, object] = {}
            rendered = Path(self._export_path) if self._export_path else None
            if rendered and rendered.exists():
                expected_duration = 0.0
                rough_cut = self._current_project_file.parent / "timeline" / "rough_cut.json"
                try:
                    expected_duration = float(
                        json.loads(rough_cut.read_text(encoding="utf-8")).get("duration_sec", 0)
                        or 0
                    )
                except (OSError, ValueError, TypeError):
                    pass
                export_config = self._config.get("export", {})
                if str(export_config.get("fit_mode", "original")).lower() == "original":
                    expected_width = int(self._media.get("width", 0) or 0)
                    expected_height = int(self._media.get("height", 0) or 0)
                else:
                    expected_width = int(export_config.get("width", 0) or 0)
                    expected_height = int(export_config.get("height", 0) or 0)
                render_report = inspect_rendered_video(
                    rendered,
                    expected_duration,
                    expected_width,
                    expected_height,
                    None,
                    self._config,
                    self._root,
                )
                if deep_scan:
                    deep_report = inspect_media_content(
                        rendered,
                        True,
                        bool(self.narrationAudioReady or self._preserve_original_audio),
                        self._config,
                        self._root,
                    )
            elif deep_scan and self.narrationAudioReady:
                deep_report = combine_quality_reports(
                    {
                        "checks": [
                            {
                                "level": "info",
                                "title": "黑帧扫描等待成片",
                                "detail": "当前还没有成片预览；生成预览后再次检查，即可扫描最终画面。",
                            }
                        ]
                    },
                    inspect_media_content(
                        Path(self._narration_audio_path),
                        False,
                        True,
                        self._config,
                        self._root,
                    ),
                )
            return combine_quality_reports(project_report, render_report, deep_report)

    @Slot(object, int)
    def _apply_quality_check_finished(self, report: object, job_id: int) -> None:
        if job_id != self._quality_job_id:
            return
        self._quality_busy = False
        self._quality_report = dict(report) if isinstance(report, dict) else {}
        passed = bool(self._quality_report.get("passed", False))
        warnings = int(self._quality_report.get("warning_count", 0) or 0)
        infos = int(self._quality_report.get("info_count", 0) or 0)
        self._notice = (
            f"成片检查通过：{warnings} 项提醒，{infos} 项说明。"
            if passed
            else f"成片检查发现 {self._quality_report.get('error_count', 0)} 项必须处理的问题。"
        )
        self.qualityChanged.emit()
        self.noticeChanged.emit()

    @Slot()
    def generateSubtitleOnlyPreview(self) -> None:
        if self._export_busy or self._quality_busy or not self._current_project_file or not self._video_path:
            return
        if not self._ensure_source_video():
            return
        project_file = self._current_project_file
        matches_file = project_file.parent / "timeline" / "matches.json"
        if not matches_file.exists():
            self._notice = "请先完成第 3 步镜头匹配"
            self.noticeChanged.emit()
            return
        try:
            from .voice_service import parse_srt_timings

            story_file = project_file.parent / "script" / "story.json"
            result = prepare_tts_srt(story_file, project_file.parent / "script" / "tts")
            subtitle_srt = Path(result["reference_srt_path"])
            segments = parse_srt_timings(subtitle_srt.read_text(encoding="utf-8-sig"))
            estimated_duration = float(result.get("estimated_duration_sec", 0) or 0)
            if estimated_duration >= SHORTS_MAX_DURATION_SEC:
                self._notice = (
                    f"参考字幕预计时长为 {self._format_time(estimated_duration)}，"
                    "超过 Shorts 三分钟上限，请先缩短故事"
                )
                self.noticeChanged.emit()
                return
            apply_voice_timing(matches_file, estimated_duration, segments)
            rough_cut_file = project_file.parent / "timeline" / "rough_cut.json"
            build_rough_cut(matches_file, rough_cut_file)
            self._load_matches(project_file)
        except (OSError, ValueError, TypeError) as exc:
            self._notice = f"无法准备仅字幕测试预览：{exc}"
            self.noticeChanged.emit()
            return
        self._start_rough_preview(
            None,
            subtitle_srt,
            (
                "仅字幕测试预览已生成（已保留原片声音，字幕时间为估算值）"
                if self._preserve_original_audio
                else "仅字幕测试预览已生成（无声音，字幕时间为估算值）"
            ),
            "storycut_subtitle_test.mp4",
        )

    @Slot(bool)
    def setPreserveOriginalAudio(self, enabled: bool) -> None:
        if enabled and int(self._media.get("audio_tracks", 0) or 0) <= 0:
            self._preserve_original_audio = False
            self._notice = "当前原视频没有可保留的音轨"
            self.noticeChanged.emit()
            self.exportChanged.emit()
            return
        self._preserve_original_audio = bool(enabled)
        if self._current_project_file:
            try:
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                payload.setdefault("settings", {}).setdefault("export", {})[
                    "preserve_original_audio"
                ] = self._preserve_original_audio
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        self._export_status = (
            "将保留原片声音，请重新生成预览"
            if self._preserve_original_audio
            else "已关闭原片声音，请重新生成预览"
        )
        self.exportChanged.emit()

    def _start_rough_preview(
        self,
        narration_audio: Path | None,
        subtitle_srt: Path | None,
        finished_status: str,
        output_filename: str,
    ) -> None:
        if not self._current_project_file:
            return
        project_file = self._current_project_file
        rough_cut_file = project_file.parent / "timeline" / "rough_cut.json"
        self._export_job_id += 1
        self._quality_job_id += 1
        job_id = self._export_job_id
        self._export_busy = True
        self._export_progress = 0.01
        self._export_status = "正在准备成片预览…"
        self.exportChanged.emit()
        output_filename = f"{self._safe_name(self._project_name)}_{output_filename}"
        output = self._export_dir / output_filename
        source = Path(self._video_path)
        render_config = self._config_with_project_style()
        preflight_report = deepcopy(self._quality_report) if narration_audio else {}

        def report(value: float, status: str) -> None:
            self._exportProgressReady.emit(value, status, job_id)

        def worker() -> None:
            try:
                result = render_rough_preview(
                    source,
                    rough_cut_file,
                    output,
                    narration_audio,
                    subtitle_srt,
                    int(self._media.get("width", 0) or 0),
                    int(self._media.get("height", 0) or 0),
                    render_config,
                    self._root,
                    report,
                )
                rendered_report = inspect_rendered_video(
                    output,
                    float(result.get("duration_sec", 0) or 0),
                    int(result.get("width", 0) or 0),
                    int(result.get("height", 0) or 0),
                    bool(result.get("has_audio", False)),
                    render_config,
                    self._root,
                )
                result["quality_report"] = combine_quality_reports(
                    preflight_report, rendered_report
                )
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                payload["stage"] = "previewed"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                relative_output = os.path.relpath(output, project_file.parent).replace("\\", "/")
                payload.setdefault("artifacts", {})["rough_preview"] = relative_output
                project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._exportFinished.emit(True, finished_status, result, job_id)
            except Exception as exc:
                self._export_dir.mkdir(parents=True, exist_ok=True)
                error_name = f"{self._safe_name(self._project_name)}_error.log"
                (self._export_dir / error_name).write_text(traceback.format_exc(), encoding="utf-8")
                self._exportFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-rough-preview", daemon=True).start()

    @Slot()
    def openRoughPreview(self) -> None:
        if self._export_path and Path(self._export_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._export_path))

    @Slot(str)
    def saveTtsSrt(self, url: str) -> None:
        if not url or not self._current_project_file:
            return
        try:
            source = self._current_project_file.parent / "script" / "tts" / "gpt_sovits_reference.srt"
            if not source.exists():
                story_file = self._current_project_file.parent / "script" / "story.json"
                prepare_tts_srt(story_file, source.parent)
            destination = Path(QUrlHelper.to_local_path(url))
            if destination.suffix.lower() != ".srt":
                destination = destination.with_suffix(".srt")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {}).pop("tts_input", None)
            payload["artifacts"]["tts_reference_srt"] = "script/tts/gpt_sovits_reference.srt"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._voice_status = f"GPT-SoVITS SRT 已导出：{destination}"
            self._notice = "SRT 已导出，可在 GPT-SoVITS 中选择该文件生成配音"
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
            "italic",
            "textColor",
            "outlineColor",
            "shadow",
            "letterSpacing",
            "animation",
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
            "fontSize", "bottomMargin", "horizontalMargin", "outlineWidth", "shadow",
            "boxPadding", "blurRadius", "blurPower", "regionPadding", "feather",
        }:
            value = int(float(value))
        elif key == "letterSpacing":
            value = min(20.0, max(-5.0, float(value)))
        elif key in {"cleanupWidth", "cleanupOpacity"}:
            value = min(1.0, max(0.02, float(value)))
        elif key in {
            "backgroundOpacity", "cleanupX", "cleanupY", "cleanupHeight",
        }:
            value = min(0.95, max(0.0, float(value)))
        elif key in {"bold", "italic", "backgroundEnabled"}:
            value = bool(value)
        elif key in {"textColor", "outlineColor"}:
            cleaned = str(value).strip().upper()
            if re.fullmatch(r"#[0-9A-F]{6}", cleaned):
                cleaned += "FF"
            value = cleaned if re.fullmatch(r"#[0-9A-F]{8}", cleaned) else "#FFFFFFFF"
        elif key == "animation":
            value = str(value) if str(value) in {"none", "fade", "pop"} else "fade"
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
                "italic": False,
                "textColor": "#FFFFFFFF",
                "outlineColor": "#000000FF",
                "shadow": 1,
                "letterSpacing": 0.0,
                "animation": "fade",
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
                "italic": False,
                "textColor": "#FFFFFFFF",
                "outlineColor": "#000000FF",
                "shadow": 1,
                "letterSpacing": 0.0,
                "animation": "fade",
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
                "italic": False,
                "textColor": "#FFFFFFFF",
                "outlineColor": "#000000FF",
                "shadow": 2,
                "letterSpacing": 0.5,
                "animation": "pop",
                "backgroundEnabled": False,
                "outlineWidth": 3,
                "boxPadding": 15,
            },
            "documentary": {
                "fontFamily": "Trebuchet MS",
                "fontSize": 47,
                "bottomMargin": 132,
                "horizontalMargin": 84,
                "bold": False,
                "italic": False,
                "textColor": "#FFF1D6FF",
                "outlineColor": "#17130FFF",
                "shadow": 2,
                "letterSpacing": 0.3,
                "animation": "fade",
                "backgroundEnabled": False,
                "outlineWidth": 3,
                "boxPadding": 12,
            },
            "science": {
                "fontFamily": "Arial",
                "fontSize": 56,
                "bottomMargin": 112,
                "horizontalMargin": 84,
                "bold": True,
                "italic": False,
                "textColor": "#FFD84DFF",
                "outlineColor": "#080A0FFF",
                "shadow": 2,
                "letterSpacing": 0.0,
                "animation": "pop",
                "backgroundEnabled": False,
                "outlineWidth": 4,
                "boxPadding": 14,
            },
            "minimal": {
                "fontFamily": "Verdana",
                "fontSize": 43,
                "bottomMargin": 120,
                "horizontalMargin": 96,
                "bold": False,
                "italic": False,
                "textColor": "#FFFFFFFF",
                "outlineColor": "#000000FF",
                "shadow": 0,
                "letterSpacing": 0.6,
                "animation": "none",
                "backgroundEnabled": False,
                "outlineWidth": 2,
                "boxPadding": 10,
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
            audio_dir = self._current_project_file.parent / "audio"
            original = audio_dir / "narration_original.wav"
            destination = audio_dir / "narration.wav"
            result = import_narration_audio(source, original, self._config, self._root)
            shutil.copy2(original, destination)
            self._narration_speed = 1.0
            working_srt = audio_dir / "narration.srt"
            original_srt = audio_dir / "narration_original.srt"
            if working_srt.exists():
                if not original_srt.exists():
                    shutil.copy2(working_srt, original_srt)
                scale_srt_timeline(original_srt, working_srt, 1.0)
            self._narration_audio_path = str(destination)
            self._narration_duration_sec = float(result["duration_sec"])
            self._apply_voice_timing_to_matches()
            over_limit = self._narration_duration_sec >= SHORTS_MAX_DURATION_SEC
            self._voice_status = (
                f"英文配音已导入，实际时长 {self._format_time(self._narration_duration_sec)}；"
                + (
                    "超过 Shorts 三分钟上限，请缩短配音后重新导入"
                    if over_limit
                    else "已使用同步 SRT 校准"
                    if self.syncedSrtReady
                    else "未导入同步 SRT，暂按句子比例分配"
                )
            )
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {})["narration_audio"] = "audio/narration.wav"
            payload.setdefault("artifacts", {})["narration_audio_original"] = "audio/narration_original.wav"
            payload.setdefault("settings", {}).setdefault("voice", {})["speed"] = 1.0
            payload["settings"]["voice"]["duration_sec"] = self._narration_duration_sec
            payload["settings"]["voice"]["audio_size"] = destination.stat().st_size
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._invalidate_duration_revision()
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
            audio_dir = self._current_project_file.parent / "audio"
            original = audio_dir / "narration_original.srt"
            destination = audio_dir / "narration.srt"
            import_synced_srt(source, original)
            segments = scale_srt_timeline(original, destination, self._narration_speed)
            result = {
                "segment_count": len(segments),
                "segments": segments,
                "duration_sec": max(item["end"] for item in segments),
            }
            self._synced_srt_path = str(destination)
            if self.narrationAudioReady:
                self._apply_voice_timing_to_matches(list(result["segments"]))
            self._voice_status = (
                f"同步字幕已导入：{result['segment_count']} 段"
                + ("，镜头时间线已按真实配音校准" if self.narrationAudioReady else "，请继续导入英文音频")
            )
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {})["narration_srt"] = "audio/narration.srt"
            payload.setdefault("artifacts", {})["narration_srt_original"] = "audio/narration_original.srt"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.voiceChanged.emit()
        except Exception as exc:
            self._notice = f"同步字幕导入失败：{exc}"
            self.noticeChanged.emit()

    @Slot()
    def autoFitNarrationToShorts(self) -> None:
        if self._voice_busy or not self.narrationAudioReady:
            return
        original_duration = self._original_narration_duration()
        suggested = recommended_shorts_speed(original_duration)
        if suggested == 1.0:
            self._notice = "当前英文配音已在 179 秒安全线内，不需要加速。"
            self.noticeChanged.emit()
            return
        if suggested is None:
            fastest_duration = original_duration / 1.25
            self._notice = (
                f"原始配音约 {self._format_time(original_duration)}，即使安全上限 1.25x 后仍约 "
                f"{self._format_time(fastest_duration)}。请先删减或重写部分文案，再重新生成配音。"
            )
            self.noticeChanged.emit()
            return
        self._start_narration_speed_processing(suggested)

    @Slot()
    def proposeNarrationDurationRevision(self) -> None:
        if self._duration_revision_busy or not self._current_project_file:
            return
        if self.durationRevisionReady:
            self.durationRevisionDialogRequested.emit()
            return
        if not self.canReviseNarrationDuration:
            self._notice = "当前配音可通过安全加速适配，或尚未超过 Shorts 上限。"
            self.noticeChanged.emit()
            return
        if not self.apiConfigured:
            self._notice = "生成精简方案需要故事 API；请先打开 API 设置完成配置。"
            self.noticeChanged.emit()
            return

        project_file = self._current_project_file
        story_file = project_file.parent / "script" / "story.json"
        events_file = project_file.parent / "analysis" / "events.json"
        if not story_file.exists() or not events_file.exists():
            self._notice = "找不到当前故事或原片事件，无法生成精简方案。"
            self.noticeChanged.emit()
            return

        self._duration_revision_job_id += 1
        job_id = self._duration_revision_job_id
        self._duration_revision_busy = True
        self._duration_revision_proposal = {}
        self._duration_revision_status = "正在根据真实配音速度计算精简量…"
        self.durationRevisionChanged.emit()
        self.durationRevisionDialogRequested.emit()

        def progress(_value: float, status: str) -> None:
            self._durationRevisionProgressReady.emit(status, job_id)

        def worker() -> None:
            try:
                proposal = propose_duration_revision(
                    events_file,
                    story_file,
                    project_file.parent / "script" / "duration_revision_proposal.json",
                    self._original_narration_duration(),
                    deepcopy(self._config),
                    self._root,
                    progress,
                )
                self._durationRevisionFinished.emit(True, "", proposal, job_id)
            except Exception as exc:
                self._durationRevisionFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-duration-revision", daemon=True).start()

    @Slot(str, int)
    def _apply_duration_revision_progress(self, status: str, job_id: int) -> None:
        if job_id != self._duration_revision_job_id:
            return
        self._duration_revision_status = status
        self.durationRevisionChanged.emit()

    @Slot(bool, str, object, int)
    def _apply_duration_revision_finished(
        self, success: bool, message: str, proposal: object, job_id: int
    ) -> None:
        if job_id != self._duration_revision_job_id:
            return
        self._duration_revision_busy = False
        if not success or not isinstance(proposal, dict):
            self._duration_revision_status = f"精简方案生成失败：{message}"
            self._notice = self._duration_revision_status
            self.durationRevisionChanged.emit()
            self.noticeChanged.emit()
            return
        self._duration_revision_proposal = dict(proposal)
        self._duration_revision_status = "精简方案已生成；确认前不会修改当前项目。"
        if self._current_project_file:
            try:
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                payload.setdefault("artifacts", {})["duration_revision_proposal"] = (
                    "script/duration_revision_proposal.json"
                )
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        self.durationRevisionChanged.emit()

    @Slot()
    def applyNarrationDurationRevision(self) -> None:
        if not self._current_project_file or self._duration_revision_busy:
            return
        revised = self._duration_revision_proposal.get("revised_story")
        if not isinstance(revised, dict) or not revised.get("narration"):
            self._notice = "没有可应用的精简方案。"
            self.noticeChanged.emit()
            return

        project_file = self._current_project_file
        project_dir = project_file.parent
        story_file = project_dir / "script" / "story.json"
        events_file = project_dir / "analysis" / "events.json"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        temporary_story = story_file.with_name("story.duration_revision.tmp.json")
        matches_file = project_dir / "timeline" / "matches.json"
        rough_cut_file = project_dir / "timeline" / "rough_cut.json"
        temporary_matches = matches_file.with_name("matches.duration_revision.tmp.json")
        temporary_rough_cut = rough_cut_file.with_name("rough_cut.duration_revision.tmp.json")
        try:
            temporary_story.write_text(
                json.dumps(revised, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            generate_shot_matches(temporary_story, events_file, temporary_matches)
            build_rough_cut(temporary_matches, temporary_rough_cut)

            archive_dir = project_dir / "archive" / f"duration_revision_{stamp}"
            archive_dir.mkdir(parents=True, exist_ok=False)
            self._snapshot_duration_revision_state(
                project_dir,
                archive_dir,
                "应用 AI 精简稿前的完整状态",
            )
            audio_dir = project_dir / "audio"
            for name in (
                "narration.wav",
                "narration_original.wav",
                "narration.srt",
                "narration_original.srt",
                "narration_whisper.json",
            ):
                source = audio_dir / name
                if source.exists():
                    source.unlink()

            temporary_story.replace(story_file)
            temporary_matches.replace(matches_file)
            temporary_rough_cut.replace(rough_cut_file)
            prepare_tts_srt(story_file, project_dir / "script" / "tts")

            payload = json.loads(project_file.read_text(encoding="utf-8"))
            artifacts = payload.setdefault("artifacts", {})
            artifacts["story"] = "script/story.json"
            artifacts["matches"] = "timeline/matches.json"
            artifacts["rough_cut"] = "timeline/rough_cut.json"
            artifacts["tts_reference_srt"] = "script/tts/gpt_sovits_reference.srt"
            artifacts["duration_revision_archive"] = archive_dir.relative_to(project_dir).as_posix()
            for key in (
                "narration_audio",
                "narration_audio_original",
                "narration_srt",
                "narration_srt_original",
                "narration_whisper",
                "rough_preview",
                "duration_revision_proposal",
                "fact_review",
                "terminology_review",
                "content_review",
            ):
                artifacts.pop(key, None)
            voice = payload.setdefault("settings", {}).setdefault("voice", {})
            voice.update({"speed": 1.0, "duration_sec": 0.0, "audio_size": 0})
            payload["stage"] = "matched"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            (project_dir / "script" / "duration_revision_proposal.json").unlink(missing_ok=True)
            (project_dir / "script" / "fact_review.json").unlink(missing_ok=True)
            (project_dir / "script" / "terminology_review.json").unlink(
                missing_ok=True
            )
            (project_dir / "script" / "content_review.json").unlink(missing_ok=True)
            self._set_story(dict(revised))
            self._set_fact_review({})
            self._set_terminology_review({})
            self._load_matches(project_file)
            self._narration_audio_path = ""
            self._synced_srt_path = ""
            self._narration_duration_sec = 0.0
            self._narration_speed = 1.0
            self._voice_status = "新 SRT 已准备；请重新生成并导入 GPT-SoVITS 配音"
            self._export_path = ""
            self._duration_revision_proposal = {}
            self._duration_revision_status = "新稿已应用"
            self._matching_status = "新稿已自动重新匹配镜头"
            self._notice = "新稿与镜头已更新；旧故事、配音和同步 SRT 已安全归档。请重新导出 SRT 并生成配音。"
            self.storyChanged.emit()
            self.matchingChanged.emit()
            self.voiceChanged.emit()
            self.exportChanged.emit()
            self.durationRevisionChanged.emit()
            self.noticeChanged.emit()
            self._refresh_recent_projects()
        except Exception as exc:
            self._notice = f"应用精简方案失败：{exc}。旧文件归档仍保留，请重新打开项目检查。"
            self.noticeChanged.emit()
        finally:
            temporary_story.unlink(missing_ok=True)
            temporary_matches.unlink(missing_ok=True)
            temporary_rough_cut.unlink(missing_ok=True)

    @Slot()
    def restoreDurationRevisionArchive(self) -> None:
        if not self._current_project_file or self._duration_revision_busy or self._voice_busy:
            return
        archive_dir = self._duration_revision_archive_path()
        if not archive_dir:
            self._notice = "找不到可恢复的应用前版本。"
            self.noticeChanged.emit()
            return
        project_file = self._current_project_file
        project_dir = project_file.parent
        state_dir = archive_dir / "state"
        saved_project = state_dir / "project.json"
        if not saved_project.exists():
            self._notice = "归档不完整：缺少应用前的 project.json。"
            self.noticeChanged.emit()
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rollback_dir = project_dir / "archive" / f"restore_replaced_{stamp}"
        try:
            rollback_dir.mkdir(parents=True, exist_ok=False)
            self._snapshot_duration_revision_state(
                project_dir,
                rollback_dir,
                "执行恢复前被替换的当前状态",
            )
            for folder_name in ("script", "timeline", "audio"):
                current = project_dir / folder_name
                replaced = rollback_dir / f"replaced_live_{folder_name}"
                if current.exists():
                    shutil.move(str(current), str(replaced))
                archived_folder = state_dir / folder_name
                if archived_folder.exists():
                    shutil.copytree(archived_folder, current)
                else:
                    current.mkdir(parents=True, exist_ok=True)

            restored_payload = json.loads(saved_project.read_text(encoding="utf-8"))
            artifacts = restored_payload.setdefault("artifacts", {})
            artifacts.pop("duration_revision_proposal", None)
            artifacts["duration_revision_archive"] = rollback_dir.relative_to(project_dir).as_posix()
            restored_payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            project_file.write_text(
                json.dumps(restored_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (project_dir / "script" / "duration_revision_proposal.json").unlink(missing_ok=True)
            self.openProject(project_file.as_uri())
            self._notice = (
                "已恢复 AI 精简前的故事、镜头、配音、SRT 和项目设置；"
                "刚才被替换的版本也已另行归档，可再次恢复。"
            )
            self._duration_revision_status = "已恢复应用前版本"
            self.durationRevisionChanged.emit()
            self.noticeChanged.emit()
            self._refresh_recent_projects()
        except Exception as exc:
            self._notice = f"恢复应用前版本失败：{exc}。现有与归档文件均未清理，请重新打开项目检查。"
            self.noticeChanged.emit()

    def _duration_revision_archive_path(self) -> Path | None:
        if not self._current_project_file or not self._current_project_file.exists():
            return None
        try:
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            relative = str(payload.get("artifacts", {}).get("duration_revision_archive", "")).strip()
            if not relative:
                return None
            project_dir = self._current_project_file.parent.resolve()
            archive_root = (project_dir / "archive").resolve()
            candidate = (project_dir / relative).resolve()
            if archive_root not in candidate.parents or not (candidate / "state" / "project.json").exists():
                return None
            return candidate
        except (OSError, ValueError, TypeError):
            return None

    def _duration_revision_archive_bundle(self) -> dict[str, object]:
        archive = self._duration_revision_archive_path()
        if not archive:
            return {}
        try:
            state = archive / "state"
            story = json.loads((state / "script" / "story.json").read_text(encoding="utf-8"))
            project = json.loads((state / "project.json").read_text(encoding="utf-8"))
            return {
                "story": story if isinstance(story, dict) else {},
                "project": project if isinstance(project, dict) else {},
            }
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _snapshot_duration_revision_state(
        project_dir: Path,
        archive_dir: Path,
        reason: str,
    ) -> None:
        state_dir = archive_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        project_file = project_dir / "project.json"
        if not project_file.exists():
            raise FileNotFoundError("当前项目缺少 project.json")
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload.setdefault("artifacts", {}).pop("duration_revision_proposal", None)
        (state_dir / "project.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for folder_name in ("script", "timeline", "audio"):
            source = project_dir / folder_name
            if source.exists():
                shutil.copytree(source, state_dir / folder_name)
        (state_dir / "script" / "duration_revision_proposal.json").unlink(missing_ok=True)
        (archive_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "reason": reason,
                    "state": "state",
                    "includes": ["project.json", "script", "timeline", "audio"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @Slot()
    def restoreNarrationSpeed(self) -> None:
        if self._voice_busy or not self.narrationAudioReady or self._narration_speed == 1.0:
            return
        self._start_narration_speed_processing(1.0)

    @Slot()
    def generateNarrationSrtWithWhisper(self) -> None:
        if self._voice_busy or not self._current_project_file or not self.narrationAudioReady:
            return
        project_file = self._current_project_file
        audio = Path(self._narration_audio_path)
        audio_dir = project_file.parent / "audio"
        working_srt = audio_dir / "narration.srt"
        transcript_json = audio_dir / "narration_whisper.json"
        config = deepcopy(self._config)
        config.setdefault("analysis", {})["language"] = "en"
        self._voice_job_id += 1
        job_id = self._voice_job_id
        self._voice_busy = True
        self._voice_status = "正在加载 Faster-Whisper，准备识别英文配音…"
        self.voiceChanged.emit()

        def progress(_value: float, status: str) -> None:
            self._voiceSrtProgressReady.emit(status, job_id)

        def model_progress(_value: float, status: str, _visible: bool) -> None:
            self._voiceSrtProgressReady.emit(status, job_id)

        def worker() -> None:
            try:
                result = transcribe_analysis_audio(
                    audio,
                    transcript_json,
                    working_srt,
                    self._narration_duration_sec,
                    config,
                    self._root,
                    progress,
                    model_progress,
                )
                original_srt = audio_dir / "narration_original.srt"
                if self._narration_speed > 1.0:
                    scale_srt_timeline(working_srt, original_srt, 1.0 / self._narration_speed)
                else:
                    shutil.copy2(working_srt, original_srt)
                segments = parse_srt_timings(working_srt.read_text(encoding="utf-8-sig"))
                result = dict(result)
                result["segments"] = segments
                self._voiceSrtFinished.emit(True, "", result, job_id)
            except Exception as exc:
                self._voiceSrtFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-voice-whisper", daemon=True).start()

    @Slot(str, int)
    def _apply_voice_srt_progress(self, status: str, job_id: int) -> None:
        if job_id != self._voice_job_id:
            return
        self._voice_status = status.replace("原片语音", "英文配音")
        self.voiceChanged.emit()

    @Slot(bool, str, object, int)
    def _apply_voice_srt_finished(
        self, success: bool, message: str, result: object, job_id: int
    ) -> None:
        if job_id != self._voice_job_id:
            return
        self._voice_busy = False
        if not success or not isinstance(result, dict):
            self._voice_status = "英文配音识别失败"
            self._notice = f"无法从英文配音生成同步 SRT：{message}"
            self.voiceChanged.emit()
            self.noticeChanged.emit()
            return
        segments = result.get("segments", [])
        self._synced_srt_path = str(self._current_project_file.parent / "audio" / "narration.srt")
        self._apply_voice_timing_to_matches(segments if isinstance(segments, list) else None)
        try:
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            artifacts = payload.setdefault("artifacts", {})
            artifacts["narration_srt"] = "audio/narration.srt"
            artifacts["narration_srt_original"] = "audio/narration_original.srt"
            artifacts["narration_whisper"] = "audio/narration_whisper.json"
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._current_project_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, ValueError, TypeError):
            pass
        count = len(segments) if isinstance(segments, list) else 0
        self._voice_status = f"已从英文配音识别出 {count} 条同步字幕；镜头时间线已校准"
        self._notice = "同步 SRT 已生成。Whisper 断句可能与原文不同，建议预览后确认。"
        self.voiceChanged.emit()
        self.noticeChanged.emit()

    def _original_narration_duration(self) -> float:
        if not self._current_project_file:
            return self._narration_duration_sec * max(1.0, self._narration_speed)
        original = self._current_project_file.parent / "audio" / "narration_original.wav"
        if original.exists():
            try:
                return probe_audio_duration(original, self._config, self._root)
            except Exception:
                pass
        return self._narration_duration_sec * max(1.0, self._narration_speed)

    def _invalidate_duration_revision(self) -> None:
        if not self._current_project_file:
            return
        self._duration_revision_job_id += 1
        self._duration_revision_busy = False
        self._duration_revision_proposal = {}
        self._duration_revision_status = ""
        proposal_file = self._current_project_file.parent / "script" / "duration_revision_proposal.json"
        proposal_file.unlink(missing_ok=True)
        try:
            payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
            payload.setdefault("artifacts", {}).pop("duration_revision_proposal", None)
            self._current_project_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, ValueError, TypeError):
            pass
        self.durationRevisionChanged.emit()

    def _start_narration_speed_processing(self, speed: float) -> None:
        if not self._current_project_file:
            return
        project_file = self._current_project_file
        audio_dir = project_file.parent / "audio"
        working_audio = audio_dir / "narration.wav"
        original_audio = audio_dir / "narration_original.wav"
        working_srt = audio_dir / "narration.srt"
        original_srt = audio_dir / "narration_original.srt"
        try:
            if not original_audio.exists():
                if not working_audio.exists():
                    raise FileNotFoundError("找不到已导入的英文配音")
                shutil.copy2(working_audio, original_audio)
            if working_srt.exists() and not original_srt.exists():
                shutil.copy2(working_srt, original_srt)
        except OSError as exc:
            self._notice = f"无法准备配音原始备份：{exc}"
            self.noticeChanged.emit()
            return

        self._voice_job_id += 1
        job_id = self._voice_job_id
        self._voice_busy = True
        self._voice_status = f"正在按 {speed:.2f}x 处理配音与同步字幕…"
        self.voiceChanged.emit()

        def worker() -> None:
            try:
                result = process_narration_speed(
                    original_audio,
                    working_audio,
                    speed,
                    self._config,
                    self._root,
                    original_srt if original_srt.exists() else None,
                    working_srt if original_srt.exists() else None,
                )
                self._voiceProcessingFinished.emit(True, "", result, job_id)
            except Exception as exc:
                self._voiceProcessingFinished.emit(False, str(exc), {}, job_id)

        threading.Thread(target=worker, name="storycut-voice-speed", daemon=True).start()

    @Slot(bool, str, object, int)
    def _apply_voice_processing_finished(
        self,
        success: bool,
        message: str,
        result: object,
        job_id: int,
    ) -> None:
        if job_id != self._voice_job_id:
            return
        self._voice_busy = False
        if not success or not isinstance(result, dict):
            self._voice_status = "配音速度处理失败"
            self._notice = f"配音速度处理失败：{message}"
            self.voiceChanged.emit()
            self.noticeChanged.emit()
            return
        self._narration_speed = float(result.get("speed", 1.0) or 1.0)
        self._narration_duration_sec = float(result.get("duration_sec", 0) or 0)
        segments = result.get("segments")
        self._apply_voice_timing_to_matches(segments if isinstance(segments, list) and segments else None)
        if self._current_project_file:
            try:
                working_audio = self._current_project_file.parent / "audio" / "narration.wav"
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                payload.setdefault("settings", {}).setdefault("voice", {})[
                    "speed"
                ] = self._narration_speed
                payload["settings"]["voice"]["duration_sec"] = self._narration_duration_sec
                payload["settings"]["voice"]["audio_size"] = working_audio.stat().st_size
                artifacts = payload.setdefault("artifacts", {})
                artifacts["narration_audio"] = "audio/narration.wav"
                artifacts["narration_audio_original"] = "audio/narration_original.wav"
                original_srt = self._current_project_file.parent / "audio" / "narration_original.srt"
                if original_srt.exists():
                    artifacts["narration_srt"] = "audio/narration.srt"
                    artifacts["narration_srt_original"] = "audio/narration_original.srt"
                payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as exc:
                self._notice = f"配音已处理完成，但保存项目状态失败：{exc}"
                self.noticeChanged.emit()
        action = "已恢复原速" if self._narration_speed == 1.0 else f"已调整为 {self._narration_speed:.2f}x"
        self._voice_status = (
            f"英文配音{action}，实际时长 {self._format_time(self._narration_duration_sec)}；"
            + ("仍超过 Shorts 上限，请删减文案" if self.narrationOverShortsLimit else "镜头与字幕时间线已同步校准")
        )
        self._export_path = ""
        self.voiceChanged.emit()
        self.exportChanged.emit()

    @staticmethod
    def _safe_name(value: str) -> str:
        forbidden = '<>:"/\\|?*'
        cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
        return cleaned or "未命名项目"

    def _next_project_name(self) -> str:
        return self._available_project_name(self._projects_dir)

    @staticmethod
    def _available_project_name(projects_dir: Path, now: datetime | None = None) -> str:
        base = f"v2-{(now or datetime.now()).strftime('%m%d')}"
        candidate = base
        suffix = 0
        while (projects_dir / candidate).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    def _planning_words_per_second(self) -> float:
        """Use the user's completed GPT-SoVITS projects to plan future script length."""
        fallback = float(
            self._config.get("story", {}).get("planning_words_per_second", 1.45)
            or 1.45
        )
        samples: list[float] = []
        for story_file in self._projects_dir.glob("*/script/story.json"):
            try:
                story = json.loads(story_file.read_text(encoding="utf-8"))
                if not str(story.get("timing_model", "")).startswith("measured_voice"):
                    continue
                words = int(story.get("word_count", 0) or 0)
                duration = float(story.get("estimated_duration_sec", 0) or 0)
                if words < 20 or duration < 10:
                    continue
                rate = words / duration
                if 0.9 <= rate <= 2.2:
                    samples.append(rate)
            except (OSError, ValueError, TypeError):
                continue
        if not samples:
            return max(0.9, min(2.2, fallback))
        samples.sort()
        middle = len(samples) // 2
        median = (
            samples[middle]
            if len(samples) % 2
            else (samples[middle - 1] + samples[middle]) / 2
        )
        return round(max(0.9, min(2.2, median)), 3)

    def _refresh_recent_projects(self) -> None:
        projects: list[dict[str, str]] = []
        stage_names = {
            "imported": "已导入",
            "analyzed": "媒体信息已读取",
            "transcribed": "语音转录完成",
            "understood": "理解原片完成",
            "scripted": "故事解说完成",
            "matched": "镜头匹配完成",
            "previewed": "成片预览完成",
            "exported": "成片已导出",
        }
        for project_file in self._projects_dir.glob("*/project.json"):
            try:
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                stage = str(payload.get("stage") or "imported")
                updated = str(payload.get("updated_at") or payload.get("created_at") or "")
                projects.append(
                    {
                        "name": str(payload.get("name") or project_file.parent.name),
                        "video": str(payload.get("source_video") or ""),
                        "stage": stage,
                        "stageText": stage_names.get(stage, stage),
                        "updated": updated,
                        "updatedText": updated.replace("T", " ")[:16],
                        "projectFile": project_file.as_uri(),
                    }
                )
            except (OSError, ValueError, TypeError):
                continue
        projects.sort(key=lambda item: item["updated"], reverse=True)
        self._recent_projects = projects[:8]
        self.recentProjectsChanged.emit()

    def _clear_current_project(self) -> None:
        self._media_job_id += 1
        self._preview_job_id += 1
        self._analysis_job_id += 1
        self._story_job_id += 1
        self._duration_revision_job_id += 1
        self._fact_review_job_id += 1
        self._terminology_review_job_id += 1
        self._export_job_id += 1
        self._quality_job_id += 1
        self._subtitle_effect_preview_job_id += 1
        self._current_project_file = None
        self._project_name = "尚未创建项目"
        self._video_path = ""
        self._media = {}
        self._cover_url = ""
        self._preview_url = ""
        self._preview_position = 0.0
        self._analysis_progress = 0.0
        self._analysis_content_mode = str(
            self._config.get("analysis", {}).get("content_mode", "speech")
        )
        self._layered_analysis_enabled = bool(
            self._config.get("layered_analysis", {}).get("enabled", True)
        )
        self._analysis_status = "等待开始"
        self._analysis_started_at = 0.0
        self._analysis_eta_seconds = -1.0
        self._analysis_estimated_total = -1.0
        self._analysis_eta_reliable = False
        self._analysis_eta_observations = []
        self._model_download_progress = 0.0
        self._model_download_status = ""
        self._model_download_visible = False
        self._events = []
        self._story_progress = 0.0
        self._story_status = "等待组织故事"
        self._story = {}
        self._story_outline = []
        self._story_narration = []
        self._fact_review_busy = False
        self._fact_review_status = "可选功能，尚未进行事实审查"
        self._fact_review = {}
        self._fact_review_issues = []
        self._fact_review_auto = bool(
            self._config.get("fact_review", {}).get("auto_after_story", False)
        )
        self._terminology_review_busy = False
        self._terminology_review_status = "可选功能，尚未检查术语一致性"
        self._terminology_review = {}
        self._terminology_review_issues = []
        self._matching_status = "等待匹配镜头"
        self._matches = []
        self._export_progress = 0.0
        self._export_status = "等待生成成片预览"
        self._export_path = ""
        self._voice_status = "等待导出 SRT 到 GPT-SoVITS"
        self._voice_busy = False
        self._narration_speed = 1.0
        self._narration_audio_path = ""
        self._narration_duration_sec = 0.0
        self._synced_srt_path = ""
        self._duration_revision_busy = False
        self._duration_revision_status = ""
        self._duration_revision_proposal = {}
        self._quality_report = {}
        self._quality_busy = False
        self._subtitle_style = self._default_subtitle_style()
        self._subtitle_effect_preview_url = ""
        self.projectChanged.emit()
        self.mediaChanged.emit()
        self.previewChanged.emit()
        self.analysisChanged.emit()
        self.eventsChanged.emit()
        self.storyChanged.emit()
        self.factReviewChanged.emit()
        self.terminologyReviewChanged.emit()
        self.matchingChanged.emit()
        self.exportChanged.emit()
        self.voiceChanged.emit()
        self.durationRevisionChanged.emit()
        self.qualityChanged.emit()
        self.subtitleStyleChanged.emit()
        self.subtitleEffectPreviewChanged.emit()

    def _start_media_analysis(self, video: Path, project_file: Path) -> None:
        self._media_job_id += 1
        job_id = self._media_job_id
        self._media_busy = True
        self._notice = "正在读取视频信息并生成封面…"
        self.mediaChanged.emit()
        self.analysisChanged.emit()
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
        self.analysisChanged.emit()
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
        now = time.monotonic()
        elapsed = now - self._analysis_started_at if self._analysis_started_at else 0.0
        if eta_seconds >= 5.0 and elapsed >= 30.0:
            self._analysis_eta_observations.append((now, now + eta_seconds))
            cutoff = now - 35.0
            self._analysis_eta_observations = [
                item for item in self._analysis_eta_observations if item[0] >= cutoff
            ]
            observations = self._analysis_eta_observations
            span = observations[-1][0] - observations[0][0] if len(observations) >= 2 else 0.0
            predicted_finishes = sorted(item[1] for item in observations)
            median_finish = (
                predicted_finishes[len(predicted_finishes) // 2]
                if predicted_finishes
                else now + eta_seconds
            )
            finish_range = (
                predicted_finishes[-1] - predicted_finishes[0]
                if len(predicted_finishes) >= 2
                else float("inf")
            )
            tolerance = max(12.0, max(0.0, median_finish - now) * 0.08)
            self._analysis_eta_reliable = (
                len(observations) >= 4 and span >= 12.0 and finish_range <= tolerance
            )
            if self._analysis_eta_reliable:
                self._analysis_eta_seconds = max(0.0, median_finish - now)
                self._analysis_eta_updated_at = now
            else:
                self._analysis_eta_seconds = -1.0
                self._analysis_eta_updated_at = 0.0
        else:
            self._analysis_eta_reliable = False
            self._analysis_eta_seconds = -1.0
            self._analysis_eta_updated_at = 0.0
            self._analysis_eta_observations = []
        self.analysisChanged.emit()

    @Slot(float, str, bool, int)
    def _apply_model_download_progress(
        self, value: float, status: str, visible: bool, job_id: int
    ) -> None:
        if job_id != self._analysis_job_id:
            return
        self._model_download_progress = min(max(value, 0.0), 1.0)
        self._model_download_status = status
        self._model_download_visible = visible
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
        self._analysis_eta_reliable = False
        self._analysis_eta_observations = []
        self._analysis_progress = 1.0 if success else self._analysis_progress
        self._analysis_status = message if success else f"分析失败：{message}"
        self._model_download_visible = False
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
            self._set_fact_review({})
            self._set_terminology_review({})
            self._matches = []
            self._matching_status = "故事已更新，请重新自动匹配镜头"
            self._export_path = ""
            self._refresh_recent_projects()
        self.storyChanged.emit()
        self.matchingChanged.emit()
        self.exportChanged.emit()
        self.noticeChanged.emit()
        if success and self._fact_review_auto:
            QTimer.singleShot(0, self.runFactReview)

    @Slot(bool, str, object, int)
    def _apply_fact_review_finished(
        self, success: bool, message: str, report: object, job_id: int
    ) -> None:
        if job_id != self._fact_review_job_id:
            return
        self._fact_review_busy = False
        self._terminology_review_busy = False
        if success and isinstance(report, dict):
            fact_report = report.get("fact_review", {})
            terminology_report = report.get("terminology_review", {})
            fact_report = fact_report if isinstance(fact_report, dict) else {}
            terminology_report = (
                terminology_report if isinstance(terminology_report, dict) else {}
            )
            self._set_fact_review(fact_report)
            self._set_terminology_review(terminology_report)
            high = int(fact_report.get("high_count", 0) or 0)
            medium = int(fact_report.get("medium_count", 0) or 0)
            low = int(fact_report.get("low_count", 0) or 0)
            term_count = int(terminology_report.get("issue_count", 0) or 0)
            if high:
                self._fact_review_status = f"审查完成：{high} 项高风险，{medium} 项需确认，{low} 项精度建议"
            elif medium or low:
                self._fact_review_status = f"审查完成：未发现高风险，另有 {medium + low} 项建议确认"
            else:
                self._fact_review_status = "审查完成：未发现明显事实风险"
            self._terminology_review_status = (
                "术语、单位和名称前后一致"
                if term_count == 0
                else f"发现 {term_count} 处可统一内容"
            )
            self._notice = (
                f"文案审查完成：事实问题 {high + medium + low} 项，"
                f"术语问题 {term_count} 项"
            )
            self._refresh_recent_projects()
        else:
            self._fact_review_status = f"文案审查失败：{message}"
            self._terminology_review_status = self._fact_review_status
            self._notice = self._fact_review_status
        self.factReviewChanged.emit()
        self.terminologyReviewChanged.emit()
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
            report = result.get("quality_report", {})
            if isinstance(report, dict) and report:
                self._quality_report = dict(report)
                self.qualityChanged.emit()
                errors = int(self._quality_report.get("error_count", 0) or 0)
                warnings = int(self._quality_report.get("warning_count", 0) or 0)
                if errors:
                    self._export_status += f"；输出实测发现 {errors} 项必须处理的问题"
                    self._notice = self._export_status
                    self.qualityDialogRequested.emit()
                elif warnings:
                    self._export_status += f"；输出实测通过，另有 {warnings} 项提醒"
                    self._notice = self._export_status
                else:
                    self._export_status += "；输出文件实测通过"
                    self._notice = self._export_status
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
        if self._show_update_dialog_after_check:
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

    @Slot(bool, str, object)
    def _apply_api_models(self, success: bool, message: str, models: object) -> None:
        self._api_models_busy = False
        self._api_models_status = message
        self._api_models = list(models) if success and isinstance(models, list) else []
        self.apiModelsChanged.emit()

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
                    technical = event.get("technical_visual", {})
                    technical = technical if isinstance(technical, dict) else {}
                    screen_text = event.get("screen_text", [])
                    visible_text = " · ".join(
                        str(value.get("text", "")).strip()
                        for value in screen_text
                        if isinstance(value, dict) and str(value.get("text", "")).strip()
                    )
                    item["technicalVisualSummary"] = str(
                        technical.get("summary", "") or visible_text
                    ).strip()
                    item["technicalVisualType"] = str(technical.get("type", "none"))
                    item["highDetailReviewed"] = bool(technical.get("high_detail_reviewed", False))
                    loaded.append(item)
            except (OSError, ValueError, TypeError):
                loaded = []
        self._events = loaded
        self.eventsChanged.emit()

    def _load_story(self, project_file: Path) -> None:
        story_file = project_file.parent / "script" / "story.json"
        if not story_file.exists():
            self._set_story({})
            self._set_fact_review({})
            self._set_terminology_review({})
            return
        try:
            story = json.loads(story_file.read_text(encoding="utf-8"))
            story, changed = refresh_story_timing(story)
            if changed:
                story_file.write_text(
                    json.dumps(story, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            self._set_story(story)
        except (OSError, ValueError, TypeError):
            self._set_story({})
        self._load_content_review(project_file)
        proposal_file = project_file.parent / "script" / "duration_revision_proposal.json"
        self._duration_revision_proposal = {}
        self._duration_revision_status = ""
        if proposal_file.exists():
            try:
                proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
                if (
                    isinstance(proposal, dict)
                    and int(proposal.get("schema_version", 0) or 0) >= 2
                    and isinstance(proposal.get("revised_story"), dict)
                ):
                    self._duration_revision_proposal = proposal
                    self._duration_revision_status = "已有未应用的精简方案，可重新查看对比。"
                else:
                    proposal_file.unlink(missing_ok=True)
                    payload = json.loads(project_file.read_text(encoding="utf-8"))
                    payload.setdefault("artifacts", {}).pop("duration_revision_proposal", None)
                    project_file.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except (OSError, ValueError, TypeError):
                pass
        self.durationRevisionChanged.emit()

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
        output: Path | None = None
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            relative = str(payload.get("artifacts", {}).get("rough_preview", ""))
            candidate = project_file.parent / relative if relative else None
            if candidate and candidate.exists():
                output = candidate
        except (OSError, ValueError, TypeError):
            pass
        if output is None:
            legacy_candidates = (
                project_file.parent / "export" / "storycut_final_preview.mp4",
                project_file.parent / "export" / "storycut_subtitle_test.mp4",
                project_file.parent / "exports" / "rough_preview.mp4",
            )
            output = next((path for path in legacy_candidates if path.exists()), None)
        self._export_path = str(output) if output else ""
        self._export_progress = 1.0 if output else 0.0
        self._export_status = "成片预览已生成，可使用系统播放器查看" if output else "等待生成成片预览"
        self.exportChanged.emit()

    def _load_voice(self, project_file: Path) -> None:
        self._voice_job_id += 1
        job_id = self._voice_job_id
        audio = project_file.parent / "audio" / "narration.wav"
        srt = project_file.parent / "audio" / "narration.srt"
        self._voice_busy = False
        self._narration_speed = 1.0
        cached_duration = 0.0
        cached_audio_size = 0
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            voice_settings = payload.get("settings", {}).get("voice", {})
            voice_settings = voice_settings if isinstance(voice_settings, dict) else {}
            self._narration_speed = max(
                1.0,
                min(1.25, float(voice_settings.get("speed", 1.0))),
            )
            cached_duration = max(0.0, float(voice_settings.get("duration_sec", 0) or 0))
            cached_audio_size = max(0, int(voice_settings.get("audio_size", 0) or 0))
        except (OSError, ValueError, TypeError):
            self._narration_speed = 1.0
        self._narration_audio_path = str(audio) if audio.exists() else ""
        self._synced_srt_path = str(srt) if srt.exists() else ""
        self._narration_duration_sec = 0.0
        if audio.exists():
            actual_size = audio.stat().st_size
            if cached_duration > 0 and cached_audio_size == actual_size:
                self._narration_duration_sec = cached_duration
            else:
                self._voice_status = "英文配音已就绪，正在后台读取实际时长…"
                self.voiceChanged.emit()

                def worker() -> None:
                    try:
                        duration = probe_audio_duration(audio, self._config, self._root)
                    except Exception:
                        duration = 0.0
                    self._voiceDurationReady.emit(duration, str(project_file), job_id)

                threading.Thread(
                    target=worker, name="storycut-voice-duration", daemon=True
                ).start()
                return
        self._update_loaded_voice_status(audio, srt, project_file)
        self.voiceChanged.emit()

    def _update_loaded_voice_status(self, audio: Path, srt: Path, project_file: Path) -> None:
        if audio.exists() and srt.exists():
            self._voice_status = (
                f"英文配音与同步字幕已就绪，时长 {self._format_time(self._narration_duration_sec)}"
                f" · {self._narration_speed:.2f}x"
            )
        elif audio.exists():
            self._voice_status = (
                f"英文配音已就绪，时长 {self._format_time(self._narration_duration_sec)}"
                f" · {self._narration_speed:.2f}x；建议导入同步 SRT"
            )
        elif (project_file.parent / "script" / "tts" / "gpt_sovits_reference.srt").exists():
            self._voice_status = "GPT-SoVITS SRT 已准备，请生成并导入英文配音"
        else:
            self._voice_status = "等待导出 SRT 到 GPT-SoVITS"

    @Slot(float, str, int)
    def _apply_voice_duration(self, duration: float, project_path: str, job_id: int) -> None:
        if (
            job_id != self._voice_job_id
            or not self._current_project_file
            or str(self._current_project_file) != project_path
        ):
            return
        audio = self._current_project_file.parent / "audio" / "narration.wav"
        srt = self._current_project_file.parent / "audio" / "narration.srt"
        self._narration_duration_sec = max(0.0, float(duration))
        if self._narration_duration_sec > 0 and audio.exists():
            try:
                payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
                voice_settings = payload.setdefault("settings", {}).setdefault("voice", {})
                voice_settings["duration_sec"] = self._narration_duration_sec
                voice_settings["audio_size"] = audio.stat().st_size
                self._current_project_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                pass
        self._update_loaded_voice_status(audio, srt, self._current_project_file)
        self.voiceChanged.emit()

    def _default_subtitle_style(self) -> dict[str, object]:
        export = self._config.get("export", {})
        return {
            "fontFamily": str(export.get("subtitle_font", "Arial")),
            "fontSize": int(export.get("subtitle_font_size", 48) or 48),
            "bottomMargin": int(export.get("subtitle_margin_v", 72) or 72),
            "horizontalMargin": int(export.get("subtitle_margin_h", 72) or 72),
            "bold": bool(export.get("subtitle_bold", True)),
            "italic": bool(export.get("subtitle_italic", False)),
            "textColor": str(export.get("subtitle_color", "#FFFFFFFF")),
            "outlineColor": str(export.get("subtitle_outline_color", "#000000FF")),
            "shadow": int(export.get("subtitle_shadow", 1) or 0),
            "letterSpacing": float(export.get("subtitle_spacing", 0) or 0),
            "animation": str(export.get("subtitle_animation", "fade")),
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
        self._preserve_original_audio = bool(
            self._config.get("export", {}).get("preserve_original_audio", False)
        )
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            saved = payload.get("settings", {}).get("subtitle", {})
            if isinstance(saved, dict):
                self._subtitle_style.update(saved)
            if self._subtitle_style.get("cleanupMode") not in {"mask", "blur", "delogo"}:
                self._subtitle_style["cleanupMode"] = "mask"
            self._subtitle_style["backgroundEnabled"] = False
            saved_export = payload.get("settings", {}).get("export", {})
            if isinstance(saved_export, dict):
                self._preserve_original_audio = bool(
                    saved_export.get("preserve_original_audio", self._preserve_original_audio)
                )
        except (OSError, ValueError, TypeError):
            pass
        self.subtitleStyleChanged.emit()
        self.exportChanged.emit()

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
        export["subtitle_italic"] = style["italic"]
        export["subtitle_color"] = style["textColor"]
        export["subtitle_outline_color"] = style["outlineColor"]
        export["subtitle_shadow"] = style["shadow"]
        export["subtitle_spacing"] = style["letterSpacing"]
        export["subtitle_animation"] = style["animation"]
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
        export["preserve_original_audio"] = (
            self._preserve_original_audio
            and int(self._media.get("audio_tracks", 0) or 0) > 0
        )
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

    def _set_fact_review(self, report: dict[str, object]) -> None:
        self._fact_review = dict(report)
        severity_names = {"high": "高风险", "medium": "需确认", "low": "精度建议"}
        category_names = {
            "source_support": "原片证据",
            "general_fact": "常识事实",
            "number_unit": "数字单位",
            "causality": "因果关系",
            "terminology": "术语表达",
        }
        issues: list[dict[str, object]] = []
        for raw in report.get("issues", []):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            severity = str(item.get("severity", "low"))
            category = str(item.get("category", "source_support"))
            item["severityText"] = severity_names.get(severity, "建议")
            item["categoryText"] = category_names.get(category, "事实")
            item["narrationText"] = "、".join(str(value) for value in item.get("narration_ids", []))
            issues.append(item)
        self._fact_review_issues = issues
        if not report:
            self._fact_review_status = "可选功能，尚未进行事实审查"
        elif bool(report.get("stale", False)):
            self._fact_review_status = "英文解说已修改，旧审查结果需要重新检查"
        else:
            count = int(report.get("issue_count", len(issues)) or 0)
            self._fact_review_status = (
                "审查完成：未发现明显事实风险"
                if count == 0
                else f"审查完成：发现 {count} 项需要人工确认的内容"
            )
        self.factReviewChanged.emit()

    def _load_fact_review(self, project_file: Path) -> None:
        review_file = project_file.parent / "script" / "fact_review.json"
        if not review_file.exists():
            self._set_fact_review({})
            return
        try:
            report = json.loads(review_file.read_text(encoding="utf-8"))
            self._set_fact_review(report if isinstance(report, dict) else {})
        except (OSError, ValueError, TypeError):
            self._set_fact_review({})

    def _load_content_review(self, project_file: Path) -> None:
        review_file = project_file.parent / "script" / "content_review.json"
        if review_file.exists():
            try:
                report = json.loads(review_file.read_text(encoding="utf-8"))
                fact = report.get("fact_review", {}) if isinstance(report, dict) else {}
                terminology = (
                    report.get("terminology_review", {})
                    if isinstance(report, dict)
                    else {}
                )
                self._set_fact_review(fact if isinstance(fact, dict) else {})
                self._set_terminology_review(
                    terminology if isinstance(terminology, dict) else {}
                )
                return
            except (OSError, ValueError, TypeError):
                pass
        self._load_fact_review(project_file)
        self._load_terminology_review(project_file)

    def _set_terminology_review(self, report: dict[str, object]) -> None:
        self._terminology_review = dict(report)
        category_names = {
            "term_variant": "术语译法",
            "name_consistency": "专有名称",
            "abbreviation": "缩写",
            "capitalization": "大小写",
            "unit_format": "单位格式",
            "number_consistency": "数字一致性",
        }
        issues: list[dict[str, object]] = []
        for raw in report.get("issues", []):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["categoryText"] = category_names.get(
                str(item.get("category", "term_variant")), "术语"
            )
            item["narrationText"] = "、".join(
                str(value) for value in item.get("narration_ids", [])
            )
            variants = item.get("variants", [])
            item["variantsText"] = " / ".join(
                str(value) for value in variants if str(value).strip()
            ) if isinstance(variants, list) else ""
            issues.append(item)
        self._terminology_review_issues = issues
        if not report:
            self._terminology_review_status = "可选功能，尚未检查术语一致性"
        elif bool(report.get("stale", False)):
            self._terminology_review_status = (
                "英文解说已修改，旧术语检查结果需要重新检查"
            )
        else:
            count = int(report.get("issue_count", len(issues)) or 0)
            self._terminology_review_status = (
                "术语检查完成：术语、单位和名称前后一致"
                if count == 0
                else f"术语检查完成：发现 {count} 处可统一内容"
            )
        self.terminologyReviewChanged.emit()

    def _load_terminology_review(self, project_file: Path) -> None:
        review_file = project_file.parent / "script" / "terminology_review.json"
        if not review_file.exists():
            self._set_terminology_review({})
            return
        try:
            report = json.loads(review_file.read_text(encoding="utf-8"))
            self._set_terminology_review(report if isinstance(report, dict) else {})
        except (OSError, ValueError, TypeError):
            self._set_terminology_review({})

    def _ensure_source_video(self) -> bool:
        source = Path(self._video_path) if self._video_path else None
        if source and source.exists():
            return True
        if source and self._current_project_file:
            expected_size = int(self._media.get("file_size", 0) or 0)
            candidates = (
                self._current_project_file.parent / "source" / source.name,
                self._root / source.name,
                self._root.parent / source.name,
                self._root.parent.parent / source.name,
            )
            for candidate in candidates:
                try:
                    if not candidate.exists() or not candidate.is_file():
                        continue
                    if expected_size and candidate.stat().st_size != expected_size:
                        continue
                    self._update_source_video_path(
                        candidate,
                        f"原视频已自动重新定位：{candidate}",
                    )
                    return True
                except OSError:
                    continue
        self._notice = "原视频已被移动或删除，请重新选择同一个原视频后继续"
        self.noticeChanged.emit()
        self.sourceVideoRelinkRequested.emit()
        return False

    def _update_source_video_path(self, path: Path, notice: str) -> None:
        if not self._current_project_file:
            return
        payload = json.loads(self._current_project_file.read_text(encoding="utf-8"))
        payload["source_video"] = str(path.resolve())
        if self._media:
            payload["media"] = dict(self._media)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._current_project_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._video_path = str(path.resolve())
        self._notice = notice
        self.projectChanged.emit()
        self.noticeChanged.emit()
        self._refresh_recent_projects()

    @staticmethod
    def _update_env_file(env_file: Path, values: dict[str, str]) -> None:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines() if env_file.exists() else []
        remaining = dict(values)
        updated: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in remaining:
                    updated.append(f"{key}={remaining.pop(key)}")
                    continue
            updated.append(line)
        if updated and updated[-1].strip():
            updated.append("")
        updated.extend(f"{key}={value}" for key, value in remaining.items())
        env_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = env_file.with_name(env_file.name + ".update_tmp")
        temporary.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
        os.replace(temporary, env_file)

    @staticmethod
    def _normalize_api_base_url(value: str) -> str:
        if not value:
            return ""
        candidate = value.strip().rstrip("/")
        lowered = candidate.lower()
        suffix = "/chat/completions"
        if lowered.endswith(suffix):
            candidate = candidate[: -len(suffix)].rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("接口地址格式不正确：应填写以 http:// 或 https:// 开头的 API 根地址")
        if parsed.query or parsed.fragment:
            raise ValueError("接口地址不能包含查询参数或 # 片段，请填写 API 根地址")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

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
