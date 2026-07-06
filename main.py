import pandas as pd
import json
import re
from pathlib import Path
import sys
from PyQt5.QtWidgets import QApplication, QFileDialog
import json
import re
from datetime import timedelta
app = QApplication(sys.argv)
print("----------------------CAPCUT - HELPER -----------------------")
print("generate a srt file in capcut ")
input("Press Enter to continue...")
print("generate the srt file through gemini")
print("""
      You are an advanced AI transcription engine. I am uploading an MP3 audio file ("0703.MP3") containing a mix of Tamil and English (Tanglish). 

Your task is to transcribe this audio directly into a perfectly synced SRT subtitle file. The current output has a delay where the text appears slightly AFTER the words are spoken. You must fix this lag.

CRITICAL TIMING & LAG-FIXING RULES:
1. Aggressive Start-Time Anchor: Force the start timestamp of every single subtitle block to match the exact millisecond the speaker begins making sound for that phrase. Do not delay the start time.
2. Anticipate Spoken Words: Subtitles must appear on screen the exact moment the first syllable is uttered, not after the phrase is completed.
3. Natural Speech Chunking: Break the subtitles into short, scannable phrases based on natural pauses. Do not let a single subtitle block contain more than 5-7 words or exceed 35 characters.
4. Enforce Micro-Gaps: Do not chain the timestamps back-to-back with zero millisecond gaps. Leave tiny millisecond gaps between blocks when the speaker takes a breath so CapCut can separate them cleanly.
5. Language & Format: Write the transcript using English/Latin script (Tanglish). Return ONLY a valid SRT format file (HH:MM:SS,mmm --> HH:MM:SS,mmm). No extra conversational text.
      """)
print("Press Enter to continue...")
print("\n\n\n")
print("extract srt from capcut......")
def microseconds_to_srt_time(us):
    """Converts microseconds into standard SRT time format (HH:MM:SS,mmm)"""
    td = timedelta(microseconds=us)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int(td.microseconds / 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def clean_capcut_text(content_json_str):
    """Parses CapCut's inner JSON string and extracts the plain text content"""
    try:
        data = json.loads(content_json_str)
        # CapCut text typically lives in 'text' key inside the JSON-in-JSON format
        text = data.get("text", "")
        # Strip XML/HTML-like tags CapCut wraps around text formatting
        text = re.sub(r'<[^>]*>', '', text)
        return text.strip()
    except Exception:
        return ""

def export_capcut_to_srt(json_path, output_srt_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        draft = json.load(f)
    
    # 1. Map text material IDs to their actual clean text strings
    text_materials = {}
    if "materials" in draft and "texts" in draft["materials"]:
        for text_mat in draft["materials"]["texts"]:
            mat_id = text_mat["id"]
            raw_content = text_mat.get("content", "")
            clean_text = clean_capcut_text(raw_content)
            if clean_text:
                text_materials[mat_id] = clean_text

    # 2. Extract subtitle segments from tracks matching the mapped texts
    srt_entries = []
    if "tracks" in draft:
        for track in draft["tracks"]:
            # Focus on text/subtitle tracks
            if track.get("type") == "text" or any(seg.get("material_id") in text_materials for seg in track.get("segments", [])):
                for segment in track.get("segments", []):
                    mat_id = segment.get("material_id")
                    if mat_id in text_materials:
                        # CapCut uses microsecond timestamps on the timeline target
                        target_range = segment.get("target_timerange", {})
                        start_us = target_range.get("start", 0)
                        duration_us = target_range.get("duration", 0)
                        end_us = start_us + duration_us
                        
                        srt_entries.append({
                            "start": start_us,
                            "end": end_us,
                            "text": text_materials[mat_id]
                        })

    # 3. Sort entries chronologically based on their exact timeline positions
    srt_entries.sort(key=lambda x: x["start"])

    # 4. Write into standard SubRip (.srt) format
    with open(output_srt_path, 'w', encoding='utf-8') as srt_file:
        for index, entry in enumerate(srt_entries, start=1):
            start_time = microseconds_to_srt_time(entry["start"])
            end_time = microseconds_to_srt_time(entry["end"])
            
            srt_file.write(f"{index}\n")
            srt_file.write(f"{start_time} --> {end_time}\n")
            srt_file.write(f"{entry['text']}\n\n")
            
    print(f"Successfully extracted {len(srt_entries)} perfectly synced timeline captions to: {output_srt_path}")

# Run the parser on your uploaded file
import sys
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QFileDialog


def select_json_file():
    """Open a file dialog and let the user manually select a JSON file."""

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    start_dir = (
        Path.home()
        / "AppData"
        / "Local"
        / "CapCut"
        / "User Data"
        / "Projects"
        / "com.lveditor.draft"
    )

    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select JSON File",
        str(start_dir),
        "JSON Files (*.json);;All Files (*)"
    )

    return file_path



json_file = select_json_file()

if json_file:
        print("Selected JSON:", json_file)
else:
        print("No file selected.")
export_capcut_to_srt(json_file, r"C:\Users\hpvic\Downloads\capgen.srt")
input("Press Enter to Continue....")
print("download/capgen.srt file is generated")
print("[UPLOAD] BOTH SRT FILE")
print("""
      You are an expert subtitling assistant and computational linguist fluent in Tamil and "Tanglish" (Tamil spoken/written using the Latin script). 

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
[Paste your Tamil SRT here]

#### 2. TARGET FILE (Tanglish - FIX THESE TIMESTAMPS)
[Paste your Tanglish SRT here]
      """)
print("\n\n\n")
print("---------------------------------generate csv file---------------------------")
print("\n\n\n")
print("upload  the srt file")
print("""
      Act as an expert Video Production AI and Metadata Engineer. 

Task: Convert the provided raw SRT subtitle track directly into a clean, downloadable CSV file layout that mirrors the exact column schema and visual syntax of "visuals.csv".

Output CSV Schema Columns:
1. caption: The raw dialogue text string from the srt block (cleanly isolated, removing timecodes/numbers).
2. keywords: At least 5 highly relevant keywords or short phrases separated explicitly by pipes (|). The keywords must maintain narrative continuity by understanding what happens in the previous and next lines, optimized perfectly for Google Stock Image search terms.
3. visual_description: A highly descriptive, cinematic prompt matching the text context. For technical overlays, use "Transparent PNG overlay elements: [assets], isolated technology assets, transparent background...". For human or environment shots, always prioritize a moody, cinematic, high-contrast studio aesthetic, specifying premium camera physics like "85mm lens, f/1.4 aperture, shallow depth of field, 8k detail".

Constraints:
- Ensure strict 1-to-1 matching row counts between srt captions and output CSV data lines.
- Do not add conversational text or markdown code block syntax inside the final downloadable format. Deliver it cleanly as an executable CSV generation structure.

Here is the raw SRT file data to convert:
      """)
input("\n\n\n make a enter further process")
def select_csv_file():

    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select CSV File",
        "",
        "CSV Files (*.csv)"
    )

    return file_path
