# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Donald Wildman
"""Retag non-GUI core: tag model over mutagen, masks, rename planning, settings.

Byte layouts come from mutagen;
this module only decides which native key each canonical field maps to.
"""
import configparser
import csv
import io
import os
import random
import re
import struct
import base64
import errno
import inspect

import mutagen
from mutagen.id3 import ID3, ID3FileType, Frames, delete as id3_delete, COMM, USLT, APIC, POPM, TXXX, WXXX, TCON
from mutagen.mp4 import MP4Tags, MP4Cover, MP4FreeForm
from mutagen.apev2 import APEv2, APEValue, BINARY
from mutagen.asf import ASFTags, ASFByteArrayAttribute, ASFDWordAttribute
from mutagen.flac import FLAC, Picture
from mutagen._vorbis import VCommentDict

# ---------------------------------------------------------------- field model

# (key, label) in UI order. Spec §1.2. Podcast fields and Music/Video Kind are not mapped.
FIELDS = [
    ("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("year", "Year"),
    ("track", "Track #"), ("genre", "Genre"), ("comment", "Comment"),
    ("composer", "Composer"), ("origartist", "Orig. Artist"), ("copyright", "Copyright"),
    ("url", "URL"), ("encodedby", "Encoded by"), ("bpm", "BPM"), ("disc", "Disc #"),
    ("rating", "Rating"), ("mood", "Mood"), ("compilation", "Part of a compilation"),
    ("lyrics", "Lyrics"),
    ("albumartist", "Album Artist"), ("conductor", "Conductor"), ("lyricist", "Lyricist"),
    ("fileurl", "File information webpage"), ("artisturl", "Artist webpage"),
    ("sourceurl", "Audio source webpage"), ("buyurl", "Buy CD webpage"),
    ("radioname", "Radio station name"), ("radiourl", "Radio station webpage"),
    ("origlyricist", "Original lyricist"), ("label", "Label"), ("isrc", "ISRC #"),
    ("catalog", "Catalog #"), ("remixer", "Remixed by"), ("key", "Initial Key"),
    ("grouping", "Grouping"), ("subtitle", "Subtitle"), ("releasetime", "Release Time"),
    ("showtitle", "Show Title"), ("episodeid", "Episode ID"),
    ("seasonnumber", "Season Number"), ("episodenumber", "Episode Number"),
    ("sorttitle", "Sort Title"), ("sortartist", "Sort Artist"),
    ("sortalbumartist", "Sort Album Artist"), ("sortalbum", "Sort Album"),
    ("sortcomposer", "Sort Composer"), ("sortshow", "Sort Show"),
]
LABELS = dict(FIELDS)
RATINGS = ["Unrated", "1 Star", "2 Stars", "3 Stars", "4 Stars", "5 Stars"]
GENRES = list(TCON.GENRES[:148])  # the 148 standard ID3v1 genres, spec §13

EXTENSIONS = {
    ".mp3", ".mp2", ".mp1", ".wma", ".asf", ".wmv", ".ogg", ".oga", ".flac", ".spx", ".opus",
    ".m4a", ".m4p", ".mp4", ".m4b", ".m4v", ".3gp", ".m4r", ".mpc", ".mp+", ".ape", ".ofr",
    ".ofs", ".wv", ".tta", ".wav", ".aiff", ".aif", ".dsf",
}

# ID3v2: plain id = text frame, W*** = url frame, TXXX:desc = user text. Spec §15.1.
ID3_MAP = {
    "title": "TIT2", "artist": "TPE1", "album": "TALB", "year": "TDRC", "track": "TRCK",
    "genre": "TCON", "composer": "TCOM", "origartist": "TOPE", "copyright": "TCOP",
    "encodedby": "TENC", "bpm": "TBPM", "disc": "TPOS", "compilation": "TCMP",
    "albumartist": "TPE2", "conductor": "TPE3", "lyricist": "TEXT", "origlyricist": "TOLY",
    "radioname": "TRSN", "label": "TPUB", "isrc": "TSRC", "remixer": "TPE4", "key": "TKEY",
    "grouping": "TIT1", "subtitle": "TIT3", "releasetime": "TDRL",
    "sorttitle": "TSOT", "sortartist": "TSOP", "sortalbumartist": "TSO2", "sortalbum": "TSOA",
    "sortcomposer": "TSOC",
    "fileurl": "WOAF", "artisturl": "WOAR", "sourceurl": "WOAS", "radiourl": "WORS",
    "buyurl": "WCOM",
    "mood": "TXXX:Mood", "catalog": "TXXX:Catalog Number", "showtitle": "TXXX:Show",
    "episodeid": "TXXX:EpisodeID", "seasonnumber": "TXXX:SeasonNumber",
    "episodenumber": "TXXX:EpisodeNumber", "sortshow": "TXXX:Sort Show",
}
ID3_V24_KEEP = ("TSOA", "TSOP", "TSOT", "TDRL")  # original writes these into v2.3 anyway
WMP_EMAIL = "Windows Media Player 9 Series"
POPM_VALUES = [0, 1, 64, 128, 196, 255]

FF = "----:com.apple.iTunes:"
MP4_MAP = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "year": "\xa9day",
    "genre": "\xa9gen", "comment": "\xa9cmt", "composer": "\xa9wrt", "origartist": "\xa9ope",
    "copyright": "cprt", "url": "\xa9url", "encodedby": "\xa9enc", "lyrics": "\xa9lyr",
    "grouping": "\xa9grp", "albumartist": "aART", "showtitle": "tvsh", "episodeid": "tven",
    "sorttitle": "sonm", "sortartist": "soar", "sortalbumartist": "soaa", "sortalbum": "soal",
    "sortcomposer": "soco", "sortshow": "sosn",
    "mood": FF + "mood", "conductor": FF + "cond", "lyricist": FF + "lyrt",
    "origlyricist": FF + "olyr", "fileurl": FF + "furl", "artisturl": FF + "aurl",
    "sourceurl": FF + "surl", "radioname": FF + "rnam", "radiourl": FF + "rurl",
    "buyurl": FF + "burl", "isrc": FF + "ISRC", "catalog": FF + "cat#", "label": FF + "labl",
    "remixer": FF + "rmix", "subtitle": FF + "subt", "releasetime": FF + "relt",
    "key": FF + "INITIALKEY",
}
MP4_INT_PAIR = {"track": "trkn", "disc": "disk"}
MP4_INT = {"bpm": "tmpo", "seasonnumber": "tvsn", "episodenumber": "tves"}

