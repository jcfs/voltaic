PREFIX ?= $(HOME)/.local
UDEV_DIR ?= /etc/udev/rules.d
DESKTOP_DIR = $(PREFIX)/share/applications
ICON_DIR = $(PREFIX)/share/icons/hicolor/scalable/apps
BIN_DIR = $(PREFIX)/bin
# Distributions that follow PEP 668 (Ubuntu 24.04, Debian 12, Fedora 39+)
# refuse `pip install --user` outright, so install into a private venv. It is
# created with --system-site-packages because PyGObject, GTK and pycairo come
# from the distribution and must stay visible inside it.
VENV_DIR ?= $(PREFIX)/share/voltaic/venv

VERSION = $(shell python3 -c "import re,pathlib; \
    print(re.search(r'^__version__ = \"([^\"]+)\"', \
    pathlib.Path('voltaic/__init__.py').read_text(), re.M).group(1))")
DEB_ROOT = build/deb/voltaic_$(VERSION)_all

.PHONY: help install install-udev uninstall run list check verify test deb

help:
	@echo "Voltaic — Logitech battery levels in the system tray"
	@echo
	@echo "  make install-udev   grant hidraw access (needs sudo, do this first)"
	@echo "  make install        install voltaic for the current user"
	@echo "  make run            run from this checkout without installing"
	@echo "  make list           print battery levels and exit"
	@echo "  make check          verify runtime dependencies are present"
	@echo "  make test           run the headless unit tests"
	@echo "  make verify         check the tray hover/click behaviour"
	@echo "  make deb            build a .deb that pulls in its own dependencies"
	@echo "  make uninstall      remove the user installation"

# Must run before `install`: without the ACL the app cannot open /dev/hidraw*.
install-udev:
	sudo install -m 0644 packaging/60-voltaic.rules $(UDEV_DIR)/60-voltaic.rules
	sudo rm -f $(UDEV_DIR)/99-voltaic.rules
	sudo udevadm control --reload-rules
	@echo
	@echo "Rules installed. Replug the receiver so they apply to it,"
	@echo "or re-bind it in place with:"
	@echo "  make rebind"

