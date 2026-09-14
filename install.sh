#!/usr/bin/env bash
# Install Vitals for the current user: launcher, icons and login autostart.
#
#   ./install.sh                  launcher + icons + start at login (supervised)
#   ./install.sh --no-autostart   launcher + icons only
#   ./install.sh --monitor-only   autostart without the web console
#   ./install.sh --plain-autostart  use the XDG entry instead of the service
#   ./install.sh --uninstall      remove everything this script installs
#
# Autostart goes through a systemd user service so that a crash, an
# out-of-memory kill or anything else that ends the process brings the panel
# straight back. Ctrl+Q exits cleanly and is left alone.
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
UNIT_DIR="$CONFIG_DIR/systemd/user"
UNIT_NAME="vitals.service"
UNIT_FILE="$UNIT_DIR/$UNIT_NAME"

autostart=1
uninstall=0
use_service=1
extra_args=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-autostart) autostart=0 ;;
        --monitor-only) extra_args=" --monitor-only" ;;
        --plain-autostart) use_service=0 ;;
        --uninstall) uninstall=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

have_systemd_user() {
    command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1
}

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
    if have_systemd_user; then
        systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    fi
    rm -f "$DESKTOP_FILE" "$AUTOSTART_FILE" "$UNIT_FILE"
    if have_systemd_user; then
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    for icon in "$APP_DIR"/assets/icons/"$APP_ID"-*.png; do
        size="${icon##*-}"; size="${size%.png}"
        rm -f "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png"
    done
    refresh_caches
    echo "Removed launcher, icons, service and autostart entry for Vitals."
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

if [ "$autostart" = 1 ] && [ "$use_service" = 1 ] && have_systemd_user; then
    # The XDG entry and the service would both launch Vitals; the second
    # instance hands off to the first and exits, which the service would read as
    # a stop. Only one of the two may be installed.
    rm -f "$AUTOSTART_FILE"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Vitals — internal web console and system monitor
PartOf=graphical-session.target
After=graphical-session.target
# No start-rate limit: the panel is expected to come back however often it dies.
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/main.py$extra_args
# Only an unclean end restarts. Ctrl+Q returns 0 and stays closed.
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME" >/dev/null
    autostart_kind="service"
elif [ "$autostart" = 1 ]; then
    if [ "$use_service" = 1 ]; then
        echo "No systemd user session; falling back to the XDG autostart entry." >&2
    fi
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
    autostart_kind="xdg"
else
    rm -f "$AUTOSTART_FILE"
    if have_systemd_user && [ -f "$UNIT_FILE" ]; then
        systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
        rm -f "$UNIT_FILE"
        systemctl --user daemon-reload
    fi
    autostart_kind="none"
fi

refresh_caches

echo "Installed launcher: $DESKTOP_FILE"
echo "Installed $installed_icons icon sizes under: $ICON_ROOT"
case "$autostart_kind" in
    service)
        echo "Starts at login:    $UNIT_FILE (restarts if it dies)"
        echo "  start now:    systemctl --user start $UNIT_NAME"
        echo "  follow:       journalctl --user -u $UNIT_NAME -f"
        echo "  disable with: ./install.sh --no-autostart"
        ;;
    xdg)
        echo "Starts at login:    $AUTOSTART_FILE (no restart on crash)"
        echo "  disable with: ./install.sh --no-autostart"
        ;;
    *)
        echo "Autostart:          not installed"
        ;;
esac
echo "Vitals should now appear in your application list."