VORBIS_MAP = {
    "title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "year": "DATE",
    "track": "TRACKNUMBER", "genre": "GENRE", "comment": "COMMENT", "composer": "COMPOSER",
    "origartist": "ORIGINALARTIST", "copyright": "COPYRIGHT", "url": "URL",
    "encodedby": "ENCODEDBY", "bpm": "BPM", "disc": "DISCNUMBER", "rating": "RATING",
    "mood": "MOOD", "compilation": "COMPILATION", "lyrics": "LYRICS",
    "albumartist": "ALBUMARTIST", "conductor": "CONDUCTOR", "lyricist": "LYRICIST",
    "origlyricist": "ORIGINALLYRICIST", "fileurl": "AUDIOFILEURL", "artisturl": "AUTHORURL",
    "sourceurl": "AUDIOSOURCEURL", "radioname": "RADIOSTATIONNAME",
    "radiourl": "RADIOSTATIONURL", "buyurl": "BUYCDURL", "label": "LABEL", "isrc": "ISRC",
    "catalog": "LABELNO", "remixer": "REMIXER", "key": "INITIALKEY", "grouping": "GROUPING",
    "subtitle": "SUBTITLE", "releasetime": "RELEASETIME", "showtitle": "SHOW",
    "episodeid": "EPISODEID", "seasonnumber": "SEASONNUMBER",
    "episodenumber": "EPISODENUMBER", "sorttitle": "TITLESORT", "sortartist": "ARTISTSORT",
    "sortalbumartist": "ALBUMARTISTSORT", "sortalbum": "ALBUMSORT",
    "sortcomposer": "COMPOSERSORT", "sortshow": "SHOWSORT",
}
VORBIS_READ_ALIASES = {"albumartist": ["ALBUM ARTIST"], "label": ["ORGANIZATION"],
                       "comment": ["DESCRIPTION"], "encodedby": ["ENCODED"]}
APE_MAP = dict(VORBIS_MAP, year="YEAR", track="TRACK")
APE_COVER = "Cover Art (Front)"

ASF_MAP = {
    "title": "Title", "artist": "Author", "comment": "Description", "copyright": "Copyright",
    "album": "WM/AlbumTitle", "year": "WM/Year", "genre": "WM/Genre",
    "composer": "WM/Composer", "origartist": "WM/OriginalArtist", "url": "WM/UserWebURL",
    "encodedby": "WM/EncodedBy", "track": "WM/TrackNumber", "bpm": "WM/BeatsPerMinute",
    "lyrics": "WM/Lyrics", "mood": "WM/Mood", "disc": "WM/PartOfSet",
    "compilation": "WM/PartOfACompilation", "albumartist": "WM/AlbumArtist",
    "conductor": "WM/Conductor", "lyricist": "WM/Writer", "origlyricist": "WM/OriginalLyricist",
    "fileurl": "WM/AudioFileURL", "artisturl": "WM/AuthorURL", "sourceurl": "WM/AudioSourceURL",
    "radioname": "WM/RadioStationName", "radiourl": "WM/RadioStationURL",
    "buyurl": "WM/MusicBuyUrl", "isrc": "WM/ISRC", "label": "WM/Publisher",
    "catalog": "WM/CatalogNumber", "key": "WM/InitialKey", "remixer": "WM/ModifiedBy",
    "grouping": "WM/ContentGroupDescription", "subtitle": "WM/SubTitle",
    "releasetime": "WM/OriginalReleaseTime", "showtitle": "WM/Show",
    "episodeid": "WM/EpisodeID", "seasonnumber": "WM/SeasonNumber",
    "episodenumber": "WM/EpisodeNumber", "sorttitle": "WM/TitleSortOrder",
    "sortartist": "WM/ArtistSortOrder", "sortalbumartist": "WM/AlbumArtistSortOrder",
    "sortalbum": "WM/AlbumSortOrder", "sortcomposer": "WM/ComposerSortOrder",
    "sortshow": "WM/ShowSortOrder",
}
ASF_RATING = [0, 1, 25, 50, 75, 99]


def family_of(tags):
    if isinstance(tags, ID3):
        return "id3"
    if isinstance(tags, MP4Tags):
        return "mp4"
    if isinstance(tags, VCommentDict):
        return "vorbis"
    if isinstance(tags, APEv2):
        return "ape"
    if isinstance(tags, ASFTags):
        return "asf"
    return None


def image_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


def stars_from(value, scale):
    """Map a stored rating number onto 0-5 stars using the nearest value in `scale`."""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    return str(min(range(1, 6), key=lambda s: abs(scale[s] - v)))


# ---------------------------------------------------------------- reading

