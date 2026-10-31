#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Don Wildman
"""Retag: music tag editor and file renamer for Linux (GTK3). Run: ./retag.py [folder]

Uses the system Python because python3-gi,
python3-mutagen and python3-pil ship with Linux Mint.
"""
import fnmatch
import os
import random
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import tagcore as tc  # noqa: E402

APP = "Retag"
LOGO = os.path.join(os.path.dirname(os.path.realpath(__file__)), "logo")
CASES = ["lower case", "UPPER CASE", "Capitalize First Letter", "Capitalize first word"]
NO_CASE = {"rating", "compilation", "lyrics", "year", "track", "disc", "bpm", "url", "fileurl",
           "artisturl", "sourceurl", "buyurl", "radiourl", "isrc", "releasetime",
           "seasonnumber", "episodenumber"}
GENERAL = ["title", "artist", "album", "year", "track", "genre", "comment", "composer", "origartist",
           "copyright", "url", "encodedby", "bpm", "disc", "rating", "mood", "compilation"]
ADDITIONAL = ["albumartist", "conductor", "lyricist", "fileurl", "artisturl", "sourceurl", "buyurl",
              "radioname", "radiourl", "origlyricist", "label", "isrc", "catalog", "remixer", "key"]
MORE = ["grouping", "subtitle", "releasetime", "showtitle", "episodeid", "seasonnumber", "episodenumber"]
SORTING = ["sorttitle", "sortartist", "sortalbumartist", "sortalbum", "sortcomposer", "sortshow"]
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".gif")
SHORTCUTS = """F4 / Ctrl+F4 / Shift+F4\tRename / Multi tag editor / Tags from file name tab
F5 / Enter\tEdit file tag
Ctrl+F5\tEdit tag in selected files
F2 / F6\tEdit file name
F7\tRename folder
F8 / Ctrl+F8\tTools bar / Folders panel
Ctrl+O\tOpen folder (address bar)
Ctrl+R\tRefresh file list
Ctrl+F\tFind (filter)
Ctrl+A / Ctrl+N / Ctrl+I\tSelect all / none / invert
Alt+Up / Alt+Down\tMove file up / down
Ctrl+T\tAuto fill track #
Ctrl+Del\tRemove tags
Del\tMove files to trash
Shift+Ctrl+C / Shift+Ctrl+V\tCopy tag / Paste tag
Ctrl+Alt+V\tPaste tag to all selected files
Ctrl+P / Ctrl+S\tPlay file / Play selected files
Ctrl+Alt+O\tOptions"""


# ---------------------------------------------------------------- small helpers

def message(parent, text, kind=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, secondary=None):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=kind, buttons=buttons, text=text)
    if secondary:
        d.format_secondary_text(secondary)
    r = d.run()
    d.destroy()
    return r


def confirm(parent, text, secondary=None):
    return message(parent, text, Gtk.MessageType.QUESTION, Gtk.ButtonsType.YES_NO, secondary) == Gtk.ResponseType.YES


def error(parent, text, secondary=None):
    message(parent, text, Gtk.MessageType.ERROR, secondary=secondary)


def show_list(parent, title, lines):
    d = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    d.add_button("_Close", Gtk.ResponseType.CLOSE)
    d.set_default_size(640, 360)
    tv = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    tv.get_buffer().set_text("\n".join(lines))
    sw = Gtk.ScrolledWindow(vexpand=True)
    sw.add(tv)
    d.get_content_area().pack_start(sw, True, True, 0)
    d.show_all()
    d.run()
    d.destroy()


def pixbuf(data, size):
    """Returns (scaled pixbuf, (w, h)) or (None, None)."""
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
    except GLib.Error:
        return None, None
    w, h = pb.get_width(), pb.get_height()
    s = min(size / w, size / h, 1)
    return pb.scale_simple(max(1, int(w * s)), max(1, int(h * s)), GdkPixbuf.InterpType.BILINEAR), (w, h)


def picture_icon(colour, size=16):
    """Framed landscape picture: colour when a track has cover art, monochrome when it hasn't."""
    import cairo
    pal = dict(frame=(0.91, 0.73, 0.14), edge=(0.54, 0.42, 0.0), sky=(0.56, 0.79, 0.94),
               sun=(1.0, 0.82, 0.25), far=(0.18, 0.43, 0.56), near=(0.31, 0.60, 0.23)) if colour else \
        dict(frame=(0.78, 0.78, 0.78), edge=(0.43, 0.43, 0.43), sky=(0.92, 0.92, 0.92),
             sun=(0.62, 0.62, 0.62), far=(0.48, 0.48, 0.48), near=(0.60, 0.60, 0.60))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    c = cairo.Context(surf)
    c.scale(size / 16, size / 16)
    c.rectangle(1.5, 2.5, 13, 11)
    c.set_source_rgb(*pal["frame"])
    c.fill_preserve()
    c.set_source_rgb(*pal["edge"])
    c.set_line_width(1)
    c.stroke()
    c.rectangle(3, 4, 10, 8)
    c.set_source_rgb(*pal["sky"])
    c.fill()
    c.arc(10.5, 6.2, 1.3, 0, 6.2832)
    c.set_source_rgb(*pal["sun"])
    c.fill()
    for points, key in (([(3, 12), (6, 7), (9.5, 12)], "far"), ([(5.5, 12), (9.5, 8.5), (13, 12)], "near")):
        c.move_to(*points[0])
        for p in points[1:]:
            c.line_to(*p)
        c.close_path()
        c.set_source_rgb(*pal[key])
        c.fill()
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


def fmt_time(sec):
    sec = int(round(sec or 0))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def colorref(value):
    v = int(value)
    return "#%02x%02x%02x" % (v & 255, (v >> 8) & 255, (v >> 16) & 255)


def to_colorref(rgba):
    return int(rgba.red * 255) | int(rgba.green * 255) << 8 | int(rgba.blue * 255) << 16


def num_key(v):
    head = v.partition("/")[0].strip()
    return (0, int(head), v) if head.isdigit() else (1, 0, v.lower())


def mru_combo(items, text):
    c = Gtk.ComboBoxText.new_with_entry()
    for i in items:
        c.append_text(i)
    c.get_child().set_text(text)
    return c


def push_mru(cfg, key, value, limit=20):
    items = [value] + [i for i in cfg.list(key) if i != value]
    cfg.set_list(key, items[:limit])


def case_row(cfg, checked_key, index_key):
    box = Gtk.Box(spacing=6)
    chk = Gtk.CheckButton(label="Case:", active=cfg.bool(checked_key))
    combo = Gtk.ComboBoxText()
    for c in CASES:
        combo.append_text(c)
    combo.set_active(cfg.int(index_key))
    combo.set_sensitive(chk.get_active())
    chk.connect("toggled", lambda w: combo.set_sensitive(w.get_active()))
    box.pack_start(chk, False, False, 0)
    box.pack_start(combo, False, False, 0)
    return box, chk, combo


def case_value(chk, combo):
    return combo.get_active() if chk.get_active() else None


def find_folder_jpg(folder, any_image=False):
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return None
    for n in names:
        if n.lower() == "folder.jpg":
            return os.path.join(folder, n)
    if any_image:
        for n in names:
            if n.lower().endswith(IMAGE_EXT):
                return os.path.join(folder, n)
    return None


def as_jpeg(mime, data):
    if mime == "image/jpeg":
        return data
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def genre_list(cfg):
    return sorted(set(tc.GENRES) | set(cfg.list("AdditionalGenresList")), key=str.lower)


def grid_rows(rows):
    g = Gtk.Grid(column_spacing=8, row_spacing=6)
    for i, (label, widget) in enumerate(rows):
        if label is not None:
            g.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
        widget.set_hexpand(True)
        g.attach(widget, 1 if label is not None else 0, i, 1 if label is not None else 2, 1)
    return g


def mask_legend(variables):
    """Small wrapped key of mask variables, e.g. "%1 Artist  %2 Title ..."."""
    nb = "\u00a0"  # non-breaking, so a pair never wraps apart
    text = "   ".join(f"<b>{GLib.markup_escape_text(k)}</b>{nb}{GLib.markup_escape_text(d).replace(' ', nb)}"
                       for k, d in variables)
    label = Gtk.Label(xalign=0, wrap=True)
    label.set_markup(f"<small>{text}</small>")
    label.get_style_context().add_class("dim-label")
    return label


def mask_button(on_click):
    b = Gtk.Button(label="…", tooltip_text="Mask Editor")
    b.connect("clicked", lambda *_: on_click())
    return b


# ---------------------------------------------------------------- mask editor (spec §9)

