# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Donald Wildman
"""Self-check for tagcore: run with `/usr/bin/python3 test_tagcore.py`. Needs ffmpeg for fixtures."""
import io
import os
import shutil
import subprocess
import tempfile

import tagcore as tc

FORMATS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
           ".opus": ["-c:a", "libopus"], ".m4a": ["-c:a", "aac"], ".wma": ["-c:a", "wmav2"],
           ".wav": ["-c:a", "pcm_s16le"], ".aiff": ["-c:a", "pcm_s16be"], ".wv": ["-c:a", "wavpack"]}


def make_audio(path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-map_metadata", "-1",
                    *FORMATS[os.path.splitext(path)[1]], path], check=True)


def png_bytes(size=(800, 400)):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def test_roundtrip(tmp, cfg):
    values = {k: f"v_{k} ünï" for k, _ in tc.FIELDS}
    values.update(year="1999", track="3/12", disc="1/2", bpm="128", rating="4", compilation="1",
                  seasonnumber="2", episodenumber="5", releasetime="2001-02-03", lyrics="line one\nline two")
    cover = ("image/png", png_bytes())
    for ext in FORMATS:
        p = os.path.join(tmp, "fixture" + ext)
        make_audio(p)
        t = tc.write_tags(p, values, cfg, covers=[cover])
        unmapped = {"mp4": {"rating"}}.get(t.family, set())
        for k, v in values.items():
            if k not in unmapped:
                assert t[k] == v, (ext, t.family, k, t[k], v)
        assert t.covers == [cover], (ext, "cover")
        t = tc.write_tags(p, {"title": "", "comment": "c2"}, cfg)  # empty = delete, others untouched
        assert t["title"] == "" and t["comment"] == "c2" and t["artist"] == values["artist"], ext
        assert t.covers == [cover], (ext, "cover kept when covers=None")
        tc.remove_tags(p, cfg)
        t = tc.Track(p)
        assert not t.has_tag, (ext, t.fields)
        print(f"  roundtrip ok {ext:6} [{t.family}]")


def test_masks(cfg):
    t = tc.Track.__new__(tc.Track)
    t.path, t.fields, t.covers = "/m/x/old name.mp3", {}, []
    t.info = {"bitrate": 320, "length": 185, "sample_rate": 44100, "bits": 16, "encoder": "LAME"}
    t.fields = {"artist": "AC/DC", "title": "Back: In Black?", "album": "Back in Black", "track": "06/10",
                "year": "1980-07-25T00:00:00", "disc": "01"}
    assert tc.expand_mask("%1 - %2", t, 1, cfg) == "AC-DC - Back- In Black"
    assert tc.expand_mask("%6. %2 [%0 %t %khz]", t, 1, cfg) == "06. Back- In Black [320 3-05 44.1]"
    assert tc.expand_mask("%1\\%4 - %3\\%6", t, 1, cfg) == os.sep.join(["AC-DC", "1980 - Back in Black", "06"])
    assert tc.expand_mask("%key%keyx", t, 1, cfg) == "x"  # longest token wins
    assert tc.expand_mask("%7 %8 %ext", t, 7, cfg) == "old name 7 mp3"
    assert tc.expand_mask("%1 - %3", t, 1, cfg, case=2) == "Ac-Dc - Back In Black"
    t.fields["album"] = "back_in_black"
    assert tc.expand_mask("%3", t, 1, cfg, case=2, underscores_to_spaces=True) == "Back In Black"
    t.fields["album"] = "Back in Black"
    assert tc.apply_case("the (best) of 2pac.mp3", 2, cfg.get("CapitalizeConfiguration"),
                         cfg.list("KeepStrCase")) == "The (Best) Of 2Pac.mp3"
    assert tc.apply_case("HELLO world", 3) == "Hello world"
    cfg.set_list("ReplStrBefore", ["back"]); cfg.set_list("ReplStrAfter", ["Front"])
    assert tc.expand_mask("%3", t, 1, cfg) == "Front in Black"
    cfg.set("ReplStrBefore", ""); cfg.set("ReplStrAfter", "")
    print("  masks ok")