class Track:
    """One music file: canonical fields, covers and read-only properties."""

    def __init__(self, path):
        self.path = path
        self.reload()

    def reload(self):
        self.fields, self.covers, self.info, self.family, self.error = {}, [], {}, None, ""
        try:
            st = os.stat(self.path)
            self.size, self.mtime, self.ctime = st.st_size, st.st_mtime, st.st_ctime
        except OSError as e:
            self.size = self.mtime = self.ctime = 0
            self.error = str(e)
            return
        try:
            f = mutagen.File(self.path)
        except Exception as e:  # corrupt structures: show the file, untouched
            self.error = str(e)
            return
        if f is None:
            self.error = "Unsupported file"
            return
        i = f.info
        self.info = {
            "length": getattr(i, "length", 0) or 0,
            "bitrate": (getattr(i, "bitrate", 0) or 0) // 1000,
            "sample_rate": getattr(i, "sample_rate", 0) or 0,
            "channels": getattr(i, "channels", 0) or 0,
            "bits": getattr(i, "bits_per_sample", 0) or 0,
            "encoder": getattr(i, "encoder_info", "") or "",
            "vbr": "VBR" if "VBR" in str(getattr(i, "bitrate_mode", "")) else "",
        }
        self.family = family_of(f.tags) or _empty_family(f)
        if f.tags is not None:
            _READERS[self.family](f, self)
        self.fields = {k: v for k, v in self.fields.items() if v}

    @property
    def has_tag(self):
        return bool(self.fields or self.covers)

    @property
    def name(self):
        return os.path.basename(self.path)

    def __getitem__(self, key):
        return self.fields.get(key, "")


def _empty_family(f):
    try:
        f.add_tags()
    except Exception:
        return None
    return family_of(f.tags)


def _read_id3(f, t):
    tags = f.tags
    for key, fid in ID3_MAP.items():
        frames = tags.getall(fid)
        if not frames:
            continue
        fr = frames[0]
        if fid.startswith("W"):
            t.fields[key] = fr.url
        elif fid == "TCON":
            t.fields[key] = "; ".join(fr.genres)
        else:
            t.fields[key] = "; ".join(str(x) for x in fr.text)
    for fr in tags.getall("COMM"):
        if not fr.desc:
            t.fields["comment"] = "\n".join(fr.text)
            break
    for fr in tags.getall("USLT"):
        t.fields["lyrics"] = fr.text
        break
    for fr in tags.getall("WXXX"):
        t.fields["url"] = fr.url
        break
    for fr in tags.getall("POPM"):
        t.fields["rating"] = stars_from(fr.rating, POPM_VALUES)
        break
    t.covers = [(fr.mime or image_mime(fr.data), fr.data) for fr in tags.getall("APIC")]


def _read_mp4(f, t):
    tags = f.tags
    for key, atom in MP4_MAP.items():
        if atom in tags:
            vals = tags[atom]
            t.fields[key] = "; ".join(v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
                                      for v in vals)
    if "\xa9gen" not in tags and "gnre" in tags:
        idx = tags["gnre"][0] - 1
        if 0 <= idx < len(TCON.GENRES):
            t.fields["genre"] = TCON.GENRES[idx]
    for key, atom in MP4_INT_PAIR.items():
        if atom in tags and tags[atom]:
            n, total = (tuple(tags[atom][0]) + (0, 0))[:2]
            t.fields[key] = f"{n}/{total}" if total else (str(n) if n else "")
    for key, atom in MP4_INT.items():
        if atom in tags and tags[atom]:
            t.fields[key] = str(tags[atom][0])
    if tags.get("cpil"):
        t.fields["compilation"] = "1" if tags["cpil"] else ""
    t.covers = [("image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg", bytes(c))
                for c in tags.get("covr", [])]


def _read_vorbis(f, t):
    tags = f.tags
    lower = {k.lower(): v for k, v in tags.items()}

    def get(name):
        v = lower.get(name.lower())
        return "; ".join(v) if v else ""

    for key, name in VORBIS_MAP.items():
        t.fields[key] = get(name)
        for alias in VORBIS_READ_ALIASES.get(key, []):
            t.fields[key] = t.fields[key] or get(alias)
    for key, totals in (("track", ("TRACKTOTAL", "TOTALTRACKS")), ("disc", ("DISCTOTAL", "TOTALDISCS"))):
        total = next((get(n) for n in totals if get(n)), "")
        if total and t.fields[key] and "/" not in t.fields[key]:
            t.fields[key] += "/" + total
    t.fields["rating"] = stars_from(t.fields.get("rating"), [0, 20, 40, 60, 80, 100])
    pics = list(getattr(f, "pictures", []))
    for b64 in lower.get("metadata_block_picture", []):
        try:
            pics.append(Picture(base64.b64decode(b64)))
        except Exception:
            pass
    t.covers = [(p.mime or image_mime(p.data), p.data) for p in pics]
    for b64 in lower.get("coverart", []):  # legacy Ogg cover field
        data = base64.b64decode(b64)
        t.covers.append((image_mime(data), data))


def _read_ape(f, t):
    tags = f.tags
    for key, name in APE_MAP.items():
        v = tags.get(name)
        if v is not None and v.kind != BINARY:
            t.fields[key] = str(v)
    t.fields["rating"] = stars_from(t.fields.get("rating"), [0, 20, 40, 60, 80, 100])
    for name in tags.keys():
        if name.lower().startswith("cover art"):
            raw = tags[name].value
            data = raw.split(b"\0", 1)[1] if b"\0" in raw else raw
            t.covers.append((image_mime(data), data))


def _read_asf(f, t):
    tags = f.tags
    for key, name in ASF_MAP.items():
        vals = tags.get(name)
        if vals:
            t.fields[key] = "; ".join(str(v.value) for v in vals)
    if t.fields.get("compilation") in ("True", "1"):
        t.fields["compilation"] = "1"
    else:
        t.fields.pop("compilation", None)
    r = tags.get("WM/SharedUserRating")
    if r:
        t.fields["rating"] = stars_from(r[0].value, ASF_RATING)
    for v in tags.get("WM/Picture", []):
        t.covers.append(_unpack_wm_picture(v.value))


