import json
import re
from datetime import timedelta

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
export_capcut_to_srt(r"C:\Users\hpvic\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\0703\Timelines\780E2A0A-9AB1-4efb-ADC3-ABB7590FC73C\draft_content.json", r"C:\Users\hpvic\Downloads\cap_sync.srt")