def select_srt_file():
    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select SRT File",
        "",
        "SRT Files (*.srt)"
    )
    return file_path
CSV_FILE = select_csv_file()
print("CSv file is selected")
SRT_FILE = select_srt_file()
print("srt file is picked")
OUTPUT_JSON = "hem_visual.json"


def parse_srt(srt_path):
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

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


def time_to_seconds(time_str):
    h, m, s_ms = time_str.split(":")
    s, ms = s_ms.split(",")

    return (
        int(h) * 3600 +
        int(m) * 60 +
        int(s) +
        int(ms) / 1000
    )


segments = parse_srt(SRT_FILE)

df = pd.read_csv(CSV_FILE)

output = {
    "segments": []
}

for i, row in df.iterrows():

    segment = segments[i]

    keywords = [
        k.strip()
        for k in str(row["keywords"]).split("|")
        if k.strip()
    ]

    duration = round(
        time_to_seconds(segment["end_time"]) -
        time_to_seconds(segment["start_time"]),
        3
    )

    item = {
        "index": segment["index"],
        "start_time": segment["start_time"],
        "end_time": segment["end_time"],
        "duration": duration,
        "caption": segment["caption"],
        "keywords": keywords,
        "visual_description": row["visual_description"],
        "images": []
    }

    for keyword in keywords:
        item["images"].append({
            "keyword": keyword,
            "prompt": f"{keyword}, transparent PNG overlay, isolated cutout, no background, alpha channel, ultra realistic, 8k"
        })

    output["segments"].append(item)

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(
        output,
        f,
        indent=4,
        ensure_ascii=False
    )

print(f"Saved: {OUTPUT_JSON}")
import json
import csv

# Input JSON file
INPUT_JSON = OUTPUT_JSON

# Output CSV file
OUTPUT_CSV = r"C:\Users\hpvic\Downloads\timeline_search_element.csv"

with open(INPUT_JSON, "r", encoding="utf-8") as f:
    data = json.load(f)

rows = []

