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

OPTIONAL TOOLS (new):
  Below the 10-step wizard, the sidebar has a separate "OPTIONAL TOOLS"
  section with three independent utilities. They are NOT part of the
  sequential pipeline -- you can open any of them at any time, in any
  order, whether or not you've done Step 1. Each one opens in its own
  window so it doesn't disturb your place in the wizard:

    1) Remove Silence      - cuts silent gaps out of a compound clip,
                              driven by an audio file (from silence.py)
    2) Duplicate Captions   - finds repeated captions already in the
                              project, splits the video at those points,
                              and drops colored markers (from duplicescene.py)
    3) Track Reducer        - compacts clips onto the lowest free track
                              and removes now-empty tracks (from TRACKREDUCE.py)

  All three are logic-wise byte-for-byte the same algorithms as your
  original standalone scripts -- only wrapped so they share this app's
  window and can be launched from one place. As a safety gate, each tool
  requires you to point it at a real .srt subtitle file for the project
  before it will run its main action (Detect/Cut/Apply) -- this is a
  sanity check that you're pointed at a finished, captioned project
  before making timeline-editing changes to it.

Requirements:  pip install PyQt5 pandas opencv-python pydub
(opencv is optional -- only used for video thumbnails in the image step)
(pydub + ffmpeg on PATH are optional -- only needed for the "Remove
 Silence" tool; the rest of the app works fine without them)
"""

import sys
import os
import re
import csv
import json
import copy
import uuid
import shutil
import time
import logging
import functools
import traceback
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path
from datetime import timedelta
from urllib.parse import quote

import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QFileDialog,
    QFrame, QSizePolicy, QMessageBox, QLineEdit, QTextEdit,
    QAbstractItemView, QStackedWidget, QPlainTextEdit,
    QDoubleSpinBox, QFormLayout, QGroupBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QColor, QPalette, QFont

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from pydub import AudioSegment
    from pydub.silence import detect_silence
except ImportError:
    AudioSegment = None
    detect_silence = None

import webbrowser

# Shared default folder the optional-tool file pickers start from (same
# folder your original standalone scripts used).
DEFAULT_CAPCUT_DIR = r"C:\Users\hpvic\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"


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



# ════════════════════════════════════════════════════════════════════════
#  OPTIONAL TOOL #1 — REMOVE SILENCE  (ported from silence.py, unchanged
#  algorithms, wrapped as an independent window launched from the
#  sidebar's OPTIONAL TOOLS section — not part of the numbered wizard)
# ════════════════════════════════════════════════════════════════════════

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

class SilenceWorker(QThread):
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


class SilenceCutterWindow(QMainWindow):
    """Optional tool #1 -- accessible any time from the main app's sidebar,
    independent of the 10-step wizard. Gated on a valid .srt file being
    selected (see _require_srt) before its main action will run."""

    def __init__(self, default_json=None, default_srt=None):
        super().__init__()
        self.setWindowTitle("CapCut Compound Clip — Silence Cutter")
        self.resize(760, 680)
        self.worker: Optional["SilenceWorker"] = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        if AudioSegment is None:
            layout.addWidget(QLabel(
                "⚠ The 'pydub' package (and ffmpeg on PATH) isn't installed, "
                "so silence detection can't run. Install with: pip install pydub"
            ))

        # --- File pickers ---
        file_group = QGroupBox("Files")
        file_layout = QFormLayout(file_group)

        json_row = QHBoxLayout()
        self.json_path_edit = QLineEdit()
        self.json_path_edit.setPlaceholderText("Select draft_content.json...")
        if default_json:
            self.json_path_edit.setText(str(default_json))
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

        srt_row = QHBoxLayout()
        self.srt_path_edit = QLineEdit()
        self.srt_path_edit.setPlaceholderText("Select the project's .srt caption file (required)...")
        if default_srt:
            self.srt_path_edit.setText(str(default_srt))
        srt_btn = QPushButton("Browse...")
        srt_btn.clicked.connect(self.pick_srt)
        srt_row.addWidget(self.srt_path_edit)
        srt_row.addWidget(srt_btn)
        file_layout.addRow("Reference SRT (required):", srt_row)

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

    def pick_srt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the project's .srt caption file", str(Path.home()), "SRT files (*.srt)"
        )
        if path:
            self.srt_path_edit.setText(path)

    def _require_srt(self):
        """Safety gate: this tool only runs once a real .srt file for the
        project has been selected."""
        text = self.srt_path_edit.text().strip().strip('"')
        if not text:
            QMessageBox.warning(
                self, "SRT file required",
                "This tool needs the project's .srt caption file selected "
                "before it can run. Please select one above."
            )
            return None
        p = Path(text)
        if not p.exists() or p.suffix.lower() != ".srt":
            QMessageBox.warning(self, "Invalid SRT file", "Please select a valid, existing .srt file.")
            return None
        return p

    def append_log(self, msg: str):
        self.log_box.appendPlainText(msg)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def on_run(self):
        if AudioSegment is None:
            QMessageBox.critical(
                self, "Missing dependency",
                "Install 'pydub' (and have ffmpeg on PATH) to use this tool:\n\npip install pydub"
            )
            return

        if self._require_srt() is None:
            return

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

        self.worker = SilenceWorker(json_path, audio_path, settings, make_backup=True)
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



# ════════════════════════════════════════════════════════════════════════
#  OPTIONAL TOOL #2 — FIX DUPLICATE CAPTIONS  (ported from
#  duplicescene.py, unchanged algorithms, wrapped as an independent
#  window launched from the sidebar's OPTIONAL TOOLS section)
# ════════════════════════════════════════════════════════════════════════

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
def dup_load_draft(json_path):
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


def dup_save_draft(draft, json_path):
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
class DuplicateCaptionWindow(QMainWindow):
    """Optional tool #2 -- accessible any time from the main app's sidebar,
    independent of the 10-step wizard. Gated on a valid .srt file being
    selected before its main "Detect" action will run."""

    def __init__(self, default_json=None, default_srt=None):
        super().__init__()
        self.setWindowTitle("CapCut Duplicate Caption Tool")
        self.resize(820, 660)

        self.json_path = str(default_json) if default_json else None
        self.draft = None
        self.main_track = None
        self.compound_clips = []
        self.target_track = None  # track that will actually be cut

        central = QWidget()
        layout = QVBoxLayout(central)

        # --- file selection ---
        file_row = QHBoxLayout()
        self.json_label = QLabel(self.json_path or "No draft_content.json selected.")
        browse_btn = QPushButton("Browse draft_content.json...")
        browse_btn.clicked.connect(self.on_browse_json)
        load_btn = QPushButton("Load Project")
        load_btn.clicked.connect(self.on_load_project)
        file_row.addWidget(self.json_label, 1)
        file_row.addWidget(browse_btn)
        file_row.addWidget(load_btn)
        layout.addLayout(file_row)

        # --- SRT gate: required before "Detect Duplicate Captions" can run ---
        srt_row = QHBoxLayout()
        srt_row.addWidget(QLabel("Reference SRT (required):"))
        self.srt_path_edit = QLineEdit()
        self.srt_path_edit.setPlaceholderText("Select the project's .srt caption file...")
        if default_srt:
            self.srt_path_edit.setText(str(default_srt))
        srt_browse_btn = QPushButton("Browse...")
        srt_browse_btn.clicked.connect(self.on_browse_srt)
        srt_row.addWidget(self.srt_path_edit, 1)
        srt_row.addWidget(srt_browse_btn)
        layout.addLayout(srt_row)

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

    def on_browse_srt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the project's .srt caption file", os.path.expanduser("~"), "SRT files (*.srt)"
        )
        if path:
            self.srt_path_edit.setText(path)

    def _require_srt(self):
        """Safety gate: this tool only detects/cuts once a real .srt file
        for the project has been selected."""
        text = self.srt_path_edit.text().strip().strip('"')
        if not text:
            QMessageBox.warning(
                self, "SRT file required",
                "This tool needs the project's .srt caption file selected "
                "before it can run. Please select one above."
            )
            return None
        if not os.path.isfile(text) or not text.lower().endswith(".srt"):
            QMessageBox.warning(self, "Invalid SRT file", "Please select a valid, existing .srt file.")
            return None
        return text

    @friendly_errors
    def on_load_project(self):
        if not self.json_path:
            raise UserFacingError("Please choose a draft_content.json file first.")

        self.draft = dup_load_draft(self.json_path)
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
        if self._require_srt() is None:
            return

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
        dup_save_draft(self.draft, self.json_path)
        QMessageBox.information(
            self, "Saved",
            "Project saved to:\n%s\n\nA backup of the previous version was "
            "kept as:\n%s.bak" % (self.json_path, self.json_path)
        )



# ════════════════════════════════════════════════════════════════════════
#  OPTIONAL TOOL #3 — TRACK REDUCER  (ported from TRACKREDUCE.py,
#  unchanged algorithms, wrapped as an independent window launched
#  from the sidebar's OPTIONAL TOOLS section)
# ════════════════════════════════════════════════════════════════════════

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


def trk_load_draft(draft_json_path):
    with open(draft_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def trk_save_draft(draft_json_path, data):
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
    """Optional tool #3 -- accessible any time from the main app's sidebar,
    independent of the 10-step wizard. Gated on a valid .srt file being
    selected before "Apply && Save" (the destructive step) will run;
    Preview stays available so you can look before you have an SRT ready."""

    def __init__(self, default_json=None, default_srt=None):
        super().__init__()
        self.setWindowTitle("CapCut Track Reducer")
        self.resize(950, 760)
        self.setStyleSheet(STYLE_SHEET)

        self.current_draft_path = None
        self.original_data = None
        self.preview_data = None
        self.preview_stats = None
        self._default_srt = str(default_srt) if default_srt else ""

        self._build_ui()

        if default_json and os.path.isfile(str(default_json)):
            self._load_path(str(default_json))

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

        srt_row = QHBoxLayout()
        srt_row.addWidget(QLabel("Reference SRT (required to Apply):"))
        self.srt_path_edit = QLineEdit()
        self.srt_path_edit.setPlaceholderText("Select the project's .srt caption file...")
        self.srt_path_edit.setText(self._default_srt)
        srt_browse_btn = QPushButton("Browse...")
        srt_browse_btn.clicked.connect(self._select_srt)
        srt_row.addWidget(self.srt_path_edit, 1)
        srt_row.addWidget(srt_browse_btn)
        file_layout.addLayout(srt_row)

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
        self._load_path(file_path)

    def _load_path(self, file_path):
        try:
            data = trk_load_draft(file_path)
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

    def _select_srt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the project's .srt caption file", os.path.expanduser("~"), "SRT files (*.srt)"
        )
        if path:
            self.srt_path_edit.setText(path)

    def _require_srt(self):
        """Safety gate: Apply && Save only runs once a real .srt file for
        the project has been selected. (Preview stays available without it.)"""
        text = self.srt_path_edit.text().strip().strip('"')
        if not text:
            QMessageBox.warning(
                self, "SRT file required",
                "Applying changes needs the project's .srt caption file "
                "selected first. Please select one above."
            )
            return None
        if not os.path.isfile(text) or not text.lower().endswith(".srt"):
            QMessageBox.warning(self, "Invalid SRT file", "Please select a valid, existing .srt file.")
            return None
        return text

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
        if self._require_srt() is None:
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
            trk_save_draft(self.current_draft_path, self.preview_data)
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
            self.original_data = trk_load_draft(self.current_draft_path)
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

# Optional tools live in their own sidebar section, separate from the
# numbered wizard above. They're independent utilities you can open at
# any time, in any order -- clicking one opens it in its own window
# instead of navigating the wizard stack.
TOOL_NAMES = [
    "Remove Silence",
    "Fix Duplicate Captions",
    "Track Reducer",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CapCut Auto Pipeline")
        self.resize(920, 760)

        self.state = PipelineState()
        self._tool_windows = {}   # idx -> open tool window instance, keeps them alive

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # sidebar: numbered wizard steps, then a separator, then the
        # independent optional tools (always clickable, any time)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        for name in STEP_NAMES:
            item = QListWidgetItem(f"🔒  {name}")
            self.sidebar.addItem(item)

        separator = QListWidgetItem("── OPTIONAL TOOLS ──")
        separator.setFlags(Qt.NoItemFlags)
        self.sidebar.addItem(separator)

        for name in TOOL_NAMES:
            item = QListWidgetItem(f"🧰  {name}")
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
        if row < 0:
            return

        # Rows past the numbered wizard are the separator + optional tools.
        # These are independent of the wizard: launch the tool window and
        # put the sidebar's highlight back on the current wizard step.
        if row >= len(STEP_NAMES):
            tool_idx = row - len(STEP_NAMES) - 1  # -1 accounts for the separator row
            if 0 <= tool_idx < len(TOOL_NAMES):
                self._launch_tool(tool_idx)
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentRow(self.current_step)
            self.sidebar.blockSignals(False)
            return

        if row == self.current_step:
            return
        # allow revisiting completed/current steps freely, but block jumping
        # ahead of an incomplete step
        if row < self.current_step or all(self.pages[i].is_complete() for i in range(row)):
            self._goto(row)
        else:
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentRow(self.current_step)
            self.sidebar.blockSignals(False)

    def _best_srt(self):
        """Pick whichever SRT the wizard has produced so far, to pre-fill
        an optional tool's SRT field (still overridable by the user)."""
        for p in (self.state.synced_srt_path, self.state.capgen_srt_path, self.state.tanglish_srt_path):
            if p:
                return p
        return None

    def _launch_tool(self, idx):
        """Open (or re-focus) one of the three independent optional tools.
        These are standalone windows -- they don't touch the wizard's
        stack/state and can be used any time, in any order, whether or
        not Step 1 has even been done."""
        win = self._tool_windows.get(idx)
        if win is None:
            default_json = self.state.json_path
            default_srt = self._best_srt()
            if idx == 0:
                win = SilenceCutterWindow(default_json=default_json, default_srt=default_srt)
            elif idx == 1:
                win = DuplicateCaptionWindow(default_json=default_json, default_srt=default_srt)
            else:
                win = TrackReducerWindow(default_json=default_json, default_srt=default_srt)
            self._tool_windows[idx] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def closeEvent(self, event):
        downloader = self.pages[6]
        if isinstance(downloader, Step7KeywordDownloader):
            downloader.stop_monitor()
        for win in self._tool_windows.values():
            if win is not None:
                win.close()
        super().closeEvent(event)

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0d1117; color: #c9d1d9;
                font-family: 'Segoe UI', 'SF Pro Text', Arial, sans-serif; font-size: 13px; }
            #sidebar { background: #161b22; border: none; padding: 6px; font-size: 13px; }
            #sidebar::item { padding: 10px 8px; border-radius: 6px; margin: 2px; }
            #sidebar::item:selected { background: #6e40c9; color: white; }
            #sidebar::item:hover { background: #21262d; }
            #sidebar::item:disabled { color: #6e7681; font-size: 10px; background: transparent; }
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