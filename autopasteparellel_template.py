import json
import os

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


# Example
if __name__ == "__main__":
    json_file = select_json_file()

    if json_file:
        print("Selected JSON:", json_file)
    else:
        print("No file selected.")
file_path = json_file

if not os.path.exists(file_path):
    print(f"Error: {file_path} not found.")
    exit()

with open(file_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# 1. Separate the text tracks from the timeline
text_tracks = [track for track in data.get("tracks", []) if track.get("type") == "text"]

if len(text_tracks) < 2:
    print(f"Error: Found {len(text_tracks)} text track(s). Need at least 2 tracks to map them.")
    exit()

track_a_segments = text_tracks[0].get("segments", [])
track_b_segments = text_tracks[1].get("segments", [])

# Map plain text materials by id
text_materials = {mat["id"]: mat for mat in data.get("materials", {}).get("texts", [])}

# Map styled "text_template" materials by id -- CapCut's animated caption
# presets don't hold text directly. A timeline segment's material_id can point
# at one of these instead of straight into materials.texts.
text_templates = {tpl["id"]: tpl for tpl in data.get("materials", {}).get("text_templates", [])}


def resolve_sub_material_ids(mat_id):
    """If mat_id points to a text_template, follow it through
    text_info_resources to find the real text material id(s) that actually
    hold renderable content (a styled caption often has more than one, e.g.
    a fill layer + an outline layer). Otherwise mat_id is already a direct
    materials.texts id."""
    if mat_id in text_templates:
        return [r["text_material_id"] for r in text_templates[mat_id].get("text_info_resources", [])]
    return [mat_id]


def get_text(mat_id):
    """Extract the plain text string out of a materials.texts entry's content field."""
    mat = text_materials.get(mat_id, {})
    content = mat.get("content", "")
    if content.startswith("{"):
        try:
            return json.loads(content).get("text", "")
        except Exception:
            return ""
    return content


def contains_placeholder(segments):
    for seg in segments:
        for sub_id in resolve_sub_material_ids(seg.get("material_id")):
            if "the quick brown fox" in get_text(sub_id).lower():
                return True
    return False


# Assign which track is the template (placeholder) and which is the source (real text)
if contains_placeholder(track_a_segments):
    template_segments = track_a_segments
    source_segments = track_b_segments
    print("Detected Track 1 as the Template Track and Track 2 as the Text Source Track.")
else:
    template_segments = track_b_segments
    source_segments = track_a_segments
    print("Detected Track 2 as the Template Track and Track 1 as the Text Source Track.")

# 2. Sort both tracks chronologically by their timeline start times to ensure perfect 1:1 mapping
template_segments.sort(key=lambda x: x.get("target_timerange", {}).get("start", 0))
source_segments.sort(key=lambda x: x.get("target_timerange", {}).get("start", 0))

print(f"Matching {len(source_segments)} text segments to {len(template_segments)} template style blocks...")

# 3. Zip them together and transfer the text values into the style structures
matched_count = 0
for src_seg, tpl_seg in zip(source_segments, template_segments):
    src_ids = resolve_sub_material_ids(src_seg.get("material_id"))
    if not src_ids:
        continue

    actual_text = get_text(src_ids[0])
    if not actual_text:
        continue

    tpl_ids = resolve_sub_material_ids(tpl_seg.get("material_id"))

    for tpl_id in tpl_ids:
        tpl_mat = text_materials.get(tpl_id)
        if not tpl_mat:
            continue

        tpl_content = tpl_mat.get("content", "")
        try:
            if tpl_content.startswith("{"):
                tpl_json = json.loads(tpl_content)
                # Swap the placeholder text with the real spoken text inside the template's style JSON
                tpl_json["text"] = actual_text
                new_content = json.dumps(tpl_json, ensure_ascii=False)
            else:
                new_content = actual_text

            # Write the text into the styled template material block
            tpl_mat["content"] = new_content
            matched_count += 1

        except Exception as e:
            print(f"Skipped a sub-block due to structural error: {e}")

    print(f"Mapped: -> '{actual_text}' into style block ({len(tpl_ids)} sub-part(s)).")

# 4. Save the modified data back
with open(file_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print(f"\nDone! Successfully updated {matched_count} styled template blocks with your real timeline text.")