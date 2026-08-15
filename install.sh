#!/bin/sh
# Voltaic installer — https://github.com/jcfs/voltaic
#
#   curl -fsSL https://raw.githubusercontent.com/jcfs/voltaic/main/install.sh | sh
#
# Installs the GTK packages Voltaic needs from your distribution, the udev
# rules that let it read the Logitech receiver, and Voltaic itself into
# ~/.local. Everything needing root is done with sudo and printed first.
#
# On Debian, Ubuntu and Mint, prefer the .deb from the releases page — the
# package manager then handles the dependencies and the udev rules for you.
#
#   -y   do not ask for confirmation
#   -h   show this help

set -eu

REPO="https://github.com/jcfs/voltaic"
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help)
            # Everything from the second line to the end of the header block.
            awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
            exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# Already root (a container, a minimal system, someone running this under
# sudo) means there is nothing to escalate and sudo may not even be present.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo > /dev/null 2>&1 \
        || die "sudo is required, or run this as root"
    SUDO="sudo"
fi

# -- work out which distribution this is -------------------------------------

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
else
    die "cannot read /etc/os-release; install the dependencies by hand (see $REPO)"
fi

PKG_INSTALL=""
PACKAGES=""
case "${ID:-} ${ID_LIKE:-}" in
    *debian*|*ubuntu*|*mint*)
        PKG_INSTALL="$SUDO apt-get install -y"
        PACKAGES="python3-gi python3-gi-cairo python3-cairo python3-venv gir1.2-gtk-3.0 gir1.2-xapp-1.0"
        ;;
    *fedora*|*rhel*|*centos*)
        PKG_INSTALL="$SUDO dnf install -y"
        PACKAGES="python3-gobject python3-cairo gtk3 xapps"
        ;;
    *arch*|*manjaro*)
        PKG_INSTALL="$SUDO pacman -S --needed --noconfirm"
        PACKAGES="python-gobject python-cairo gtk3 xapp"
        ;;
    *suse*)
        PKG_INSTALL="$SUDO zypper install -y"
        PACKAGES="python3-gobject python3-gobject-Gdk python3-cairo typelib-1_0-Gtk-3_0 typelib-1_0-XApp-1_0"
        ;;
    *)
        die "unrecognised distribution '${ID:-unknown}'. Install PyGObject, GTK 3 and pycairo, then run 'make install' from a checkout."
        ;;
esac

command -v git > /dev/null 2>&1 || die "git is required"
command -v python3 > /dev/null 2>&1 || die "python3 is required"

# -- say what is about to happen ---------------------------------------------

say "Voltaic installer"
echo
echo "  1. install system packages:"
echo "       $PKG_INSTALL $PACKAGES"
echo "  2. install udev rules to /etc/udev/rules.d/60-voltaic.rules"
echo "       (this is what lets you read the receiver without root)"
echo "  3. install Voltaic into ~/.local"
echo
if [ -n "$SUDO" ]; then
    echo "Steps 1 and 2 need sudo. Nothing else is done as root."
fi
echo

if [ "$ASSUME_YES" -eq 0 ]; then
    if [ -r /dev/tty ]; then
        printf 'Continue? [y/N] '
        read -r reply < /dev/tty
    else
        die "no terminal to confirm on; re-run with -y to proceed unattended"
    fi
    case "$reply" in
        y|Y|yes|YES) ;;
        *) echo "Cancelled."; exit 0 ;;
    esac
fi

# -- do it -------------------------------------------------------------------

say "==> Installing system packages"
# shellcheck disable=SC2086
$PKG_INSTALL $PACKAGES

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT INT TERM

say "==> Fetching Voltaic"
git clone --depth 1 --quiet "$REPO" "$WORKDIR/voltaic"
cd "$WORKDIR/voltaic"

say "==> Installing udev rules"
$SUDO install -d /etc/udev/rules.d
$SUDO install -m 0644 packaging/60-voltaic.rules /etc/udev/rules.d/60-voltaic.rules
$SUDO rm -f /etc/udev/rules.d/99-voltaic.rules
# A machine without a running udev (a container, a chroot) can still be a
# valid install target, so this is a warning rather than the end of it.
if command -v udevadm > /dev/null 2>&1; then
    $SUDO udevadm control --reload-rules \
        || warn "could not reload udev rules"
    # Replay the rules against hidraw nodes that already exist, so the ACL
    # applies to a receiver that is plugged in right now. Without this the
    # receiver would have to be unplugged and plugged back in.
    $SUDO udevadm trigger --subsystem-match=hidraw --action=change || true
else
    warn "udevadm not found — replug the receiver for the rules to apply"
fi

say "==> Installing Voltaic"
make install

# Installing the packages is not the same as them working. openSUSE shipped
# a "gtk3" that carries no Python typelib, which made this script report
# success while the tray could not start at all — so check what was
# installed rather than trusting the package manager's exit code.
say "==> Checking the install"
VENV_PYTHON="$HOME/.local/share/voltaic/venv/bin/python"
if "$VENV_PYTHON" - <<'CHECK' 2>/dev/null
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: F401
import cairo  # noqa: F401
CHECK
then
    echo "GTK 3 and pycairo are usable."
else
    warn "Voltaic is installed but GTK 3 is not usable, so the tray icon"
    warn "will not start. The package names this script used for"
    warn "'${ID:-unknown}' are probably wrong — please report it at"
    warn "$REPO/issues so the next person does not hit this."
    exit 1
fi

echo
say "Done."
echo "Start it with:  $HOME/.local/bin/voltaic"
echo "or search for Voltaic in your application launcher."
echo
echo "If no Logitech devices appear, the receiver needs re-enumerating:"
echo "  cd $PWD && make rebind"
echo "(that directory is temporary — clone $REPO if you need it again)"