def _unpack_wm_picture(b):
    size = struct.unpack_from("<I", b, 1)[0]
    pos, strings = 5, []
    for _ in range(2):  # mime, description: UTF-16LE, NUL terminated
        end = pos
        while b[end:end + 2] != b"\0\0":
            end += 2
        strings.append(b[pos:end].decode("utf-16-le"))
        pos = end + 2
    data = b[pos:pos + size]
    return (strings[0] or image_mime(data), data)


def _pack_wm_picture(mime, data, ptype=3):
    return (struct.pack("<BI", ptype, len(data)) + mime.encode("utf-16-le") + b"\0\0"
            + b"\0\0" + data)


_READERS = {"id3": _read_id3, "mp4": _read_mp4, "vorbis": _read_vorbis, "ape": _read_ape,
            "asf": _read_asf}


# ---------------------------------------------------------------- writing

class TagError(Exception):
    pass


def apply_album_artist_rules(fields, cfg):
    """Spec §6.3, applied on every save path. Rule 3 wins when both apply."""
    if cfg.bool("AlwaysCopyArtistToAlbumArtistOnSave"):
        fields["albumartist"] = fields.get("artist", "")
    elif cfg.bool("CopyArtistToAlbumArtistOnSave") and not fields.get("albumartist"):
        fields["albumartist"] = fields.get("artist", "")
    if cfg.bool("AutoSetAlbumArtistToVariousArtists") and fields.get("compilation"):
        fields["albumartist"] = "Various Artists"


def trim_year(value, cfg):
    if cfg.bool("AutoTrimYearFrame") and re.match(r"^\d{4}-\d\d", value or ""):
        return value[:4]
    return value


def write_tags(path, changes, cfg, covers=None):
    """Write `changes` {field: text} (empty text deletes the field); covers=None leaves art alone.

    Returns the reloaded Track. Raises TagError with a user-facing message.
    """
    try:
        f = mutagen.File(path)
    except Exception as e:
        raise TagError(f"Error while working with music file:\n{e}\nFile name: {path}")
    if f is None:
        raise TagError(f"Can't write tag to this type of file: {path}")
    if f.tags is None:
        f.add_tags()
    fam = family_of(f.tags)
    if fam is None:
        raise TagError(f"Can't write tag to this type of file: {path}")
    current = Track.__new__(Track)
    current.fields, current.covers = {}, []
    _READERS[fam](f, current)
    merged = {k: v for k, v in current.fields.items() if v}
    merged.update(changes)
    apply_album_artist_rules(merged, cfg)
    if "year" in merged:
        merged["year"] = trim_year(merged["year"], cfg)
    todo = {k: (v or "").strip() if k != "lyrics" else (v or "")
            for k, v in merged.items() if k in changes or current.fields.get(k, "") != v}
    if fam == "mp4":  # spec §6.4
        for k in ("track", "disc", "bpm"):
            if k in todo:
                todo[k] = re.sub(r"[^0-9./]", "", todo[k])
    _WRITERS[fam](f, todo, covers, cfg)
    st = os.stat(path)
    try:
        if fam == "id3":
            _save_id3(f, cfg)
        else:
            f.save()
    except PermissionError:
        raise TagError(f"Failed to open file: {path}\n"
                       "Disc is write protected or file is busy by another application.")
    except Exception as e:
        raise TagError(f"Error while working with music file:\n{e}\nFile name: {path}")
    if cfg.bool("DontChangeFileTime"):
        os.utime(path, (st.st_atime, st.st_mtime))
    return Track(path)


def _save_id3(f, cfg):
    keep = [fr for fid in ID3_V24_KEEP for fr in f.tags.getall(fid)]
    f.tags.update_to_v23()
    for fr in keep:
        f.tags.add(fr)
    kwargs = {"v2_version": 3}
    if "v1" in inspect.signature(f.save).parameters:
        # AfterSavingID3v2: 0 always create/update ID3v1, 1 update only if exists, 2 delete
        kwargs["v1"] = {0: 2, 1: 1, 2: 0}.get(cfg.int("AfterSavingID3v2"), 1)
    f.save(**kwargs)


def _id3_encoding(text, cfg):
    if cfg.bool("WriteAnsiToID3EvenIfUnicode"):
        return 0
    if cfg.bool("WriteUnicodeToID3"):
        return 1
    try:
        text.encode("latin-1")
        return 0
    except UnicodeEncodeError:
        return 1


def _write_id3(f, todo, covers, cfg):
    tags = f.tags
    for key, v in todo.items():
        enc = _id3_encoding(v, cfg)
        if key == "comment":
            for k in [k for k in tags.keys() if k.startswith("COMM:") and tags[k].desc == ""]:
                del tags[k]
            if v:
                tags.add(COMM(encoding=enc, lang="eng", desc="", text=[v]))
        elif key == "lyrics":
            tags.delall("USLT")
            if v:
                tags.add(USLT(encoding=enc, lang="eng", desc="", text=v))
        elif key == "url":
            tags.delall("WXXX")
            if v:
                tags.add(WXXX(encoding=enc, desc="", url=v))
        elif key == "rating":
            tags.delall("POPM")
            if v and v != "0":
                tags.add(POPM(email=WMP_EMAIL, rating=POPM_VALUES[int(v)], count=0))
        elif key in ID3_MAP:
            fid = ID3_MAP[key]
            tags.delall(fid)
            if not v:
                continue
            if fid.startswith("TXXX:"):
                tags.add(TXXX(encoding=enc, desc=fid[5:], text=[v]))
            elif fid.startswith("W"):
                tags.add(Frames[fid](url=v))
            else:
                tags.add(Frames[fid](encoding=enc, text=[v]))
    if covers is not None:
        tags.delall("APIC")
        ptype = 3 if cfg.bool("MarkCoverArtAsFront") else 0
        for i, (mime, data) in enumerate(covers):
            tags.add(APIC(encoding=0, mime=mime, type=ptype, desc=" " * i, data=data))


