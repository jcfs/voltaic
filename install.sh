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

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

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
        PKG_INSTALL="sudo apt-get install -y"
        PACKAGES="python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-3.0 gir1.2-xapp-1.0"
        ;;
    *fedora*|*rhel*|*centos*)
        PKG_INSTALL="sudo dnf install -y"
        PACKAGES="python3-gobject python3-cairo gtk3 xapps"
        ;;
    *arch*|*manjaro*)
        PKG_INSTALL="sudo pacman -S --needed --noconfirm"
        PACKAGES="python-gobject python-cairo gtk3 xapp"
        ;;
    *suse*)
        PKG_INSTALL="sudo zypper install -y"
        PACKAGES="python3-gobject python3-gobject-Gdk python3-cairo gtk3"
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
echo "Steps 1 and 2 need sudo. Nothing else is done as root."
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
sudo install -m 0644 packaging/60-voltaic.rules /etc/udev/rules.d/60-voltaic.rules
sudo rm -f /etc/udev/rules.d/99-voltaic.rules
sudo udevadm control --reload-rules
# Replay the rules against hidraw nodes that already exist, so the ACL
# applies to a receiver that is plugged in right now. Without this the
# receiver would have to be unplugged and plugged back in.
sudo udevadm trigger --subsystem-match=hidraw --action=change || true

say "==> Installing Voltaic"
make install

echo
say "Done."
echo "Start it with:  $HOME/.local/bin/voltaic"
echo "or search for Voltaic in your application launcher."
echo
echo "If no Logitech devices appear, the receiver needs re-enumerating:"
echo "  cd $PWD && make rebind"
echo "(that directory is temporary — clone $REPO if you need it again)"