# Re-enumerate the receiver so freshly installed rules take effect without
# physically unplugging anything.
.PHONY: rebind
rebind:
	@set -e; \
	found=0; \
	for d in /sys/bus/usb/devices/*/idVendor; do \
	  [ "$$(cat $$d 2>/dev/null)" = "046d" ] || continue; \
	  dev=$$(basename $$(dirname $$d)); \
	  case "$$dev" in *:*) continue;; esac; \
	  echo "re-binding $$dev"; \
	  echo -n "$$dev" | sudo tee /sys/bus/usb/drivers/usb/unbind >/dev/null; \
	  sleep 1; \
	  echo -n "$$dev" | sudo tee /sys/bus/usb/drivers/usb/bind >/dev/null; \
	  found=1; \
	done; \
	[ "$$found" = 1 ] || echo "no Logitech USB device found"

install:
	python3 -m venv --system-site-packages $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --quiet --upgrade .
	install -d $(BIN_DIR)
	ln -sf $(VENV_DIR)/bin/voltaic $(BIN_DIR)/voltaic
	install -d $(ICON_DIR)
	install -m 0644 packaging/voltaic.svg $(ICON_DIR)/voltaic.svg
	install -d $(DESKTOP_DIR)
	# The launcher resolves Exec against the session's PATH, which does not
	# always include ~/.local/bin. Bake in the absolute path instead.
	sed 's|^Exec=voltaic$$|Exec=$(BIN_DIR)/voltaic|' packaging/voltaic.desktop \
	    > $(DESKTOP_DIR)/voltaic.desktop
	chmod 0644 $(DESKTOP_DIR)/voltaic.desktop
	-update-desktop-database $(DESKTOP_DIR) 2>/dev/null
	-gtk-update-icon-cache -f -t $(PREFIX)/share/icons/hicolor 2>/dev/null
	@echo
	@echo "Installed. Start it with:  $(BIN_DIR)/voltaic"
	@echo "or search for Voltaic in your application launcher."
	@echo "Enable 'Start at login' from the tray icon's right-click menu."

uninstall:
	rm -rf $(VENV_DIR)
	rm -f $(BIN_DIR)/voltaic
	rm -f $(DESKTOP_DIR)/voltaic.desktop
	rm -f $(ICON_DIR)/voltaic.svg
	rm -f $(HOME)/.config/autostart/voltaic.desktop
	rm -rf $(HOME)/.cache/voltaic
	-update-desktop-database $(DESKTOP_DIR) 2>/dev/null
	-gtk-update-icon-cache -f -t $(PREFIX)/share/icons/hicolor 2>/dev/null

run:
	python3 -m voltaic

list:
	@python3 -m voltaic --list

# A native package is the one format that can carry the udev rules as well as
# declare the GTK dependencies, which turns the whole install into a single
# command. Deliberately plain files, no venv: on a .deb the dependencies are
# the package manager's job.
deb:
	rm -rf $(DEB_ROOT)
	install -d $(DEB_ROOT)/DEBIAN
	sed 's|@VERSION@|$(VERSION)|' packaging/debian/control.in \
	    > $(DEB_ROOT)/DEBIAN/control
	install -m 0755 packaging/debian/postinst $(DEB_ROOT)/DEBIAN/postinst
	install -m 0755 packaging/debian/postrm $(DEB_ROOT)/DEBIAN/postrm

	install -d $(DEB_ROOT)/usr/lib/python3/dist-packages/voltaic
	install -m 0644 voltaic/*.py \
	    $(DEB_ROOT)/usr/lib/python3/dist-packages/voltaic/

	install -d $(DEB_ROOT)/usr/bin
	printf '#!/usr/bin/python3\nimport sys\n\nfrom voltaic.__main__ import main\n\nsys.exit(main())\n' \
	    > $(DEB_ROOT)/usr/bin/voltaic
	chmod 0755 $(DEB_ROOT)/usr/bin/voltaic

	install -Dm0644 packaging/60-voltaic.rules \
	    $(DEB_ROOT)/usr/lib/udev/rules.d/60-voltaic.rules
	install -Dm0644 packaging/voltaic.desktop \
	    $(DEB_ROOT)/usr/share/applications/voltaic.desktop
	install -Dm0644 packaging/voltaic.svg \
	    $(DEB_ROOT)/usr/share/icons/hicolor/scalable/apps/voltaic.svg
	install -Dm0644 LICENSE $(DEB_ROOT)/usr/share/doc/voltaic/copyright

	fakeroot dpkg-deb --build $(DEB_ROOT) build/voltaic_$(VERSION)_all.deb
	@echo
	@echo "Built build/voltaic_$(VERSION)_all.deb"
	@echo "Install it with:  sudo apt install ./build/voltaic_$(VERSION)_all.deb"

# Parsing and model layers only — no display, no receiver, no GTK, which is
# what lets these run in CI.
test:
	@python3 -m unittest discover -s tests -p 'test_*.py'

# Needs a real tray, so quit any running instance first: two copies would
# fight over the hidraw node.
verify:
	@python3 tests/verify_ui.py

check:
	@python3 -c "import gi; gi.require_version('Gtk','3.0'); \
from gi.repository import Gtk; print('GTK 3          ok')"
	@python3 -c "import cairo; print('pycairo        ok')"
	@python3 -c "import gi; \
exec(\"try:\\n gi.require_version('XApp','1.0'); print('XApp           ok')\\nexcept Exception: print('XApp           missing (will fall back to AppIndicator)')\")"
	@python3 -c "from voltaic.hidpp import find_hidpp_paths as f; p=f(); \
print('HID++ node     ' + (', '.join(p) if p else 'none found — is the receiver plugged in?'))"
