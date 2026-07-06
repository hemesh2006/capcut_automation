import json
import copy
import uuid
import csv
import os


def uid():
    return str(uuid.uuid4()).upper()


def srt_time_to_seconds(time_str):
    h, m, s_ms = time_str.strip().split(":")
    s, ms = s_ms.split(",")

    return (
        int(h) * 3600
        + int(m) * 60
        + int(s)
        + int(ms) / 1000
    )


def find_photo_template(data):

    for track in data["tracks"]:

        if track.get("type") != "video":
            continue

        for seg in track.get("segments", []):

            material_id = seg.get("material_id")

            for mat in data["materials"]["videos"]:

                if (
                    mat.get("id") == material_id
                    and mat.get("type") == "photo"
                ):
                    return track, seg, mat

    raise Exception(
        "No photo template found. Add one image manually in CapCut and save project."
    )


def create_new_track(template_track):

    new_track = copy.deepcopy(template_track)

    new_track["id"] = uid()
    new_track["segments"] = []

    return new_track


def get_free_track(
    data,
    photo_track,
    start_us,
    end_us
):

    photo_tracks = []

    for track in data["tracks"]:

        if track.get("type") != "video":
            continue

        photo_tracks.append(track)

    for track in photo_tracks:

        overlap = False

        for seg in track.get("segments", []):

            seg_start = seg["target_timerange"]["start"]
            seg_end = (
                seg_start
                + seg["target_timerange"]["duration"]
            )

            if (
                start_us < seg_end
                and end_us > seg_start
            ):
                overlap = True
                break

        if not overlap:
            return track

    new_track = create_new_track(photo_track)

    data["tracks"].append(new_track)

    return new_track


def add_photo_segment(
    data,
    photo_track,
    photo_segment_template,
    photo_material_template,
    image_path,
    start_time,
    end_time
):

    duration_us = int(
        (end_time - start_time)
        * 1000000
    )

    start_us = int(
        start_time * 1000000
    )

    end_us = int(
        end_time * 1000000
    )

    material_id = uid()

    material = copy.deepcopy(
        photo_material_template
    )

    material["id"] = material_id
    material["path"] = image_path
    material["material_name"] = os.path.basename(
        image_path
    )

    material["duration"] = duration_us

    data["materials"]["videos"].append(
        material
    )

    target_track = get_free_track(
        data,
        photo_track,
        start_us,
        end_us
    )

    segment = copy.deepcopy(
        photo_segment_template
    )

    segment["id"] = uid()

    segment["material_id"] = material_id

    segment["source_timerange"] = {
        "start": 0,
        "duration": duration_us
    }

    segment["target_timerange"] = {
        "start": start_us,
        "duration": duration_us
    }

    segment["render_timerange"] = {
        "start": start_us,
        "duration": duration_us
    }

    target_track["segments"].append(
        segment
    )


def process_csv(
    csv_path,
    json_path
):

    with open(
        json_path,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    (
        photo_track,
        photo_segment_template,
        photo_material_template
    ) = find_photo_template(data)

    added = 0
    skipped = 0
    max_end_us = 0

    with open(
        csv_path,
        "r",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        rows = list(reader)

    for row in rows:

        try:

            image_path = row["path"].strip()

            if not image_path:
                skipped += 1
                continue

            if not os.path.exists(
                image_path
            ):
                print(
                    f"SKIPPED : {image_path}"
                )
                skipped += 1
                continue

            start_time = srt_time_to_seconds(
                row["start_time"]
            )

            end_time = srt_time_to_seconds(
                row["end_time"]
            )

            if end_time <= start_time:
                skipped += 1
                continue

            add_photo_segment(
                data=data,
                photo_track=photo_track,
                photo_segment_template=photo_segment_template,
                photo_material_template=photo_material_template,
                image_path=image_path,
                start_time=start_time,
                end_time=end_time
            )

            max_end_us = max(
                max_end_us,
                int(end_time * 1000000)
            )

            added += 1

            print(
                f"ADDED : {os.path.basename(image_path)} "
                f"{start_time:.3f} -> {end_time:.3f}"
            )

        except Exception as e:

            skipped += 1

            print(
                f"ERROR : {e}"
            )

    for track in data["tracks"]:

        if track.get("type") == "video":

            track["segments"].sort(
                key=lambda s:
                s["target_timerange"]["start"]
            )

    if max_end_us > 0:
        data["duration"] = max_end_us

    with open(
        json_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            separators=(",", ":")
        )

    print()
    print("=" * 40)
    print(f"ADDED   : {added}")
    print(f"SKIPPED : {skipped}")
    print("=" * 40)
import sys
from PyQt5.QtWidgets import QApplication, QFileDialog

app = QApplication(sys.argv)
file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select CapCut JSON",
        r"C:\Users\hpvic\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft",
        "JSON Files (*.json)"
    )

print(file_path)

csv_path, _ = QFileDialog.getOpenFileName(
    None,
    "Select CSV File",
    "",
    "CSV Files (*.csv)"
)


process_csv(
        csv_path=csv_path,
        json_path=file_path
    )
print("-----------------------caption template maker----------------------")
print("make sure caption template is selected and align perfect in caption srt ")
input("press enter to continue...")
print("run autopasteparallel.py")