def _split_pair(v):
    a, _, b = v.partition("/")
    to_int = lambda s: int(s) if s.strip().isdigit() else 0
    return to_int(a), to_int(b)


def _write_mp4(f, todo, covers, cfg):
    tags = f.tags
    for key, v in todo.items():
        if key in MP4_INT_PAIR:
            atom = MP4_INT_PAIR[key]
            n, total = _split_pair(v)
            tags.pop(atom, None)
            if n or total:
                tags[atom] = [(n, total)]
        elif key in MP4_INT:
            atom = MP4_INT[key]
            tags.pop(atom, None)
            if v.split(".")[0].isdigit():
                tags[atom] = [int(v.split(".")[0])]
        elif key == "compilation":
            tags["cpil"] = bool(v)
        elif key in MP4_MAP:
            atom = MP4_MAP[key]
            tags.pop(atom, None)
            if key == "genre":
                tags.pop("gnre", None)
            if v:
                tags[atom] = [MP4FreeForm(v.encode("utf-8"))] if atom.startswith(FF) else [v]
    if covers is not None:
        tags["covr"] = [MP4Cover(d, MP4Cover.FORMAT_PNG if m == "image/png" else MP4Cover.FORMAT_JPEG)
                        for m, d in covers]
        if not covers:
            del tags["covr"]


def _vorbis_like_values(todo, cfg, own_totals):
    out = {}
    for key, v in todo.items():
        if key == "rating":
            v = str(int(v) * 20) if v and v != "0" else ""
        if key in ("track", "disc") and own_totals:
            n, _, total = v.partition("/")
            out[(key, "n")] = n
            out[(key, "total")] = total
            continue
        out[(key, None)] = v
    return out


def _write_vorbis(f, todo, covers, cfg):
    tags = f.tags
    own = cfg.bool("SaveVorbisTrackDiscTotalsToItOwnFrames")
    totals = {"track": "TRACKTOTAL", "disc": "DISCTOTAL"}
    for (key, part), v in _vorbis_like_values(todo, cfg, own).items():
        name = totals[key] if part == "total" else VORBIS_MAP[key]
        for n in [name] + VORBIS_READ_ALIASES.get(key, []):
            if n in tags:
                del tags[n]
        if v:
            tags[name] = [v]
    if covers is not None:
        for n in ("METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME"):
            if n in tags:
                del tags[n]
        pics = []
        for mime, data in covers:
            p = Picture()
            p.type, p.mime, p.data = 3, mime, data
            pics.append(p)
        if isinstance(f, FLAC):
            f.clear_pictures()
            for p in pics:
                f.add_picture(p)
        elif pics:
            tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(p.write()).decode("ascii") for p in pics]


def _write_ape(f, todo, covers, cfg):
    tags = f.tags
    for (key, _), v in _vorbis_like_values(todo, cfg, False).items():
        name = APE_MAP[key]
        if name in tags:
            del tags[name]
        if v:
            tags[name] = v
    if covers is not None:
        for name in [n for n in tags.keys() if n.lower().startswith("cover art")]:
            del tags[name]
        if covers:
            mime, data = covers[0]
            ext = {"image/png": "png", "image/gif": "gif", "image/bmp": "bmp"}.get(mime, "jpg")
            tags[APE_COVER] = APEValue(f"cover.{ext}".encode() + b"\0" + data, BINARY)


def _write_asf(f, todo, covers, cfg):
    tags = f.tags
    for key, v in todo.items():
        if key == "rating":
            _asf_del(tags, "WM/SharedUserRating")
            if v and v != "0":
                tags["WM/SharedUserRating"] = [ASFDWordAttribute(ASF_RATING[int(v)])]
            continue
        name = ASF_MAP[key]
        _asf_del(tags, name)
        if v:
            if len(v.encode("utf-16-le")) > 65535:
                raise TagError("Can't write more than 64k of data into wma frame.")
            tags[name] = [v]
    if covers is not None:
        _asf_del(tags, "WM/Picture")
        if covers:
            tags["WM/Picture"] = [ASFByteArrayAttribute(_pack_wm_picture(m, d)) for m, d in covers]


def _asf_del(tags, name):
    if name in tags:
        del tags[name]


_WRITERS = {"id3": _write_id3, "mp4": _write_mp4, "vorbis": _write_vorbis, "ape": _write_ape,
            "asf": _write_asf}


def remove_tags(path, cfg):
    try:
        f = mutagen.File(path)
        if f is None or f.tags is None:
            return
        st = os.stat(path)
        if isinstance(f, FLAC):
            f.clear_pictures()
            f.tags.clear()
            f.save()
        elif isinstance(f, ID3FileType):
            id3_delete(path, delete_v1=cfg.bool("DeleteID3v1WhenDeletingID3v2"), delete_v2=True)
        elif family_of(f.tags) == "asf":
            f.tags.clear()
            f.save()
        else:
            f.delete()
        if cfg.bool("DontChangeFileTime"):
            os.utime(path, (st.st_atime, st.st_mtime))
    except Exception as e:
        raise TagError(f"Error while working with music file:\n{e}\nFile name: {path}")