for segment in data["segments"]:
    start_time = segment["start_time"]
    end_time = segment["end_time"]

    # Join all keywords into one string
    keywords = ", ".join(segment.get("keywords", []))

    rows.append([
        start_time,
        end_time,
        keywords
    ])

with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["start_time", "end_time", "keywords"])
    writer.writerows(rows)

print(f"Saved: {OUTPUT_CSV}")
print("-------------------------------------google search-----------------------------")
input("Press Enter to continue...")

import sys
import os
import re
import csv
import webbrowser
from pathlib import Path
from urllib.parse import quote

import cv2
import pandas as pd
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QFileDialog,
    QFrame, QSizePolicy, QMessageBox, QLineEdit,
    QTextEdit, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QColor, QPalette

# ─────────────────────────────────────────────────────────────
# HARDCODED SRT — your tanglish_3words_captions.srt
# (You can also click "Load SRT" in the app to replace this)
# ─────────────────────────────────────────────────────────────
HARDCODED_SRT = """1
00:00:00,000 --> 00:00:01,078
ChatGPT video la

2
00:00:01,078 --> 00:00:02,156
first vanchu. So

3
00:00:02,156 --> 00:00:03,235
adhuvane ellarum use

4
00:00:03,235 --> 00:00:04,313
pannitu erundhom? But

5
00:00:04,313 --> 00:00:05,392
aana naama edhukkaga

6
00:00:05,392 --> 00:00:06,470
Claude ku move

7
00:00:06,470 --> 00:00:07,549
pannom? So coding

8
00:00:07,549 --> 00:00:08,627
task panradhuku, or

9
00:00:08,627 --> 00:00:09,705
oru problem vandhu

10
00:00:09,705 --> 00:00:10,784
solve panradhuku, So

11
00:00:10,784 --> 00:00:11,862
Claude dhaan vandhu

12
00:00:11,862 --> 00:00:12,941
romba important, adhu

13
00:00:12,941 --> 00:00:14,019
vandhu advanced ah

14
00:00:14,019 --> 00:00:15,098
eruku, Adhu vandhu

15
00:00:15,098 --> 00:00:16,176
paatheenga na world

16
00:00:16,176 --> 00:00:17,254
laye advanced aana

17
00:00:17,254 --> 00:00:18,333
models laam paatheenga

18
00:00:18,333 --> 00:00:19,411
na vandhu create

19
00:00:19,411 --> 00:00:20,490
panraanga. Clable model

20
00:00:20,490 --> 00:00:21,568
appdi paatheenga na

21
00:00:21,568 --> 00:00:22,647
create pannirukaanga. So

22
00:00:22,647 --> 00:00:23,725
edhukkaga namma vandhu

23
00:00:23,725 --> 00:00:24,803
ChatGPT use pannama

24
00:00:24,803 --> 00:00:25,882
Claude vandhu use

25
00:00:25,882 --> 00:00:26,960
panrom? Even though

26
00:00:26,960 --> 00:00:28,039
Claude la paatheenga

27
00:00:28,039 --> 00:00:29,117
na limited aana

28
00:00:29,117 --> 00:00:30,196
tokens dhaan use

29
00:00:30,196 --> 00:00:31,274
panrom. Namma Even

30
00:00:31,274 --> 00:00:32,352
though paid ey

31
00:00:32,352 --> 00:00:33,431
pannaalum, so weekly

32
00:00:33,431 --> 00:00:34,509
token, daily token,

33
00:00:34,509 --> 00:00:35,588
monthly token— So

34
00:00:35,588 --> 00:00:36,666
idhu ellaathaiyume paatheenga

35
00:00:36,666 --> 00:00:37,745
na limit pottu

36
00:00:37,745 --> 00:00:38,823
erukaanga. So naan

37
00:00:38,823 --> 00:00:39,901
vandhu use pannanum

38
00:00:39,901 --> 00:00:40,980
na kooda, Some

39
00:00:40,980 --> 00:00:42,058
limit varaikkum dhaan

40
00:00:42,058 --> 00:00:43,137
ennala use panna

41
00:00:43,137 --> 00:00:44,215
mudiyudhu la? Avlo

42
00:00:44,215 --> 00:00:45,294
important edhukkaga indha

43
00:00:45,294 --> 00:00:46,372
Claude ku mattum

44
00:00:46,372 --> 00:00:47,450
kudukraanga? So ellaa

45
00:00:47,450 --> 00:00:48,529
model maadhiri dhaane

46
00:00:48,529 --> 00:00:49,607
idhuvum appdinu keeteenga

47
00:00:49,607 --> 00:00:50,686
na adhudhaan illa.

48
00:00:50,686 --> 00:00:51,764
So first of

49
00:00:51,764 --> 00:00:52,843
all, indha Claude

50
00:00:52,843 --> 00:00:53,921
kum ChatGPT kum

51
00:00:53,921 --> 00:00:54,999
namma eppdi differentiate

52
00:00:54,999 --> 00:00:56,078
pannalaam na, ChatGPT

53
00:00:56,078 --> 00:00:57,156
vandhu oru text-based

54
00:00:57,156 --> 00:00:58,235
data vu ku

55
00:00:58,235 --> 00:00:59,313
paatheenga na oru

56
00:00:59,313 --> 00:01:00,392
nalla performance ey

57
00:01:00,392 --> 00:01:01,470
paatheenga na kudukkum.

58
00:01:01,470 --> 00:01:02,549
Claude vandhu eppdi

59
00:01:02,549 --> 00:01:03,627
train pannanga na,

60
00:01:03,627 --> 00:01:04,705
oru Instruction Set

61
00:01:04,705 --> 00:01:05,784
Fine-Tuning. So Instruction

62
00:01:05,784 --> 00:01:06,862
Set Fine-Tuning use

63
00:01:06,862 --> 00:01:07,941
panni dhaan indha

64
00:01:07,941 --> 00:01:09,019
Claude ey paatheenga

65
00:01:09,019 --> 00:01:10,098
na train pannirukaanga.

66
00:01:10,098 --> 00:01:11,176
So andha model

67
00:01:11,176 --> 00:01:12,254
paatheenga na grammar,

68
00:01:12,254 --> 00:01:13,333
oru context eppdi

69
00:01:13,333 --> 00:01:14,411
kudukkanum, appdindra ellaame

70
00:01:14,411 --> 00:01:15,490
vandhu therinju vachirukkum.

71
00:01:15,490 --> 00:01:16,568
But aana adha

72
00:01:16,568 --> 00:01:17,647
vandhu eppdi present

73
00:01:17,647 --> 00:01:18,725
pannanum appdinu paatheenga

74
00:01:18,725 --> 00:01:19,803
na theriyaadhu. So

75
00:01:19,803 --> 00:01:20,882
namma inga dhaan

76
00:01:20,882 --> 00:01:21,960
paatheenga na Instruction

77
00:01:21,960 --> 00:01:23,039
Set Fine-Tuning appdinu

78
00:01:23,039 --> 00:01:24,117
kekrom. So oru

79
00:01:24,117 --> 00:01:25,196
developers or coders

80
00:01:25,196 --> 00:01:26,274
vandhu eppdi oru

81
00:01:26,274 --> 00:01:27,352
question ey vandhu

82
00:01:27,352 --> 00:01:28,431
raise pannuvaanga, So

83
00:01:28,431 --> 00:01:29,509
andha question ey

84
00:01:29,509 --> 00:01:30,588
eppdi solve pannalaam,

85
00:01:30,588 --> 00:01:31,666
So adhu vandhu

86
00:01:31,666 --> 00:01:32,745
eppdi solve panraanga

87
00:01:32,745 --> 00:01:33,823
nradha oru instruction

88
00:01:33,823 --> 00:01:34,901
set ey use

89
00:01:34,901 --> 00:01:35,980
panni dhaan namma

90
00:01:35,980 --> 00:01:37,058
vandhu Claude ey

91
00:01:37,058 --> 00:01:38,137
paatheenga na design

92
00:01:38,137 --> 00:01:39,215
pannirukaanga. So idhanaala

93
00:01:39,215 --> 00:01:40,294
dhaan paatheenga na

94
00:01:40,294 --> 00:01:41,372
Claude ku paatheenga

95
00:01:41,372 --> 00:01:42,450
na over aana

96
00:01:42,450 --> 00:01:43,529
demand eruku. So

97
00:01:43,529 --> 00:01:44,607
indha video ungaluku

98
00:01:44,607 --> 00:01:45,686
pudichirundha like pannunga

99
00:01:45,686 --> 00:01:46,764
and subscribe pannunga.

100
00:01:46,764 --> 00:01:47,843
And indha video

101
00:01:47,843 --> 00:01:48,921
ungaluku pudichirundha like

102
00:01:48,921 --> 00:01:49,999
pannunga, follow pannunga."""


