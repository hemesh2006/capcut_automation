# -*- coding: utf-8 -*-
"""
CapCut Duplicate Caption Tool  (single-file PyQt5 app)
=======================================================

What it does
------------
1. You pick a CapCut `draft_content.json`.
2. If the project has more than one compound clip, they are listed so you
   can select the one to work on, then press OK.
3. The tool reads the CAPTIONS already inside the project timeline and
   finds any caption text that repeats (a "duplicate caption").
4. For every duplicate caption found:
     - the video (of the compound clip / main track) is CUT (split) at
       that caption's start/end points - nothing is removed or deleted,
       the clip is simply split into pieces "in place" on the timeline.
     - a colored marker is added at that point:
         * the FIRST time a caption's text appears  -> one color
         * every time the SAME text appears again   -> a different color
5. Save writes back to the exact same draft_content.json path (a .bak
   backup of the previous version is made automatically first).

Any error anywhere shows a plain-English message box instead of a crash.

Install:  pip install PyQt5
Run:      python capcut_duplicate_caption_tool.py
"""

import copy
import functools
import json
import logging
import os
import shutil
import sys
import traceback
import uuid

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QGroupBox, QFormLayout,
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
# Default folder to start browsing CapCut projects from.
DEFAULT_CAPCUT_DIR = (
    r"C:\Users\hpvic\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
)

# Marker colors (taken directly from a real CapCut project's markers, so
# they match what you'd get by adding a flag manually in the app):
#   - the LAST time a caption's text appears = treated as the ORIGINAL /
#     best-quality take -> blue
#   - every occurrence BEFORE that (including the very first one) is
#     treated as a DUPLICATE / lower-quality take -> red/coral
ORIGINAL_COLOR = "#00c1cd"       # blue/teal - last occurrence (the keeper)
DUPLICATE_COLOR = "#FC7265"      # coral/red - first / earlier occurrence(s)