def resize_cover(mime, data, max_dim, quality, to_jpeg):
    """Spec §6.2: only touches images whose longest side exceeds max_dim."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if max(img.size) <= max_dim:
        return mime, data
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    out = io.BytesIO()
    if to_jpeg or mime == "image/jpeg":
        img.convert("RGB").save(out, "JPEG", quality=quality)
        return "image/jpeg", out.getvalue()
    img.save(out, img.format or "PNG")
    return mime, out.getvalue()


# ---------------------------------------------------------------- masks

# Rename variables, spec §4.2. Values are callables (track, position) -> str.
def _fmt_time(sec):
    sec = int(round(sec or 0))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _num_part(v):
    return v.partition("/")[0].strip()


RENAME_VARS = {
    "%1": "artist", "%2": "title", "%3": "album", "%4": "year", "%5": "genre", "%6": "track",
    "%9": "comment", "%b": "bpm", "%cp": "composer", "%aa": "albumartist", "%co": "conductor",
    "%#": "disc", "%rt": "releasetime", "%md": "mood", "%lb": "label", "%rb": "remixer",
    "%key": "key", "%swt": "showtitle", "%eid": "episodeid", "%snr": "seasonnumber",
    "%enr": "episodenumber",
}
FILE_VARS = {  # not tag fields: always available
    "%7": lambda t, i: os.path.splitext(t.name)[0],
    "%8": lambda t, i: str(i),
    "%0": lambda t, i: str(t.info.get("bitrate", "") or ""),
    "%t": lambda t, i: _fmt_time(t.info.get("length")),
    "%rnd": lambda t, i: str(random.randint(0, 99999)),
    "%ext": lambda t, i: os.path.splitext(t.name)[1].lstrip("."),
    "%khz": lambda t, i: f"{t.info.get('sample_rate', 0) / 1000:g}",
    "%dbit": lambda t, i: str(t.info.get("bits", "") or ""),
    "%enc": lambda t, i: t.info.get("encoder", ""),
}
VAR_HELP = [
    ("%1", "Artist"), ("%2", "Title"), ("%3", "Album"), ("%4", "Year"), ("%5", "Genre"),
    ("%6", "Track #"), ("%7", "Original file name"), ("%8", "File number"), ("%9", "Comment"),
    ("%0", "Bitrate"), ("%t", "Play time"), ("%b", "BPM"), ("%cp", "Composer"),
    ("%aa", "Album Artist"), ("%co", "Conductor"), ("%#", "Disc #"), ("%rt", "Release Time"),
    ("%md", "Mood"), ("%lb", "Label"), ("%rb", "Remixed by"), ("%key", "Initial Key"),
    ("%swt", "Show Title"), ("%eid", "Episode ID"), ("%snr", "Season number"),
    ("%enr", "Episode number"), ("%rnd", "Random number"), ("%ext", "File extension"),
    ("%khz", "Sample rate"), ("%dbit", "Bit depth"), ("%enc", "Encoder"),
]
# File-name-to-tag variables, spec §7: tag vars only, plus %d = skip.
FTT_VARS = dict(RENAME_VARS)
FTT_HELP = [(k, d) for k, d in VAR_HELP if k in FTT_VARS] + [("%d", "Skip")]


def _token_re(tokens):
    return re.compile("|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)))


_RENAME_RE = _token_re(list(RENAME_VARS) + list(FILE_VARS))
_FTT_RE = _token_re(list(FTT_VARS) + ["%d"])
ILLEGAL = '*?|<>"'


def sanitize(value):
    """Tag text must never create paths: separators become '-', other illegal chars vanish."""
    value = re.sub(r"[\\/:]", "-", value)
    value = "".join(c for c in value if c not in ILLEGAL and c >= " ")
    return value.strip()


def mask_uses_tags(mask):
    return any(m.group(0) in RENAME_VARS for m in _RENAME_RE.finditer(mask))


def apply_case(text, mode, delimiters=" ", keep_words=()):
    """mode: 0 lower, 1 UPPER, 2 Capitalize First Letter (each word), 3 Capitalize first word."""
    if mode == 0:
        text = text.lower()
    elif mode == 1:
        text = text.upper()
    elif mode == 2:
        out, cap = [], True
        for c in text:
            out.append(c.upper() if cap else c.lower())
            cap = c in delimiters
        text = "".join(out)
    elif mode == 3:
        text = text[:1].upper() + text[1:].lower()
    if mode in (2, 3):
        for w in keep_words:
            if w:
                text = re.sub(re.escape(w), lambda m, w=w: w, text, flags=re.IGNORECASE)
    return text


def expand_mask(mask, track, position, cfg, case=None, underscores_to_spaces=False, replacements=True):
    """Return the new name (no extension). `\\` or `/` in the mask become folder separators."""
    def value(m):
        tok = m.group(0)
        if tok in FILE_VARS:
            v = FILE_VARS[tok](track, position)
        else:
            key = RENAME_VARS[tok]
            v = track[key]
            if key == "track":
                v = _num_part(v)
            elif key == "disc":
                v = _num_part(v)
                if cfg.bool("AlwaysRemoveLeadingZeroFromDiscValue"):
                    v = v.lstrip("0") or v
            elif key == "year":
                v = trim_year(v, cfg)
        return sanitize(v).replace("\x00", "")

    parts = re.split(r"[\\/]", mask)
    out = []
    for part in parts:
        s = _RENAME_RE.sub(value, part)
        if underscores_to_spaces:  # before case, so "my_song" capitalises as "My Song"
            s = s.replace("_", " ")
        if replacements:
            for before, after in cfg.replacements():
                if before:
                    s = re.sub(re.escape(before), lambda m, a=after: a, s, flags=re.IGNORECASE)
        if case is not None:
            s = apply_case(s, case, cfg.get("CapitalizeConfiguration"), cfg.list("KeepStrCase"))
        out.append(s.strip())
    return os.sep.join(out)


def parse_filename(mask, path):
    """Spec §7. Returns {field: value} or None when the name doesn't match the mask."""
    mask = mask.replace("/", "\\")
    depth = mask.count("\\")
    comps = os.path.splitext(path)[0].split(os.sep)
    if len(comps) < depth + 1:
        return None
    text = "\\".join(comps[-(depth + 1):])
    pattern, keys, pos = "", [], 0
    for m in _FTT_RE.finditer(mask):
        pattern += re.escape(mask[pos:m.start()]) + "(.*?)"
        keys.append(None if m.group(0) == "%d" else FTT_VARS[m.group(0)])
        pos = m.end()
    pattern += re.escape(mask[pos:])
    if not keys:
        return None
    m = re.fullmatch(pattern, text, flags=re.DOTALL)
    if not m:
        return None
    return {k: v.strip() for k, v in zip(keys, m.groups()) if k}