# ─────────────────────────────────────────────────────────────
# SRT PARSER — extracts only the text lines, ordered by index
# ─────────────────────────────────────────────────────────────
def parse_srt(srt_text: str) -> list:
    """Return a plain list of caption strings in SRT order."""
    srt_text = srt_text.replace('\r\n', '\n').replace('\r', '\n')
    pattern = re.compile(
        r'\d+\n'
        r'\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\n'
        r'([\s\S]+?)(?=\n\n\d+\n|\Z)',
        re.MULTILINE
    )
    return [m.group(1).strip().replace('\n', ' ') for m in pattern.finditer(srt_text)]


# ─────────────────────────────────────────────────────────────
# DOWNLOAD MONITOR (background QThread)
# ─────────────────────────────────────────────────────────────
class DownloadMonitor(QThread):
    """
    Polls ~/Downloads every 1.5 s for newly completed files.
    Emits new_file(path_str) when a supported file appears.
    """
    new_file = pyqtSignal(str)

    SUPPORTED = {'.png', '.jpg', '.jpeg', '.webp', '.bmp',
                 '.mp4', '.mov', '.avi', '.mkv', '.webm'}
    IGNORE    = {'.crdownload', '.tmp', '.part', '.download'}

    def __init__(self, downloads_dir: Path, parent=None):
        super().__init__(parent)
        self.downloads_dir = downloads_dir
        self._running = True
        self._known: set = set()
        self._seed()

    def _seed(self):
        """Record files that already exist so we don't fire on startup."""
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

                # Remove deleted files from known set
                self._known = {n for n in self._known if n in current}
            except Exception:
                pass
            self.msleep(1500)

    def stop(self):
        self._running = False


