#!/usr/bin/env bash
# Install Vitals for the current user: launcher, icons and login autostart.
#
#   ./install.sh                  launcher + icons + start at login
#   ./install.sh --no-autostart   launcher + icons only
#   ./install.sh --monitor-only   autostart without the web console
#   ./install.sh --uninstall      remove everything this script installs
set -euo pipefail

APP_ID="dev.favoratti.Vitals"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
DESKTOP_DIR="$DATA_DIR/applications"
ICON_ROOT="$DATA_DIR/icons/hicolor"
AUTOSTART_DIR="$CONFIG_DIR/autostart"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"
AUTOSTART_FILE="$AUTOSTART_DIR/$APP_ID.desktop"

autostart=1
uninstall=0
extra_args=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-autostart) autostart=0 ;;
        --monitor-only) extra_args=" --monitor-only" ;;
        --uninstall) uninstall=1 ;;
        -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

refresh_caches() {
    if command -v gtk4-update-icon-cache >/dev/null 2>&1; then
        gtk4-update-icon-cache -q -t -f "$ICON_ROOT" 2>/dev/null || true
    elif command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f "$ICON_ROOT" 2>/dev/null || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi
}

if [ "$uninstall" = 1 ]; then
    rm -f "$DESKTOP_FILE" "$AUTOSTART_FILE"
    for icon in "$APP_DIR"/assets/icons/"$APP_ID"-*.png; do
        size="${icon##*-}"; size="${size%.png}"
        rm -f "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png"
    done
    refresh_caches
    echo "Removed launcher, icons and autostart entry for Vitals."
    exit 0
fi

# -- icons -------------------------------------------------------------------

installed_icons=0
for icon in "$APP_DIR"/assets/icons/"$APP_ID"-*.png; do
    [ -e "$icon" ] || continue
    size="${icon##*-}"; size="${size%.png}"
    dest_dir="$ICON_ROOT/${size}x${size}/apps"
    mkdir -p "$dest_dir"
    cp -f "$icon" "$dest_dir/$APP_ID.png"
    installed_icons=$((installed_icons + 1))
done

if [ "$installed_icons" = 0 ]; then
    echo "No icons in assets/icons; run: python3 tools/make_icon.py" >&2
fi

# -- launcher ----------------------------------------------------------------

mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Vitals
GenericName=Console and System Monitor
Comment=Internal web console over a real-time CPU, memory, network and GPU monitor
Exec=python3 $APP_DIR/main.py
Path=$APP_DIR
Icon=$APP_ID
Terminal=false
Categories=System;Monitor;
Keywords=system;monitor;cpu;memory;gpu;process;task;logs;dashboard;
StartupNotify=true
StartupWMClass=$APP_ID
Actions=monitor-only;

[Desktop Action monitor-only]
Name=System monitor only
Exec=python3 $APP_DIR/main.py --monitor-only
EOF
chmod +x "$DESKTOP_FILE"

# -- autostart ---------------------------------------------------------------

if [ "$autostart" = 1 ]; then
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Vitals
Comment=Internal web console over a real-time system monitor
Exec=python3 $APP_DIR/main.py$extra_args
Path=$APP_DIR
Icon=$APP_ID
Terminal=false
StartupNotify=true
StartupWMClass=$APP_ID
X-GNOME-Autostart-enabled=true
# Let the session finish logging in before starting a web process.
X-GNOME-Autostart-Delay=5
EOF
    chmod +x "$AUTOSTART_FILE"
else
    rm -f "$AUTOSTART_FILE"
fi

refresh_caches

echo "Installed launcher: $DESKTOP_FILE"
echo "Installed $installed_icons icon sizes under: $ICON_ROOT"
if [ "$autostart" = 1 ]; then
    echo "Starts at login:    $AUTOSTART_FILE"
    echo "  disable with: ./install.sh --no-autostart"
else
    echo "Autostart:          not installed"
fi
echo "Vitals should now appear in your application list."