LOG_PATH = os.path.join(os.path.expanduser("~"), "CapCutDuplicateCaptionTool.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("CapCutDuplicateCaptionTool")


# ----------------------------------------------------------------------
# Error handling helpers
# ----------------------------------------------------------------------
class UserFacingError(Exception):
    """Raise this to show the user a plain-English message."""
    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or ""


def friendly_errors(func):
    """Wrap a PyQt slot so any exception becomes a friendly message box."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # Qt's `clicked` signal passes an extra "checked" bool argument to
        # whatever slot it's connected to. None of this app's on_* methods
        # take extra arguments, so we deliberately swallow/ignore whatever
        # Qt passes in rather than forwarding it to func.
        try:
            return func(self)
        except UserFacingError as e:
            logger.warning("UserFacingError: %s | details=%s", e.message, e.details)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Please check this")
            box.setText(e.message)
            if e.details:
                box.setDetailedText(e.details)
            box.exec_()
        except Exception as e:  # noqa: BLE001 - deliberate catch-all
            logger.error("Unhandled exception: %s\n%s", e, traceback.format_exc())
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Something went wrong")
            box.setText(
                "Something went wrong while doing that. Nothing has been "
                "saved. You can try again, or check the log file for "
                "technical details."
            )
            box.setDetailedText("%s\n\nFull log: %s" % (traceback.format_exc(), LOG_PATH))
            box.exec_()
    return wrapper


def us_to_timecode(us):
    total_ms = int(round(us / 1000.0))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d.%03d" % (h, m, s, ms)


# ----------------------------------------------------------------------
# Draft (draft_content.json) load / save
# ----------------------------------------------------------------------
def load_draft(json_path):
    if not json_path or not os.path.isfile(json_path):
        raise UserFacingError(
            "That project file (draft_content.json) could not be found. "
            "Please choose the file again."
        )
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            draft = json.load(f)
    except json.JSONDecodeError as e:
        raise UserFacingError(
            "This file does not look like a valid CapCut project file "
            "(it could not be read as JSON). Please double check you "
            "selected the correct draft_content.json.",
            details=str(e),
        )
    except OSError as e:
        raise UserFacingError(
            "The project file could not be opened. It may be in use by "
            "another program (e.g. CapCut itself) - please close CapCut "
            "and try again.",
            details=str(e),
        )

    if "tracks" not in draft or "materials" not in draft:
        raise UserFacingError(
            "This JSON file does not look like a CapCut draft_content.json "
            "(it is missing the expected project data). Please choose the "
            "correct file."
        )
    return draft


def save_draft(draft, json_path):
    """Always writes back to the SAME path, after making a .bak backup."""
    if not json_path:
        raise UserFacingError("No project file path is set - nothing to save.")
    try:
        if os.path.isfile(json_path):
            shutil.copy2(json_path, json_path + ".bak")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, separators=(",", ":"))
    except OSError as e:
        raise UserFacingError(
            "The project could not be saved. Please make sure CapCut is "
            "closed and that you have permission to write to that folder, "
            "then try again.",
            details=str(e),
        )
    logger.info("Draft saved to %s (backup at %s.bak)", json_path, json_path)


# ----------------------------------------------------------------------
# Track / compound clip discovery
# ----------------------------------------------------------------------
def get_video_tracks(draft):
    return [t for t in draft.get("tracks", []) if t.get("type") == "video"]


def get_main_video_track(draft):
    """The video track with the largest total duration = the main footage."""
    video_tracks = get_video_tracks(draft)
    if not video_tracks:
        raise UserFacingError("No video track could be found in this project.")

    best_track, best_duration = None, -1
    for t in video_tracks:
        total = sum(s.get("target_timerange", {}).get("duration", 0) for s in t.get("segments", []))
        if total > best_duration:
            best_duration, best_track = total, t
    return best_track


def list_compound_clips(draft):
    """
    Returns compound/combined clips found in the project:
        [{"id", "name", "duration_us", "track": <track dict it lives on>}]
    Detected from materials['drafts'] (nested sub-drafts) and from any
    video material whose 'type' isn't the plain 'video'/'photo' value.
    """
    found = []
    materials = draft.get("materials", {})

    drafts_list = materials.get("drafts") or []
    for entry in drafts_list:
        mat_id = entry.get("id")
        track = _find_track_with_material(draft, mat_id)
        found.append({
            "id": mat_id or str(uuid.uuid4()),
            "name": entry.get("name") or entry.get("draft_name") or "Compound clip",
            "duration_us": entry.get("duration", 0),
            "track": track,
        })

    for v in materials.get("videos", []) or []:
        v_type = (v.get("type") or "").lower()
        if v_type not in ("video", "photo") and v_type != "":
            track = _find_track_with_material(draft, v.get("id"))
            found.append({
                "id": v.get("id"),
                "name": v.get("material_name") or "Compound clip",
                "duration_us": v.get("duration", 0),
                "track": track,
            })
    return found


def _find_track_with_material(draft, material_id):
    if not material_id:
        return None
    for t in get_video_tracks(draft):
        for seg in t.get("segments", []):
            if seg.get("material_id") == material_id:
                return t
    return None


# ----------------------------------------------------------------------
# Caption extraction (reads the captions already inside the project)
# ----------------------------------------------------------------------
def _text_material_plain_text(text_material):
    """materials.texts[i]['content'] is itself a JSON string like
    {"text": "...", "styles": [...]}. Pull just the plain text out."""
    raw = text_material.get("content", "")
    try:
        parsed = json.loads(raw)
        return (parsed.get("text") or "").strip()
    except (json.JSONDecodeError, TypeError):
        return (raw or "").strip()


def extract_captions(draft):
    """
    Returns a chronological list of captions already in the project:
        [{"start_us", "end_us", "text"}]

    CapCut stores caption text (materials.texts) and caption timing
    (segments on a 'text' track) separately, lined up by position/order
    rather than by a shared id, so that's how they're matched up here:
    the text track's segments are sorted by start time and paired, in
    order, with the entries in materials.texts.
    """
    text_tracks = [t for t in draft.get("tracks", []) if t.get("type") == "text"]
    texts = draft.get("materials", {}).get("texts", [])

    if not text_tracks or not texts:
        raise UserFacingError(
            "No captions were found inside this project, so there is "
            "nothing to check for duplicates."
        )

    # Use the text track with the most segments (the main caption track).
    track = max(text_tracks, key=lambda t: len(t.get("segments", [])))
    segments = sorted(track.get("segments", []), key=lambda s: s["target_timerange"]["start"])

    n = min(len(segments), len(texts))
    if n == 0:
        raise UserFacingError("No caption segments were found to check.")
    if len(segments) != len(texts):
        logger.warning(
            "Caption segment count (%d) and text material count (%d) "
            "differ - only matching the first %d.", len(segments), len(texts), n
        )

    captions = []
    for seg, text_mat in zip(segments[:n], texts[:n]):
        tr = seg["target_timerange"]
        captions.append({
            "start_us": tr["start"],
            "end_us": tr["start"] + tr["duration"],
            "text": _text_material_plain_text(text_mat),
        })
    return captions


def find_duplicate_caption_groups(captions):
    """
    Groups captions by their (normalized) text. Only returns groups that
    repeat 2+ times, each sorted chronologically (index 0 = first
    occurrence, index 1+ = duplicate occurrence(s)).
    """
    groups = {}
    for cap in captions:
        key = cap["text"].strip().lower()
        if not key:
            continue
        groups.setdefault(key, []).append(cap)

    duplicate_groups = [sorted(v, key=lambda c: c["start_us"]) for v in groups.values() if len(v) > 1]
    duplicate_groups.sort(key=lambda g: g[0]["start_us"])
    return duplicate_groups


# ----------------------------------------------------------------------
# Non-destructive splitting ("cut, don't delete") + markers
# ----------------------------------------------------------------------
def split_segments_at_points(segments, points_us):
    """
    Splits whichever segment(s) a timeline point falls inside into two
    (or more) adjacent segments at that exact point. Nothing is removed:
    every microsecond of the original segments is still present
    afterwards, just possibly divided into more pieces, in the same
    place on the timeline.
    """
    points = sorted(set(p for p in points_us if p is not None))
    if not points:
        return segments

    result = []
    for seg in segments:
        t_start = seg["target_timerange"]["start"]
        t_dur = seg["target_timerange"]["duration"]
        t_end = t_start + t_dur
        src = seg.get("source_timerange")
        s_start = src["start"] if src else None

        inner_points = [p for p in points if t_start < p < t_end]
        if not inner_points:
            result.append(seg)
            continue

        boundaries = [t_start] + inner_points + [t_end]
        for i in range(len(boundaries) - 1):
            b0, b1 = boundaries[i], boundaries[i + 1]
            piece = copy.deepcopy(seg)
            piece["id"] = str(uuid.uuid4()).upper()
            piece["target_timerange"] = {"start": b0, "duration": b1 - b0}
            if src is not None:
                offset = b0 - t_start
                piece["source_timerange"] = {"start": s_start + offset, "duration": b1 - b0}
            result.append(piece)
    return result


def add_marker(draft, start_us, duration_us, color_hex, label=""):
    """
    Adds one colored timeline marker/flag, matching CapCut's real schema
    (confirmed from an actual project file):

        "time_marks": {
            "id": "",
            "mark_items": [
                {"id": ..., "time_range": {"start":..,"duration":..},
                 "color": "#HEX", "title": "..."},
                ...
            ]
        }

    (Not a plain list - it's a dict with a 'mark_items' list inside, and
    the label field is called 'title', not 'content'.)
    """
    tm = draft.get("time_marks")
    if not isinstance(tm, dict) or not isinstance(tm.get("mark_items"), list):
        tm = {"id": "", "mark_items": []}
        draft["time_marks"] = tm

    marker = {
        "id": str(uuid.uuid4()).upper(),
        "time_range": {"start": int(start_us), "duration": int(duration_us)},
        "color": color_hex,
        "title": label,
    }
    tm["mark_items"].append(marker)
    return marker


def apply_duplicate_captions(draft, target_track, duplicate_groups):
    """
    For every occurrence in every duplicate-caption group:
      - split (cut) the target track at that occurrence's start/end,
        without deleting anything
      - add a colored marker (first occurrence = green, duplicates = red)
    Returns the number of markers added.
    """
    if target_track is None:
        raise UserFacingError("No video track could be found to cut.")

    split_points = []
    for group in duplicate_groups:
        for cap in group:
            split_points.append(cap["start_us"])
            split_points.append(cap["end_us"])

    target_track["segments"] = split_segments_at_points(target_track.get("segments", []), split_points)

    marker_count = 0
    for group in duplicate_groups:
        total_in_group = len(group)
        last_index = total_in_group - 1
        for occurrence_index, cap in enumerate(group):
            is_original = (occurrence_index == last_index)
            color = ORIGINAL_COLOR if is_original else DUPLICATE_COLOR
            if is_original:
                label = "Original (best take, %d of %d): %s" % (
                    occurrence_index + 1, total_in_group, cap["text"][:40]
                )
            else:
                label = "Duplicate %d of %d: %s" % (
                    occurrence_index + 1, total_in_group, cap["text"][:40]
                )
            add_marker(draft, cap["start_us"], cap["end_us"] - cap["start_us"], color, label)
            marker_count += 1

    return marker_count


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CapCut Duplicate Caption Tool")
        self.resize(820, 600)

        self.json_path = None
        self.draft = None
        self.main_track = None
        self.compound_clips = []
        self.target_track = None  # track that will actually be cut

        central = QWidget()
        layout = QVBoxLayout(central)

        # --- file selection ---
        file_row = QHBoxLayout()
        self.json_label = QLabel("No draft_content.json selected.")
        browse_btn = QPushButton("Browse draft_content.json...")
        browse_btn.clicked.connect(self.on_browse_json)
        load_btn = QPushButton("Load Project")
        load_btn.clicked.connect(self.on_load_project)
        file_row.addWidget(self.json_label, 1)
        file_row.addWidget(browse_btn)
        file_row.addWidget(load_btn)
        layout.addLayout(file_row)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # --- compound clip selection ---
        compound_box = QGroupBox("Compound clips (only matters if more than one is found)")
        compound_layout = QVBoxLayout(compound_box)
        self.compound_list = QListWidget()
        compound_btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK - use selected compound clip")
        ok_btn.clicked.connect(self.on_confirm_compound_clip)
        main_btn = QPushButton("Use main video track instead")
        main_btn.clicked.connect(self.on_use_main_track)
        compound_btn_row.addWidget(ok_btn)
        compound_btn_row.addWidget(main_btn)
        compound_layout.addWidget(self.compound_list)
        compound_layout.addLayout(compound_btn_row)
        layout.addWidget(compound_box)

        # --- run detection ---
        run_row = QHBoxLayout()
        run_btn = QPushButton("Detect Duplicate Captions -> Cut + Add Markers")
        run_btn.clicked.connect(self.on_run)
        run_row.addWidget(run_btn)
        layout.addLayout(run_row)

        legend = QLabel(
            "Legend:  \u25CF Duplicate / earlier take, incl. the first occurrence (red)     "
            "\u25CF Original / best take, the LAST occurrence (blue)\n"
            "Nothing is deleted - duplicate clips are only cut (split) and marked "
            "in the same place on the timeline. Marker text shows the duplicate "
            "count, e.g. 'Duplicate 1 of 2'."
        )
        legend.setWordWrap(True)
        layout.addWidget(legend)

        self.results_table = QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["Caption text", "Time", "Marker color"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.results_table)

        # --- save ---
        save_row = QHBoxLayout()
        self.status_label = QLabel("No project loaded.")
        self.save_btn = QPushButton("Save Project (overwrite original .json)")
        self.save_btn.clicked.connect(self.on_save)
        save_row.addWidget(self.status_label, 1)
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)

        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    @friendly_errors
    def on_browse_json(self):
        start_dir = DEFAULT_CAPCUT_DIR if os.path.isdir(DEFAULT_CAPCUT_DIR) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select draft_content.json", start_dir, "CapCut draft (*.json)"
        )
        if path:
            self.json_path = path
            self.json_label.setText(path)

    @friendly_errors
    def on_load_project(self):
        if not self.json_path:
            raise UserFacingError("Please choose a draft_content.json file first.")

        self.draft = load_draft(self.json_path)
        self.main_track = get_main_video_track(self.draft)
        self.target_track = self.main_track
        self.compound_clips = list_compound_clips(self.draft)

        self.summary_label.setText(
            "Project: %s\nSegments on main track: %d\nCompound clips found: %d"
            % (
                self.draft.get("name", "(unnamed)"),
                len(self.main_track.get("segments", [])),
                len(self.compound_clips),
            )
        )
        self.status_label.setText("Project loaded: %s" % os.path.basename(self.json_path))

        self.compound_list.clear()
        for clip in self.compound_clips:
            item = QListWidgetItem("%s  (%.2fs)" % (clip["name"], clip["duration_us"] / 1_000_000.0))
            item.setData(Qt.UserRole, clip)
            self.compound_list.addItem(item)

        if len(self.compound_clips) > 1:
            QMessageBox.information(
                self, "Compound clips found",
                "This project has more than one compound clip. Please "
                "select one in the list and press 'OK - use selected "
                "compound clip'."
            )
        else:
            QMessageBox.information(self, "Project loaded", "The project loaded successfully.")

    @friendly_errors
    def on_confirm_compound_clip(self):
        item = self.compound_list.currentItem()
        if not item:
            raise UserFacingError("Please select a compound clip from the list first.")
        clip = item.data(Qt.UserRole)
        if clip.get("track") is None:
            raise UserFacingError(
                "That compound clip's video track could not be located in "
                "the project, so it can't be cut. Using the main video "
                "track instead."
            )
        self.target_track = clip["track"]
        QMessageBox.information(self, "OK", "Now working on compound clip: %s" % clip["name"])

    @friendly_errors
    def on_use_main_track(self):
        self.target_track = self.main_track
        QMessageBox.information(self, "OK", "Working on the main video track.")

    @friendly_errors
    def on_run(self):
        if not self.draft:
            raise UserFacingError("Please load a project first.")

        captions = extract_captions(self.draft)
        duplicate_groups = find_duplicate_caption_groups(captions)

        if not duplicate_groups:
            QMessageBox.information(self, "No duplicates found", "No duplicate captions were found in this project.")
            self.results_table.setRowCount(0)
            return

        marker_count = apply_duplicate_captions(self.draft, self.target_track, duplicate_groups)

        self.results_table.setRowCount(0)
        for group in duplicate_groups:
            total_in_group = len(group)
            last_index = total_in_group - 1
            for occurrence_index, cap in enumerate(group):
                is_original = (occurrence_index == last_index)
                color = ORIGINAL_COLOR if is_original else DUPLICATE_COLOR
                row = self.results_table.rowCount()
                self.results_table.insertRow(row)
                self.results_table.setItem(row, 0, QTableWidgetItem(cap["text"]))
                self.results_table.setItem(
                    row, 1,
                    QTableWidgetItem("%s - %s" % (us_to_timecode(cap["start_us"]), us_to_timecode(cap["end_us"]))),
                )
                if is_original:
                    status_text = "Original (%d of %d)" % (occurrence_index + 1, total_in_group)
                else:
                    status_text = "Duplicate (%d of %d)" % (occurrence_index + 1, total_in_group)
                color_item = QTableWidgetItem(status_text)
                color_item.setBackground(QColor(color))
                self.results_table.setItem(row, 2, color_item)

        QMessageBox.information(
            self, "Done",
            "%d duplicate caption group(s) found, %d marker(s) added, and the "
            "video was cut (split) at each of those points - nothing was "
            "deleted.\n\nRemember to press 'Save Project' to write these "
            "changes to the .json file." % (len(duplicate_groups), marker_count)
        )

    @friendly_errors
    def on_save(self):
        if not self.draft:
            raise UserFacingError("There is no loaded project to save yet.")
        save_draft(self.draft, self.json_path)
        QMessageBox.information(
            self, "Saved",
            "Project saved to:\n%s\n\nA backup of the previous version was "
            "kept as:\n%s.bak" % (self.json_path, self.json_path)
        )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()