def test_parse():
    assert tc.parse_filename("%1 - %2", "/a/Artist - Title - Live.mp3") == {"artist": "Artist", "title": "Title - Live"}
    assert tc.parse_filename("%1\\%3\\%6-%2", "/m/Band/Album/03-Song.flac") == \
        {"artist": "Band", "album": "Album", "track": "03", "title": "Song"}
    assert tc.parse_filename("%6. %d - %2", "/x/01. junk - Name.mp3") == {"track": "01", "title": "Name"}
    assert tc.parse_filename("%1 - %2", "/x/NoSeparator.mp3") is None
    print("  parse ok")


def test_rename(tmp, cfg):
    d = os.path.join(tmp, "ren")
    os.makedirs(d)
    tracks = []
    for i, (artist, title) in enumerate([("A", "One"), ("A", "One"), ("B", "Two"), ("C", "Three")]):
        p = os.path.join(d, f"f{i}.mp3")
        make_audio(p)
        tracks.append(tc.write_tags(p, {"artist": artist, "title": title}, cfg))
    open(os.path.join(d, "B - Two.mp3"), "w").close()  # collision with a file outside the batch
    items = tc.plan_renames(list(enumerate(tracks, 1)), "%1 - %2", cfg)
    assert items[0].error and items[1].error, "duplicate targets in one batch must be refused"
    assert items[2].new_path == os.path.join(d, "B - Two (1).mp3")
    assert items[3].new_path == os.path.join(d, "C - Three.mp3")
    cfg.set("DontRenameFileIfFileWithThisNameExists", 1)
    assert tc.plan_renames([(1, tracks[2])], "%1 - %2", cfg)[0].error
    cfg.set("DontRenameFileIfFileWithThisNameExists", 0)
    undo, errors = tc.execute_renames(items)
    assert len(undo) == 2 and len(errors) == 2
    assert os.path.exists(os.path.join(d, "C - Three.mp3")) and tracks[3].path.endswith("C - Three.mp3")
    items = tc.plan_renames([(1, tracks[3])], "%1\\%2", cfg)  # mask folders get created
    undo2, errors = tc.execute_renames(items)
    assert not errors and os.path.exists(os.path.join(d, "C", "Three.mp3"))
    restored, errors = tc.undo_renames(undo2)
    assert not errors and not os.path.exists(os.path.join(d, "C")), "undo removes folders it created"
    restored, errors = tc.undo_renames(undo)
    assert not errors and os.path.exists(os.path.join(d, "f2.mp3")) and os.path.exists(os.path.join(d, "f3.mp3"))
    print("  rename/undo ok")


def test_settings(tmp):
    cfg = tc.Settings(os.path.join(tmp, "cfg", "Retag.ini"))
    cfg.set_list("HistoryList", ["/a b/c", "/plain", 'with,comma'])
    cfg.save()
    again = tc.Settings(cfg.path)
    assert again.list("HistoryList") == ["/a b/c", "/plain", "with,comma"]
    assert again.get("RenameFileNameMask") == "%1 - %2"
    calls = []
    again.on_change = lambda: calls.append(1)
    again.set("UseSubfolders", True)
    again.set("UseSubfolders", 1)  # unchanged value: no save triggered
    assert calls == [1], calls
    assert tc.nearest_existing_folder(os.path.join(tmp, "gone", "deeper")) == tmp
    assert tc.nearest_existing_folder("") == os.path.expanduser("~")
    print("  settings ok")


def test_resize():
    mime, data = tc.resize_cover("image/png", png_bytes((1200, 300)), 600, 70, True)
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    assert mime == "image/jpeg" and img.size == (600, 150)
    small = png_bytes((100, 100))
    assert tc.resize_cover("image/png", small, 600, 70, True) == ("image/png", small)
    print("  resize ok")


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="retag-test-")
    try:
        cfg = tc.Settings(os.path.join(tmp, "none.ini"))
        test_masks(cfg)
        test_parse()
        test_settings(tmp)
        test_resize()
        test_roundtrip(tmp, cfg)
        test_rename(tmp, cfg)
        print("ALL OK")
    finally:
        shutil.rmtree(tmp)
