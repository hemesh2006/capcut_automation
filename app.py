"""
CapCut Auto Pipeline
=====================
One PyQt5 app that walks you through your whole caption/visual workflow,
step by step, instead of running four separate scripts by hand.

What changed vs. your original scripts (main.py, srt_from_capcut.py,
modul_hem.py, autopasteparellel_template.py):

  - LOGIC IS UNCHANGED. Every algorithm (SRT extraction, JSON/CSV building,
    photo-segment insertion, caption-template text mapping) is the same
    code, just wrapped in functions that take parameters instead of
    hard-coded paths / QFileDialog calls sprinkled everywhere.
  - AI PROMPTS ARE UNCHANGED. The exact prompt text you had for the
    Gemini transcription step, the timing-sync step, and the CSV step
    is reproduced verbatim. The app only auto-appends your real data
    under the existing "paste here" instructions so you don't have to
    copy/paste transcripts by hand.
  - THE CAPCUT PROJECT JSON IS SELECTED EXACTLY ONCE (Step 1). Every
    later step that needs it (image insertion, caption-style sync)
    reuses that same path automatically. No repeat dialogs.
  - EVERY GENERATED FILE IS SAVED TWICE, AUTOMATICALLY:
      1) into one project folder:  ~/CapCutAutoPipeline/<project_name>/
      2) mirrored into your Downloads folder
    so you never have to hunt for "which file was that".

Requirements:  pip install PyQt5 pandas opencv-python
(opencv is optional -- only used for video thumbnails in the image step)
"""

import sys
import os
import re
import csv
import json
import copy
import uuid
import shutil
from pathlib import Path
from datetime import timedelta
from urllib.parse import quote

import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QFileDialog,
    QFrame, QSizePolicy, QMessageBox, QLineEdit, QTextEdit,
    QAbstractItemView, QStackedWidget, QPlainTextEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QColor, QPalette, QFont

try:
    import cv2
except ImportError:
    cv2 = None
import webbrowser


# ════════════════════════════════════════════════════════════════════════
#  CORE PIPELINE LOGIC  (ported from your scripts, unchanged algorithms)
# ════════════════════════════════════════════════════════════════════════

# ---------- Stage A: CapCut draft JSON -> perfectly-timed native SRT ----------
# (from srt_from_capcut.py)