# ---------------------------------------------------------------- rename planning

class RenameItem:
    def __init__(self, track, new_path=None, error=""):
        self.track, self.old_path, self.new_path, self.error = track, track.path, new_path, error


def plan_renames(entries, mask, cfg, case=None, underscores=False):
    """entries: [(position, Track)] in list order. Returns [RenameItem] (spec §4.3)."""
    items, uses_tags = [], mask_uses_tags(mask)
    for pos, t in entries:
        if uses_tags and not t.has_tag:
            items.append(RenameItem(t, error="Error in file or file does not contain tag "
                                             "or its new name doesn't contain any text."))
            continue
        name = expand_mask(mask, t, pos, cfg, case, underscores)
        if not os.path.basename(name).strip() or name.strip(os.sep) == "":
            items.append(RenameItem(t, error="New name doesn't contain any text."))
            continue
        ext = os.path.splitext(t.path)[1]
        new = os.path.normpath(os.path.join(os.path.dirname(t.path), name + ext))
        items.append(RenameItem(t, new))
    counts = {}
    for it in items:
        if it.new_path:
            counts[it.new_path] = counts.get(it.new_path, 0) + 1
    sources = {it.old_path for it in items}
    taken = set()
    for it in items:
        if not it.new_path:
            continue
        if counts[it.new_path] > 1:
            it.error = ("You are trying to create more than one file with the same name in a folder: "
                        + os.path.basename(it.new_path))
            it.new_path = None
            continue
        if it.new_path == it.old_path:
            continue
        if (os.path.exists(it.new_path) and it.new_path not in sources) or it.new_path in taken:
            if it.new_path not in taken and os.path.samefile(it.new_path, it.old_path):
                pass  # case-only change on a case-insensitive mount (vfat/NTFS)
            elif cfg.bool("DontRenameFileIfFileWithThisNameExists"):
                it.error = "File with this name already exists: " + os.path.basename(it.new_path)
                it.new_path = None
                continue
            else:
                it.new_path = free_name(it.new_path, taken)
        taken.add(it.new_path)
    return items


def free_name(path, taken=()):
    """`File.mp3` -> `File (1).mp3`, `File (2).mp3` ... until neither on disk nor in `taken`."""
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{base} ({n}){ext}") or f"{base} ({n}){ext}" in taken:
        n += 1
    return f"{base} ({n}){ext}"


def execute_renames(items):
    """Rename on disk. Returns (undo_log, errors). undo_log = [(old, new, created_dirs)]."""
    undo, errors = [], []
    for it in items:
        if it.error:
            errors.append(f"{it.track.name}: {it.error}")
            continue
        if not it.new_path or it.new_path == it.old_path:
            continue
        created = []
        d = os.path.dirname(it.new_path)
        try:
            missing = []
            while d and not os.path.isdir(d):
                missing.append(d)
                d = os.path.dirname(d)
            for m in reversed(missing):
                os.mkdir(m)
                created.append(m)
        except OSError as e:
            errors.append(f"Cannot create folder: {e}")
            continue
        try:
            if os.path.exists(it.new_path) and not os.path.samefile(it.new_path, it.old_path):
                raise FileExistsError(errno.EEXIST, "File exists")
            os.rename(it.old_path, it.new_path)
        except OSError as e:
            if e.errno == errno.EXDEV:
                errors.append("Files cannot be moved to the other drive!")
            else:
                errors.append(f"Cannot rename file: {it.old_path} to {it.new_path}. {e.strerror}")
            for c in reversed(created):
                _rmdir_quiet(c)
            continue
        it.track.path = it.new_path
        undo.append((it.old_path, it.new_path, created))
    return undo, errors


def undo_renames(undo):
    """Reverse a batch. Returns ({new: old} actually restored, errors)."""
    restored, errors = {}, []
    for old, new, created in reversed(undo):
        try:
            os.rename(new, old)
            restored[new] = old
        except OSError as e:
            errors.append(f"Cannot rename file: {new} to {old}. {e.strerror}")
            continue
        for c in reversed(created):
            _rmdir_quiet(c)
    return restored, errors


