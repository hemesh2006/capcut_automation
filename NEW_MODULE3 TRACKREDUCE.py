"""
CapCut Track Reducer
=====================
A single-file PyQt5 desktop tool. You manually pick a draft_content.json
file (the file dialog opens straight into your CapCut projects folder), and
the tool pushes video/image/text/audio clips down onto the lowest free
track, removing any track that becomes empty. Includes Preview, Apply
(with automatic backup) and Restore.

Default starting folder for the file picker:
    C:\\Users\\hpvic\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft

Run:
    pip install PyQt5
    python track_reducer_app.py
"""

import sys
import os
import json
import shutil
import copy

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView
)

DEFAULT_START_DIR = r"C:\Users\hpvic\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
BACKUP_SUFFIX = ".track_reducer_backup.json"

# Only these track types get reduced. "video" tracks in CapCut also hold
# image/photo clips, so this covers video + image + text + audio.
REDUCIBLE_TYPES = {"video", "text", "audio"}


# ----------------------------------------------------------------------
# Core logic (pure functions on the JSON — no UI code here)
# ----------------------------------------------------------------------

def backup_path_for(draft_json_path):
    return draft_json_path + BACKUP_SUFFIX


def load_draft(draft_json_path):
    with open(draft_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_draft(draft_json_path, data):
    with open(draft_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def make_backup(draft_json_path):
    bpath = backup_path_for(draft_json_path)
    if not os.path.exists(bpath):
        shutil.copyfile(draft_json_path, bpath)
    return bpath


def restore_backup(draft_json_path):
    bpath = backup_path_for(draft_json_path)
    if not os.path.exists(bpath):
        raise FileNotFoundError("No backup found for this draft. Nothing to restore.")
    shutil.copyfile(bpath, draft_json_path)
    os.remove(bpath)


def _overlaps(a_start, a_dur, b_start, b_dur):
    return a_start < (b_start + b_dur) and b_start < (a_start + a_dur)


def _segment_range(seg):
    tr = seg.get("target_timerange", {"start": 0, "duration": 0})
    return tr.get("start", 0), tr.get("duration", 0)


def reduce_tracks(data):
    """
    Move every clip (video/image/text) down onto the lowest track of the same
    type that is free at that time range. Drop any track left with zero
    segments, then re-stamp every remaining segment's `track_render_index`
    to match its track's FINAL position in the tracks array.

    Bug this fixes: CapCut ties each segment to its track via
    `track_render_index`, which always equals that track's index in the
    `tracks` array. Previously this value was only patched by copying it
    from a segment already sitting in the destination track — if the
    destination was empty, or if track removal later shifted every index,
    the value went stale. CapCut then couldn't resolve the segment's real
    track and the clip (often text) silently failed to render, looking
    "deleted" even though it was still present in the JSON.

    `render_index` is left untouched — it turned out to be an internal
    counter/ID unrelated to which track a segment lives on, not a stacking
    value, so there's no reason to touch it.

    Returns (new_data, stats_dict). Operates on a deep copy.
    """
    original_data = data
    data = copy.deepcopy(data)
    tracks = data.get("tracks", [])

    groups = {}
    for idx, t in enumerate(tracks):
        if t.get("type") in REDUCIBLE_TYPES:
            groups.setdefault(t.get("type"), []).append(idx)

    stats = {
        "before_counts": {t: len(idxs) for t, idxs in groups.items()},
        "after_counts": {},
        "moved_segments": 0,
        "removed_tracks": 0,
    }

    # --- Pass 1: move clips down onto free lower tracks ---
    changed = True
    while changed:
        changed = False
        for ttype, idxs in groups.items():
            for i in range(len(idxs) - 1, 0, -1):
                cur_track = tracks[idxs[i]]
                lower_track = tracks[idxs[i - 1]]
                cur_segs = cur_track.get("segments", [])

                for seg in list(cur_segs):
                    s_start, s_dur = _segment_range(seg)
                    lower_segs = lower_track.get("segments", [])
                    conflict = any(
                        _overlaps(s_start, s_dur, *_segment_range(ls))
                        for ls in lower_segs
                    )
                    if not conflict:
                        cur_segs.remove(seg)
                        lower_segs.append(seg)
                        lower_segs.sort(key=lambda x: _segment_range(x)[0])
                        stats["moved_segments"] += 1
                        changed = True

    # --- Pass 2: drop tracks that ended up empty (only reducible types) ---
    new_tracks = []
    for idx, t in enumerate(tracks):
        if t.get("type") in REDUCIBLE_TYPES and len(t.get("segments", [])) == 0:
            stats["removed_tracks"] += 1
            continue
        new_tracks.append(t)
    data["tracks"] = new_tracks

    # --- Pass 3: re-stamp track_render_index to match FINAL array position ---
    # This is the critical fix — every segment must point at where its track
    # actually ends up, not where it used to be before other tracks were
    # removed and everything shifted.
    for new_idx, t in enumerate(new_tracks):
        for seg in t.get("segments", []):
            if "track_render_index" in seg:
                seg["track_render_index"] = new_idx

    for ttype in stats["before_counts"]:
        stats["after_counts"][ttype] = sum(1 for t in new_tracks if t.get("type") == ttype)

    # Hard safety check: only tracks may have changed. Every element must
    # still be present, exactly once, nothing added/removed/altered.
    total_before, total_after = verify_conservation(original_data, data)
    stats["total_elements_before"] = total_before
    stats["total_elements_after"] = total_after

    return data, stats


def _collect_all_segment_ids(data):
    """Map: track_type -> set of segment ids, across every track (all types)."""
    by_type = {}
    for t in data.get("tracks", []):
        ttype = t.get("type", "unknown")
        ids = by_type.setdefault(ttype, set())
        for seg in t.get("segments", []):
            ids.add(seg.get("id"))
    return by_type


def verify_conservation(before_data, after_data):
    """
    Hard safety check: confirm that ONLY tracks changed — every element
    (segment) that existed before still exists after, exactly once, with
    nothing added, removed, or duplicated. Only its track assignment may
    differ. Raises RuntimeError with details if this is ever violated.
    """
    before_ids = _collect_all_segment_ids(before_data)
    after_ids = _collect_all_segment_ids(after_data)

    all_types = set(before_ids.keys()) | set(after_ids.keys())
    problems = []
    for ttype in sorted(all_types):
        b = before_ids.get(ttype, set())
        a = after_ids.get(ttype, set())
        if b != a:
            missing = b - a
            extra = a - b
            problems.append(
                f"[{ttype}] before={len(b)} after={len(a)} "
                f"missing={len(missing)} extra={len(extra)}"
            )

    if problems:
        raise RuntimeError(
            "Element conservation check FAILED — aborting to protect your timeline.\n"
            "Only tracks should be reduced; no element should be added, removed, "
            "or changed.\n" + "\n".join(problems)
        )

    total_before = sum(len(s) for s in before_ids.values())
    total_after = sum(len(s) for s in after_ids.values())
    return total_before, total_after


def track_summary(data):
    summary = {}
    for t in data.get("tracks", []):
        ttype = t.get("type", "unknown")
        summary.setdefault(ttype, []).append(len(t.get("segments", [])))
    return summary


# ----------------------------------------------------------------------
# Stylesheet — colorful, rounded, smooth look
# ----------------------------------------------------------------------

STYLE_SHEET = """
QMainWindow {
    background-color: #1e1f29;
}
QWidget {
    color: #e6e6f0;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #3a3b4d;
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px;
    background-color: #262738;
    font-weight: 600;
    color: #9d8cff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QLineEdit {
    background-color: #1a1b26;
    border: 1px solid #3a3b4d;
    border-radius: 6px;
    padding: 6px 8px;
    color: #e6e6f0;
    selection-background-color: #9d8cff;
}
QLineEdit:focus {
    border: 1px solid #9d8cff;
}
QPushButton {
    background-color: #7c6ef2;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #9d8cff;
}
QPushButton:pressed {
    background-color: #6152c9;
}
QPushButton:disabled {
    background-color: #43445a;
    color: #8a8a9a;
}
QPushButton#applyBtn {
    background-color: #2ec27e;
}
QPushButton#applyBtn:hover {
    background-color: #4fdb99;
}
QPushButton#applyBtn:disabled {
    background-color: #43445a;
}
QPushButton#restoreBtn {
    background-color: #e05b6a;
}
QPushButton#restoreBtn:hover {
    background-color: #f07d8a;
}
QPushButton#restoreBtn:disabled {
    background-color: #43445a;
}
QTableWidget {
    background-color: #1a1b26;
    alternate-background-color: #20212e;
    gridline-color: #3a3b4d;
    border: 1px solid #3a3b4d;
    border-radius: 8px;
    selection-background-color: #4b3f9e;
}
QHeaderView::section {
    background-color: #33344a;
    color: #cfcfff;
    padding: 6px;
    border: none;
    font-weight: 600;
}
QTextEdit {
    background-color: #14151f;
    border: 1px solid #3a3b4d;
    border-radius: 8px;
    padding: 6px;
    color: #b7f5c8;
    font-family: Consolas, 'Courier New', monospace;
}
QLabel#pathLabel {
    color: #a0a0b8;
    font-style: italic;
}
QLabel#titleLabel {
    font-size: 20px;
    font-weight: 700;
    color: #ffffff;
    padding: 4px 0 2px 0;
}
QLabel#subtitleLabel {
    color: #9d8cff;
    padding-bottom: 6px;
}
QScrollBar:vertical {
    background: #1a1b26;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #7c6ef2;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #9d8cff;
}
"""


# ----------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------

class TrackReducerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CapCut Track Reducer")
        self.resize(950, 700)

        self.current_draft_path = None
        self.original_data = None
        self.preview_data = None
        self.preview_stats = None

        self._build_ui()

    # ---------------- UI construction ----------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        title = QLabel("CapCut Track Reducer")
        title.setObjectName("titleLabel")
        subtitle = QLabel("Merge clips onto lower free tracks and clean up your timeline.")
        subtitle.setObjectName("subtitleLabel")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        # --- File selection ---
        file_box = QGroupBox("Draft File")
        file_layout = QVBoxLayout(file_box)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No file selected...")
        self.path_edit.setReadOnly(True)
        select_btn = QPushButton("Select draft_content.json")
        select_btn.clicked.connect(self._select_file)
        row.addWidget(self.path_edit, stretch=1)
        row.addWidget(select_btn)
        file_layout.addLayout(row)
        hint = QLabel(f"Opens starting from: {DEFAULT_START_DIR}")
        hint.setObjectName("pathLabel")
        hint.setWordWrap(True)
        file_layout.addWidget(hint)
        outer.addWidget(file_box)

        # --- Track summary table ---
        table_box = QGroupBox("Track Summary")
        table_layout = QVBoxLayout(table_box)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Track Type", "Before (clips per track)", "After (clips per track)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        table_layout.addWidget(self.table)
        outer.addWidget(table_box, stretch=1)

        # --- Action buttons row ---
        actions_box = QGroupBox("Actions")
        actions_layout = QHBoxLayout(actions_box)
        self.preview_btn = QPushButton("Preview (Analyze)")
        self.preview_btn.clicked.connect(self._on_preview)
        self.apply_btn = QPushButton("Apply && Save")
        self.apply_btn.setObjectName("applyBtn")
        self.apply_btn.clicked.connect(self._on_apply)
        self.restore_btn = QPushButton("Restore Backup")
        self.restore_btn.setObjectName("restoreBtn")
        self.restore_btn.clicked.connect(self._on_restore)
        for b in (self.preview_btn, self.apply_btn, self.restore_btn):
            b.setEnabled(False)
        actions_layout.addWidget(self.preview_btn)
        actions_layout.addWidget(self.apply_btn)
        actions_layout.addWidget(self.restore_btn)
        outer.addWidget(actions_box)

        # --- Log ---
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        outer.addWidget(log_box, stretch=1)

        self.statusBar().showMessage("Ready — select a draft_content.json file to begin.")

    # ---------------- Helpers ----------------

    def _log(self, msg):
        self.log.append(msg)

    def _fill_table(self, before_summary, after_summary=None):
        self.table.setRowCount(0)
        types = sorted(set(before_summary.keys()) | set((after_summary or {}).keys()))
        for ttype in types:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(ttype))
            before_list = before_summary.get(ttype, [])
            self.table.setItem(row, 1, QTableWidgetItem(f"{len(before_list)} track(s): {before_list}"))
            if after_summary is not None:
                after_list = after_summary.get(ttype, [])
                after_txt = f"{len(after_list)} track(s): {after_list}"
            else:
                after_txt = "-"
            self.table.setItem(row, 2, QTableWidgetItem(after_txt))

    # ---------------- Actions ----------------

    def _select_file(self):
        start_dir = DEFAULT_START_DIR if os.path.isdir(DEFAULT_START_DIR) else os.path.expanduser("~")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select draft_content.json",
            start_dir,
            "CapCut Draft JSON (*.json);;All Files (*)"
        )
        if not file_path:
            return

        try:
            data = load_draft(file_path)
        except Exception as e:
            QMessageBox.critical(self, "Load failed", f"Could not read this file:\n{e}")
            return

        self.current_draft_path = file_path
        self.original_data = data
        self.preview_data = None
        self.preview_stats = None

        self.path_edit.setText(file_path)
        before = track_summary(data)
        self._fill_table(before)

        has_backup = os.path.exists(backup_path_for(file_path))
        self.preview_btn.setEnabled(True)
        self.apply_btn.setEnabled(False)
        self.restore_btn.setEnabled(has_backup)

        self._log(f"\nLoaded: {file_path}")
        if has_backup:
            self._log("A backup already exists for this draft (Restore is available).")
        self.statusBar().showMessage("Draft loaded.", 4000)

    def _on_preview(self):
        if self.original_data is None:
            return
        try:
            new_data, stats = reduce_tracks(self.original_data)
        except Exception as e:
            QMessageBox.critical(self, "Analysis failed", str(e))
            return

        self.preview_data = new_data
        self.preview_stats = stats

        before = track_summary(self.original_data)
        after = track_summary(new_data)
        self._fill_table(before, after)

        self._log(
            f"\nPreview:\n"
            f"  Segments moved: {stats['moved_segments']}\n"
            f"  Tracks removed: {stats['removed_tracks']}\n"
            f"  Before: {stats['before_counts']}\n"
            f"  After:  {stats['after_counts']}\n"
            f"  Total elements before: {stats['total_elements_before']}  |  "
            f"Total elements after: {stats['total_elements_after']}  "
            f"{'✓ match' if stats['total_elements_before'] == stats['total_elements_after'] else '✗ MISMATCH'}"
        )

        if stats["moved_segments"] == 0 and stats["removed_tracks"] == 0:
            self._log("Timeline is already optimal — nothing to reduce.")
            self.apply_btn.setEnabled(False)
        else:
            self.apply_btn.setEnabled(True)
        self.statusBar().showMessage("Preview ready.", 4000)

    def _on_apply(self):
        if self.preview_data is None or self.current_draft_path is None:
            return
        confirm = QMessageBox.question(
            self, "Apply changes",
            f"This will overwrite:\n{self.current_draft_path}\n\n"
            "A backup of the current file will be kept so you can Restore later.\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            make_backup(self.current_draft_path)
            save_draft(self.current_draft_path, self.preview_data)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return

        self._log(f"\nApplied changes and saved to:\n{self.current_draft_path}")
        self._log("Backup saved — use 'Restore Backup' if you want to undo this.")

        self.original_data = self.preview_data
        self.preview_data = None
        self.apply_btn.setEnabled(False)
        self.restore_btn.setEnabled(True)
        self.statusBar().showMessage("Changes applied and saved.", 5000)

    def _on_restore(self):
        if self.current_draft_path is None:
            return
        confirm = QMessageBox.question(
            self, "Restore backup",
            "This will discard the reduced version and restore the original\n"
            "draft_content.json for this file.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            restore_backup(self.current_draft_path)
            self.original_data = load_draft(self.current_draft_path)
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))
            return

        self.preview_data = None
        self.preview_stats = None
        before = track_summary(self.original_data)
        self._fill_table(before)

        self.apply_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)
        self._log("\nRestored original draft_content.json.")
        self.statusBar().showMessage("Restored original file.", 5000)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)
    win = TrackReducerWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()