# ─────────────────────────────────────────────────────────────
# PREVIEW HELPERS
# ─────────────────────────────────────────────────────────────
def load_image_preview(path: str, max_w=520, max_h=220) -> QPixmap:
    pix = QPixmap(path)
    if pix.isNull():
        return QPixmap()
    return pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def load_video_thumbnail(path: str, max_w=520, max_h=220) -> QPixmap:
    try:
        cap = cv2.VideoCapture(path)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return QPixmap()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(img)
        return pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return QPixmap()


# ─────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────
class KeywordDownloader(QMainWindow):

    IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
    VIDEO_EXT = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Keyword Downloader")
        self.setFixedSize(550, 820)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        # ── Application state ──
        self.csv_path:      Path | None = None
        self.df:            pd.DataFrame | None = None
        self.current_index: int  = 0
        self.detected_file: str | None = None
        self.monitor:       DownloadMonitor | None = None

        # ── Captions: start from hardcoded SRT ──
        self.captions: list = parse_srt(HARDCODED_SRT)
        self.srt_source: str = "Built-in (tanglish_3words_captions.srt)"

        self.downloads_dir = Path.home() / "Downloads"

        self._build_ui()
        self._apply_styles()
        self._show_welcome()

    # ════════════════════════════════════════════════════════
    #  UI CONSTRUCTION
    # ════════════════════════════════════════════════════════
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(7)

        # ── Row 1: Load CSV + Load SRT ──
        top = QHBoxLayout()
        self.load_csv_btn = QPushButton("📂 Load CSV")
        self.load_csv_btn.setObjectName("loadBtn")
        self.load_csv_btn.clicked.connect(self.load_csv)

        self.load_srt_btn = QPushButton("💬 Load SRT")
        self.load_srt_btn.setObjectName("srtBtn")
        self.load_srt_btn.clicked.connect(self.load_srt)

        self.csv_label = QLabel("No CSV loaded")
        self.csv_label.setObjectName("csvLabel")
        self.csv_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        top.addWidget(self.load_csv_btn)
        top.addWidget(self.load_srt_btn)
        top.addWidget(self.csv_label)
        root.addLayout(top)

        # SRT source indicator
        self.srt_label = QLabel(f"SRT: {self.srt_source}")
        self.srt_label.setObjectName("srtSourceLabel")
        root.addWidget(self.srt_label)

        self._hr(root)

        # ── Segment header ──
        self.segment_label = QLabel("Segment — / —")
        self.segment_label.setObjectName("segmentLabel")
        root.addWidget(self.segment_label)

        self.time_label = QLabel("Time: —")
        self.time_label.setObjectName("timeLabel")
        root.addWidget(self.time_label)

        self._hr(root)

        # ── Captions panel ──
        cap_row = QHBoxLayout()
        cap_icon = QLabel("📝")
        cap_title = QLabel("Caption")
        cap_title.setObjectName("sectionHeader")
        cap_row.addWidget(cap_icon)
        cap_row.addWidget(cap_title)
        cap_row.addStretch()
        root.addLayout(cap_row)

        self.caption_box = QTextEdit()
        self.caption_box.setObjectName("captionBox")
        self.caption_box.setReadOnly(True)
        self.caption_box.setFixedHeight(70)
        root.addWidget(self.caption_box)

        # ── Keywords ──
        kw_hdr = QHBoxLayout()
        kw_hdr.addWidget(QLabel("🔑"))
        kw_title = QLabel("Keywords  (click to search)")
        kw_title.setObjectName("sectionHeader")
        kw_hdr.addWidget(kw_title)
        kw_hdr.addStretch()
        root.addLayout(kw_hdr)

        self.keyword_list = QListWidget()
        self.keyword_list.setObjectName("keywordList")
        self.keyword_list.setFixedHeight(110)
        self.keyword_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.keyword_list.itemSelectionChanged.connect(self._on_keyword_selected)
        root.addWidget(self.keyword_list)

        # ── Search bar ──
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("Edit keyword before searching…")
        self.search_edit.returnPressed.connect(self._manual_search)
        self.search_go_btn = QPushButton("🔍 Google Images")
        self.search_go_btn.setObjectName("searchBtn")
        self.search_go_btn.clicked.connect(self._manual_search)
        search_row.addWidget(self.search_edit)
        search_row.addWidget(self.search_go_btn)
        root.addLayout(search_row)

        self._hr(root)

        # ── Preview ──
        prev_hdr = QHBoxLayout()
        prev_hdr.addWidget(QLabel("🖼"))
        prev_title = QLabel("Preview")
        prev_title.setObjectName("sectionHeader")
        prev_hdr.addWidget(prev_title)
        prev_hdr.addStretch()
        root.addLayout(prev_hdr)

        self.preview_label = QLabel("No preview")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(526, 200)
        root.addWidget(self.preview_label, alignment=Qt.AlignHCenter)

        self.file_path_label = QLabel("Detected file: —")
        self.file_path_label.setObjectName("filePathLabel")
        self.file_path_label.setWordWrap(True)
        root.addWidget(self.file_path_label)

        self._hr(root)

        # ── Navigation ──
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀  Previous")
        self.prev_btn.setObjectName("prevBtn")
        self.prev_btn.clicked.connect(self.go_previous)

        self.skip_btn = QPushButton("⏭  Skip")
        self.skip_btn.setObjectName("skipBtn")
        self.skip_btn.clicked.connect(self.go_skip)

        self.next_btn = QPushButton("Next  ▶")
        self.next_btn.setObjectName("nextBtn")
        self.next_btn.clicked.connect(self.go_next)

        nav.addWidget(self.prev_btn)
        nav.addWidget(self.skip_btn)
        nav.addWidget(self.next_btn)
        root.addLayout(nav)

        # ── Status ──
        self.status_label = QLabel("Load a CSV to begin.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status_label)

    def _hr(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("divider")
        layout.addWidget(line)

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0d1117;
                color: #c9d1d9;
                font-family: 'Segoe UI', 'SF Pro Text', Arial, sans-serif;
                font-size: 13px;
            }
            /* ── buttons ── */
            #loadBtn {
                background: #1f6feb; color: white;
                border: none; padding: 6px 14px;
                border-radius: 6px; font-weight: bold;
            }
            #loadBtn:hover { background: #388bfd; }

            #srtBtn {
                background: #388080; color: white;
                border: none; padding: 6px 14px;
                border-radius: 6px; font-weight: bold;
            }
            #srtBtn:hover { background: #3fb0b0; }

            /* ── labels ── */
            #csvLabel        { color: #8b949e; font-size: 11px; }
            #srtSourceLabel  { color: #3fb0b0; font-size: 11px; font-style: italic; }
            #segmentLabel    { font-size: 17px; font-weight: bold; color: #c084fc; }
            #timeLabel       { color: #79c0ff; font-size: 12px; }
            #sectionHeader   { color: #8b949e; font-size: 11px; font-weight: bold;
                               letter-spacing: 0.06em; text-transform: uppercase; }

            /* ── caption box ── */
            #captionBox {
                background: #161b22;
                border: 1px solid #3fb0b0;
                border-radius: 5px;
                color: #e6edf3;
                font-size: 13px;
                padding: 5px 7px;
                line-height: 1.5;
            }

            /* ── keyword list ── */
            #keywordList {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 6px;
                color: #e6edf3;
                font-size: 13px;
            }
            #keywordList::item          { padding: 4px 8px; }
            #keywordList::item:selected { background: #6e40c9; color: white; border-radius: 3px; }
            #keywordList::item:hover    { background: #1c2d4a; }

            /* ── search ── */
            #searchEdit {
                background: #161b22; border: 1px solid #30363d;
                border-radius: 5px; color: #e6edf3; padding: 5px 8px;
            }
            #searchBtn {
                background: #238636; color: white;
                border: none; padding: 5px 12px;
                border-radius: 5px; font-weight: bold;
            }
            #searchBtn:hover { background: #2ea043; }

            /* ── preview ── */
            #previewLabel {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 6px;
                color: #484f58;
                font-size: 12px;
            }
            #filePathLabel { color: #484f58; font-size: 11px; }

            /* ── divider ── */
            QFrame#divider { color: #21262d; max-height: 1px; }

            /* ── nav buttons ── */
            #prevBtn, #skipBtn, #nextBtn {
                padding: 8px 0; border-radius: 6px;
                font-weight: bold; font-size: 13px; border: none;
            }
            #prevBtn        { background: #21262d; color: #8b949e; }
            #prevBtn:hover  { background: #30363d; color: #c9d1d9; }
            #skipBtn        { background: #5a3e00; color: #e3b341; }
            #skipBtn:hover  { background: #7d5400; }
            #nextBtn        { background: #196127; color: #56d364; }
            #nextBtn:hover  { background: #238636; }
            #prevBtn:disabled, #skipBtn:disabled, #nextBtn:disabled {
                background: #161b22; color: #484f58;
            }
            #statusLabel { color: #484f58; font-size: 11px; }
        """)

    def _show_welcome(self):
        self.segment_label.setText("Keyword Downloader")
        self.time_label.setText("Load a CSV file to begin.")
        self.caption_box.setPlainText(f"SRT loaded: {len(self.captions)} captions.")
        self._set_nav_enabled(False)

    # ════════════════════════════════════════════════════════
    #  FILE LOADING
    # ════════════════════════════════════════════════════════
    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV File", str(Path.home()), "CSV Files (*.csv)"
        )
        if not path:
            return
        self.csv_path = Path(path)
        try:
            self.df = pd.read_csv(self.csv_path, dtype=str).fillna("")
        except Exception as e:
            QMessageBox.critical(self, "CSV Error", f"Could not read CSV:\n{e}")
            return

        if 'path' not in self.df.columns:
            self.df['path'] = ""
            self._save_csv()

        self.csv_label.setText(f"📄 {self.csv_path.name}  ({len(self.df)} rows)")
        self.current_index = 0          # always start from row 0
        self._start_monitor()
        self._load_row(0)
        self._set_nav_enabled(True)

    def load_srt(self):
        """Allow user to load a different SRT file at runtime."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open SRT File", str(Path.home()), "SRT Files (*.srt);;All Files (*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding='utf-8', errors='replace')
            parsed = parse_srt(text)
            if not parsed:
                QMessageBox.warning(self, "SRT Warning",
                    "No captions could be parsed from that file.\n"
                    "Check the SRT format and try again.")
                return
            self.captions   = parsed
            self.srt_source = Path(path).name
            self.srt_label.setText(f"SRT: {self.srt_source}  ({len(self.captions)} captions)")
            self.status_label.setText(f"✔ SRT loaded: {len(self.captions)} captions.")

        except Exception as e:
            QMessageBox.critical(self, "SRT Error", f"Could not read SRT:\n{e}")

    # ════════════════════════════════════════════════════════
    #  CSV HELPERS
    # ════════════════════════════════════════════════════════
    def _find_first_empty(self) -> int:
        if self.df is None:
            return 0
        for i in range(len(self.df)):
            val = str(self.df.at[i, 'path']).strip()
            if val in ('', 'nan'):
                return i
        return max(0, len(self.df) - 1)

    def _save_csv(self):
        if self.df is not None and self.csv_path is not None:
            try:
                self.df.to_csv(self.csv_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
            except Exception as e:
                self.status_label.setText(f"⚠ Save error: {e}")

    # ════════════════════════════════════════════════════════
    #  ROW DISPLAY
    # ════════════════════════════════════════════════════════
    def _load_row(self, idx: int):
        """Display all data for row idx."""
        if self.df is None or not (0 <= idx < len(self.df)):
            return

        self.current_index = idx
        self.detected_file = None
        self._clear_preview()

        row   = self.df.iloc[idx]
        total = len(self.df)
        start = str(row.get('start_time', '')).strip()
        end   = str(row.get('end_time',   '')).strip()

        self.segment_label.setText(f"Segment {idx + 1}  /  {total}")
        self.time_label.setText(f"⏱  {start}  →  {end}")

        # ── Keywords ──
        kw_raw  = str(row.get('keywords', '')).strip()
        keywords = [k.strip() for k in kw_raw.split(',') if k.strip()]
        self.keyword_list.blockSignals(True)
        self.keyword_list.clear()
        for kw in keywords:
            self.keyword_list.addItem(QListWidgetItem(kw))
        self.keyword_list.blockSignals(False)

        # ── Captions ──
        self._refresh_captions(idx)

        # ── Existing path ──
        existing = str(row.get('path', '')).strip()
        has_path = existing not in ('', 'nan', 'None')

        if has_path:
            self.file_path_label.setText(f"✔ Saved: {existing}")
            self._show_preview(existing)
            self.status_label.setText(f"Row {idx+1}: already saved — browse only.")
            if keywords:
                self.keyword_list.blockSignals(True)
                self.keyword_list.setCurrentRow(0)
                self.keyword_list.blockSignals(False)
                self.search_edit.setText(keywords[0])
        else:
            self.file_path_label.setText("Detected file: waiting for download…")
            self.status_label.setText(f"Row {idx+1}: waiting for download…")
            if keywords:
                self.keyword_list.blockSignals(True)
                self.keyword_list.setCurrentRow(0)
                self.keyword_list.blockSignals(False)
                self.search_edit.setText(keywords[0])
                self._open_google(keywords[0])

    def _refresh_captions(self, idx: int):
        """Show the caption at the same index as the CSV row."""
        if 0 <= idx < len(self.captions):
            self.caption_box.setPlainText(self.captions[idx])
        else:
            self.caption_box.setPlainText("(no caption for this row)")

    # ════════════════════════════════════════════════════════
    #  NAVIGATION
    # ════════════════════════════════════════════════════════
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

    def go_previous(self):
        if self.df is None:
            return
        prev = self.current_index - 1
        if prev >= 0:
            self._load_row(prev)

    def _set_nav_enabled(self, on: bool):
        for btn in (self.prev_btn, self.skip_btn, self.next_btn):
            btn.setEnabled(on)

    # ════════════════════════════════════════════════════════
    #  KEYWORD & SEARCH
    # ════════════════════════════════════════════════════════
    def _on_keyword_selected(self):
        items = self.keyword_list.selectedItems()
        if items:
            self.search_edit.setText(items[0].text())

    def _manual_search(self):
        kw = self.search_edit.text().strip()
        if kw:
            self._open_google(kw)

    def _open_google(self, keyword: str):
        url = f"https://www.google.com/search?q={quote(keyword)}&tbm=isch"
        webbrowser.open(url)
        self.status_label.setText(f"🔍 Searching: {keyword}")

    # ════════════════════════════════════════════════════════
    #  DOWNLOAD MONITORING
    # ════════════════════════════════════════════════════════
    def _start_monitor(self):
        if self.monitor and self.monitor.isRunning():
            self.monitor.stop()
            self.monitor.wait()
        self.monitor = DownloadMonitor(self.downloads_dir)
        self.monitor.new_file.connect(self._on_new_download)
        self.monitor.start()

    def _on_new_download(self, file_path: str):
        self.detected_file = file_path
        self.file_path_label.setText(f"📥 Detected: {file_path}")
        self.status_label.setText(f"📥 New file: {Path(file_path).name}")
        self._show_preview(file_path)

    # ════════════════════════════════════════════════════════
    #  PREVIEW
    # ════════════════════════════════════════════════════════
    def _clear_preview(self):
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("No preview")

    def _show_preview(self, file_path: str):
        p   = Path(file_path)
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

    # ════════════════════════════════════════════════════════
    #  CLEANUP
    # ════════════════════════════════════════════════════════
    def closeEvent(self, event):
        if self.monitor and self.monitor.isRunning():
            self.monitor.stop()
            self.monitor.wait()
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    dark = QPalette()
    dark.setColor(QPalette.Window,          QColor(13,  17,  23))
    dark.setColor(QPalette.WindowText,      QColor(201, 209, 217))
    dark.setColor(QPalette.Base,            QColor(22,  27,  34))
    dark.setColor(QPalette.AlternateBase,   QColor(13,  17,  23))
    dark.setColor(QPalette.Text,            QColor(230, 237, 243))
    dark.setColor(QPalette.Button,          QColor(33,  38,  45))
    dark.setColor(QPalette.ButtonText,      QColor(201, 209, 217))
    dark.setColor(QPalette.Highlight,       QColor(110, 64,  201))
    dark.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(dark)

    win = KeywordDownloader()
    win.show()
    print("BEFORE EXEC")
    app.exec_()
    print("thankyoyfor closing")
    import modul_hem
    return


if __name__ == "__main__":
    main()