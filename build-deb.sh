#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Don Wildman
# Builds retag_<version>_all.deb. Install with: sudo apt install ./retag_<version>_all.deb
set -e
umask 022
VERSION="${1:-1.0.0}"
DIR="$(cd "$(dirname "$0")" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
chmod 755 "$STAGE"
LIB="$STAGE/usr/lib/retag"

mkdir -p "$LIB/logo" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/icons/hicolor/16x16/apps" \
         "$STAGE/usr/share/doc/retag" "$STAGE/DEBIAN"
install -m 755 "$DIR/retag.py" "$LIB/"
install -m 644 "$DIR/tagcore.py" "$LIB/"
install -m 644 "$DIR"/logo/*.svg "$LIB/logo/"
ln -s ../lib/retag/retag.py "$STAGE/usr/bin/retag"
sed -e 's|^Exec=.*|Exec=retag %f|' -e 's|^Icon=.*|Icon=retag|' "$DIR/retag.desktop" > "$STAGE/usr/share/applications/retag.desktop"
install -m 644 "$DIR/logo/retag-icon.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/retag.svg"
install -m 644 "$DIR/logo/retag-icon-16.svg" "$STAGE/usr/share/icons/hicolor/16x16/apps/retag.svg"
install -m 644 "$DIR/LICENSE" "$STAGE/usr/share/doc/retag/copyright"

cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: retag
Version: $VERSION
Architecture: all
Maintainer: Don Wildman <don@takemycard.co.za>
Depends: python3, python3-gi, gir1.2-gtk-3.0, python3-mutagen, python3-pil, librsvg2-common
Section: sound
Priority: optional
Installed-Size: $(du -sk "$STAGE/usr" | cut -f1)
Description: Music tag editor and file renamer
 Edit music tags in bulk, rename files from tags, and fill tags from file names.
CONTROL

dpkg-deb --root-owner-group --build "$STAGE" "$DIR/retag_${VERSION}_all.deb"
