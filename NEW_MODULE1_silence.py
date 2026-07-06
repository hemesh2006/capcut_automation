#!/usr/bin/env python3
"""
capcut_silence_cutter.py
=========================

Single-file PyQt5 tool that removes silence from a CapCut project by
editing draft_content.json directly.

You give it:
  1. The project's draft_content.json
  2. The narration/music audio file that drives the timing (mp3/wav/etc)

It detects silence in the audio, then finds the "compound clip" in the
JSON and splits it into back-to-back segments that skip the silent parts —
with a small padded buffer and a short audio fade at every new cut so
nothing sounds like an abrupt jump-cut. A timestamped backup is always
made before the original JSON is overwritten.

Requires ffmpeg on PATH (pydub uses it to decode mp3/wav/etc).

Install:
    pip install pydub PyQt5

Run:
    python capcut_silence_cutter.py
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from pydub import AudioSegment
from pydub.silence import detect_silence

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

DEFAULT_CAPCUT_DIR = r"C:\Users\hpvic\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"


# ============================================================================
# CORE LOGIC — CapCut draft_content.json editing
# ============================================================================
#
# Key facts about CapCut's draft_content.json this relies on
# (reverse-engineered from a real project file):
#
# - All timing fields (target_timerange / source_timerange .start / .duration)
#   are in MICROSECONDS (1 second = 1,000,000).
# - A "compound clip" is a normal segment sitting on a normal video track,
#   whose `material_id` points into `materials.videos`. What makes it a
#   *compound* clip is that the segment's `extra_material_refs` list also
#   contains the `id` of an entry in `materials.drafts` whose `type` is
#   `"combination"` (that entry holds the nested mini-project of the
#   compound clip). We use that link to reliably detect compound-clip
#   segments instead of guessing from names.
# - Splitting a compound clip works exactly like splitting a normal clip:
#   slice its `source_timerange` / `target_timerange` into consecutive
#   pieces. The nested draft inside materials.drafts[i]['draft'] is left
#   untouched — CapCut renders whichever slice of the compound's internal
#   timeline the outer segment's source_timerange points at.
# - "Smooth" cuts are achieved two ways here:
#     1. Padding: we don't cut exactly at the detected silence boundary —
#        we leave a small buffer of the (silent) audio on each side of the
#        cut so we never clip the tail/head of a word.
#     2. Fades: each new segment gets a short audio fade-in (if it starts
#        right after a cut) and/or fade-out (if a cut follows it), via a
#        `materials.audio_fades` entry referenced through the segment's
#        `extra_material_refs` — the same mechanism CapCut itself uses.

US_PER_MS = 1000  # microseconds per millisecond


def new_id() -> str:
    """CapCut-style id: uppercase UUID4 with hyphens, e.g. 394743CD-D866-..."""
    return str(uuid.uuid4()).upper()


@dataclass
class CutSettings:
    min_silence_len_ms: int = 500       # ignore silences shorter than this
    silence_offset_db: float = 16.0     # threshold = audio.dBFS - this value
    padding_ms: int = 120               # buffer kept on each side of a cut
    min_cut_len_ms: int = 250           # skip cuts that would end up shorter than this
    fade_ms: int = 80                   # fade-in/out length applied at each new cut edge
    seek_step_ms: int = 10              # silence-detection scan resolution


@dataclass
class CompoundClipRef:
    track_index: int
    segment_index: int
    segment: dict


def find_compound_clip_segments(draft: dict) -> List[CompoundClipRef]:
    """Locate every segment that is a compound clip, anywhere in the project.

    A segment is a compound clip if one of its extra_material_refs points
    at a materials.drafts[] entry (those entries are how CapCut stores the
    nested mini-timeline of a compound/combination clip).
    """
    drafts = draft.get("materials", {}).get("drafts", [])
    draft_ids = {d.get("id") for d in drafts if d.get("id")}
    if not draft_ids:
        return []

    matches: List[CompoundClipRef] = []
    for t_idx, track in enumerate(draft.get("tracks", [])):
        for s_idx, seg in enumerate(track.get("segments", [])):
            refs = set(seg.get("extra_material_refs", []) or [])
            if refs & draft_ids:
                matches.append(CompoundClipRef(t_idx, s_idx, seg))
    return matches


def _silences_to_keep_ranges(
    silences_ms: List[Tuple[int, int]],
    total_ms: int,
    settings: CutSettings,
) -> List[Tuple[int, int]]:
    """Turn detected silence windows into the list of (start_ms, end_ms)
    ranges we should KEEP, after padding each silence inward and dropping
    cuts that would be too short to bother with."""
    pad = settings.padding_ms
    min_cut = settings.min_cut_len_ms

    cut_ranges: List[Tuple[int, int]] = []
    for s_start, s_end in silences_ms:
        c_start = s_start + pad
        c_end = s_end - pad
        if c_end - c_start >= min_cut:
            cut_ranges.append((max(0, c_start), min(total_ms, c_end)))

    # Merge any overlapping/adjacent cut ranges (can happen with tight padding)
    cut_ranges.sort()
    merged: List[Tuple[int, int]] = []
    for c in cut_ranges:
        if merged and c[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], c[1]))
        else:
            merged.append(c)

    # Complement of the cut ranges, clipped to [0, total_ms]
    keep: List[Tuple[int, int]] = []
    cursor = 0
    for c_start, c_end in merged:
        if c_start > cursor:
            keep.append((cursor, c_start))
        cursor = max(cursor, c_end)
    if cursor < total_ms:
        keep.append((cursor, total_ms))

    return [(s, e) for s, e in keep if e > s]


def _make_audio_fade(fade_in_us: int, fade_out_us: int) -> dict:
    return {
        "id": new_id(),
        "type": "audio_fade",
        "fade_in_duration": max(0, fade_in_us),
        "fade_out_duration": max(0, fade_out_us),
    }


def cut_compound_clip(
    draft: dict,
    clip_ref: CompoundClipRef,
    silences_ms: List[Tuple[int, int]],
    settings: CutSettings,
) -> dict:
    """Mutates `draft` in place: replaces the single compound-clip segment
    with a sequence of segments that skip the detected silences, with
    padded / faded cut edges. Returns a small summary dict for logging.
    """
    seg = clip_ref.segment
    orig_source_start = seg["source_timerange"]["start"]
    orig_duration = seg["source_timerange"]["duration"]
    orig_target_start = seg["target_timerange"]["start"]
    total_ms = orig_duration // US_PER_MS

    keep_ranges_ms = _silences_to_keep_ranges(silences_ms, total_ms, settings)
    if not keep_ranges_ms:
        return {"cuts_made": 0, "removed_ms": 0, "new_segment_count": 1}

    fade_us = settings.fade_ms * US_PER_MS
    new_segments: List[dict] = []
    target_cursor_us = orig_target_start

    audio_fades_list = draft.setdefault("materials", {}).setdefault("audio_fades", [])

    for i, (k_start_ms, k_end_ms) in enumerate(keep_ranges_ms):
        piece = copy.deepcopy(seg)
        piece["id"] = new_id()

        k_start_us = k_start_ms * US_PER_MS
        k_end_us = k_end_ms * US_PER_MS
        piece_duration_us = k_end_us - k_start_us

        piece["source_timerange"] = {
            "start": orig_source_start + k_start_us,
            "duration": piece_duration_us,
        }
        piece["target_timerange"] = {
            "start": target_cursor_us,
            "duration": piece_duration_us,
        }
        # render_timerange mirrors target_timerange's shape in the source file
        # (start 0 / duration 0 = "use target_timerange"); keep it neutral.
        piece["render_timerange"] = {"start": 0, "duration": 0}

        has_cut_before = i > 0
        has_cut_after = i < len(keep_ranges_ms) - 1
        if has_cut_before or has_cut_after:
            fade_mat = _make_audio_fade(
                fade_in_us=fade_us if has_cut_before else 0,
                fade_out_us=fade_us if has_cut_after else 0,
            )
            audio_fades_list.append(fade_mat)
            refs = list(piece.get("extra_material_refs", []) or [])
            refs.append(fade_mat["id"])
            piece["extra_material_refs"] = refs

        new_segments.append(piece)
        target_cursor_us += piece_duration_us

    draft["tracks"][clip_ref.track_index]["segments"][clip_ref.segment_index : clip_ref.segment_index + 1] = new_segments

    removed_ms = total_ms - sum(e - s for s, e in keep_ranges_ms)
    return {
        "cuts_made": len(keep_ranges_ms) - 1,
        "removed_ms": removed_ms,
        "new_segment_count": len(new_segments),
    }


def recompute_project_duration(draft: dict) -> int:
    """Project duration = the furthest segment end across every track."""
    max_end = 0
    for track in draft.get("tracks", []):
        for seg in track.get("segments", []):
            tr = seg.get("target_timerange", {})
            end = tr.get("start", 0) + tr.get("duration", 0)
            max_end = max(max_end, end)
    return max_end


def apply_silence_cuts(
    draft: dict,
    silences_ms: List[Tuple[int, int]],
    settings: CutSettings,
    clip_index: Optional[int] = None,
) -> dict:
    """High-level entry point.

    Finds compound-clip segment(s), cuts the one at `clip_index` (or the
    first one found if there's exactly one / clip_index is None), updates
    the project's overall duration, and returns a summary dict.
    """
    clips = find_compound_clip_segments(draft)
    if not clips:
        raise ValueError("No compound clip found in this project (no segment references a materials.drafts entry).")

    target = clips[0] if clip_index is None else clips[clip_index]

    summary = cut_compound_clip(draft, target, silences_ms, settings)
    draft["duration"] = recompute_project_duration(draft)
    summary["compound_clips_found"] = len(clips)
    summary["new_project_duration_ms"] = draft["duration"] // US_PER_MS
    return summary


# ============================================================================
# SILENCE DETECTION — thin wrapper around pydub
# ============================================================================

def detect_silences(
    path: Path,
    min_silence_len_ms: int = 500,
    silence_offset_db: float = 16.0,
    seek_step_ms: int = 10,
) -> Tuple[List[Tuple[int, int]], int, float]:
    """Returns (silences, audio_duration_ms, threshold_dbfs_used).

    silence_offset_db is how far BELOW the clip's average loudness (dBFS)
    counts as silence — e.g. 16 means "16dB quieter than the average is
    silence". This adapts automatically to quiet vs loud recordings,
    which is more robust than a fixed dBFS number.
    """
    audio = AudioSegment.from_file(str(path))
    threshold = audio.dBFS - silence_offset_db

    silences = detect_silence(
        audio,
        min_silence_len=min_silence_len_ms,
        silence_thresh=threshold,
        seek_step=seek_step_ms,
    )
    return silences, len(audio), threshold


def find_latest_backup(json_path: Path) -> Optional[Path]:
    """Find the most recent timestamped backup for a given draft_content.json,
    e.g. draft_content.backup_20260704_101530.json -> newest one wins."""
    pattern = f"{json_path.stem}.backup_*{json_path.suffix}"
    candidates = sorted(json_path.parent.glob(pattern))
    return candidates[-1] if candidates else None


# ============================================================================
# GUI — PyQt5
# ============================================================================

class Worker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)  # success, message

    def __init__(self, json_path: Path, audio_path: Path, settings: CutSettings, make_backup: bool):
        super().__init__()
        self.json_path = json_path
        self.audio_path = audio_path
        self.settings = settings
        self.make_backup = make_backup

    def run(self):
        try:
            self.log.emit(f"📄 Loading project: {self.json_path}")
            draft = json.loads(self.json_path.read_text(encoding="utf-8"))

            clips = find_compound_clip_segments(draft)
            if not clips:
                self.done.emit(False, "No compound clip found in this project's JSON.")
                return
            self.log.emit(f"🔎 Found {len(clips)} compound clip segment(s) in the project.")

            self.log.emit(f"🎧 Analyzing audio for silence: {self.audio_path}")
            silences, audio_ms, thresh = detect_silences(
                self.audio_path,
                min_silence_len_ms=self.settings.min_silence_len_ms,
                silence_offset_db=self.settings.silence_offset_db,
                seek_step_ms=self.settings.seek_step_ms,
            )
            self.log.emit(
                f"   audio length: {audio_ms/1000:.2f}s | threshold: {thresh:.1f} dBFS | "
                f"raw silences found: {len(silences)}"
            )
            if not silences:
                self.done.emit(False, "No silence detected with the current settings — try lowering the dB offset or min silence length.")
                return

            if self.make_backup:
                backup_path = self.json_path.with_name(
                    f"{self.json_path.stem}.backup_{time.strftime('%Y%m%d_%H%M%S')}{self.json_path.suffix}"
                )
                shutil.copyfile(self.json_path, backup_path)
                self.log.emit(f"🛟 Backup saved: {backup_path.name}")

            self.log.emit("✂️  Cutting silence out of the compound clip...")
            summary = apply_silence_cuts(draft, silences, self.settings)
            self.log.emit(
                f"   cuts made: {summary['cuts_made']} | "
                f"silence removed: {summary['removed_ms']/1000:.2f}s | "
                f"new segment count: {summary['new_segment_count']} | "
                f"new project duration: {summary['new_project_duration_ms']/1000:.2f}s"
            )

            self.json_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log.emit(f"💾 Saved: {self.json_path}")

            self.done.emit(
                True,
                f"Done. Removed {summary['removed_ms']/1000:.2f}s of silence across "
                f"{summary['cuts_made']} cuts.\nNew duration: {summary['new_project_duration_ms']/1000:.2f}s",
            )
        except Exception as e:
            self.log.emit(f"❌ {traceback.format_exc()}")
            self.done.emit(False, str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CapCut Compound Clip — Silence Cutter")
        self.resize(760, 640)
        self.worker: Worker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- File pickers ---
        file_group = QGroupBox("Files")
        file_layout = QFormLayout(file_group)

        json_row = QHBoxLayout()
        self.json_path_edit = QLineEdit()
        self.json_path_edit.setPlaceholderText("Select draft_content.json...")
        json_btn = QPushButton("Browse...")
        json_btn.clicked.connect(self.pick_json)
        json_row.addWidget(self.json_path_edit)
        json_row.addWidget(json_btn)
        file_layout.addRow("CapCut JSON:", json_row)

        audio_row = QHBoxLayout()
        self.audio_path_edit = QLineEdit()
        self.audio_path_edit.setPlaceholderText("Select audio file (mp3/wav)...")
        audio_btn = QPushButton("Browse...")
        audio_btn.clicked.connect(self.pick_audio)
        audio_row.addWidget(self.audio_path_edit)
        audio_row.addWidget(audio_btn)
        file_layout.addRow("Audio file:", audio_row)

        layout.addWidget(file_group)

        # --- Settings ---
        settings_group = QGroupBox("Silence detection & cut settings")
        settings_layout = QFormLayout(settings_group)

        self.min_silence_spin = QSpinBox()
        self.min_silence_spin.setRange(50, 10000)
        self.min_silence_spin.setSuffix(" ms")
        self.min_silence_spin.setValue(500)
        settings_layout.addRow("Minimum silence length:", self.min_silence_spin)

        self.db_offset_spin = QDoubleSpinBox()
        self.db_offset_spin.setRange(1, 60)
        self.db_offset_spin.setSuffix(" dB below average")
        self.db_offset_spin.setValue(16.0)
        settings_layout.addRow("Silence threshold:", self.db_offset_spin)

        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 2000)
        self.padding_spin.setSuffix(" ms")
        self.padding_spin.setValue(120)
        settings_layout.addRow("Padding kept around each cut:", self.padding_spin)

        self.min_cut_spin = QSpinBox()
        self.min_cut_spin.setRange(0, 5000)
        self.min_cut_spin.setSuffix(" ms")
        self.min_cut_spin.setValue(250)
        settings_layout.addRow("Skip cuts shorter than:", self.min_cut_spin)

        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(0, 1000)
        self.fade_spin.setSuffix(" ms")
        self.fade_spin.setValue(80)
        settings_layout.addRow("Fade in/out at each cut:", self.fade_spin)

        layout.addWidget(settings_group)

        # --- Actions ---
        actions_row = QHBoxLayout()
        self.run_btn = QPushButton("✂️  Detect Silence && Cut")
        self.run_btn.clicked.connect(self.on_run)
        actions_row.addWidget(self.run_btn)

        self.restore_btn = QPushButton("🔄 Restore Original")
        self.restore_btn.setToolTip("Undo — restores the JSON from its most recent backup")
        self.restore_btn.clicked.connect(self.on_restore)
        actions_row.addWidget(self.restore_btn)

        layout.addLayout(actions_row)

        layout.addWidget(QLabel("Log:"))
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)

    def pick_json(self):
        start_dir = DEFAULT_CAPCUT_DIR if Path(DEFAULT_CAPCUT_DIR).exists() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Select draft_content.json", start_dir, "JSON files (*.json)")
        if path:
            self.json_path_edit.setText(path)

    def pick_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select audio file", str(Path.home()), "Audio files (*.mp3 *.wav *.m4a *.aac *.flac)"
        )
        if path:
            self.audio_path_edit.setText(path)

    def append_log(self, msg: str):
        self.log_box.appendPlainText(msg)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def on_run(self):
        json_path = Path(self.json_path_edit.text().strip().strip('"'))
        audio_path = Path(self.audio_path_edit.text().strip().strip('"'))

        if not json_path.exists():
            QMessageBox.warning(self, "Missing file", "Please select a valid draft_content.json.")
            return
        if not audio_path.exists():
            QMessageBox.warning(self, "Missing file", "Please select a valid audio file.")
            return

        settings = CutSettings(
            min_silence_len_ms=self.min_silence_spin.value(),
            silence_offset_db=self.db_offset_spin.value(),
            padding_ms=self.padding_spin.value(),
            min_cut_len_ms=self.min_cut_spin.value(),
            fade_ms=self.fade_spin.value(),
        )

        self.run_btn.setEnabled(False)
        self.log_box.clear()

        self.worker = Worker(json_path, audio_path, settings, make_backup=True)
        self.worker.log.connect(self.append_log)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def on_restore(self):
        json_path = Path(self.json_path_edit.text().strip().strip('"'))
        if not str(json_path):
            QMessageBox.warning(self, "Missing file", "Please select the draft_content.json first.")
            return

        backup_path = find_latest_backup(json_path)
        if not backup_path:
            QMessageBox.warning(
                self,
                "No backup found",
                f"No backup file found next to:\n{json_path}\n\n"
                "Backups are only created after you run a cut at least once.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Restore original?",
            f"This will overwrite the current JSON with the backup:\n\n{backup_path.name}\n\n"
            "Your current (cut) version will be lost unless it also has a backup. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            shutil.copyfile(backup_path, json_path)
            self.append_log(f"🔄 Restored {json_path.name} from backup: {backup_path.name}")
            QMessageBox.information(self, "Restored", f"Original restored from:\n{backup_path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))

    def on_done(self, success: bool, message: str):
        self.run_btn.setEnabled(True)
        if success:
            QMessageBox.information(self, "Done", message)
        else:
            QMessageBox.critical(self, "Failed", message)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()