class MaskEditor(Gtk.Dialog):
    def __init__(self, parent, mask, saved, sample, variables):
        super().__init__(title="Mask Editor", transient_for=parent, modal=True)
        self.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_OK", Gtk.ResponseType.OK)
        self.set_default_size(620, 460)
        self.sample = sample
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        self.entry = Gtk.Entry(text=mask)
        self.result = Gtk.Label(xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.END)
        self.entry.connect("changed", lambda *_: self.update())
        box.pack_start(grid_rows([("Mask:", self.entry), ("Result example:", self.result)]), False, False, 0)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6)
        for var, desc in variables:
            b = Gtk.Button(label=f"{var} {desc}")
            b.connect("clicked", lambda _b, v=var: self.insert(v))
            flow.add(b)
        b = Gtk.Button(label="Insert Folder ( \\ )")
        b.connect("clicked", lambda *_: self.insert("\\"))
        flow.add(b)
        box.pack_start(flow, False, False, 0)
        box.pack_start(Gtk.Label(label="Saved masks (double-click to use):", xalign=0), False, False, 0)
        self.store = Gtk.ListStore(str)
        for m in saved:
            self.store.append([m])
        self.view = Gtk.TreeView(model=self.store, headers_visible=False)
        self.view.append_column(Gtk.TreeViewColumn("Mask", Gtk.CellRendererText(), text=0))
        self.view.connect("row-activated", lambda v, p, c: self.entry.set_text(self.store[p][0]))
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.view)
        row = Gtk.Box(spacing=6)
        row.pack_start(sw, True, True, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for label, cb in (("Add Current Mask", self.add), ("Remove Mask", self.remove),
                          ("Move Up", lambda: self.move(-1)), ("Move Down", lambda: self.move(1))):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            btns.pack_start(b, False, False, 0)
        row.pack_start(btns, False, False, 0)
        box.pack_start(row, True, True, 0)
        self.update()
        self.show_all()

    def insert(self, text):
        pos = self.entry.get_position()
        self.entry.insert_text(text, pos)
        self.entry.set_position(pos + len(text))
        self.entry.grab_focus_without_selecting()

    def update(self):
        self.result.set_text(self.sample(self.entry.get_text()))

    def add(self):
        m = self.entry.get_text()
        if m and m not in [r[0] for r in self.store]:
            self.store.append([m])

    def selected_iter(self):
        return self.view.get_selection().get_selected()[1]

    def remove(self):
        it = self.selected_iter()
        if it:
            self.store.remove(it)

    def move(self, d):
        it = self.selected_iter()
        if not it:
            return
        other = self.store.iter_previous(it) if d < 0 else self.store.iter_next(it)
        if other:
            self.store.swap(it, other)

    def get(self):
        """(mask, saved masks) or None when cancelled."""
        r = self.run()
        out = (self.entry.get_text(), [row[0] for row in self.store]) if r == Gtk.ResponseType.OK else None
        self.destroy()
        return out


# ---------------------------------------------------------------- tag editor (spec §6)

class TagEditor(Gtk.Window):
    """Single mode edits tracks[index] with Save and Prev/Next; multi mode writes checked fields to all."""

    def __init__(self, app, tracks, index=0, single=True):
        super().__init__(title="Tag Editor", transient_for=app, modal=True,
                         type_hint=Gdk.WindowTypeHint.DIALOG, window_position=Gtk.WindowPosition.CENTER_ON_PARENT)
        self.app, self.cfg, self.tracks, self.index, self.single = app, app.cfg, tracks, index, single
        self.set_default_size(self.cfg.int("TagEditorWidth"), self.cfg.int("TagEditorHeight"))
        self.loading = False
        self.fields = {}
        self.covers = []
        self.last_focus = "title"
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, border_width=8)
        self.add(root)

        if single:
            self.name_entry = Gtk.Entry()
            self.ext_label = Gtk.Label()
            row = Gtk.Box(spacing=6)
            row.pack_start(Gtk.Label(label="File name:"), False, False, 0)
            row.pack_start(self.name_entry, True, True, 0)
            row.pack_start(self.ext_label, False, False, 0)
            root.pack_start(row, False, False, 0)

        nb = Gtk.Notebook()
        root.pack_start(nb, True, True, 0)
        general = Gtk.Box(spacing=12, border_width=8)
        general.pack_start(self.field_grid(GENERAL), True, True, 0)
        general.pack_start(self.cover_box(), False, False, 0)
        nb.append_page(general, Gtk.Label(label="General"))
        nb.append_page(self.lyrics_page(), Gtk.Label(label="Lyrics"))
        for title, keys in (("Additional", ADDITIONAL), ("More Additional", MORE), ("Sorting", SORTING)):
            page = Gtk.ScrolledWindow()
            g = self.field_grid(keys)
            g.set_border_width(8)
            page.add(g)
            nb.append_page(page, Gtk.Label(label=title))

        bar = Gtk.Box(spacing=6)
        for label, cb in (("Select All", lambda: self.check_all(True)), ("Select None", lambda: self.check_all(False)),
                          ("Clear All", self.clear_all), ("Play", self.play)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            bar.pack_start(b, False, False, 0)
        cbox, self.case_chk, self.case_combo = case_row(
            self.cfg, "FormTagEditorRadioGroupCaseChecked", "FormTagEditorRadioGroupCaseItemIndex")
        bar.pack_start(cbox, False, False, 12)
        root.pack_start(bar, False, False, 0)

        bar = Gtk.Box(spacing=6)
        b = Gtk.Button(label="Remove Tag")
        b.connect("clicked", lambda *_: self.remove_tag())
        bar.pack_start(b, False, False, 0)
        buttons = [("_Cancel", self.close), ("_Save", self.on_save)]
        if single:
            buttons = [("Save and _Prev", lambda: self.save_and_move(-1))] + buttons + \
                      [("Save and _Next", lambda: self.save_and_move(1))]
        for label, cb in reversed(buttons):
            b = Gtk.Button.new_with_mnemonic(label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            bar.pack_end(b, False, False, 0)
            if label == "_Save":
                b.set_can_default(True)
                self.set_default(b)
        root.pack_start(bar, False, False, 0)

        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", lambda *_: self.on_close() or False)
        self.load()
        self.show_all()
        self.focus_field(self.last_focus)

    # ---- widgets
    def make_widget(self, key):
        if key == "genre":
            w = Gtk.ComboBoxText.new_with_entry()
            for g in genre_list(self.cfg):
                w.append_text(g)
        elif key == "rating":
            w = Gtk.ComboBoxText()
            for r in tc.RATINGS:
                w.append_text(r)
        elif key == "compilation":
            w = Gtk.CheckButton(label="Yes")
        elif key == "lyrics":
            w = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        else:
            w = Gtk.Entry(activates_default=True)
            if key in ("seasonnumber", "episodenumber"):
                w.set_max_length(9)
        return w

    @staticmethod
    def wget(w):
        if isinstance(w, Gtk.TextView):
            b = w.get_buffer()
            return b.get_text(b.get_start_iter(), b.get_end_iter(), False)
        if isinstance(w, Gtk.CheckButton):
            return "1" if w.get_active() else ""
        if isinstance(w, Gtk.ComboBoxText) and not w.get_has_entry():
            return str(w.get_active()) if w.get_active() > 0 else ""
        if isinstance(w, Gtk.ComboBox):
            return w.get_child().get_text()
        return w.get_text()

    @staticmethod
    def wset(w, v, placeholder=None):
        if isinstance(w, Gtk.TextView):
            w.get_buffer().set_text(v)
        elif isinstance(w, Gtk.CheckButton):
            w.set_active(bool(v))
        elif isinstance(w, Gtk.ComboBoxText) and not w.get_has_entry():
            w.set_active(int(v) if v.isdigit() else 0)
        else:
            entry = w.get_child() if isinstance(w, Gtk.ComboBox) else w
            entry.set_text(v)
            entry.set_placeholder_text(placeholder or "")

    def watch(self, key, w):
        def changed(*_):
            if not self.loading:
                self.fields[key][0].set_active(True)
        if isinstance(w, Gtk.TextView):
            w.get_buffer().connect("changed", changed)
        elif isinstance(w, Gtk.CheckButton):
            w.connect("toggled", changed)
        else:
            w.connect("changed", changed)
        focus_target = w.get_child() if isinstance(w, Gtk.ComboBox) and w.get_has_entry() else w
        focus_target.connect("focus-in-event", lambda *_: setattr(self, "last_focus", key) or False)

    def field_grid(self, keys):
        g = Gtk.Grid(column_spacing=8, row_spacing=4)
        for i, key in enumerate(keys):
            chk = Gtk.CheckButton(label=tc.LABELS[key], tooltip_text="Checked fields are written on save")
            w = self.make_widget(key)
            w.set_hexpand(True)
            self.fields[key] = (chk, w)
            self.watch(key, w)
            g.attach(chk, 0, i, 1, 1)
            g.attach(w, 1, i, 1, 1)
        return g

    def lyrics_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, border_width=8)
        chk = Gtk.CheckButton(label="Lyrics")
        tv = self.make_widget("lyrics")
        self.fields["lyrics"] = (chk, tv)
        self.watch("lyrics", tv)
        box.pack_start(chk, False, False, 0)
        sw = Gtk.ScrolledWindow(vexpand=True, shadow_type=Gtk.ShadowType.IN)
        sw.add(tv)
        box.pack_start(sw, True, True, 0)
        row = Gtk.Box(spacing=6)
        for label, cb in (("Load from Text File", self.lyrics_load), ("Save to Text File", self.lyrics_save),
                          ("Clear", lambda: self.wset(tv, "")),
                          ("Remove Leading Spaces",
                           lambda: self.wset(tv, "\n".join(l.lstrip() for l in self.wget(tv).split("\n"))))):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            row.pack_start(b, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def cover_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.cover_chk = Gtk.CheckButton(label="Cover art")
        box.pack_start(self.cover_chk, False, False, 0)
        ev = Gtk.EventBox()
        frame = Gtk.Frame()
        self.cover_img = Gtk.Image()
        self.cover_img.set_size_request(220, 220)
        frame.add(self.cover_img)
        ev.add(frame)
        ev.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        ev.drag_dest_add_uri_targets()
        ev.connect("drag-data-received", self.on_cover_drop)
        box.pack_start(ev, False, False, 0)
        self.cover_info = Gtk.Label(label="", wrap=True, max_width_chars=30)
        box.pack_start(self.cover_info, False, False, 0)
        grid = Gtk.Grid(column_spacing=4, row_spacing=4, column_homogeneous=True)
        for i, (label, cb) in enumerate((("Add…", self.cover_add), ("Remove", self.cover_remove),
                                         ("Save to File…", self.cover_save), ("Search Web", self.cover_search))):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            grid.attach(b, i % 2, i // 2, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.info_label = Gtk.Label(xalign=0, yalign=0, selectable=True)
        info = Gtk.Frame(label="File information")
        self.info_label.set_margin_start(6)
        self.info_label.set_margin_bottom(6)
        info.add(self.info_label)
        box.pack_start(info, False, False, 0)
        return box

    # ---- load / save
    def targets(self):
        return [self.tracks[self.index]] if self.single else self.tracks

    def load(self):
        self.loading = True
        ts = self.targets()
        if self.single:
            t = ts[0]
            self.set_title(f"Tag Editor [{t.name}]")
            stem, ext = os.path.splitext(t.name)
            self.name_entry.set_text(stem)
            self.ext_label.set_text(ext)
        else:
            self.set_title(f"Tag Editor [{len(ts)} File(s)]")
        for key, (chk, w) in self.fields.items():
            values = {t[key] for t in ts}
            chk.set_active(False)
            if len(values) == 1:
                self.wset(w, values.pop())
            else:
                self.wset(w, "", "<different values>")
        first = ts[0].covers
        same = all(t.covers == first for t in ts)
        self.covers = list(first) if same else []
        self.cover_chk.set_active(False)
        self.show_cover("" if same else "Selected files have different cover art")
        t = ts[0]
        i = t.info
        lines = [f"Duration: {fmt_time(i.get('length'))}",
                 f"Bitrate: {i.get('bitrate', 0)} kbps {i.get('vbr', '')}",
                 f"Channels: {i.get('channels', '')}", f"Samplerate: {i.get('sample_rate', '')} Hz",
                 f"Bit depth: {i.get('bits') or '-'}", f"Encoder: {i.get('encoder') or '-'}",
                 f"Tag: {t.family or '-'}", f"Size: {fmt_size(t.size)}"]
        self.info_label.set_text("\n".join(lines) if self.single else f"{len(ts)} files\n" + "\n".join(lines[3:4]))
        self.loading = False

    def show_cover(self, note=""):
        if self.covers:
            mime, data = self.covers[0]
            pb, dims = pixbuf(data, 220)
            self.cover_img.set_from_pixbuf(pb)
            extra = f" (+{len(self.covers) - 1} more)" if len(self.covers) > 1 else ""
            self.cover_info.set_text(f"Picture: {dims[0]} x {dims[1]}, {mime.split('/')[-1].upper()}, "
                                     f"{fmt_size(len(data))}{extra}" if dims else "Unreadable image")
        else:
            self.cover_img.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            self.cover_info.set_text(note or "Drag and drop cover art here")

    def changes(self):
        out = {k: self.wget(w) for k, (chk, w) in self.fields.items() if chk.get_active()}
        case = case_value(self.case_chk, self.case_combo)
        if case is not None:
            delims, keep = self.cfg.get("CapitalizeConfiguration"), self.cfg.list("KeepStrCase")
            out = {k: v if k in NO_CASE else tc.apply_case(v, case, delims, keep) for k, v in out.items()}
        return out

    def save(self):
        changes = self.changes()
        covers = self.covers if self.cover_chk.get_active() else None
        for t in self.targets():
            while True:
                try:
                    self.app.save_track(t, changes, covers)
                    break
                except tc.TagError as e:
                    d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                          text=str(e))
                    d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Skip", Gtk.ResponseType.REJECT,
                                  "_Retry", Gtk.ResponseType.OK)
                    r = d.run()
                    d.destroy()
                    if r == Gtk.ResponseType.OK:
                        continue
                    if r == Gtk.ResponseType.REJECT:
                        break
                    self.app.refresh_rows()
                    return False
        if self.single and not self.rename_file():
            return False
        self.app.refresh_rows()
        return True

    def rename_file(self):
        t = self.tracks[self.index]
        stem = self.name_entry.get_text().strip()
        if stem == os.path.splitext(t.name)[0]:
            return True
        new = os.path.join(os.path.dirname(t.path), stem + os.path.splitext(t.name)[1])
        if not stem or "/" in stem:
            error(self, "Invalid file name.")
            return False
        if os.path.exists(new):
            error(self, f"Can't rename file {t.name}", "A file with this name already exists.")
            return False
        try:
            os.rename(t.path, new)
        except OSError as e:
            error(self, f"Can't rename file or folder {t.name}. Error message: {e.strerror}")
            return False
        t.path = new
        return True

    def on_save(self):
        if self.save():
            self.on_close()
            self.destroy()

    def save_and_move(self, d):
        if not self.save():
            return
        new = self.index + d
        if not 0 <= new < len(self.tracks):
            message(self, "No more files!")
            self.load()
            return
        self.index = new
        self.app.focus_track(self.tracks[new])
        self.load()
        self.focus_field(self.last_focus if self.cfg.bool("SaveFocusedFieldForSaveNext") else "title")

    def focus_field(self, key):
        w = self.fields[key][1]
        (w.get_child() if isinstance(w, Gtk.ComboBox) and w.get_has_entry() else w).grab_focus()

    def remove_tag(self):
        if not confirm(self, "Remove tag from file?" if self.single else
                       f"Do you really want to remove tags from {len(self.tracks)} files (cannot be undone)?"):
            return
        for t in self.targets():
            try:
                tc.remove_tags(t.path, self.cfg)
                t.reload()
            except tc.TagError as e:
                error(self, str(e))
        self.app.refresh_rows()
        self.load()

    def on_close(self):
        w, h = self.get_size()
        self.cfg.set("TagEditorWidth", w)
        self.cfg.set("TagEditorHeight", h)
        self.cfg.set("FormTagEditorRadioGroupCaseChecked", self.case_chk.get_active())
        self.cfg.set("FormTagEditorRadioGroupCaseItemIndex", self.case_combo.get_active())

    def on_key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.on_close()
            self.destroy()
            return True
        return False

    def check_all(self, state):
        for chk, _ in self.fields.values():
            chk.set_active(state)

    def clear_all(self):
        for chk, w in self.fields.values():
            self.wset(w, "")
            chk.set_active(True)

    def play(self):
        self.app.play([t.path for t in self.targets()])

    # ---- lyrics + cover
    def lyrics_load(self):
        path = self.app.choose_file(self, "Load Lyrics", [("Text files", ["*.txt", "*.lrc"])])
        if path:
            with open(path, encoding="utf-8", errors="replace") as fh:
                self.wset(self.fields["lyrics"][1], fh.read())

    def lyrics_save(self):
        path = self.app.choose_file(self, "Save Lyrics", [("Text files", ["*.txt"])], save=True,
                                    name=os.path.splitext(self.targets()[0].name)[0] + ".txt")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.wget(self.fields["lyrics"][1]))

    def set_cover_from_file(self, path):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            error(self, str(e))
            return
        mime = tc.image_mime(data)
        if self.cfg.bool("TagWriteOptionsResizeBigCoverArtWhenAdding"):
            mime, data = tc.resize_cover(mime, data, self.cfg.int("TagWriteOptionsMaxCoverArtDimension"),
                                         self.cfg.int("TagWriteOptionsCoverArtJpegQuality"),
                                         self.cfg.bool("TagWriteOptionsConvertCoverArtToJpeg"))
        self.covers = [(mime, data)]
        self.cover_chk.set_active(True)
        self.show_cover()

    def cover_add(self):
        start = (self.cfg.get("CoverArtFilesPath") if self.cfg.bool("SavePathWhenAddingCoverArt") else "") \
            or os.path.dirname(self.targets()[0].path)
        path = self.app.choose_file(self, "Add Cover Art",
                                    [("Images", ["*" + e for e in IMAGE_EXT] + ["*" + e.upper() for e in IMAGE_EXT])],
                                    folder=start)
        if path:
            self.cfg.set("CoverArtFilesPath", os.path.dirname(path))
            self.set_cover_from_file(path)

    def on_cover_drop(self, widget, ctx, x, y, data, info, time):
        uris = data.get_uris()
        if uris:
            self.set_cover_from_file(Gio.File.new_for_uri(uris[0]).get_path())

    def cover_remove(self):
        self.covers = []
        self.cover_chk.set_active(True)
        self.show_cover()

    def cover_save(self):
        if not self.covers:
            return
        mime, data = self.covers[0]
        ext = {"image/png": ".png", "image/gif": ".gif", "image/bmp": ".bmp"}.get(mime, ".jpg")
        path = self.app.choose_file(self, "Save Cover Art", [("Images", ["*" + ext])], save=True, name="cover" + ext,
                                    folder=os.path.dirname(self.targets()[0].path))
        if path:
            with open(path, "wb") as fh:
                fh.write(data)

    def cover_search(self):
        t = self.targets()[0]
        q = GLib.uri_escape_string(f"{t['artist']} {t['album']}".strip(), None, False)
        Gtk.show_uri_on_window(self, self.cfg.get("CoverSearchUrl").replace("%s", q), Gdk.CURRENT_TIME)