def _rmdir_quiet(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


# ---------------------------------------------------------------- settings

DEFAULTS = {
    "CurrentFolder": os.path.expanduser("~"), "HistoryList": "", "FavoritesList": "",
    "UseSubfolders": "0", "UseSpecificStartFolder": "0", "SpecificStartFolder": "",
    "RenameFileNameMask": "%1 - %2", "RenameFileNameMaskItems": "%1 - %2,%6 - %2,%6. %1 - %2",
    "RenameFilesCaseChecked": "0", "RenameFilesCaseItemIndex": "2", "ReplaceSpacesChecked": "0",
    "FileToTagFileNameMask": "%1-%2", "FileToTagFileNameMaskItems": "%1-%2,%1 - %2,%6 - %2,%6 - %1 - %2",
    "FileToTagCaseChecked": "0", "FileToTagCaseItemIndex": "2", "ReplaceUnderscoresChecked": "0",
    "FolderNameMask": "%1 - %3", "FolderNameMaskItems": "%1 - %3,%4 - %3,%1 - %4 - %3",
    "TagEditorCaseChecked": "0", "TagEditorCaseItemIndex": "2",
    "FormTagEditorRadioGroupCaseChecked": "0", "FormTagEditorRadioGroupCaseItemIndex": "2",
    "DontCloseTagEdOnErr": "1", "DontChangeFileTime": "0",
    "DontRenameFileIfFileWithThisNameExists": "0",
    "CapitalizeConfiguration": '_ ()[]-{}"&#.1234567890\\/',
    "UseColors": "1", "RedColor": "15191295", "UseLowBitrateColor": "0", "LowBitrate": "96",
    "LowBitrateColor": "16553082", "HideFilesExtension": "0",
    "FileListColumns": "name,cover,artist,title,album,year,track,genre,time,bitrate,size",
    "Mp3Player": "", "CoverSearchUrl": "https://www.google.com/search?tbm=isch&q=%s",
    "ReplStrBefore": "", "ReplStrAfter": "",
    "KeepStrCase": ".mp3,.asf,.wma,.ogg,.flac,.m4a,.m4b,.mp4,.ape",
    "CopyArtistToAlbumArtistOnSave": "0", "AlwaysCopyArtistToAlbumArtistOnSave": "0",
    "AutoSetAlbumArtistToVariousArtists": "0",
    "WriteUnicodeToID3": "1", "WriteAnsiToID3EvenIfUnicode": "0",
    "TagWriteOptionsResizeBigCoverArtWhenAdding": "0", "TagWriteOptionsMaxCoverArtDimension": "600",
    "TagWriteOptionsCoverArtJpegQuality": "70", "TagWriteOptionsConvertCoverArtToJpeg": "0",
    "CovertArtDimensionValue": "600", "CovertArtJpegQualityValue": "70", "CovertArtToJpegChecked": "0",
    "SaveFocusedFieldForSaveNext": "1", "UncheckAllChecksInMTagEditor": "0",
    "ShowAnyImageIfNoFolderJpg": "0", "MarkCoverArtAsFront": "1", "AutoTrimYearFrame": "1",
    "SaveVorbisTrackDiscTotalsToItOwnFrames": "0", "DeleteID3v1WhenDeletingID3v2": "1",
    "AfterSavingID3v2": "1", "AlwaysRemoveLeadingZeroFromDiscValue": "0",
    "DontAddLeadingZeroToTrackNr": "0", "SavePathWhenAddingCoverArt": "0", "CoverArtFilesPath": "",
    "RemoveTagQuestion": "1", "AdditionalGenresList": "",
    "PasteTagTitle": "1", "PasteTagTrack": "1", "PasteTagBPM": "1", "PasteTagLyrics": "1",
    "PasteTagArtist": "0", "FilterByItemIndex": "0", "ToolsBarVisible": "1", "FoldersVisible": "1",
    "WindowWidth": "1200", "WindowHeight": "800", "TagEditorWidth": "820", "TagEditorHeight": "640",
}


class Settings:
    """INI persistence in the original's key names (spec §14), section [Config]."""

    def __init__(self, path=None):
        self.path = path or os.path.join(
            os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "Retag", "Retag.ini")
        self.cp = configparser.ConfigParser(interpolation=None)
        self.cp.optionxform = str
        self.cp.read(self.path, encoding="utf-8")
        if not self.cp.has_section("Config"):
            self.cp.add_section("Config")
        self.on_change = None  # the GUI hooks a debounced save here so prefs survive a crash

    def get(self, key):
        return self.cp.get("Config", key, fallback=DEFAULTS.get(key, ""))

    def set(self, key, value):
        if isinstance(value, bool):
            value = int(value)
        value = str(value)
        if self.cp.get("Config", key, fallback=None) == value:
            return
        self.cp.set("Config", key, value)
        if self.on_change:
            self.on_change()

    def bool(self, key):
        return self.get(key).strip() not in ("", "0", "false", "False")

    def int(self, key):
        try:
            return int(self.get(key))
        except ValueError:
            return int(DEFAULTS.get(key, "0") or 0)

    def list(self, key):
        """Comma list, items quoted when they contain separators (the original's format)."""
        text = self.get(key)
        return [x for x in next(csv.reader([text]), [])] if text else []

    def set_list(self, key, items):
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow(list(items))
        self.set(key, buf.getvalue())

    def replacements(self):
        return list(zip(self.list("ReplStrBefore"), self.list("ReplStrAfter")))

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            self.cp.write(fh)
        os.replace(tmp, self.path)


def nearest_existing_folder(path):
    """The folder itself, or its closest surviving parent (e.g. after a drive is unplugged)."""
    path = os.path.abspath(path) if path else ""
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        path = "" if parent == path else parent
    return path or os.path.expanduser("~")


def scan_folder(folder, recursive, stop=lambda: False):
    """Yield music file paths in name order."""
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files, key=str.lower):
                if stop():
                    return
                if os.path.splitext(name)[1].lower() in EXTENSIONS:
                    yield os.path.join(root, name)
    else:
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            return
        for name in names:
            p = os.path.join(folder, name)
            if os.path.splitext(name)[1].lower() in EXTENSIONS and os.path.isfile(p):
                yield p