def microseconds_to_srt_time(us):
    td = timedelta(microseconds=us)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int(td.microseconds / 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def clean_capcut_text(content_json_str):
    try:
        data = json.loads(content_json_str)
        text = data.get("text", "")
        text = re.sub(r'<[^>]*>', '', text)
        return text.strip()
    except Exception:
        return ""


def export_capcut_to_srt(json_path, output_srt_path):
    """Extract CapCut's own (perfectly-synced) caption track to an SRT file.
    Returns the number of entries written."""
    with open(json_path, 'r', encoding='utf-8') as f:
        draft = json.load(f)

    text_materials = {}
    if "materials" in draft and "texts" in draft["materials"]:
        for text_mat in draft["materials"]["texts"]:
            mat_id = text_mat["id"]
            raw_content = text_mat.get("content", "")
            clean_text = clean_capcut_text(raw_content)
            if clean_text:
                text_materials[mat_id] = clean_text

    srt_entries = []
    if "tracks" in draft:
        for track in draft["tracks"]:
            if track.get("type") == "text" or any(
                seg.get("material_id") in text_materials for seg in track.get("segments", [])
            ):
                for segment in track.get("segments", []):
                    mat_id = segment.get("material_id")
                    if mat_id in text_materials:
                        target_range = segment.get("target_timerange", {})
                        start_us = target_range.get("start", 0)
                        duration_us = target_range.get("duration", 0)
                        end_us = start_us + duration_us
                        srt_entries.append({
                            "start": start_us,
                            "end": end_us,
                            "text": text_materials[mat_id]
                        })

    srt_entries.sort(key=lambda x: x["start"])

    with open(output_srt_path, 'w', encoding='utf-8') as srt_file:
        for index, entry in enumerate(srt_entries, start=1):
            start_time = microseconds_to_srt_time(entry["start"])
            end_time = microseconds_to_srt_time(entry["end"])
            srt_file.write(f"{index}\n")
            srt_file.write(f"{start_time} --> {end_time}\n")
            srt_file.write(f"{entry['text']}\n\n")

    return len(srt_entries)


# ---------- Stage B: generic SRT parsing (used by CSV/JSON build + downloader) ----------
# (from main.py)

def parse_srt_full(srt_path_or_text, is_text=False):
    """Parse an SRT into [{index, start_time, end_time, caption}, ...]."""
    if is_text:
        content = srt_path_or_text
    else:
        with open(srt_path_or_text, "r", encoding="utf-8") as f:
            content = f.read()

    content = content.replace('\r\n', '\n').replace('\r', '\n')

    pattern = re.compile(
        r'(\d+)\n'
        r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n'
        r'(.*?)\n\n',
        re.DOTALL
    )

    segments = []
    for match in pattern.finditer(content + "\n\n"):
        segments.append({
            "index": int(match.group(1)),
            "start_time": match.group(2),
            "end_time": match.group(3),
            "caption": match.group(4).replace("\n", " ").strip()
        })
    return segments


def parse_srt_captions_only(srt_text):
    """Return a plain list of caption strings in SRT order (used by the
    keyword-image step, mirrors main.py's lightweight parser)."""
    srt_text = srt_text.replace('\r\n', '\n').replace('\r', '\n')
    pattern = re.compile(
        r'\d+\n'
        r'\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\n'
        r'([\s\S]+?)(?=\n\n\d+\n|\Z)',
        re.MULTILINE
    )
    return [m.group(1).strip().replace('\n', ' ') for m in pattern.finditer(srt_text)]


def time_to_seconds(time_str):
    h, m, s_ms = time_str.split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


# ---------- Stage C: synced SRT + keyword CSV -> hem_visual.json + timeline csv ----------
# (from main.py)

def build_visual_json(srt_segments, csv_path, output_json_path):
    df = pd.read_csv(csv_path)

    output = {"segments": []}

    for i, row in df.iterrows():
        if i >= len(srt_segments):
            break
        segment = srt_segments[i]

        keywords = [k.strip() for k in str(row["keywords"]).split("|") if k.strip()]

        duration = round(
            time_to_seconds(segment["end_time"]) - time_to_seconds(segment["start_time"]), 3
        )

        item = {
            "index": segment["index"],
            "start_time": segment["start_time"],
            "end_time": segment["end_time"],
            "duration": duration,
            "caption": segment["caption"],
            "keywords": keywords,
            "visual_description": row.get("visual_description", ""),
            "images": []
        }

        for keyword in keywords:
            item["images"].append({
                "keyword": keyword,
                "prompt": f"{keyword}, transparent PNG overlay, isolated cutout, no background, alpha channel, ultra realistic, 8k"
            })

        output["segments"].append(item)

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    return output


def build_timeline_csv(visual_json_path, output_csv_path):
    with open(visual_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for segment in data["segments"]:
        start_time = segment["start_time"]
        end_time = segment["end_time"]
        keywords = ", ".join(segment.get("keywords", []))
        rows.append([start_time, end_time, keywords])

    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["start_time", "end_time", "keywords"])
        writer.writerows(rows)

    return len(rows)


# ---------- Stage D: insert downloaded photos into the CapCut timeline ----------
# (from modul_hem.py)

def _uid():
    return str(uuid.uuid4()).upper()


def _srt_time_to_seconds(time_str):
    h, m, s_ms = time_str.strip().split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _find_photo_template(data):
    for track in data["tracks"]:
        if track.get("type") != "video":
            continue
        for seg in track.get("segments", []):
            material_id = seg.get("material_id")
            for mat in data["materials"]["videos"]:
                if mat.get("id") == material_id and mat.get("type") == "photo":
                    return track, seg, mat
    raise Exception("No photo template found. Add one image manually in CapCut and save project.")


def _create_new_track(template_track):
    new_track = copy.deepcopy(template_track)
    new_track["id"] = _uid()
    new_track["segments"] = []
    return new_track


def _get_free_track(data, photo_track, start_us, end_us):
    photo_tracks = [t for t in data["tracks"] if t.get("type") == "video"]
    for track in photo_tracks:
        overlap = False
        for seg in track.get("segments", []):
            seg_start = seg["target_timerange"]["start"]
            seg_end = seg_start + seg["target_timerange"]["duration"]
            if start_us < seg_end and end_us > seg_start:
                overlap = True
                break
        if not overlap:
            return track
    new_track = _create_new_track(photo_track)
    data["tracks"].append(new_track)
    return new_track


def _add_photo_segment(data, photo_track, photo_segment_template, photo_material_template,
                        image_path, start_time, end_time):
    duration_us = int((end_time - start_time) * 1000000)
    start_us = int(start_time * 1000000)
    end_us = int(end_time * 1000000)

    material_id = _uid()
    material = copy.deepcopy(photo_material_template)
    material["id"] = material_id
    material["path"] = image_path
    material["material_name"] = os.path.basename(image_path)
    material["duration"] = duration_us
    data["materials"]["videos"].append(material)

    target_track = _get_free_track(data, photo_track, start_us, end_us)

    segment = copy.deepcopy(photo_segment_template)
    segment["id"] = _uid()
    segment["material_id"] = material_id
    segment["source_timerange"] = {"start": 0, "duration": duration_us}
    segment["target_timerange"] = {"start": start_us, "duration": duration_us}
    segment["render_timerange"] = {"start": start_us, "duration": duration_us}
    target_track["segments"].append(segment)


def process_csv_into_json(csv_path, json_path, log=print):
    """Same logic as modul_hem.process_csv, but returns (added, skipped)
    instead of exiting the process, and reuses the already-selected
    CapCut json_path (no dialog)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    photo_track, photo_segment_template, photo_material_template = _find_photo_template(data)

    added = 0
    skipped = 0
    max_end_us = 0

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        try:
            image_path = row["path"].strip()
            if not image_path or image_path.lower() == "none":
                skipped += 1
                continue
            if not os.path.exists(image_path):
                log(f"SKIPPED (missing file): {image_path}")
                skipped += 1
                continue

            start_time = _srt_time_to_seconds(row["start_time"])
            end_time = _srt_time_to_seconds(row["end_time"])
            if end_time <= start_time:
                skipped += 1
                continue

            _add_photo_segment(
                data=data,
                photo_track=photo_track,
                photo_segment_template=photo_segment_template,
                photo_material_template=photo_material_template,
                image_path=image_path,
                start_time=start_time,
                end_time=end_time
            )
            max_end_us = max(max_end_us, int(end_time * 1000000))
            added += 1
            log(f"ADDED: {os.path.basename(image_path)}  {start_time:.3f} -> {end_time:.3f}")

        except Exception as e:
            skipped += 1
            log(f"ERROR: {e}")

    for track in data["tracks"]:
        if track.get("type") == "video":
            track["segments"].sort(key=lambda s: s["target_timerange"]["start"])

    if max_end_us > 0:
        data["duration"] = max_end_us

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    return added, skipped


# ---------- Stage E: paste real caption text into the styled caption template ----------
# (from autopasteparellel_template.py)

def _resolve_sub_material_ids(mat_id, text_templates):
    if mat_id in text_templates:
        return [r["text_material_id"] for r in text_templates[mat_id].get("text_info_resources", [])]
    return [mat_id]


def _get_text(mat_id, text_materials):
    mat = text_materials.get(mat_id, {})
    content = mat.get("content", "")
    if content.startswith("{"):
        try:
            return json.loads(content).get("text", "")
        except Exception:
            return ""
    return content


def _contains_placeholder(segments, text_materials, text_templates):
    for seg in segments:
        for sub_id in _resolve_sub_material_ids(seg.get("material_id"), text_templates):
            if "the quick brown fox" in _get_text(sub_id, text_materials).lower():
                return True
    return False


def run_autopaste_sync(json_path, log=print):
    """Same logic as autopasteparellel_template.py, wrapped as a function
    that reuses the already-selected json_path (no dialog). Returns the
    number of styled template blocks that were updated."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    text_tracks = [track for track in data.get("tracks", []) if track.get("type") == "text"]
    if len(text_tracks) < 2:
        raise Exception(f"Found {len(text_tracks)} text track(s). Need at least 2 tracks to map them.")

    track_a_segments = text_tracks[0].get("segments", [])
    track_b_segments = text_tracks[1].get("segments", [])

    text_materials = {mat["id"]: mat for mat in data.get("materials", {}).get("texts", [])}
    text_templates = {tpl["id"]: tpl for tpl in data.get("materials", {}).get("text_templates", [])}

    if _contains_placeholder(track_a_segments, text_materials, text_templates):
        template_segments = track_a_segments
        source_segments = track_b_segments
        log("Detected Track 1 as the Template Track and Track 2 as the Text Source Track.")
    else:
        template_segments = track_b_segments
        source_segments = track_a_segments
        log("Detected Track 2 as the Template Track and Track 1 as the Text Source Track.")

    template_segments.sort(key=lambda x: x.get("target_timerange", {}).get("start", 0))
    source_segments.sort(key=lambda x: x.get("target_timerange", {}).get("start", 0))

    log(f"Matching {len(source_segments)} text segments to {len(template_segments)} template style blocks...")

    matched_count = 0
    for src_seg, tpl_seg in zip(source_segments, template_segments):
        src_ids = _resolve_sub_material_ids(src_seg.get("material_id"), text_templates)
        if not src_ids:
            continue

        actual_text = _get_text(src_ids[0], text_materials)
        if not actual_text:
            continue

        tpl_ids = _resolve_sub_material_ids(tpl_seg.get("material_id"), text_templates)

        for tpl_id in tpl_ids:
            tpl_mat = text_materials.get(tpl_id)
            if not tpl_mat:
                continue
            tpl_content = tpl_mat.get("content", "")
            try:
                if tpl_content.startswith("{"):
                    tpl_json = json.loads(tpl_content)
                    tpl_json["text"] = actual_text
                    new_content = json.dumps(tpl_json, ensure_ascii=False)
                else:
                    new_content = actual_text
                tpl_mat["content"] = new_content
                matched_count += 1
            except Exception as e:
                log(f"Skipped a sub-block due to structural error: {e}")

        log(f"Mapped: -> '{actual_text}' into style block ({len(tpl_ids)} sub-part(s)).")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return matched_count


# ════════════════════════════════════════════════════════════════════════
#  AI PROMPTS  (verbatim from main.py -- do not edit the instructions,
#  only the "DATA" appended underneath is auto-filled by the app)
# ════════════════════════════════════════════════════════════════════════

PROMPT_TRANSCRIBE = """You are an advanced AI transcription engine. I am uploading an MP3 audio file ("0703.MP3") containing a mix of Tamil and English (Tanglish).

Your task is to transcribe this audio directly into a perfectly synced SRT subtitle file. The current output has a delay where the text appears slightly AFTER the words are spoken. You must fix this lag.

CRITICAL TIMING & LAG-FIXING RULES:
1. Aggressive Start-Time Anchor: Force the start timestamp of every single subtitle block to match the exact millisecond the speaker begins making sound for that phrase. Do not delay the start time.
2. Anticipate Spoken Words: Subtitles must appear on screen the exact moment the first syllable is uttered, not after the phrase is completed.
3. Natural Speech Chunking: Break the subtitles into short, scannable phrases based on natural pauses. Do not let a single subtitle block contain more than 5-7 words or exceed 35 characters.
4. Enforce Micro-Gaps: Do not chain the timestamps back-to-back with zero millisecond gaps. Leave tiny millisecond gaps between blocks when the speaker takes a breath so CapCut can separate them cleanly.
5. Language & Format: Write the transcript using English/Latin script (Tanglish). Return ONLY a valid SRT format file (HH:MM:SS,mmm --> HH:MM:SS,mmm). No extra conversational text."""

PROMPT_SYNC_TEMPLATE = """You are an expert subtitling assistant and computational linguist fluent in Tamil and "Tanglish" (Tamil spoken/written using the Latin script).

### Task Overview
I have two SRT subtitle files for the exact same video timeline:
1. **Source File (Tamil Script):** This file was generated by CapCut and has 100% PERFECT video synchronization/timestamps. However, it is written in native Tamil script.
2. **Target File (Tanglish):** This file contains a phonetic transcript or translation written in English/Latin text. However, its current timestamps are completely out of sync, drift over time, or have different phrase segment lengths.

Your job is to rebuild the Target File (Tanglish) by copying the exact timeline pacing, start times, and end times from the Source File (Tamil Script), ensuring that the spoken words match the timing flawlessly.

### Mapping Rules & Logic
1. **Anchor by Meaning:** Analyze the phonetic/Tanglish words in the target file and map them to their exact semantic equivalent in the Tamil script file (e.g., "padichirupinga" maps to "படிச்சிருப்பீங்க", "coding thaan important" maps to "கோடிங் தான் இப்போர்ட்டண்ட்").
2. **Preserve CapCut Timestamps:** Do not invent new timestamps. Use the exact `HH:MM:SS,mmm --> HH:MM:SS,mmm` blocks provided in the Source Tamil file.
3. **Handle Segment Mismatches:**
   - If the Tanglish file breaks a sentence across 3 blocks but the Tamil file combines it into 1 block, merge the Tanglish text into that single Tamil timestamp block.
   - Ensure that no dialogue text from the Tanglish version is left behind or skipped.
4. **Output Format:** Provide only the final, correctly synced Tanglish SRT file inside a clean code block. Do not add conversational intro/outro text.

---
### INPUT DATA

#### 1. SOURCE FILE (Tamil - PERFECT TIMESTAMPS)
{tamil_srt}

#### 2. TARGET FILE (Tanglish - FIX THESE TIMESTAMPS)
{tanglish_srt}"""

PROMPT_CSV_TEMPLATE = """Act as an expert Video Production AI and Metadata Engineer.

Task: Convert the provided raw SRT subtitle track directly into a clean, downloadable CSV file layout that mirrors the exact column schema and visual syntax of "visuals.csv".

Output CSV Schema Columns:
1. caption: The raw dialogue text string from the srt block (cleanly isolated, removing timecodes/numbers).
2. keywords: At least 5 highly relevant keywords or short phrases separated explicitly by pipes (|). The keywords must maintain narrative continuity by understanding what happens in the previous and next lines, optimized perfectly for Google Stock Image search terms.
3. visual_description: A highly descriptive, cinematic prompt matching the text context. For technical overlays, use "Transparent PNG overlay elements: [assets], isolated technology assets, transparent background...". For human or environment shots, always prioritize a moody, cinematic, high-contrast studio aesthetic, specifying premium camera physics like "85mm lens, f/1.4 aperture, shallow depth of field, 8k detail".

Constraints:
- Ensure strict 1-to-1 matching row counts between srt captions and output CSV data lines.
- Do not add conversational text or markdown code block syntax inside the final downloadable format. Deliver it cleanly as an executable CSV generation structure.

Here is the raw SRT file data to convert:
{srt_data}"""


# ════════════════════════════════════════════════════════════════════════
#  SHARED STATE
# ════════════════════════════════════════════════════════════════════════

class PipelineState:
    def __init__(self):
        self.json_path = None          # CapCut draft_content.json -- selected ONCE
        self.project_dir = None        # ~/CapCutAutoPipeline/<project_name>/
        self.downloads_dir = Path.home() / "Downloads"

        self.tanglish_srt_path = None
        self.capgen_srt_path = None    # perfectly-timed, native-language, from CapCut itself
        self.synced_srt_path = None    # final, correctly-timed Tanglish captions
        self.visuals_csv_path = None   # caption/keywords/visual_description csv from AI
        self.hem_json_path = None
        self.timeline_csv_path = None  # start_time,end_time,keywords  (+ path, filled in step 7)

    def mirror_to_downloads(self, project_path: Path):
        """Copy a file that already lives in the project folder into Downloads too."""
        try:
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(project_path, self.downloads_dir / project_path.name)
        except Exception:
            pass

    def save_text(self, filename: str, text: str) -> Path:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        p = self.project_dir / filename
        p.write_text(text, encoding="utf-8")
        self.mirror_to_downloads(p)
        return p


# ════════════════════════════════════════════════════════════════════════
#  SMALL UI HELPERS
# ════════════════════════════════════════════════════════════════════════

def section_header(text):
    lbl = QLabel(text)
    lbl.setObjectName("sectionHeader")
    return lbl


def hr():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setObjectName("divider")
    return line


class StepPage(QWidget):
    """Base class for a wizard page. Override is_complete() for pages that
    gate progression on an action being finished."""
    status_changed = pyqtSignal()

    def is_complete(self) -> bool:
        return True

    def on_enter(self, state: PipelineState):
        """Called every time the page becomes visible."""
        pass


# ════════════════════════════════════════════════════════════════════════
#  STEP 1 — Select the CapCut project JSON (ONLY dialog for the project file)
# ════════════════════════════════════════════════════════════════════════

class Step1SelectProject(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(section_header("STEP 1 · SELECT YOUR CAPCUT PROJECT"))

        info = QLabel(
            "Pick the draft_content.json for the CapCut project you're working on.\n\n"
            "You only do this once — every later step (extracting captions, "
            "inserting images, syncing the caption style) reuses this exact "
            "file automatically."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        btn = QPushButton("📂  Select CapCut draft_content.json")
        btn.setObjectName("bigActionBtn")
        btn.clicked.connect(self.select_file)
        root.addWidget(btn)

        self.path_label = QLabel("No file selected yet.")
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("pathLabel")
        root.addWidget(self.path_label)

        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setObjectName("pathLabel")
        root.addWidget(self.folder_label)

        root.addStretch()

    def select_file(self):
        start_dir = (
            Path.home() / "AppData" / "Local" / "CapCut" / "User Data"
            / "Projects" / "com.lveditor.draft"
        )
        if not start_dir.exists():
            start_dir = Path.home()

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select CapCut draft_content.json", str(start_dir),
            "JSON Files (*.json);;All Files (*)"
        )
        if not file_path:
            return

        self.state.json_path = Path(file_path)

        project_name = self.state.json_path.parent.name or self.state.json_path.stem
        base = Path.home() / "CapCutAutoPipeline"
        project_dir = base / project_name
        n = 1
        while project_dir.exists() and not (project_dir / ".capcut_pipeline_marker").exists():
            project_dir = base / f"{project_name}_{n}"
            n += 1
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / ".capcut_pipeline_marker").touch(exist_ok=True)
        self.state.project_dir = project_dir
        self.state.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.path_label.setText(f"✔ CapCut project: {self.state.json_path}")
        self.folder_label.setText(
            f"📁 Project files will be saved to: {project_dir}\n"
            f"📁 Also mirrored to: {self.state.downloads_dir}"
        )
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 2 — Transcription prompt (Gemini) -> paste Tanglish SRT
# ════════════════════════════════════════════════════════════════════════

class Step2Transcribe(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 2 · TRANSCRIBE THE AUDIO (GEMINI)"))

        desc = QLabel(
            "1. Upload your audio to Gemini (or your preferred AI) along with the prompt below.\n"
            "2. Paste the Tanglish SRT it gives you into the box underneath, then save."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.prompt_box = QTextEdit()
        self.prompt_box.setReadOnly(True)
        self.prompt_box.setPlainText(PROMPT_TRANSCRIBE)
        self.prompt_box.setFixedHeight(160)
        root.addWidget(self.prompt_box)

        copy_btn = QPushButton("📋  Copy Prompt")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(PROMPT_TRANSCRIBE))
        root.addWidget(copy_btn)

        root.addWidget(hr())
        root.addWidget(section_header("PASTE THE TANGLISH SRT RESULT HERE"))

        self.paste_box = QPlainTextEdit()
        self.paste_box.setPlaceholderText("Paste the Tanglish SRT text from Gemini...")
        root.addWidget(self.paste_box)

        save_btn = QPushButton("💾  Save Tanglish SRT")
        save_btn.setObjectName("bigActionBtn")
        save_btn.clicked.connect(self.save)
        root.addWidget(save_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)

    def save(self):
        text = self.paste_box.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Nothing to save", "Paste the Tanglish SRT text first.")
            return
        segs = parse_srt_full(text, is_text=True)
        if not segs:
            QMessageBox.warning(self, "Doesn't look like SRT",
                                 "Couldn't find valid SRT blocks in that text. Check the format.")
            return
        path = self.state.save_text("tanglish.srt", text)
        self.state.tanglish_srt_path = path
        self.status_label.setText(f"✔ Saved {len(segs)} captions -> {path}")
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 3 — Auto-extract CapCut's own perfectly-timed SRT (no dialog!)
# ════════════════════════════════════════════════════════════════════════

class Step3ExtractCapcutSrt(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 3 · EXTRACT CAPCUT'S PERFECT TIMING"))

        desc = QLabel(
            "This reads the caption track CapCut already generated inside your project "
            "file (native language, perfectly synced) and saves it as an SRT.\n\n"
            "No file picker needed — it reuses the project you selected in Step 1."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.run_btn = QPushButton("⚙  Extract CapCut Captions (capgen.srt)")
        self.run_btn.setObjectName("bigActionBtn")
        self.run_btn.clicked.connect(self.run)
        root.addWidget(self.run_btn)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Preview will appear here after extraction...")
        root.addWidget(self.preview)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)

    def run(self):
        if not self.state.json_path:
            QMessageBox.warning(self, "Missing project", "Go back to Step 1 and select your CapCut project first.")
            return
        out_path = self.state.project_dir / "capgen.srt"
        try:
            count = export_capcut_to_srt(self.state.json_path, out_path)
        except Exception as e:
            QMessageBox.critical(self, "Extraction failed", str(e))
            return

        if count == 0:
            QMessageBox.warning(self, "No captions found",
                                 "No text track segments were found in this project's JSON.")
            return

        self.state.mirror_to_downloads(out_path)
        self.state.capgen_srt_path = out_path
        self.preview.setPlainText(out_path.read_text(encoding="utf-8")[:3000])
        self.status_label.setText(f"✔ Extracted {count} captions -> {out_path}")
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 4 — Sync timing prompt -> paste corrected Tanglish SRT
# ════════════════════════════════════════════════════════════════════════

class Step4SyncTiming(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 4 · SYNC THE TIMING"))

        desc = QLabel(
            "This prompt is pre-filled with CapCut's perfectly-timed captions (Step 3) "
            "and your Tanglish transcript (Step 2). Send it to your AI, then paste the "
            "corrected, perfectly-timed Tanglish SRT it returns."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.prompt_box = QTextEdit()
        self.prompt_box.setReadOnly(True)
        self.prompt_box.setFixedHeight(180)
        root.addWidget(self.prompt_box)

        copy_btn = QPushButton("📋  Copy Prompt (with your data filled in)")
        copy_btn.clicked.connect(self.copy_prompt)
        root.addWidget(copy_btn)

        root.addWidget(hr())
        root.addWidget(section_header("PASTE THE SYNCED TANGLISH SRT RESULT HERE"))

        self.paste_box = QPlainTextEdit()
        self.paste_box.setPlaceholderText("Paste the corrected, synced SRT text...")
        root.addWidget(self.paste_box)

        save_btn = QPushButton("💾  Save Synced SRT")
        save_btn.setObjectName("bigActionBtn")
        save_btn.clicked.connect(self.save)
        root.addWidget(save_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)

    def on_enter(self, state):
        if state.capgen_srt_path and state.tanglish_srt_path:
            tamil = state.capgen_srt_path.read_text(encoding="utf-8")
            tanglish = state.tanglish_srt_path.read_text(encoding="utf-8")
            self._filled_prompt = PROMPT_SYNC_TEMPLATE.format(tamil_srt=tamil, tanglish_srt=tanglish)
            self.prompt_box.setPlainText(self._filled_prompt)
        else:
            self._filled_prompt = None
            self.prompt_box.setPlainText(
                "⚠ Complete Step 2 (Tanglish SRT) and Step 3 (CapCut SRT) first."
            )

    def copy_prompt(self):
        if not self._filled_prompt:
            QMessageBox.warning(self, "Not ready", "Complete Steps 2 and 3 first.")
            return
        QApplication.clipboard().setText(self._filled_prompt)

    def save(self):
        text = self.paste_box.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Nothing to save", "Paste the synced SRT text first.")
            return
        segs = parse_srt_full(text, is_text=True)
        if not segs:
            QMessageBox.warning(self, "Doesn't look like SRT",
                                 "Couldn't find valid SRT blocks in that text. Check the format.")
            return
        path = self.state.save_text("synced.srt", text)
        self.state.synced_srt_path = path
        self.status_label.setText(f"✔ Saved {len(segs)} synced captions -> {path}")
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 5 — CSV-generation prompt -> paste visuals.csv content
# ════════════════════════════════════════════════════════════════════════

class Step5GenerateCsv(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 5 · GENERATE KEYWORDS CSV"))

        desc = QLabel(
            "This prompt is pre-filled with your synced SRT (Step 4). Send it to your AI, "
            "then paste the CSV it returns (caption, keywords, visual_description)."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.prompt_box = QTextEdit()
        self.prompt_box.setReadOnly(True)
        self.prompt_box.setFixedHeight(180)
        root.addWidget(self.prompt_box)

        copy_btn = QPushButton("📋  Copy Prompt (with your SRT filled in)")
        copy_btn.clicked.connect(self.copy_prompt)
        root.addWidget(copy_btn)

        root.addWidget(hr())
        root.addWidget(section_header("PASTE THE CSV RESULT HERE"))

        self.paste_box = QPlainTextEdit()
        self.paste_box.setPlaceholderText("caption,keywords,visual_description\n...")
        root.addWidget(self.paste_box)

        save_btn = QPushButton("💾  Save visuals.csv")
        save_btn.setObjectName("bigActionBtn")
        save_btn.clicked.connect(self.save)
        root.addWidget(save_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)

    def on_enter(self, state):
        if state.synced_srt_path:
            srt_text = state.synced_srt_path.read_text(encoding="utf-8")
            self._filled_prompt = PROMPT_CSV_TEMPLATE.format(srt_data=srt_text)
            self.prompt_box.setPlainText(self._filled_prompt)
        else:
            self._filled_prompt = None
            self.prompt_box.setPlainText("⚠ Complete Step 4 (Synced SRT) first.")

    def copy_prompt(self):
        if not self._filled_prompt:
            QMessageBox.warning(self, "Not ready", "Complete Step 4 first.")
            return
        QApplication.clipboard().setText(self._filled_prompt)

    def save(self):
        text = self.paste_box.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Nothing to save", "Paste the CSV text first.")
            return
        path = self.state.save_text("visuals.csv", text)
        try:
            df = pd.read_csv(path)
            required = {"caption", "keywords", "visual_description"}
            if not required.issubset(set(c.strip() for c in df.columns)):
                raise ValueError(f"Missing columns. Found: {list(df.columns)}")
        except Exception as e:
            QMessageBox.critical(self, "CSV problem", f"Couldn't parse that as the expected CSV:\n{e}")
            return
        self.state.visuals_csv_path = path
        self.status_label.setText(f"✔ Saved {len(df)} rows -> {path}")
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 6 — Auto-build hem_visual.json + timeline_search_element.csv
# ════════════════════════════════════════════════════════════════════════

class Step6BuildPlan(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 6 · BUILD THE VISUAL PLAN"))

        desc = QLabel(
            "Combines your synced SRT (Step 4) and keywords CSV (Step 5) into:\n"
            "  • hem_visual.json — full plan with per-segment image prompts\n"
            "  • timeline_search_element.csv — the sheet the image-finder step uses\n\n"
            "No file pickers — both inputs are already known."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.run_btn = QPushButton("⚙  Build Visual Plan")
        self.run_btn.setObjectName("bigActionBtn")
        self.run_btn.clicked.connect(self.run)
        root.addWidget(self.run_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)
        root.addStretch()

    def run(self):
        if not (self.state.synced_srt_path and self.state.visuals_csv_path):
            QMessageBox.warning(self, "Not ready", "Complete Steps 4 and 5 first.")
            return

        segs = parse_srt_full(self.state.synced_srt_path)
        hem_json_path = self.state.project_dir / "hem_visual.json"
        timeline_csv_path = self.state.project_dir / "timeline_search_element.csv"

        try:
            build_visual_json(segs, self.state.visuals_csv_path, hem_json_path)
            row_count = build_timeline_csv(hem_json_path, timeline_csv_path)
        except Exception as e:
            QMessageBox.critical(self, "Build failed", str(e))
            return

        self.state.mirror_to_downloads(hem_json_path)
        self.state.mirror_to_downloads(timeline_csv_path)
        self.state.hem_json_path = hem_json_path
        self.state.timeline_csv_path = timeline_csv_path

        self.status_label.setText(
            f"✔ Built plan for {row_count} segments.\n"
            f"  {hem_json_path}\n  {timeline_csv_path}"
        )
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 7 — Keyword-driven image/video finder & downloader (embedded)
#  (adapted from the KeywordDownloader in main.py: same behaviour, but it
#  auto-loads the CSV Step 6 built, so there is no "Load CSV" dialog
#  unless you explicitly want to swap files.)
# ════════════════════════════════════════════════════════════════════════

class DownloadMonitor(QThread):
    new_file = pyqtSignal(str)
    SUPPORTED = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.mp4', '.mov', '.avi', '.mkv', '.webm'}
    IGNORE = {'.crdownload', '.tmp', '.part', '.download'}

    def __init__(self, downloads_dir: Path, parent=None):
        super().__init__(parent)
        self.downloads_dir = downloads_dir
        self._running = True
        self._known = set()
        self._seed()

    def _seed(self):
        try:
            for f in self.downloads_dir.iterdir():
                if f.is_file():
                    self._known.add(f.name)
        except Exception:
            pass

    def run(self):
        while self._running:
            try:
                current = {}
                for f in self.downloads_dir.iterdir():
                    if f.is_file():
                        current[f.name] = f
                for name, path in current.items():
                    if name not in self._known:
                        ext = path.suffix.lower()
                        if ext not in self.IGNORE and ext in self.SUPPORTED:
                            self._known.add(name)
                            self.new_file.emit(str(path))
                self._known = {n for n in self._known if n in current}
            except Exception:
                pass
            self.msleep(1500)

    def stop(self):
        self._running = False


def load_image_preview(path: str, max_w=520, max_h=200) -> QPixmap:
    pix = QPixmap(path)
    if pix.isNull():
        return QPixmap()
    return pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def load_video_thumbnail(path: str, max_w=520, max_h=200) -> QPixmap:
    if cv2 is None:
        return QPixmap()
    try:
        cap = cv2.VideoCapture(path)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return QPixmap()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format_RGB888)
        return QPixmap.fromImage(img).scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return QPixmap()


class Step7KeywordDownloader(StepPage):
    IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
    VIDEO_EXT = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}

    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self.csv_path = None
        self.df = None
        self.current_index = 0
        self.detected_file = None
        self.monitor = None
        self.captions = []
        self._auto_loaded_for = None

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.addWidget(section_header("STEP 7 · FIND & DOWNLOAD VISUALS"))

        top = QHBoxLayout()
        self.reload_btn = QPushButton("🔄 Reload plan CSV")
        self.reload_btn.clicked.connect(lambda: self._load_csv(self.state.timeline_csv_path))
        self.other_csv_btn = QPushButton("📂 Use a different CSV")
        self.other_csv_btn.clicked.connect(self._pick_other_csv)
        self.csv_label = QLabel("No CSV loaded")
        self.csv_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top.addWidget(self.reload_btn)
        top.addWidget(self.other_csv_btn)
        top.addWidget(self.csv_label)
        root.addLayout(top)
        root.addWidget(hr())

        self.segment_label = QLabel("Segment — / —")
        self.segment_label.setObjectName("segmentLabel")
        root.addWidget(self.segment_label)

        self.time_label = QLabel("Time: —")
        root.addWidget(self.time_label)
        root.addWidget(hr())

        root.addWidget(section_header("Caption"))
        self.caption_box = QTextEdit()
        self.caption_box.setReadOnly(True)
        self.caption_box.setFixedHeight(60)
        root.addWidget(self.caption_box)

        root.addWidget(section_header("Keywords (click to search)"))
        self.keyword_list = QListWidget()
        self.keyword_list.setFixedHeight(90)
        self.keyword_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.keyword_list.itemSelectionChanged.connect(self._on_keyword_selected)
        root.addWidget(self.keyword_list)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Edit keyword before searching...")
        self.search_edit.returnPressed.connect(self._manual_search)
        self.search_go_btn = QPushButton("🔍 Google Images")
        self.search_go_btn.clicked.connect(self._manual_search)
        search_row.addWidget(self.search_edit)
        search_row.addWidget(self.search_go_btn)
        root.addLayout(search_row)
        root.addWidget(hr())

        root.addWidget(section_header("Preview"))
        self.preview_label = QLabel("No preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(180)
        root.addWidget(self.preview_label)

        self.file_path_label = QLabel("Detected file: —")
        self.file_path_label.setWordWrap(True)
        root.addWidget(self.file_path_label)
        root.addWidget(hr())

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.clicked.connect(self.go_previous)
        self.skip_btn = QPushButton("⏭ Skip")
        self.skip_btn.clicked.connect(self.go_skip)
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(self.go_next)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.skip_btn)
        nav.addWidget(self.next_btn)
        root.addLayout(nav)

        self.status_label = QLabel("Load the plan CSV to begin.")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status_label)

        self._set_nav_enabled(False)

    # ---- lifecycle ----
    def on_enter(self, state):
        if state.synced_srt_path:
            self.captions = parse_srt_captions_only(state.synced_srt_path.read_text(encoding="utf-8"))
        if state.timeline_csv_path and self._auto_loaded_for != state.timeline_csv_path:
            self._load_csv(state.timeline_csv_path)
            self._auto_loaded_for = state.timeline_csv_path

    def is_complete(self):
        if self.df is None:
            return False
        return all(str(v).strip() not in ('', 'nan') for v in self.df['path'])

    # ---- CSV loading ----
    def _pick_other_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV File", str(Path.home()), "CSV Files (*.csv)")
        if path:
            self._load_csv(Path(path))

    def _load_csv(self, csv_path):
        if not csv_path:
            QMessageBox.warning(self, "No plan yet", "Finish Step 6 first (or pick a CSV manually).")
            return
        self.csv_path = Path(csv_path)
        try:
            self.df = pd.read_csv(self.csv_path, dtype=str).fillna("")
        except Exception as e:
            QMessageBox.critical(self, "CSV Error", f"Could not read CSV:\n{e}")
            return

        if 'path' not in self.df.columns:
            self.df['path'] = ""
            self._save_csv()

        self.csv_label.setText(f"📄 {self.csv_path.name}  ({len(self.df)} rows)")
        self.current_index = self._find_first_empty()
        self._start_monitor()
        self._load_row(self.current_index)
        self._set_nav_enabled(True)

    def _find_first_empty(self):
        if self.df is None:
            return 0
        for i in range(len(self.df)):
            if str(self.df.at[i, 'path']).strip() in ('', 'nan'):
                return i
        return max(0, len(self.df) - 1)

    def _save_csv(self):
        if self.df is not None and self.csv_path is not None:
            try:
                self.df.to_csv(self.csv_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
                self.state.mirror_to_downloads(self.csv_path)
            except Exception as e:
                self.status_label.setText(f"⚠ Save error: {e}")

    # ---- row display ----
    def _load_row(self, idx):
        if self.df is None or not (0 <= idx < len(self.df)):
            return
        self.current_index = idx
        self.detected_file = None
        self._clear_preview()

        row = self.df.iloc[idx]
        total = len(self.df)
        start = str(row.get('start_time', '')).strip()
        end = str(row.get('end_time', '')).strip()

        self.segment_label.setText(f"Segment {idx + 1} / {total}")
        self.time_label.setText(f"⏱ {start} → {end}")

        kw_raw = str(row.get('keywords', '')).strip()
        keywords = [k.strip() for k in kw_raw.split(',') if k.strip()]
        self.keyword_list.blockSignals(True)
        self.keyword_list.clear()
        for kw in keywords:
            self.keyword_list.addItem(QListWidgetItem(kw))
        self.keyword_list.blockSignals(False)

        if 0 <= idx < len(self.captions):
            self.caption_box.setPlainText(self.captions[idx])
        else:
            self.caption_box.setPlainText("(no caption for this row)")

        existing = str(row.get('path', '')).strip()
        has_path = existing not in ('', 'nan', 'None')
        if has_path:
            self.file_path_label.setText(f"✔ Saved: {existing}")
            self._show_preview(existing)
            self.status_label.setText(f"Row {idx + 1}: already saved — browse only.")
        else:
            self.file_path_label.setText("Detected file: waiting for download...")
            self.status_label.setText(f"Row {idx + 1}: waiting for download...")

        if keywords:
            self.keyword_list.blockSignals(True)
            self.keyword_list.setCurrentRow(0)
            self.keyword_list.blockSignals(False)
            self.search_edit.setText(keywords[0])
            if not has_path:
                self._open_google(keywords[0])

    # ---- navigation ----
    def go_next(self):
        if self.df is None:
            return
        existing = str(self.df.at[self.current_index, 'path']).strip()
        if existing in ('', 'nan', 'None') and self.detected_file:
            self.df.at[self.current_index, 'path'] = self.detected_file
            self._save_csv()
            self.status_label.setText(f"✔ Saved: {Path(self.detected_file).name}")

        nxt = self.current_index + 1
        if nxt < len(self.df):
            self._load_row(nxt)
        else:
            self.status_label.setText("🎉 All rows complete!")
        self.status_changed.emit()

    def go_skip(self):
        if self.df is None:
            return
        existing = str(self.df.at[self.current_index, 'path']).strip()
        if existing in ('', 'nan', 'None'):
            self.df.at[self.current_index, 'path'] = 'None'
            self._save_csv()
        nxt = self.current_index + 1
        if nxt < len(self.df):
            self._load_row(nxt)
        else:
            self.status_label.setText("All rows processed.")
        self.status_changed.emit()

    def go_previous(self):
        if self.df is None:
            return
        prev = self.current_index - 1
        if prev >= 0:
            self._load_row(prev)

    def _set_nav_enabled(self, on):
        for btn in (self.prev_btn, self.skip_btn, self.next_btn):
            btn.setEnabled(on)

    # ---- keyword search ----
    def _on_keyword_selected(self):
        items = self.keyword_list.selectedItems()
        if items:
            self.search_edit.setText(items[0].text())

    def _manual_search(self):
        kw = self.search_edit.text().strip()
        if kw:
            self._open_google(kw)

    def _open_google(self, keyword):
        url = f"https://www.google.com/search?q={quote(keyword)}&tbm=isch"
        webbrowser.open(url)
        self.status_label.setText(f"🔍 Searching: {keyword}")

    # ---- download monitor ----
    def _start_monitor(self):
        if self.monitor and self.monitor.isRunning():
            self.monitor.stop()
            self.monitor.wait()
        self.monitor = DownloadMonitor(self.state.downloads_dir)
        self.monitor.new_file.connect(self._on_new_download)
        self.monitor.start()

    def _on_new_download(self, file_path):
        self.detected_file = file_path
        self.file_path_label.setText(f"📥 Detected: {file_path}")
        self.status_label.setText(f"📥 New file: {Path(file_path).name}")
        self._show_preview(file_path)

    # ---- preview ----
    def _clear_preview(self):
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("No preview")

    def _show_preview(self, file_path):
        p = Path(file_path)
        ext = p.suffix.lower()
        pix = QPixmap()
        if ext in self.IMAGE_EXT:
            pix = load_image_preview(file_path)
        elif ext in self.VIDEO_EXT:
            pix = load_video_thumbnail(file_path)
        if pix and not pix.isNull():
            self.preview_label.setPixmap(pix)
            self.preview_label.setText("")
        else:
            self.preview_label.setText(f"⚠ Preview unavailable\n{p.name}")

    def stop_monitor(self):
        if self.monitor and self.monitor.isRunning():
            self.monitor.stop()
            self.monitor.wait()


# ════════════════════════════════════════════════════════════════════════
#  STEP 8 — Insert downloaded images into the CapCut timeline (no dialogs)
# ════════════════════════════════════════════════════════════════════════

class Step8InsertImages(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 8 · INSERT IMAGES INTO THE TIMELINE"))

        desc = QLabel(
            "Writes every downloaded image/video from Step 7 into your CapCut project as "
            "timed photo segments. Uses the same project JSON from Step 1 and the same "
            "plan CSV from Step 6/7 — no file pickers.\n\n"
            "A backup of your project JSON is made first, just in case."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.run_btn = QPushButton("⚙  Insert Images Into Timeline")
        self.run_btn.setObjectName("bigActionBtn")
        self.run_btn.clicked.connect(self.run)
        root.addWidget(self.run_btn)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        root.addWidget(self.log_box)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)

    def run(self):
        if not (self.state.json_path and self.state.timeline_csv_path):
            QMessageBox.warning(self, "Not ready", "Complete Steps 1, 6 and 7 first.")
            return

        backup = self.state.project_dir / "backup_before_images.json"
        try:
            shutil.copy(self.state.json_path, backup)
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", f"Refusing to continue without a backup:\n{e}")
            return

        self.log_box.clear()
        try:
            added, skipped = process_csv_into_json(
                csv_path=self.state.timeline_csv_path,
                json_path=self.state.json_path,
                log=lambda msg: self.log_box.appendPlainText(msg)
            )
        except Exception as e:
            QMessageBox.critical(self, "Insert failed", str(e))
            return

        self.status_label.setText(f"✔ Added {added} segments, skipped {skipped}. (Backup: {backup.name})")
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 9 — Paste real captions into the styled caption template (no dialogs)
# ════════════════════════════════════════════════════════════════════════

class Step9SyncCaptionStyle(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state
        self._complete = False

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("STEP 9 · SYNC THE CAPTION STYLE TEMPLATE"))

        desc = QLabel(
            "Before running this: in CapCut, make sure your styled caption template track "
            "is created and perfectly aligned in time with your plain text/source caption "
            "track (this is the same manual alignment step your original workflow needed).\n\n"
            "Then click the button — it reuses the same project JSON from Step 1."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.run_btn = QPushButton("⚙  Run Caption Template Sync")
        self.run_btn.setObjectName("bigActionBtn")
        self.run_btn.clicked.connect(self.run)
        root.addWidget(self.run_btn)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        root.addWidget(self.log_box)

        self.status_label = QLabel("")
        self.status_label.setObjectName("pathLabel")
        root.addWidget(self.status_label)

    def run(self):
        if not self.state.json_path:
            QMessageBox.warning(self, "Not ready", "Complete Step 1 first.")
            return

        backup = self.state.project_dir / "backup_before_caption_sync.json"
        try:
            shutil.copy(self.state.json_path, backup)
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", f"Refusing to continue without a backup:\n{e}")
            return

        self.log_box.clear()
        try:
            matched = run_autopaste_sync(
                self.state.json_path,
                log=lambda msg: self.log_box.appendPlainText(msg)
            )
        except Exception as e:
            QMessageBox.critical(self, "Sync failed", str(e))
            return

        self.status_label.setText(f"✔ Updated {matched} styled caption blocks. (Backup: {backup.name})")
        self._complete = True
        self.status_changed.emit()

    def is_complete(self):
        return self._complete


# ════════════════════════════════════════════════════════════════════════
#  STEP 10 — Done
# ════════════════════════════════════════════════════════════════════════

class Step10Done(StepPage):
    def __init__(self, state: PipelineState):
        super().__init__()
        self.state = state

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(section_header("🎉  DONE"))

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        open_btn = QPushButton("📁 Open Project Folder")
        open_btn.setObjectName("bigActionBtn")
        open_btn.clicked.connect(self.open_folder)
        root.addWidget(open_btn)
        root.addStretch()

    def on_enter(self, state):
        lines = ["Your CapCut project has been fully updated. Files saved:\n"]
        for label, p in [
            ("Tanglish transcript", state.tanglish_srt_path),
            ("CapCut-timed captions", state.capgen_srt_path),
            ("Synced captions", state.synced_srt_path),
            ("Keyword CSV", state.visuals_csv_path),
            ("Visual plan JSON", state.hem_json_path),
            ("Timeline plan CSV", state.timeline_csv_path),
        ]:
            if p:
                lines.append(f"• {label}: {p}")
        lines.append(f"\nProject folder: {state.project_dir}")
        lines.append(f"Also mirrored to: {state.downloads_dir}")
        self.summary.setText("\n".join(lines))

    def open_folder(self):
        if self.state.project_dir:
            try:
                os.startfile(self.state.project_dir)  # Windows
            except AttributeError:
                webbrowser.open(str(self.state.project_dir))
            except Exception:
                QMessageBox.information(self, "Project folder", str(self.state.project_dir))


# ════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW — wizard shell with sidebar navigation
# ════════════════════════════════════════════════════════════════════════

STEP_NAMES = [
    "1. Select Project",
    "2. Transcribe Audio",
    "3. Extract CapCut SRT",
    "4. Sync Timing",
    "5. Generate CSV",
    "6. Build Visual Plan",
    "7. Find & Download Visuals",
    "8. Insert Images",
    "9. Sync Caption Style",
    "10. Done",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CapCut Auto Pipeline")
        self.resize(920, 760)

        self.state = PipelineState()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        for name in STEP_NAMES:
            item = QListWidgetItem(f"🔒  {name}")
            self.sidebar.addItem(item)
        self.sidebar.currentRowChanged.connect(self._sidebar_clicked)
        layout.addWidget(self.sidebar)

        # right side: stacked pages + nav bar
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 16, 20, 12)

        self.stack = QStackedWidget()
        self.pages = [
            Step1SelectProject(self.state),
            Step2Transcribe(self.state),
            Step3ExtractCapcutSrt(self.state),
            Step4SyncTiming(self.state),
            Step5GenerateCsv(self.state),
            Step6BuildPlan(self.state),
            Step7KeywordDownloader(self.state),
            Step8InsertImages(self.state),
            Step9SyncCaptionStyle(self.state),
            Step10Done(self.state),
        ]
        for p in self.pages:
            p.status_changed.connect(self._refresh_sidebar)
            self.stack.addWidget(p)
        right_layout.addWidget(self.stack)

        nav_bar = QHBoxLayout()
        self.back_btn = QPushButton("◀  Back")
        self.back_btn.clicked.connect(self.go_back)
        self.next_btn = QPushButton("Next  ▶")
        self.next_btn.setObjectName("bigActionBtn")
        self.next_btn.clicked.connect(self.go_next)
        nav_bar.addWidget(self.back_btn)
        nav_bar.addStretch()
        nav_bar.addWidget(self.next_btn)
        right_layout.addLayout(nav_bar)

        layout.addWidget(right)

        self.current_step = 0
        self._apply_styles()
        self._goto(0)

    def _goto(self, idx):
        self.current_step = idx
        self.stack.setCurrentIndex(idx)
        self.pages[idx].on_enter(self.state)
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(idx)
        self.sidebar.blockSignals(False)
        self.back_btn.setEnabled(idx > 0)
        self.next_btn.setText("Finish" if idx == len(self.pages) - 1 else "Next  ▶")
        self.next_btn.setVisible(idx < len(self.pages) - 1)
        self._refresh_sidebar()

    def _refresh_sidebar(self):
        for i, name in enumerate(STEP_NAMES):
            if i < self.current_step:
                icon = "✅"
            elif i == self.current_step:
                icon = "▶"
            else:
                icon = "🔒"
            self.sidebar.item(i).setText(f"{icon}  {name}")

    def go_next(self):
        page = self.pages[self.current_step]
        if not page.is_complete():
            QMessageBox.information(self, "Not finished yet",
                                     "Finish this step before moving on.")
            return
        if self.current_step < len(self.pages) - 1:
            self._goto(self.current_step + 1)

    def go_back(self):
        if self.current_step > 0:
            self._goto(self.current_step - 1)

    def _sidebar_clicked(self, row):
        if row < 0 or row == self.current_step:
            return
        # allow revisiting completed/current steps freely, but block jumping
        # ahead of an incomplete step
        if row < self.current_step or all(self.pages[i].is_complete() for i in range(row)):
            self._goto(row)
        else:
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentRow(self.current_step)
            self.sidebar.blockSignals(False)

    def closeEvent(self, event):
        downloader = self.pages[6]
        if isinstance(downloader, Step7KeywordDownloader):
            downloader.stop_monitor()
        super().closeEvent(event)

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0d1117; color: #c9d1d9;
                font-family: 'Segoe UI', 'SF Pro Text', Arial, sans-serif; font-size: 13px; }
            #sidebar { background: #161b22; border: none; padding: 6px; font-size: 13px; }
            #sidebar::item { padding: 10px 8px; border-radius: 6px; margin: 2px; }
            #sidebar::item:selected { background: #6e40c9; color: white; }
            #sidebar::item:hover { background: #21262d; }
            #sectionHeader { color: #79c0ff; font-size: 15px; font-weight: bold; }
            #segmentLabel { font-size: 16px; font-weight: bold; color: #c084fc; }
            #pathLabel { color: #8b949e; font-size: 11px; }
            QPushButton { background: #21262d; color: #c9d1d9; border: none;
                padding: 8px 14px; border-radius: 6px; }
            QPushButton:hover { background: #30363d; }
            #bigActionBtn { background: #196127; color: #56d364; font-weight: bold; padding: 10px 16px; }
            #bigActionBtn:hover { background: #238636; color: white; }
            QTextEdit, QPlainTextEdit, QLineEdit {
                background: #161b22; border: 1px solid #30363d; border-radius: 6px;
                color: #e6edf3; padding: 6px; }
            QListWidget { background: #161b22; border: 1px solid #30363d; border-radius: 6px; }
            QListWidget::item:selected { background: #6e40c9; color: white; }
            QFrame#divider { color: #21262d; max-height: 1px; }
        """)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()