# ---------------------------------------------------------------- previews

class RenamePreview(Gtk.Dialog):
    def __init__(self, app, entries, mask, case, spaces):
        super().__init__(title="Rename Files Preview", transient_for=app, modal=True)
        self.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Rename", Gtk.ResponseType.OK)
        self.set_default_size(900, 520)
        self.app, self.entries, self.case, self.spaces = app, entries, case, spaces
        box = self.get_content_area()
        box.set_border_width(8)
        box.set_spacing(6)
        self.mask = mru_combo(app.cfg.list("RenameFileNameMaskItems"), mask)
        self.mask.connect("changed", lambda *_: self.update())
        row = Gtk.Box(spacing=6)
        row.pack_start(Gtk.Label(label="File name mask:"), False, False, 0)
        row.pack_start(self.mask, True, True, 0)
        row.pack_start(mask_button(self.edit_mask), False, False, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(mask_legend(tc.VAR_HELP), False, False, 0)
        self.store = Gtk.ListStore(str, str, str)
        view = Gtk.TreeView(model=self.store)
        for i, title in enumerate(("Before rename", "After rename")):
            r = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.MIDDLE)
            c = Gtk.TreeViewColumn(title, r, text=i, foreground=2)
            c.set_resizable(True)
            c.set_expand(True)
            view.append_column(c)
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(view)
        box.pack_start(sw, True, True, 0)
        self.status = Gtk.Label(xalign=0)
        box.pack_start(self.status, False, False, 0)
        self.update()
        self.show_all()

    def edit_mask(self):
        r = self.app.mask_editor(self.mask.get_active_text(), "RenameFileNameMaskItems", rename=True, parent=self)
        if r:
            self.mask.get_child().set_text(r)

    def update(self):
        self.items = tc.plan_renames(self.entries, self.mask.get_active_text(), self.app.cfg, self.case, self.spaces)
        self.store.clear()
        base = self.app.folder
        for it in self.items:
            after = os.path.relpath(it.new_path, base) if it.new_path else it.error
            self.store.append([os.path.relpath(it.old_path, base), after, "#c0392b" if it.error else None])
        bad = sum(1 for it in self.items if it.error)
        self.status.set_text(f"Total {len(self.items)} tracks" + (f", {bad} with errors" if bad else ""))

    def get(self):
        r = self.run()
        out = self.mask.get_active_text() if r == Gtk.ResponseType.OK else None
        self.destroy()
        return out


class FTTPreview(Gtk.Dialog):
    def __init__(self, app, tracks, mask, transform):
        super().__init__(title="Get Tags from File Name Preview", transient_for=app, modal=True)
        self.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Write Tags", Gtk.ResponseType.OK)
        self.set_default_size(900, 520)
        self.app, self.tracks, self.transform = app, tracks, transform
        box = self.get_content_area()
        box.set_border_width(8)
        box.set_spacing(6)
        self.mask = mru_combo(app.cfg.list("FileToTagFileNameMaskItems"), mask)
        self.mask.connect("changed", lambda *_: self.update())
        row = Gtk.Box(spacing=6)
        row.pack_start(Gtk.Label(label="File name mask:"), False, False, 0)
        row.pack_start(self.mask, True, True, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(mask_legend(tc.FTT_HELP), False, False, 0)
        self.sw = Gtk.ScrolledWindow(vexpand=True)
        box.pack_start(self.sw, True, True, 0)
        self.update()
        self.show_all()

    def update(self):
        mask = self.mask.get_active_text()
        parsed = [self.transform(tc.parse_filename(mask, t.path)) for t in self.tracks]
        keys = [k for k, _ in tc.FIELDS if any(p and k in p for p in parsed)]
        store = Gtk.ListStore(*[str] * (len(keys) + 1))
        for t, p in zip(self.tracks, parsed):
            store.append([t.name] + [(p or {}).get(k, "" if p else "— no match —") for k in keys])
        view = Gtk.TreeView(model=store)
        for i, title in enumerate(["File"] + [tc.LABELS[k] for k in keys]):
            c = Gtk.TreeViewColumn(title, Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END), text=i)
            c.set_resizable(True)
            c.set_min_width(80)
            c.set_expand(True)
            view.append_column(c)
        if self.sw.get_child():
            self.sw.remove(self.sw.get_child())
        self.sw.add(view)
        view.show()

    def get(self):
        r = self.run()
        out = self.mask.get_active_text() if r == Gtk.ResponseType.OK else None
        self.destroy()
        return out


# ---------------------------------------------------------------- options (spec §14)

OPTION_PAGES = [
    ("General", [
        ("check", "DontCloseTagEdOnErr", "Don't close tag editor on error"),
        ("check", "DontChangeFileTime", "Don't change file timestamp when saving tags"),
        ("check", "DontRenameFileIfFileWithThisNameExists", "Don't rename file if a file with the new name exists"),
        ("entry", "CapitalizeConfiguration", "Capitalize after these characters"),
        ("check", "UseSpecificStartFolder", "Always start in a specific folder"),
        ("folder", "SpecificStartFolder", "Start folder"),
        ("entry", "Mp3Player", "Music player command (blank = system default)"),
        ("entry", "CoverSearchUrl", "Cover art search URL (%s = artist album)"),
    ]),
    ("File List", [
        ("radio", "FileListSelection", "After opening a folder select",
         ["Files in the root folder", "All files", "Nothing"]),
        ("check", "UseColors", "Highlight files without tags"),
        ("color", "RedColor", "Colour for files without tags"),
        ("check", "UseLowBitrateColor", "Highlight low bitrate files"),
        ("spin", "LowBitrate", "Low bitrate threshold (kbps)", 8, 1411),
        ("color", "LowBitrateColor", "Low bitrate colour"),
        ("check", "HideFilesExtension", "Hide file extensions"),
        ("check", "ShowAnyImageIfNoFolderJpg", "Show any image when folder.jpg is missing"),
    ]),
    ("Tags", [
        ("check", "CopyArtistToAlbumArtistOnSave", "Copy Artist to Album Artist on save if Album Artist is empty"),
        ("check", "AlwaysCopyArtistToAlbumArtistOnSave", "Always copy Artist to Album Artist on save"),
        ("check", "AutoSetAlbumArtistToVariousArtists",
         "Set Album Artist to \"Various Artists\" for compilations"),
        ("check", "WriteUnicodeToID3", "ID3v2: always write Unicode"),
        ("check", "WriteAnsiToID3EvenIfUnicode", "ID3v2: force ASCII/Latin-1"),
        ("radio", "AfterSavingID3v2", "On saving ID3v2 tag",
         ["Always create/update ID3v1", "Update ID3v1 only if it exists", "Delete ID3v1"]),
        ("check", "DeleteID3v1WhenDeletingID3v2", "Delete ID3v1 when removing ID3v2"),
        ("check", "MarkCoverArtAsFront", "Mark ID3v2 cover art as \"Cover (front)\""),
        ("check", "AutoTrimYearFrame", "Trim timestamp years to yyyy"),
        ("check", "SaveVorbisTrackDiscTotalsToItOwnFrames", "Vorbis: write TRACKTOTAL / DISCTOTAL separately"),
        ("check", "AlwaysRemoveLeadingZeroFromDiscValue", "Always remove leading zero from disc # when renaming"),
        ("check", "DontAddLeadingZeroToTrackNr", "No leading zero when auto-filling track #"),
        ("check", "SaveFocusedFieldForSaveNext", "Keep focused field on Save and Next"),
        ("check", "UncheckAllChecksInMTagEditor", "Uncheck multi tag editor fields after saving"),
        ("check", "RemoveTagQuestion", "Ask before removing tags"),
        ("entry", "AdditionalGenresList", "Additional genres (comma separated)"),
    ]),
    ("Cover Art", [
        ("check", "TagWriteOptionsResizeBigCoverArtWhenAdding", "Resize big cover art when adding"),
        ("spin", "TagWriteOptionsMaxCoverArtDimension", "Maximum dimension (px)", 50, 5000),
        ("spin", "TagWriteOptionsCoverArtJpegQuality", "JPEG quality", 10, 100),
        ("check", "TagWriteOptionsConvertCoverArtToJpeg", "Convert resized cover art to JPEG"),
        ("check", "SavePathWhenAddingCoverArt", "Remember the folder used when adding cover art"),
    ]),
]


