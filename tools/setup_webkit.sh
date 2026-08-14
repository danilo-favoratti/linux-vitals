#!/usr/bin/env bash
# Unpacks the GTK4 WebKit introspection data into vendor/ without needing root.
#
# Ubuntu installs libwebkitgtk-6.0 as part of the desktop but leaves out
# gir1.2-webkit-6.0, which is what PyGObject needs to see the WebKit namespace.
# If you can run apt, "sudo apt install gir1.2-webkit-6.0" is the better fix.
set -euo pipefail

cd "$(dirname "$0")/.."
dest="vendor/girepository-1.0"

if compgen -G "/usr/lib/*/girepository-1.0/WebKit-6.0.typelib" >/dev/null; then
    echo "WebKit 6.0 typelib is already installed system-wide; nothing to do."
    exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "Downloading introspection packages..."
(cd "$work" && apt-get download gir1.2-webkit-6.0 gir1.2-javascriptcoregtk-6.0)

for deb in "$work"/*.deb; do
    dpkg -x "$deb" "$work/root"
done

mkdir -p "$dest"
find "$work/root" -name '*.typelib' -exec cp {} "$dest/" \;

echo "Installed into $dest:"
ls -1 "$dest"