class OptionsDialog(Gtk.Dialog):
    def __init__(self, app):
        super().__init__(title="Options", transient_for=app, modal=True)
        self.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_OK", Gtk.ResponseType.OK)
        self.set_default_size(640, 520)
        self.cfg = app.cfg
        self.readers = []
        nb = Gtk.Notebook()
        self.get_content_area().pack_start(nb, True, True, 0)
        for title, rows in OPTION_PAGES:
            g = Gtk.Grid(column_spacing=10, row_spacing=6, border_width=10)
            for i, row in enumerate(rows):
                self.add_row(g, i, *row)
            sw = Gtk.ScrolledWindow()
            sw.add(g)
            nb.append_page(sw, Gtk.Label(label=title))
        nb.append_page(self.strings_page(), Gtk.Label(label="File Rename Strings"))
        self.show_all()

    def add_row(self, g, i, kind, key, label, *extra):
        cfg = self.cfg
        if kind == "check":
            w = Gtk.CheckButton(label=label, active=cfg.bool(key))
            g.attach(w, 0, i, 2, 1)
            self.readers.append((key, w.get_active))
            return
        g.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
        if kind == "entry":
            w = Gtk.Entry(text=cfg.get(key), hexpand=True)
            read = w.get_text
        elif kind == "folder":
            w = Gtk.FileChooserButton(action=Gtk.FileChooserAction.SELECT_FOLDER)
            if os.path.isdir(cfg.get(key)):
                w.set_filename(cfg.get(key))
            read = lambda w=w: w.get_filename() or ""
        elif kind == "spin":
            w = Gtk.SpinButton.new_with_range(extra[0], extra[1], 1)
            w.set_value(cfg.int(key))
            read = lambda w=w: w.get_value_as_int()
        elif kind == "color":
            rgba = Gdk.RGBA()
            rgba.parse(colorref(cfg.get(key)))
            w = Gtk.ColorButton(rgba=rgba)
            read = lambda w=w: to_colorref(w.get_rgba())
        elif kind == "radio":
            w = Gtk.ComboBoxText()
            for o in extra[0]:
                w.append_text(o)
            w.set_active(cfg.int(key))
            read = w.get_active
        g.attach(w, 1, i, 1, 1)
        self.readers.append((key, read))

    def strings_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, border_width=10)
        box.pack_start(Gtk.Label(label="Word replacements applied when renaming (case-insensitive):", xalign=0),
                       False, False, 0)
        self.repl = Gtk.ListStore(str, str)
        for a, b in self.cfg.replacements():
            self.repl.append([a, b])
        view = Gtk.TreeView(model=self.repl)
        for i, title in enumerate(("Find", "Replace with")):
            r = Gtk.CellRendererText(editable=True)
            r.connect("edited", lambda _r, path, text, i=i: self.repl.set_value(self.repl.get_iter(path), i, text))
            c = Gtk.TreeViewColumn(title, r, text=i)
            c.set_expand(True)
            view.append_column(c)
        sw = Gtk.ScrolledWindow(vexpand=True, shadow_type=Gtk.ShadowType.IN)
        sw.add(view)
        box.pack_start(sw, True, True, 0)
        row = Gtk.Box(spacing=6)
        for label, cb in (("Add", lambda: self.repl.append(["", ""])),
                          ("Delete Row", lambda: (lambda it: it and self.repl.remove(it))(
                              view.get_selection().get_selected()[1])),
                          ("Clear All", self.repl.clear)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            row.pack_start(b, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.keep = Gtk.Entry(text=self.cfg.get("KeepStrCase"))
        box.pack_start(grid_rows([("Always keep case of (comma separated):", self.keep)]), False, False, 0)
        return box

    def get(self):
        ok = self.run() == Gtk.ResponseType.OK
        if ok:
            for key, read in self.readers:
                self.cfg.set(key, read())
            rows = [(r[0], r[1]) for r in self.repl if r[0]]
            self.cfg.set_list("ReplStrBefore", [a for a, _ in rows])
            self.cfg.set_list("ReplStrAfter", [b for _, b in rows])
            self.cfg.set("KeepStrCase", self.keep.get_text())
            self.cfg.save()
        self.destroy()
        return ok


# ---------------------------------------------------------------- main window

class App(Gtk.Window):
    def __init__(self, start=None):
        super().__init__(title=APP)
        self.cfg = tc.Settings()
        self.set_default_size(self.cfg.int("WindowWidth"), self.cfg.int("WindowHeight"))
        self.folder = ""
        self.hidden = set()
        self.filter_pat = ""
        self.undo = []
        self.clipboard = None
        self.sel_widgets = []
        self.scan_stop = None
        self.accel = Gtk.AccelGroup()
        self.add_accel_group(self.accel)

        self.cover_icons = (picture_icon(False), picture_icon(True))  # [has cover art]
        if not self.cfg.bool("CoverColumnAdded"):  # show the new column once in older configs
            cols = [c for c in self.cfg.list("FileListColumns") if c != "cover"]
            cols.insert(cols.index("name") + 1 if "name" in cols else 0, "cover")
            self.cfg.set_list("FileListColumns", cols)
            self.cfg.set("CoverColumnAdded", 1)
        self.store = Gtk.ListStore(object)
        self.columns = self.column_defs()
        for i, c in enumerate(self.columns):
            self.store.set_sort_func(i, self.sort_func, c[3])
        self.fmodel = self.store.filter_new()
        self.fmodel.set_visible_func(self.row_visible)

        self.main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(self.main)
        self.main.pack_start(self.build_menu(), False, False, 0)
        self.main.pack_start(self.build_toolbar(), False, False, 0)
        self.main.pack_start(self.build_filterbar(), False, False, 0)
        self.hpaned = Gtk.Paned()
        self.main.pack_start(self.hpaned, True, True, 0)
        self.folders_panel = self.build_folder_tree()
        self.hpaned.pack1(self.folders_panel, False, False)
        self.vpaned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.hpaned.pack2(self.vpaned, True, False)
        self.vpaned.pack1(self.build_file_list(), True, False)
        self.tools = self.build_tools()
        self.vpaned.pack2(self.tools, False, False)
        self.hpaned.set_position(self.cfg.int("FoldersWidth") or 260)
        self.main.pack_start(self.build_statusbar(), False, False, 0)

        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", self.on_quit)
        self.show_all()
        self.progress.hide()
        self.vpaned.set_position(self.cfg.int("FileListHeight") or max(200, self.get_size()[1] - 420))
        self.folders_panel.set_visible(self.cfg.bool("FoldersVisible"))
        self.tools.set_visible(self.cfg.bool("ToolsBarVisible"))
        self.update_selection()
        self._save_timer = None
        self.cfg.on_change = self.schedule_save
        if start is None:
            start = self.cfg.get("SpecificStartFolder") if self.cfg.bool("UseSpecificStartFolder") \
                else self.cfg.get("CurrentFolder")
        GLib.idle_add(self.open_folder, tc.nearest_existing_folder(start))

    def schedule_save(self):
        """Debounced write of the settings file, so preferences persist without a clean quit."""
        if self._save_timer:
            GLib.source_remove(self._save_timer)

        def fire():
            self._save_timer = None
            self.cfg.save()
            return False

        self._save_timer = GLib.timeout_add(500, fire)

    # ---------------- columns (spec §3.2)
    def column_defs(self):
        f = lambda k: (lambda t: t[k])
        info = lambda k: (lambda t: t.info.get(k, 0) or 0)
        cols = [
            ("name", "File Name", self.display_name, lambda t: t.name.lower(), 260),
            ("subfolder", "Subfolder", self.subfolder, lambda t: self.subfolder(t).lower(), 140),
            ("ext", "File Ext.", lambda t: os.path.splitext(t.name)[1].lstrip("."),
             lambda t: os.path.splitext(t.name)[1].lower(), 60),
        ]
        for key, label in tc.FIELDS:
            if key in ("lyrics", "rating", "compilation"):
                continue
            sk = (lambda t, k=key: num_key(t[k])) if key in ("track", "disc", "bpm", "year") else \
                (lambda t, k=key: t[k].lower())
            cols.append((key, label, f(key), sk, 60 if key in ("year", "track", "disc", "bpm") else 150))
        cols += [
            ("rating", "Rating", lambda t: "★" * int(t["rating"] or 0), lambda t: t["rating"], 80),
            ("compilation", "Compilation", lambda t: "✔" if t["compilation"] else "", lambda t: t["compilation"], 50),
            ("lyrics", "Lyrics", lambda t: "✔" if t["lyrics"] else "", lambda t: bool(t["lyrics"]), 50),
            ("time", "Time", lambda t: fmt_time(t.info.get("length")), info("length"), 60),
            ("bitrate", "Bitrate", lambda t: f"{t.info.get('bitrate', '')}{' VBR' if t.info.get('vbr') else ''}",
             info("bitrate"), 70),
            ("hz", "Hz", lambda t: str(t.info.get("sample_rate", "")), info("sample_rate"), 60),
            ("channels", "Channels", lambda t: str(t.info.get("channels", "")), info("channels"), 60),
            ("bits", "Bit Depth", lambda t: str(t.info.get("bits") or ""), info("bits"), 60),
            ("encoder", "Encoder", lambda t: t.info.get("encoder", ""), lambda t: t.info.get("encoder", ""), 100),
            ("size", "Size", lambda t: fmt_size(t.size), lambda t: t.size, 80),
            ("modified", "Date Modified", lambda t: GLib.DateTime.new_from_unix_local(int(t.mtime)).format("%Y-%m-%d %H:%M"),
             lambda t: t.mtime, 130),
            ("cover", "Cover Art", lambda t: self.cover_icons[bool(t.covers)], lambda t: bool(t.covers), 70),
            ("coversize", "Cover Size", lambda t: fmt_size(len(t.covers[0][1])) if t.covers else "",
             lambda t: len(t.covers[0][1]) if t.covers else 0, 80),
            ("tagver", "Tag Version", lambda t: t.error or t.family or "", lambda t: t.family or "", 80),
        ]
        return cols

    def display_name(self, t):
        return os.path.splitext(t.name)[0] if self.cfg.bool("HideFilesExtension") else t.name

    def subfolder(self, t):
        rel = os.path.relpath(os.path.dirname(t.path), self.folder) if self.folder else ""
        return "" if rel == "." else rel

    def sort_func(self, model, a, b, key):
        ta, tb = model[a][0], model[b][0]
        if ta is None or tb is None:
            return 0
        ka, kb = key(ta), key(tb)
        try:
            return (ka > kb) - (ka < kb)
        except TypeError:
            return (str(ka) > str(kb)) - (str(ka) < str(kb))

    # ---------------- UI construction
    def menu_items(self, menu, items):
        for it in items:
            if it is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            label, accel, cb, flags = (it + ("",))[:4]
            if "c" in flags:
                w = Gtk.CheckMenuItem.new_with_mnemonic(label)
                w.set_active(cb(None))
                w.connect("toggled", lambda m, cb=cb: cb(m.get_active()))
            else:
                w = Gtk.MenuItem.new_with_mnemonic(label)
                w.connect("activate", lambda _m, cb=cb: cb())
            for a in filter(None, accel.split("|")):
                key, mods = Gtk.accelerator_parse(a)
                w.add_accelerator("activate", self.accel, key, mods, Gtk.AccelFlags.VISIBLE)
            if "s" in flags:
                self.sel_widgets.append(w)
            menu.append(w)

    def submenu(self, bar, title, items):
        m = Gtk.Menu()
        mi = Gtk.MenuItem.new_with_mnemonic(title)
        mi.set_submenu(m)
        bar.append(mi)
        if items:
            self.menu_items(m, items)
        return m

    def build_menu(self):
        bar = Gtk.MenuBar()
        self.submenu(bar, "_File", [
            ("Edit File _Tag", "F5", self.edit_tag, "s"),
            ("Edit Tag in _Selected Files", "<Control>F5", self.edit_tags_multi, "s"),
            ("Edit File _Name", "F2|F6", self.edit_file_name, "s"),
            ("Rename _Folder", "F7", self.rename_folder, "s"),
            ("_Remove Tags", "<Control>Delete", self.remove_tags, "s"), None,
            ("_Play File", "<Control>p", lambda: self.play([self.focused().path]) if self.focused() else None, "s"),
            ("Play Selected Files", "<Control>s", lambda: self.play([t.path for t in self.selected()]), "s"), None,
            ("Move File _Up", "<Alt>Up", lambda: self.move_rows(-1), "s"),
            ("Move File _Down", "<Alt>Down", lambda: self.move_rows(1), "s"), None,
            ("_Copy Tag", "<Shift><Control>c", self.copy_tag, "s"),
            ("Pa_ste Tag", "<Shift><Control>v", self.paste_tag, "s"),
            ("Paste Tag to _All Selected Files", "<Control><Alt>v", self.paste_tag_selected, "s"), None,
            ("Move to _Trash", "Delete", self.trash_files, "s"), None,
            ("_Quit", "<Control>q", self.on_quit),
        ])
        self.submenu(bar, "_Edit", [
            ("Select _All", "<Control>a", lambda: self.view.get_selection().select_all()),
            ("Select _None", "<Control>n", lambda: self.view.get_selection().unselect_all()),
            ("_Invert Selection", "<Control>i", self.invert_selection), None,
            ("_Copy to Folder…", "", lambda: self.copy_move(False), "s"),
            ("_Move to Folder…", "", lambda: self.copy_move(True), "s"), None,
            ("_Find…", "<Control>f", lambda: self.filter_entry.grab_focus()),
        ])
        view = self.submenu(bar, "_View", [
            ("_Tools Bar", "F8", lambda v: self.cfg.bool("ToolsBarVisible") if v is None else self.toggle_panel("tools", v), "c"),
            ("_Folders", "<Control>F8", lambda v: self.cfg.bool("FoldersVisible") if v is None else self.toggle_panel("folders", v), "c"),
            None,
            ("Search _Subfolders", "", lambda v: self.cfg.bool("UseSubfolders") if v is None else self.set_subfolders(v), "c"),
            ("_Random Sort Order", "", self.shuffle),
            None,
            ("Refresh File _List", "<Control>r", self.refresh),
            ("Open _Folder…", "<Control>o", self.choose_folder),
        ])
        cols = Gtk.Menu()
        mi = Gtk.MenuItem(label="File List Columns")
        mi.set_submenu(cols)
        view.append(mi)
        shown = self.cfg.list("FileListColumns")
        for i, c in enumerate(self.columns):
            w = Gtk.CheckMenuItem(label=c[1], active=c[0] in shown)
            w.connect("toggled", lambda m, i=i: self.view.get_column(self.col_index(i)).set_visible(m.get_active()))
            cols.append(w)
        self.submenu(bar, "_Tools", [
            ("_Rename Files", "F4", lambda: self.show_tool(0)),
            ("_Multi File Tag Editor", "<Control>F4", lambda: self.show_tool(1)),
            ("_Get Tags from File Name", "<Shift>F4", lambda: self.show_tool(2)), None,
            ("Auto Fill _Track #", "<Control>t", self.autofill_track, "s"), None,
            ("Add Folder Cover Art to Files Tag", "", self.add_folder_cover_to_files, "s"),
            ("Extract Folder Cover Art from Files Tag", "", self.extract_folder_cover, "s"),
            ("Delete Folder Cover Art from Selected Folders", "", self.delete_folder_covers, "s"),
            ("Resize Files Cover Art…", "", self.resize_file_covers, "s"),
        ])
        self.submenu(bar, "_Options", [("_Options…", "<Control><Alt>o", self.options)])
        self.favorites_menu = self.submenu(bar, "F_avorites", [])
        self.rebuild_favorites()
        self.submenu(bar, "_Help", [
            ("_Keyboard Shortcuts", "", lambda: show_list(self, "Keyboard Shortcuts", SHORTCUTS.split("\n"))),
            ("_About", "", self.about),
        ])
        return bar

    def col_index(self, def_index):
        for i, c in enumerate(self.view.get_columns()):
            if c.def_index == def_index:
                return i

    def build_toolbar(self):
        tb = Gtk.Toolbar()
        tb.set_style(Gtk.ToolbarStyle.ICONS)
        for icon, tip, cb, needs_sel in (
                ("go-up", "Up one folder", lambda: self.open_folder(os.path.dirname(self.folder)), False),
                ("view-refresh", "Refresh file list (Ctrl+R)", self.refresh, False), (None,) * 4,
                ("document-properties", "Edit file tag (F5)", self.edit_tag, True),
                ("edit-select-all", "Edit tag in selected files (Ctrl+F5)", self.edit_tags_multi, True),
                ("edit-rename", "Rename folder (F7)", self.rename_folder, True),
                ("edit-clear", "Remove tags (Ctrl+Del)", self.remove_tags, True), (None,) * 4,
                ("media-playback-start", "Play selected files", lambda: self.play([t.path for t in self.selected()]), True),
                ("preferences-system", "Options (Ctrl+Alt+O)", self.options, False)):
            if icon is None:
                tb.insert(Gtk.SeparatorToolItem(), -1)
                continue
            b = Gtk.ToolButton(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda _b, cb=cb: cb())
            if needs_sel:
                self.sel_widgets.append(b)
            tb.insert(b, -1)
        self.sub_toggle = Gtk.ToggleToolButton(icon_name="folder-saved-search", tooltip_text="Search subfolders",
                                               active=self.cfg.bool("UseSubfolders"))
        self.sub_toggle.connect("toggled", lambda b: self.set_subfolders(b.get_active()))
        tb.insert(self.sub_toggle, -1)
        item = Gtk.ToolItem()
        item.set_expand(True)
        self.address = mru_combo(self.cfg.list("HistoryList"), "")
        self.address.get_child().connect("activate", lambda e: self.open_folder(os.path.expanduser(e.get_text())))
        self.address.connect("changed", self.on_address_pick)
        box = Gtk.Box(spacing=4, margin_start=8)
        box.pack_start(Gtk.Label(label="Address:"), False, False, 0)
        box.pack_start(self.address, True, True, 0)
        item.add(box)
        tb.insert(item, -1)
        return tb

    def on_address_pick(self, combo):
        if combo.get_active() >= 0:
            self.open_folder(combo.get_active_text())

    def build_filterbar(self):
        box = Gtk.Box(spacing=6, border_width=4)
        box.pack_start(Gtk.Label(label="Filter by:"), False, False, 0)
        self.filter_by = Gtk.ComboBoxText()
        self.filter_keys = ["name"] + [k for k, _ in tc.FIELDS]
        self.filter_by.append_text("File Name")
        for _, label in tc.FIELDS:
            self.filter_by.append_text(label)
        self.filter_by.set_active(min(self.cfg.int("FilterByItemIndex"), len(self.filter_keys) - 1))
        box.pack_start(self.filter_by, False, False, 0)
        self.filter_entry = Gtk.SearchEntry(placeholder_text="b*, *text*, *text")
        self.filter_entry.connect("activate", lambda *_: self.apply_filter())
        box.pack_start(self.filter_entry, True, True, 0)
        for label, cb in (("Filter!", self.apply_filter), ("Hide Selected Files", self.hide_selected),
                          ("Show All", self.show_all_files)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            box.pack_start(b, False, False, 0)
        return box

    def build_folder_tree(self):
        self.tree_store = Gtk.TreeStore(str, str)
        roots = [("Home", os.path.expanduser("~")), ("File System", "/")]
        media = f"/media/{os.environ.get('USER', '')}"
        if os.path.isdir(media):
            roots.append(("Removable Media", media))
        for name, path in roots:
            it = self.tree_store.append(None, [name, path])
            self.tree_store.append(it, ["", None])
        self.tree = Gtk.TreeView(model=self.tree_store, headers_visible=False)
        col = Gtk.TreeViewColumn("Folder")
        ri = Gtk.CellRendererPixbuf(icon_name="folder")
        rt = Gtk.CellRendererText()
        col.pack_start(ri, False)
        col.pack_start(rt, True)
        col.add_attribute(rt, "text", 0)
        self.tree.append_column(col)
        self.tree.connect("row-expanded", self.on_tree_expand)
        self.tree.get_selection().connect("changed", self.on_tree_select)
        self._tree_timer = None
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(180, -1)
        sw.add(self.tree)
        return sw

    def on_tree_expand(self, view, it, path):
        child = self.tree_store.iter_children(it)
        if child is None or self.tree_store[child][1] is not None:
            return
        parent = self.tree_store[it][1]
        try:
            names = sorted((n for n in os.listdir(parent) if not n.startswith(".")
                            and os.path.isdir(os.path.join(parent, n))), key=str.lower)
        except OSError:
            names = []
        for n in names:
            sub = self.tree_store.append(it, [n, os.path.join(parent, n)])
            self.tree_store.append(sub, ["", None])
        self.tree_store.remove(child)

    def on_tree_select(self, sel):
        model, it = sel.get_selected()
        if not it or not model[it][1]:
            return
        path = model[it][1]
        if self._tree_timer:
            GLib.source_remove(self._tree_timer)

        def fire():
            self._tree_timer = None
            return self.open_folder(path)

        self._tree_timer = GLib.timeout_add(250, fire)  # debounce keyboard navigation

    def build_file_list(self):
        self.view = Gtk.TreeView(model=self.fmodel, rubber_banding=True, enable_search=False)
        self.view.set_fixed_height_mode(True)
        shown = self.cfg.list("FileListColumns")
        order = {k: i for i, k in enumerate(shown)}
        widths = dict(x.split(":", 1) for x in self.cfg.list("FileListColumnWidths") if ":" in x)
        defs = sorted(range(len(self.columns)), key=lambda i: order.get(self.columns[i][0], 999 + i))
        for i in defs:
            cid, title, getter, _, width = self.columns[i]
            r = Gtk.CellRendererPixbuf(xalign=0.5) if cid == "cover" else \
                Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title)
            col.def_index = i
            col.pack_start(r, True)
            col.set_cell_data_func(r, self.cell_func, getter)
            col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            col.set_fixed_width(int(widths.get(cid, width)))
            col.set_resizable(True)
            col.set_reorderable(True)
            col.set_clickable(True)
            col.set_visible(cid in shown)
            col.connect("clicked", self.on_header, i)
            self.view.append_column(col)
        sel = self.view.get_selection()
        sel.set_mode(Gtk.SelectionMode.MULTIPLE)
        sel.connect("changed", lambda *_: self.update_selection())
        self.view.connect("cursor-changed", lambda *_: self.update_focus())
        self.view.connect("row-activated", lambda *_: self.edit_tag())
        self.view.connect("button-press-event", self.on_list_button)
        sw = Gtk.ScrolledWindow()
        sw.add(self.view)
        return sw

    def cell_func(self, col, r, model, it, getter):
        t = model[it][0]
        if t is None:
            return
        is_icon = isinstance(r, Gtk.CellRendererPixbuf)
        r.set_property("pixbuf" if is_icon else "text", getter(t))
        bg = None
        if self.cfg.bool("UseColors") and not t.has_tag:
            bg = colorref(self.cfg.get("RedColor"))
        elif self.cfg.bool("UseLowBitrateColor") and 0 < t.info.get("bitrate", 0) < self.cfg.int("LowBitrate"):
            bg = colorref(self.cfg.get("LowBitrateColor"))
        r.set_property("cell-background", bg)
        if not is_icon:
            r.set_property("foreground", "#000000" if bg else None)

    def on_header(self, col, i):
        cur, order = self.store.get_sort_column_id()
        order = Gtk.SortType.DESCENDING if cur == i and order == Gtk.SortType.ASCENDING else Gtk.SortType.ASCENDING
        self.store.set_sort_column_id(i, order)
        for c in self.view.get_columns():
            c.set_sort_indicator(c is col)
        col.set_sort_order(order)

    def on_list_button(self, view, ev):
        # Right-click keeps the existing multi-selection and opens a context menu.
        if ev.button != 3:
            return False
        hit = view.get_path_at_pos(int(ev.x), int(ev.y))
        sel = view.get_selection()
        if hit and not sel.path_is_selected(hit[0]):
            view.set_cursor(hit[0], None, False)
        m = Gtk.Menu()
        for label, cb in (("Edit File Tag (F5)", self.edit_tag), ("Edit Tag in Selected Files", self.edit_tags_multi),
                          ("Edit File Name", self.edit_file_name), ("Rename Folder", self.rename_folder),
                          (None, None), ("Play", lambda: self.play([t.path for t in self.selected()])),
                          ("Open Containing Folder", self.open_containing), (None, None),
                          ("Copy Tag", self.copy_tag), ("Paste Tag to All Selected", self.paste_tag_selected),
                          (None, None), ("Remove Tags", self.remove_tags), ("Move to Trash", self.trash_files)):
            if label is None:
                m.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _m, cb=cb: cb())
            m.append(mi)
        m.show_all()
        m.popup_at_pointer(ev)
        return True

    def build_tools(self):
        box = Gtk.Box(spacing=8, border_width=6)
        self.notebook = Gtk.Notebook()
        self.notebook.append_page(self.build_rename_tab(), Gtk.Label(label="Rename Files (F4)"))
        self.notebook.append_page(self.build_multi_tab(), Gtk.Label(label="Multi File Tag Editor (Ctrl+F4)"))
        self.notebook.append_page(self.build_ftt_tab(), Gtk.Label(label="Get Tags from File Name (Shift+F4)"))
        box.pack_start(self.notebook, True, True, 0)
        box.pack_start(self.build_cover_panels(), False, False, 0)
        return box

    def build_rename_tab(self):
        cfg = self.cfg
        self.rn_mask = mru_combo(cfg.list("RenameFileNameMaskItems"), cfg.get("RenameFileNameMask"))
        self.rn_result = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, selectable=True)
        case, self.rn_case, self.rn_case_combo = case_row(cfg, "RenameFilesCaseChecked", "RenameFilesCaseItemIndex")
        self.rn_spaces = Gtk.CheckButton(label="Replace underscores with spaces", active=cfg.bool("ReplaceSpacesChecked"))
        for w, sig in ((self.rn_mask, "changed"), (self.rn_case, "toggled"), (self.rn_case_combo, "changed"),
                       (self.rn_spaces, "toggled")):
            w.connect(sig, lambda *_: self.update_previews())
        mask_row = Gtk.Box(spacing=4)
        mask_row.pack_start(self.rn_mask, True, True, 0)
        mask_row.pack_start(mask_button(lambda: self.edit_mask_for(self.rn_mask, "RenameFileNameMaskItems", True)),
                            False, False, 0)
        opts = Gtk.Box(spacing=12)
        opts.pack_start(case, False, False, 0)
        opts.pack_start(self.rn_spaces, False, False, 0)
        btns = self.button_row([("Preview…", lambda: self.rename_files(True), True),
                                ("Rename", lambda: self.rename_files(False), True),
                                ("Undo", self.undo_rename, False)])
        self.undo_btn = btns.get_children()[-1]
        self.undo_btn.set_sensitive(False)
        return grid_rows([("File name mask:", mask_row), (None, mask_legend(tc.VAR_HELP)), ("Result:", self.rn_result),
                          (None, opts), (None, btns)])

    def build_multi_tab(self):
        self.mt_fields = {}
        rows = []
        for key in ("artist", "album", "year", "genre", "comment"):
            chk = Gtk.CheckButton(label=tc.LABELS[key])
            if key == "genre":
                w = Gtk.ComboBoxText.new_with_entry()
                for g in genre_list(self.cfg):
                    w.append_text(g)
                entry = w.get_child()
            else:
                w = entry = Gtk.Entry()
            entry.connect("changed", lambda _e, c=chk: c.set_active(True))
            self.mt_fields[key] = (chk, entry)
            box = Gtk.Box(spacing=6)
            chk.set_size_request(110, -1)
            box.pack_start(chk, False, False, 0)
            box.pack_start(w, True, True, 0)
            rows.append((None, box))
        case, self.mt_case, self.mt_case_combo = case_row(self.cfg, "TagEditorCaseChecked", "TagEditorCaseItemIndex")
        rows.append((None, case))
        rows.append((None, self.button_row([
            ("Save Tags", self.multi_save, True), ("Remove Tags", self.remove_tags, True),
            ("Copy from Focused File", self.multi_copy_focused, True),
            ("Edit All Supported Tag Fields…", self.edit_tags_multi, True)])))
        g = grid_rows(rows)
        sw = Gtk.ScrolledWindow()
        sw.add(g)
        return sw

    def build_ftt_tab(self):
        cfg = self.cfg
        self.ftt_mask = mru_combo(cfg.list("FileToTagFileNameMaskItems"), cfg.get("FileToTagFileNameMask"))
        self.ftt_result = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, selectable=True)
        case, self.ftt_case, self.ftt_case_combo = case_row(cfg, "FileToTagCaseChecked", "FileToTagCaseItemIndex")
        self.ftt_unders = Gtk.CheckButton(label="Replace underscores with spaces",
                                          active=cfg.bool("ReplaceUnderscoresChecked"))
        for w, sig in ((self.ftt_mask, "changed"), (self.ftt_case, "toggled"), (self.ftt_case_combo, "changed"),
                       (self.ftt_unders, "toggled")):
            w.connect(sig, lambda *_: self.update_previews())
        mask_row = Gtk.Box(spacing=4)
        mask_row.pack_start(self.ftt_mask, True, True, 0)
        mask_row.pack_start(mask_button(lambda: self.edit_mask_for(self.ftt_mask, "FileToTagFileNameMaskItems", False)),
                            False, False, 0)
        opts = Gtk.Box(spacing=12)
        opts.pack_start(case, False, False, 0)
        opts.pack_start(self.ftt_unders, False, False, 0)
        btns = self.button_row([("Preview…", lambda: self.ftt_write(True), True),
                                ("Write Tags", lambda: self.ftt_write(False), True)])
        return grid_rows([("File name mask:", mask_row), (None, mask_legend(tc.FTT_HELP)), ("Preview:", self.ftt_result),
                          (None, opts), (None, btns)])

    def build_cover_panels(self):
        box = Gtk.Box(spacing=8)
        self.fc_img, self.fc_info = Gtk.Image(), Gtk.Label()
        self.tc_img, self.tc_info = Gtk.Image(), Gtk.Label()
        for title, img, info, buttons in (
                ("Folder cover art", self.fc_img, self.fc_info,
                 [("Set as folder.jpg", self.panel_folder_image_to_jpg), ("Delete", self.panel_delete_folder_jpg)]),
                ("File cover art", self.tc_img, self.tc_info, [("Set as Folder Cover Art", self.panel_tag_to_folder)])):
            frame = Gtk.Frame(label=title)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, border_width=4)
            img.set_size_request(140, 140)
            ev = Gtk.EventBox()
            ev.add(img)
            if img is self.fc_img:
                ev.connect("button-press-event", self.on_folder_cover_click)
            v.pack_start(ev, False, False, 0)
            info.set_max_width_chars(20)
            info.set_ellipsize(Pango.EllipsizeMode.END)
            v.pack_start(info, False, False, 0)
            for label, cb in buttons:
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda _b, cb=cb: cb())
                v.pack_start(b, False, False, 0)
            frame.add(v)
            box.pack_start(frame, False, False, 0)
        return box

    def build_statusbar(self):
        box = Gtk.Box(spacing=12, border_width=3)
        self.status = Gtk.Label(xalign=0)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_size_request(240, -1)
        box.pack_start(self.status, True, True, 4)
        box.pack_end(self.progress, False, False, 4)
        return box

    def button_row(self, buttons):
        row = Gtk.Box(spacing=6)
        for label, cb, needs_sel in buttons:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=cb: cb())
            if needs_sel:
                self.sel_widgets.append(b)
            row.pack_start(b, False, False, 0)
        return row

    # ---------------- keyboard: text fields get keys before menu accelerators
    def on_key(self, win, ev):
        if isinstance(self.get_focus(), (Gtk.Entry, Gtk.TextView)):
            return self.propagate_key_event(ev)
        return False

    # ---------------- folder loading (spec §3.1)
    def open_folder(self, folder):
        folder = os.path.abspath(folder or "/")
        if not os.path.isdir(folder):
            error(self, f"Folder not found: {folder}")
            return False
        if self.scan_stop:
            self.scan_stop.set()
        self.folder = folder
        self.set_title(f"{APP} — {folder}")
        self.cfg.set("CurrentFolder", folder)
        push_mru(self.cfg, "HistoryList", folder, 30)
        self.address.handler_block_by_func(self.on_address_pick)
        self.address.remove_all()
        for h in self.cfg.list("HistoryList"):
            self.address.append_text(h)
        self.address.get_child().set_text(folder)
        self.address.handler_unblock_by_func(self.on_address_pick)
        stop = self.scan_stop = threading.Event()
        recursive = self.cfg.bool("UseSubfolders")
        dlg = {}

        def show_dialog():
            if stop.is_set() or dlg.get("done"):
                return False
            d = Gtk.Dialog(title="Searching for Music Files...", transient_for=self, modal=True)
            d.add_button("_Stop", Gtk.ResponseType.CANCEL)
            d.connect("response", lambda *_: stop.set())
            d.set_default_size(420, -1)
            dlg["label"] = Gtk.Label(label="Searching for files...", xalign=0)
            dlg["bar"] = Gtk.ProgressBar()
            area = d.get_content_area()
            area.set_border_width(10)
            area.set_spacing(8)
            area.pack_start(dlg["label"], False, False, 0)
            area.pack_start(dlg["bar"], False, False, 0)
            d.show_all()
            dlg["d"] = d
            return False

        def progress(text, frac):
            if "label" in dlg:
                dlg["label"].set_text(text)
                dlg["bar"].set_fraction(frac) if frac is not None else dlg["bar"].pulse()
            return False

        def done(tracks):
            dlg["done"] = True
            if "d" in dlg:
                dlg["d"].destroy()
            if stop is not self.scan_stop:
                return False
            self.scan_stop = None
            self.fill(tracks)
            return False

        def work():
            paths = []
            for p in tc.scan_folder(folder, recursive, stop.is_set):
                paths.append(p)
                if len(paths) % 200 == 0:
                    GLib.idle_add(progress, f"Found {len(paths)} files...", None)
            tracks = []
            for i, p in enumerate(paths, 1):
                if stop.is_set():
                    break
                tracks.append(tc.Track(p))
                if i % 20 == 0:
                    GLib.idle_add(progress, f"Read {i} of {len(paths)} files...", i / len(paths))
            GLib.idle_add(done, tracks)

        GLib.timeout_add(400, show_dialog)
        threading.Thread(target=work, daemon=True).start()
        self.update_focus()
        return False

    def fill(self, tracks):
        sort = self.store.get_sort_column_id()
        self.view.set_model(None)
        self.store.set_sort_column_id(Gtk.TREE_SORTABLE_UNSORTED_SORT_COLUMN_ID, Gtk.SortType.ASCENDING)
        self.store.clear()
        self.hidden.clear()
        for t in tracks:
            self.store.append([t])
        if sort[0] is not None and sort[0] >= 0:
            self.store.set_sort_column_id(*sort)
        self.view.set_model(self.fmodel)
        policy = self.cfg.int("FileListSelection")
        sel = self.view.get_selection()
        if policy == 1:
            sel.select_all()
        elif policy == 0:
            for row in self.fmodel:
                if os.path.dirname(row[0].path) == self.folder:
                    sel.select_iter(row.iter)
        if len(self.fmodel):
            self.view.set_cursor(Gtk.TreePath(0), None, False)
            if policy == 2:
                sel.unselect_all()
        self.update_selection()
        self.update_focus()

    def refresh(self):
        self.open_folder(self.folder)

    def choose_folder(self):
        d = Gtk.FileChooserDialog(title="Open Folder", transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Open", Gtk.ResponseType.OK)
        d.set_current_folder(self.folder or os.path.expanduser("~"))
        if d.run() == Gtk.ResponseType.OK:
            path = d.get_filename()
            d.destroy()
            self.open_folder(path)
        else:
            d.destroy()

    def set_subfolders(self, on):
        if on == self.cfg.bool("UseSubfolders"):
            return
        self.cfg.set("UseSubfolders", on)
        self.sub_toggle.set_active(on)
        self.refresh()

    def toggle_panel(self, which, visible):
        if which == "tools":
            self.cfg.set("ToolsBarVisible", visible)
            self.tools.set_visible(visible)
        else:
            self.cfg.set("FoldersVisible", visible)
            self.folders_panel.set_visible(visible)

    def show_tool(self, page):
        self.tools.set_visible(True)
        self.cfg.set("ToolsBarVisible", 1)
        self.notebook.set_current_page(page)

    # ---------------- favorites
    def rebuild_favorites(self):
        m = self.favorites_menu
        for c in m.get_children():
            m.remove(c)
        for label, cb in (("Add Current Folder to Favorites", self.fav_add),
                          ("Remove Current Folder from Favorites", self.fav_remove)):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _m, cb=cb: cb())
            m.append(mi)
        favs = self.cfg.list("FavoritesList")
        if favs:
            m.append(Gtk.SeparatorMenuItem())
        for f in favs:
            mi = Gtk.MenuItem(label=f)
            mi.connect("activate", lambda _m, f=f: self.open_folder(f))
            m.append(mi)
        m.show_all()

    def fav_add(self):
        favs = self.cfg.list("FavoritesList")
        if self.folder not in favs:
            self.cfg.set_list("FavoritesList", favs + [self.folder])
            self.rebuild_favorites()

    def fav_remove(self):
        self.cfg.set_list("FavoritesList", [f for f in self.cfg.list("FavoritesList") if f != self.folder])
        self.rebuild_favorites()

    # ---------------- selection model (spec §3.3)
    def selected(self):
        model, paths = self.view.get_selection().get_selected_rows()
        return [model[p][0] for p in paths]

    def focused(self):
        path, _ = self.view.get_cursor()
        if path is not None and len(self.fmodel) > path.get_indices()[0]:
            return self.fmodel[path][0]
        sel = self.selected()
        return sel[0] if sel else None

    def visible(self):
        return [row[0] for row in self.fmodel]

    def positions(self):
        return {t: i for i, t in enumerate(self.visible(), 1)}

    def focus_track(self, track):
        for row in self.fmodel:
            if row[0] is track:
                self.view.set_cursor(row.path, None, False)
                return

    def invert_selection(self):
        sel = self.view.get_selection()
        for row in self.fmodel:
            (sel.unselect_iter if sel.iter_is_selected(row.iter) else sel.select_iter)(row.iter)

    def update_selection(self):
        sel = self.selected()
        for w in self.sel_widgets:
            w.set_sensitive(bool(sel))
        vis = self.visible()
        total = sum(t.info.get("length", 0) for t in vis)
        chosen = sum(t.info.get("length", 0) for t in sel)
        self.status.set_text(f"Total files: {len(vis)}    Selected files: {len(sel)}    "
                             f"Total time: {fmt_time(total)}    Selected time: {fmt_time(chosen)}")
        self.update_previews()

    def update_focus(self):
        self.update_previews()
        self.update_cover_panels()

    def update_previews(self):
        t = self.focused()
        if not t:
            self.rn_result.set_text("")
            self.ftt_result.set_text("")
            return
        mask = self.rn_mask.get_active_text() or ""
        pos = self.positions().get(t, 1)
        name = tc.expand_mask(mask, t, pos, self.cfg, case_value(self.rn_case, self.rn_case_combo),
                              self.rn_spaces.get_active()) if mask else ""
        self.rn_result.set_text(name + os.path.splitext(t.name)[1] if name else "")
        parsed = self.ftt_transform(tc.parse_filename(self.ftt_mask.get_active_text() or "", t.path))
        self.ftt_result.set_text("  |  ".join(f"{tc.LABELS[k]}: {v}" for k, v in parsed.items())
                                 if parsed else "File name doesn't match the mask")

    # ---------------- filter (spec §3.6)
    def row_visible(self, model, it, _data):
        t = model[it][0]
        if t is None or t in self.hidden:
            return False
        if not self.filter_pat:
            return True
        key = self.filter_keys[self.filter_by.get_active()]
        value = self.display_name(t) if key == "name" else t[key]
        return fnmatch.fnmatchcase(value.lower(), self.filter_pat)

    def apply_filter(self):
        text = self.filter_entry.get_text().strip().lower()
        self.filter_pat = text if not text or any(c in text for c in "*?") else f"*{text}*"
        self.cfg.set("FilterByItemIndex", self.filter_by.get_active())
        self.fmodel.refilter()
        self.update_selection()

    def hide_selected(self):
        self.hidden.update(self.selected())
        self.fmodel.refilter()
        self.update_selection()

    def show_all_files(self):
        self.hidden.clear()
        self.filter_entry.set_text("")
        self.filter_pat = ""
        self.fmodel.refilter()
        self.update_selection()

    # ---------------- ordering
    def unsort(self):
        self.store.set_sort_column_id(Gtk.TREE_SORTABLE_UNSORTED_SORT_COLUMN_ID, Gtk.SortType.ASCENDING)
        for c in self.view.get_columns():
            c.set_sort_indicator(False)

    def move_rows(self, d):
        self.unsort()
        sel = self.view.get_selection()
        _, paths = sel.get_selected_rows()
        idx = [p.get_indices()[0] for p in paths]
        n = len(self.fmodel)
        if not idx or (d < 0 and idx[0] == 0) or (d > 0 and idx[-1] == n - 1):
            return
        for i in (idx if d < 0 else reversed(idx)):
            a = self.fmodel.convert_iter_to_child_iter(self.fmodel.get_iter(Gtk.TreePath(i)))
            b = self.fmodel.convert_iter_to_child_iter(self.fmodel.get_iter(Gtk.TreePath(i + d)))
            self.store.swap(a, b)
        self.update_previews()

    def shuffle(self):
        self.unsort()
        n = len(self.store)
        if n:
            self.store.reorder(random.sample(range(n), n))
        self.update_previews()

    # ---------------- writing helpers
    def save_track(self, t, changes, covers=None):
        new = tc.write_tags(t.path, changes, self.cfg, covers)
        t.__dict__.update(new.__dict__)

    def refresh_rows(self):
        for row in self.store:
            self.store.row_changed(row.path, row.iter)
        self.update_selection()
        self.update_focus()

    def run_batch(self, tracks, fn, title):
        """Run fn(track) over tracks with a progress bar; returns the error list."""
        errors = []
        self.main.set_sensitive(False)
        self.progress.show()
        self.progress.set_text(title)
        for i, t in enumerate(tracks, 1):
            try:
                fn(t)
            except (tc.TagError, OSError) as e:
                errors.append(f"{t.name}: {e}")
            self.progress.set_fraction(i / len(tracks))
            while Gtk.events_pending():
                Gtk.main_iteration()
        self.progress.hide()
        self.main.set_sensitive(True)
        self.refresh_rows()
        if errors:
            show_list(self, f"{len(errors)} error(s)", errors)
        return errors

    def ensure_selection(self):
        sel = self.selected()
        if not len(self.fmodel):
            error(self, "There are no files in the file list!")
        elif not sel:
            error(self, "Select files first.")
        return sel

    # ---------------- rename files (spec §4)
    def rename_files(self, preview):
        tracks = self.ensure_selection()
        if not tracks:
            return
        mask = (self.rn_mask.get_active_text() or "").strip()
        if not mask:
            error(self, "First fill in the File name mask!")
            return
        pos = self.positions()
        entries = [(pos.get(t, i), t) for i, t in enumerate(tracks, 1)]
        case = case_value(self.rn_case, self.rn_case_combo)
        spaces = self.rn_spaces.get_active()
        if preview:
            mask = RenamePreview(self, entries, mask, case, spaces).get()
            if not mask:
                return
            self.rn_mask.get_child().set_text(mask)
        items = tc.plan_renames(entries, mask, self.cfg, case, spaces)
        undo, errors = tc.execute_renames(items)
        push_mru(self.cfg, "RenameFileNameMaskItems", mask)
        self.cfg.set("RenameFileNameMask", mask)
        if undo:
            self.undo = undo
            self.undo_btn.set_sensitive(True)
        self.refresh_rows()
        self.status.set_text(f"Renamed {len(undo)} of {len(items)} files.")
        if errors:
            show_list(self, f"{len(errors)} file(s) not renamed", errors)
            failed = {it.track for it in items if it.error}
            if undo and failed and confirm(self, "Some files were not renamed. Select only the files not renamed?"):
                sel = self.view.get_selection()
                sel.unselect_all()
                for row in self.fmodel:
                    if row[0] in failed:
                        sel.select_iter(row.iter)

    def undo_rename(self):
        if not self.undo:
            return
        restored, errors = tc.undo_renames(self.undo)
        for row in self.store:
            if row[0].path in restored:
                row[0].path = restored[row[0].path]
        self.undo = []
        self.undo_btn.set_sensitive(False)
        self.refresh_rows()
        if errors:
            show_list(self, "Undo errors", errors)

    # ---------------- masks
    def mask_editor(self, mask, items_key, rename, parent=None):
        t = self.focused()

        def sample(m):
            if not t:
                return ""
            if rename:
                return tc.expand_mask(m, t, self.positions().get(t, 1), self.cfg) + os.path.splitext(t.name)[1]
            parsed = tc.parse_filename(m, t.path)
            return "  |  ".join(f"{tc.LABELS[k]}: {v}" for k, v in parsed.items()) if parsed else "no match"

        r = MaskEditor(parent or self, mask or "", self.cfg.list(items_key), sample,
                       tc.VAR_HELP if rename else tc.FTT_HELP).get()
        if r is None:
            return None
        self.cfg.set_list(items_key, r[1])
        return r[0]

    def edit_mask_for(self, combo, items_key, rename):
        r = self.mask_editor(combo.get_active_text(), items_key, rename)
        if r is not None:
            combo.remove_all()
            for m in self.cfg.list(items_key):
                combo.append_text(m)
            combo.get_child().set_text(r)

    # ---------------- multi file tag editor tab (spec §5)
    def multi_save(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        changes = {k: e.get_text() for k, (chk, e) in self.mt_fields.items() if chk.get_active()}
        case = case_value(self.mt_case, self.mt_case_combo)
        if case is not None:
            delims, keep = self.cfg.get("CapitalizeConfiguration"), self.cfg.list("KeepStrCase")
            changes = {k: v if k in NO_CASE else tc.apply_case(v, case, delims, keep) for k, v in changes.items()}
        self.run_batch(tracks, lambda t: self.save_track(t, changes), "Saving tags…")
        if self.cfg.bool("UncheckAllChecksInMTagEditor"):
            for chk, _ in self.mt_fields.values():
                chk.set_active(False)

    def multi_copy_focused(self):
        t = self.focused()
        if t:
            for k, (chk, e) in self.mt_fields.items():
                e.set_text(t[k])
                chk.set_active(False)

    def remove_tags(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        if self.cfg.bool("RemoveTagQuestion") and not confirm(
                self, "Do you really want to remove tags from all selected files (cannot be undone)?"):
            return
        self.run_batch(tracks, lambda t: (tc.remove_tags(t.path, self.cfg), t.reload()), "Removing tags…")

    # ---------------- get tags from file name (spec §7)
    def ftt_transform(self, parsed):
        if not parsed:
            return parsed
        case = case_value(self.ftt_case, self.ftt_case_combo)
        out = {}
        for k, v in parsed.items():
            if self.ftt_unders.get_active():
                v = v.replace("_", " ").strip()
            if case is not None and k not in NO_CASE:
                v = tc.apply_case(v, case, self.cfg.get("CapitalizeConfiguration"), self.cfg.list("KeepStrCase"))
            out[k] = v
        return out

    def ftt_write(self, preview):
        tracks = self.ensure_selection()
        if not tracks:
            return
        mask = (self.ftt_mask.get_active_text() or "").strip()
        if not mask:
            error(self, "First fill in the File name mask!")
            return
        if preview:
            mask = FTTPreview(self, tracks, mask, self.ftt_transform).get()
            if not mask:
                return
            self.ftt_mask.get_child().set_text(mask)
        push_mru(self.cfg, "FileToTagFileNameMaskItems", mask)
        self.cfg.set("FileToTagFileNameMask", mask)

        def write(t):
            parsed = self.ftt_transform(tc.parse_filename(mask, t.path))
            if not parsed:
                raise tc.TagError("file name doesn't match the mask")
            self.save_track(t, parsed)

        self.run_batch(tracks, write, "Writing tags…")

    # ---------------- dialogs on files
    def edit_tag(self):
        t = self.focused()
        if t:
            vis = self.visible()
            TagEditor(self, vis, vis.index(t), single=True)

    def edit_tags_multi(self):
        tracks = self.ensure_selection()
        if tracks:
            TagEditor(self, tracks, single=False)

    def edit_file_name(self):
        t = self.focused()
        if not t:
            return
        vis = self.visible()
        index = vis.index(t)
        d = Gtk.Dialog(title="Edit file name", transient_for=self, modal=True)
        d.add_buttons("Edit and _Prev", 1, "Edit and _Next", 2, "_Cancel", Gtk.ResponseType.CANCEL,
                      "_OK", Gtk.ResponseType.OK)
        d.set_default_size(560, -1)
        d.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry(max_length=252, activates_default=True)
        ext = Gtk.Label()
        row = Gtk.Box(spacing=6, border_width=10)
        row.pack_start(entry, True, True, 0)
        row.pack_start(ext, False, False, 0)
        d.get_content_area().pack_start(row, False, False, 0)

        def load():
            cur = vis[index]
            stem, e = os.path.splitext(cur.name)
            entry.set_text(stem)
            ext.set_text(e)
            self.focus_track(cur)

        entry.connect("changed", lambda e: d.set_title(
            f"Edit file name (full new length {len(os.path.join(os.path.dirname(vis[index].path), e.get_text() + ext.get_text()))})"))
        load()
        d.show_all()
        while True:
            r = d.run()
            if r not in (1, 2, Gtk.ResponseType.OK):
                break
            cur = vis[index]
            stem = entry.get_text().strip()
            if stem and stem != os.path.splitext(cur.name)[0]:
                new = os.path.join(os.path.dirname(cur.path), stem + ext.get_text())
                if "/" in stem or os.path.exists(new):
                    error(d, f"Can't rename file {cur.name}", "Invalid name or a file with this name already exists.")
                    continue
                try:
                    os.rename(cur.path, new)
                    cur.path = new
                    self.refresh_rows()
                except OSError as e:
                    error(d, f"Can't rename file or folder {cur.name}. Error message: {e.strerror}")
                    continue
            if r == Gtk.ResponseType.OK:
                break
            new_index = index + (1 if r == 2 else -1)
            if not 0 <= new_index < len(vis):
                message(d, "No more files!")
                continue
            index = new_index
            load()
        d.destroy()

    def rename_folder(self):
        t = self.focused()
        tracks = self.selected()
        if not t:
            return
        cfg = self.cfg
        d = Gtk.Dialog(title="Rename Folder", transient_for=self, modal=True)
        d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Rename", Gtk.ResponseType.OK)
        d.set_default_size(560, -1)
        mask = mru_combo(cfg.list("FolderNameMaskItems"), cfg.get("FolderNameMask"))
        result = Gtk.Label(xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        case, chk, combo = case_row(cfg, "FolderCaseChecked", "FolderCaseItemIndex")
        spaces = Gtk.CheckButton(label="Replace underscores with spaces")
        all_folders = Gtk.CheckButton(label="Apply to all highlighted folders that contain files")
        pos = self.positions()

        def name_for(track):
            return tc.expand_mask(mask.get_active_text() or "", track, pos.get(track, 1), cfg,
                                  case_value(chk, combo), spaces.get_active()).replace(os.sep, "-")

        def update(*_):
            result.set_text(name_for(t))

        for w, sig in ((mask, "changed"), (chk, "toggled"), (combo, "changed"), (spaces, "toggled")):
            w.connect(sig, update)
        mrow = Gtk.Box(spacing=4)
        mrow.pack_start(mask, True, True, 0)
        mrow.pack_start(mask_button(lambda: self.edit_mask_for(mask, "FolderNameMaskItems", True)), False, False, 0)
        g = grid_rows([("Current folder:", Gtk.Label(label=os.path.basename(os.path.dirname(t.path)), xalign=0)),
                       ("Folder name mask:", mrow), (None, mask_legend(tc.VAR_HELP)), ("Result:", result), (None, case), (None, spaces),
                       (None, all_folders)])
        g.set_border_width(10)
        d.get_content_area().pack_start(g, True, True, 0)
        update()
        d.show_all()
        if d.run() != Gtk.ResponseType.OK:
            d.destroy()
            return
        m = (mask.get_active_text() or "").strip()
        apply_all = all_folders.get_active()
        cfg.set("FolderCaseChecked", chk.get_active())
        cfg.set("FolderCaseItemIndex", combo.get_active())
        d.destroy()
        if not m:
            error(self, 'First fill in the "Folder name mask"!')
            return
        cfg.set("FolderNameMask", m)
        push_mru(cfg, "FolderNameMaskItems", m)
        groups = OrderedDict()
        for track in (sorted(tracks or [t], key=lambda x: pos.get(x, 0)) if apply_all else [t]):
            groups.setdefault(os.path.dirname(track.path), track)
        errors = []
        for folder in sorted(groups, key=lambda f: f.count(os.sep), reverse=True):  # deepest first
            track = groups[folder]
            if tc.mask_uses_tags(m) and not track.has_tag:
                errors.append(f"Cannot rename folder: {folder} - its files do not contain tag data.")
                continue
            name = name_for(track)
            new = os.path.join(os.path.dirname(folder), name)
            if not name or new == folder:
                continue
            if os.path.exists(new):
                errors.append(f"Cannot rename folder {folder}: {name} already exists.")
                continue
            try:
                os.rename(folder, new)
            except OSError as e:
                errors.append(f"Can't rename file or folder {folder}. Error message: {e.strerror}")
                continue
            for row in self.store:
                p = row[0].path
                if p.startswith(folder + os.sep):
                    row[0].path = new + p[len(folder):]
            if self.folder == folder or self.folder.startswith(folder + os.sep):
                self.folder = new + self.folder[len(folder):]
                self.address.get_child().set_text(self.folder)
                self.cfg.set("CurrentFolder", self.folder)
            favs = self.cfg.list("FavoritesList")
            if folder in favs:
                self.cfg.set_list("FavoritesList", [new if f == folder else f for f in favs])
                self.rebuild_favorites()
        self.undo = []
        self.undo_btn.set_sensitive(False)
        self.refresh_rows()
        if errors:
            show_list(self, "Folders not renamed", errors)

    # ---------------- copy / paste tag (spec §11)
    def copy_tag(self):
        t = self.focused()
        if t:
            self.clipboard = (dict(t.fields), list(t.covers))
            self.status.set_text(f"Tag copied from {t.name}")

    def paste_tag(self):
        t = self.focused()
        if t:
            self.paste_to([t], set())

    def paste_tag_selected(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        if not self.clipboard:
            error(self, "Nothing to paste!")
            return
        d = Gtk.Dialog(title=f"Paste Tag to {len(tracks)} File(s)", transient_for=self, modal=True)
        d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Paste", Gtk.ResponseType.OK)
        opts = [("PasteTagTitle", "title", "Don't paste Title"), ("PasteTagTrack", "track", "Don't paste Track #"),
                ("PasteTagBPM", "bpm", "Don't paste BPM"), ("PasteTagArtist", "artist", "Don't paste Artist"),
                ("PasteTagLyrics", "lyrics", "Don't paste Lyrics")]
        checks = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, border_width=10)
        for key, field, label in opts:
            c = Gtk.CheckButton(label=label, active=self.cfg.bool(key))
            box.pack_start(c, False, False, 0)
            checks.append((key, field, c))
        d.get_content_area().pack_start(box, False, False, 0)
        d.show_all()
        ok = d.run() == Gtk.ResponseType.OK
        excluded = set()
        for key, field, c in checks:
            self.cfg.set(key, c.get_active())
            if c.get_active():
                excluded.add(field)
        d.destroy()
        if ok:
            self.paste_to(tracks, excluded)

    def paste_to(self, tracks, excluded):
        if not self.clipboard:
            error(self, "Nothing to paste!")
            return
        fields, covers = self.clipboard
        changes = {k: fields.get(k, "") for k, _ in tc.FIELDS if k not in excluded}
        self.run_batch(tracks, lambda t: self.save_track(t, changes, covers), "Pasting tag…")

    # ---------------- auto fill track #
    def autofill_track(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        pos = self.positions()
        ordered = sorted(tracks, key=lambda t: pos.get(t, 0))
        fmt = "{}" if self.cfg.bool("DontAddLeadingZeroToTrackNr") else "{:02d}"
        numbers = {t: fmt.format(i) for i, t in enumerate(ordered, 1)}
        self.run_batch(ordered, lambda t: self.save_track(t, {"track": numbers[t]}), "Numbering…")

    # ---------------- cover art tools (spec §12)
    def folders_of(self, tracks):
        out = OrderedDict()
        for t in tracks:
            out.setdefault(os.path.dirname(t.path), []).append(t)
        return out

    def ask_overwrite(self, text, label):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
        c = Gtk.CheckButton(label=label)
        d.get_message_area().pack_start(c, False, False, 0)
        c.show()
        ok = d.run() == Gtk.ResponseType.OK
        d.destroy()
        return ok, c.get_active()

    def add_folder_cover_to_files(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        ok, overwrite = self.ask_overwrite("Embed folder.jpg into the tag of each selected file?",
                                           "Overwrite existing cover art")
        if not ok:
            return
        added = [0]

        def add(t):
            jpg = find_folder_jpg(os.path.dirname(t.path))
            if not jpg or (t.covers and not overwrite):
                return
            with open(jpg, "rb") as fh:
                data = fh.read()
            self.save_track(t, {}, [(tc.image_mime(data), data)])
            added[0] += 1

        self.run_batch(tracks, add, "Adding cover art…")
        message(self, f"Cover art was added to {added[0]} files from {len(tracks)} processed files!")

    def extract_folder_cover(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        ok, overwrite = self.ask_overwrite("Save the cover art of the files as folder.jpg in each folder?",
                                           "Overwrite existing folder.jpg")
        if not ok:
            return
        saved = 0
        for folder, ts in self.folders_of(tracks).items():
            src = next((t for t in ts if t.covers), None)
            existing = find_folder_jpg(folder)
            if not src or (existing and not overwrite):
                continue
            try:
                with open(existing or os.path.join(folder, "folder.jpg"), "wb") as fh:
                    fh.write(as_jpeg(*src.covers[0]))
                saved += 1
            except OSError as e:
                error(self, str(e))
        self.update_cover_panels()
        message(self, f'Saved {saved} "folder.jpg" files')

    def delete_folder_covers(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        jpgs = [j for j in (find_folder_jpg(f) for f in self.folders_of(tracks)) if j]
        if not jpgs or not confirm(self, f'Move {len(jpgs)} "folder.jpg" file(s) to trash?'):
            return
        n = 0
        for j in jpgs:
            try:
                Gio.File.new_for_path(j).trash(None)
                n += 1
            except GLib.Error as e:
                error(self, e.message)
        self.update_cover_panels()
        message(self, f'Deleted {n} "folder.jpg" files')

    def resize_file_covers(self):
        tracks = self.ensure_selection()
        if not tracks:
            return
        cfg = self.cfg
        d = Gtk.Dialog(title="Resize Cover Art", transient_for=self, modal=True)
        d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Resize", Gtk.ResponseType.OK)
        dim = Gtk.SpinButton.new_with_range(50, 5000, 10)
        dim.set_value(cfg.int("CovertArtDimensionValue"))
        q = Gtk.SpinButton.new_with_range(10, 100, 1)
        q.set_value(cfg.int("CovertArtJpegQualityValue"))
        jpg = Gtk.CheckButton(label="Convert to JPEG", active=cfg.bool("CovertArtToJpegChecked"))
        g = grid_rows([("Maximum dimension:", dim), ("JPEG quality:", q), (None, jpg)])
        g.set_border_width(10)
        d.get_content_area().pack_start(g, False, False, 0)
        d.show_all()
        ok = d.run() == Gtk.ResponseType.OK
        cfg.set("CovertArtDimensionValue", dim.get_value_as_int())
        cfg.set("CovertArtJpegQualityValue", q.get_value_as_int())
        cfg.set("CovertArtToJpegChecked", jpg.get_active())
        d.destroy()
        if not ok:
            return
        resized = [0]

        def resize(t):
            if not t.covers:
                return
            new = [tc.resize_cover(m, data, dim.get_value_as_int(), q.get_value_as_int(), jpg.get_active())
                   for m, data in t.covers]
            if new != t.covers:
                self.save_track(t, {}, new)
                resized[0] += 1

        self.run_batch(tracks, resize, "Resizing cover art…")
        message(self, f"Resized {resized[0]} files cover art from {len(tracks)} processed files.")

    # ---------------- cover panels
    def panel_folder(self):
        t = self.focused()
        return os.path.dirname(t.path) if t else self.folder

    def update_cover_panels(self):
        folder = self.panel_folder()
        img = find_folder_jpg(folder, self.cfg.bool("ShowAnyImageIfNoFolderJpg")) if folder else None
        self.panel_image = img
        if img:
            try:
                with open(img, "rb") as fh:
                    data = fh.read()
                pb, dims = pixbuf(data, 140)
            except OSError:
                pb, dims = None, None
            self.fc_img.set_from_pixbuf(pb)
            self.fc_info.set_text(f"{os.path.basename(img)} {dims[0]}x{dims[1]}" if dims else os.path.basename(img))
        else:
            self.fc_img.set_from_icon_name("folder-pictures", Gtk.IconSize.DIALOG)
            self.fc_info.set_text("No folder.jpg")
        t = self.focused()
        if t and t.covers:
            pb, dims = pixbuf(t.covers[0][1], 140)
            self.tc_img.set_from_pixbuf(pb)
            self.tc_info.set_text(f"{dims[0]}x{dims[1]} {fmt_size(len(t.covers[0][1]))}" if dims else "")
        else:
            self.tc_img.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            self.tc_info.set_text("No cover art")

    def on_folder_cover_click(self, w, ev):
        if ev.type == Gdk.EventType._2BUTTON_PRESS and getattr(self, "panel_image", None):
            Gtk.show_uri_on_window(self, Gio.File.new_for_path(self.panel_image).get_uri(), Gdk.CURRENT_TIME)

    def panel_folder_image_to_jpg(self):
        img = getattr(self, "panel_image", None)
        if not img or os.path.basename(img).lower() == "folder.jpg":
            return
        with open(img, "rb") as fh:
            data = fh.read()
        with open(os.path.join(os.path.dirname(img), "folder.jpg"), "wb") as fh:
            fh.write(as_jpeg(tc.image_mime(data), data))
        self.update_cover_panels()

    def panel_delete_folder_jpg(self):
        img = find_folder_jpg(self.panel_folder() or "")
        if img and confirm(self, f"Move {img} to trash?"):
            Gio.File.new_for_path(img).trash(None)
            self.update_cover_panels()

    def panel_tag_to_folder(self):
        t = self.focused()
        if not t or not t.covers:
            return
        folder = os.path.dirname(t.path)
        existing = find_folder_jpg(folder)
        if existing and not confirm(self, "folder.jpg already exists. Overwrite it?"):
            return
        with open(existing or os.path.join(folder, "folder.jpg"), "wb") as fh:
            fh.write(as_jpeg(*t.covers[0]))
        self.update_cover_panels()

    # ---------------- file operations
    def play(self, paths):
        if not paths:
            return
        player = self.cfg.get("Mp3Player").strip()
        try:
            if player:
                subprocess.Popen(player.split() + paths)
                return
            ctype, _ = Gio.content_type_guess(paths[0], None)
            app = Gio.AppInfo.get_default_for_type(ctype, False)
            if app is None:
                error(self, "No music player is associated with this file type.",
                      "Set a player in Options → General.")
                return
            files = [Gio.File.new_for_path(p) for p in paths]
            app.launch(files if app.supports_files() or len(files) == 1 else files[:1], None)
        except (OSError, GLib.Error) as e:
            error(self, "Cannot start the music player.", str(e))

    def open_containing(self):
        t = self.focused()
        if t:
            Gtk.show_uri_on_window(self, Gio.File.new_for_path(os.path.dirname(t.path)).get_uri(), Gdk.CURRENT_TIME)

    def trash_files(self):
        tracks = self.ensure_selection()
        if not tracks or not confirm(self, f"Move {len(tracks)} file(s) to trash?"):
            return
        gone = set()

        def trash(t):
            try:
                Gio.File.new_for_path(t.path).trash(None)
            except GLib.Error as e:
                raise OSError(e.message)
            gone.add(t)

        self.run_batch(tracks, trash, "Moving to trash…")
        self.remove_rows(gone)

    def remove_rows(self, tracks):
        it = self.store.get_iter_first()
        while it:
            if self.store[it][0] in tracks:
                if not self.store.remove(it):  # remove() advances `it` to the next row
                    break
            else:
                it = self.store.iter_next(it)
        self.update_selection()

    def copy_move(self, move):
        tracks = self.ensure_selection()
        if not tracks:
            return
        d = Gtk.FileChooserDialog(title="Move to Folder" if move else "Copy to Folder", transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Select", Gtk.ResponseType.OK)
        d.set_current_folder(self.folder)
        target = d.get_filename() if d.run() == Gtk.ResponseType.OK else None
        d.destroy()
        if not target:
            return

        def op(t):
            dest = os.path.join(target, t.name)
            if os.path.exists(dest):
                raise OSError(f"{dest} already exists")
            if move:
                shutil.move(t.path, dest)
                t.path = dest
            else:
                shutil.copy2(t.path, dest)

        self.run_batch(tracks, op, "Moving…" if move else "Copying…")

    def choose_file(self, parent, title, patterns, save=False, name=None, folder=None):
        d = Gtk.FileChooserDialog(title=title, transient_for=parent,
                                  action=Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN)
        d.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save" if save else "_Open", Gtk.ResponseType.OK)
        d.set_do_overwrite_confirmation(True)
        for label, globs in patterns:
            f = Gtk.FileFilter()
            f.set_name(label)
            for g in globs:
                f.add_pattern(g)
            d.add_filter(f)
        if folder and os.path.isdir(folder):
            d.set_current_folder(folder)
        if name:
            d.set_current_name(name)
        path = d.get_filename() if d.run() == Gtk.ResponseType.OK else None
        d.destroy()
        return path

    # ---------------- options / about / quit
    def options(self):
        if OptionsDialog(self).get():
            self.view.queue_draw()
            self.refresh_rows()

    def about(self):
        d = Gtk.AboutDialog(transient_for=self, modal=True, program_name=APP,
                            comments="Music tag editor and file renamer.",
                            logo=GdkPixbuf.Pixbuf.new_from_file_at_size(os.path.join(LOGO, "retag-icon.svg"), 128, 128))
        d.run()
        d.destroy()

    def on_quit(self, *_):
        cfg = self.cfg
        w, h = self.get_size()
        cfg.set("WindowWidth", w)
        cfg.set("WindowHeight", h)
        cfg.set("FoldersWidth", self.hpaned.get_position())
        cfg.set("FileListHeight", self.vpaned.get_position())
        cols = self.view.get_columns()
        cfg.set_list("FileListColumns", [self.columns[c.def_index][0] for c in cols if c.get_visible()])
        cfg.set_list("FileListColumnWidths", [f"{self.columns[c.def_index][0]}:{c.get_width()}"
                                              for c in cols if c.get_visible()])
        cfg.set("RenameFileNameMask", self.rn_mask.get_active_text() or "")
        cfg.set("RenameFilesCaseChecked", self.rn_case.get_active())
        cfg.set("RenameFilesCaseItemIndex", self.rn_case_combo.get_active())
        cfg.set("ReplaceSpacesChecked", self.rn_spaces.get_active())
        cfg.set("FileToTagFileNameMask", self.ftt_mask.get_active_text() or "")
        cfg.set("FileToTagCaseChecked", self.ftt_case.get_active())
        cfg.set("FileToTagCaseItemIndex", self.ftt_case_combo.get_active())
        cfg.set("ReplaceUnderscoresChecked", self.ftt_unders.get_active())
        cfg.set("TagEditorCaseChecked", self.mt_case.get_active())
        cfg.set("TagEditorCaseItemIndex", self.mt_case_combo.get_active())
        cfg.save()
        Gtk.main_quit()
        return False


def splash(then, seconds=5):
    """Borderless logo-only window; calls then() after `seconds`, then closes."""
    w = Gtk.Window(decorated=False, resizable=False, type_hint=Gdk.WindowTypeHint.SPLASHSCREEN,
                   window_position=Gtk.WindowPosition.CENTER)
    css = Gtk.CssProvider()
    css.load_from_data(b"window { background: #ffffff; }")
    w.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    img = Gtk.Image.new_from_pixbuf(
        GdkPixbuf.Pixbuf.new_from_file_at_size(os.path.join(LOGO, "retag-lockup.svg"), 480, -1))
    img.set_margin_top(56)
    img.set_margin_bottom(56)
    img.set_margin_start(64)
    img.set_margin_end(64)
    w.add(img)
    w.show_all()

    def done():
        then()  # open the main window first so there is no gap on screen
        w.destroy()
        return False
    GLib.timeout_add_seconds(seconds, done)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else None
    Gtk.Window.set_default_icon_from_file(os.path.join(LOGO, "retag-icon.svg"))
    splash(lambda: App(os.path.abspath(start) if start else None))
    Gtk.main()


if __name__ == "__main__":
    main()
