PREFIX ?= $(HOME)/.local
UDEV_DIR ?= /etc/udev/rules.d
DESKTOP_DIR = $(PREFIX)/share/applications
BIN_DIR = $(PREFIX)/bin
# Distributions that follow PEP 668 (Ubuntu 24.04, Debian 12, Fedora 39+)
# refuse `pip install --user` outright, so install into a private venv. It is
# created with --system-site-packages because PyGObject, GTK and pycairo come
# from the distribution and must stay visible inside it.
VENV_DIR ?= $(PREFIX)/share/voltaic/venv

.PHONY: help install install-udev uninstall run list check verify

help:
	@echo "Voltaic — Logitech battery levels in the system tray"
	@echo
	@echo "  make install-udev   grant hidraw access (needs sudo, do this first)"
	@echo "  make install        install voltaic for the current user"
	@echo "  make run            run from this checkout without installing"
	@echo "  make list           print battery levels and exit"
	@echo "  make check          verify runtime dependencies are present"
	@echo "  make verify         check the tray hover/click behaviour"
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
	install -d $(DESKTOP_DIR)
	# The launcher resolves Exec against the session's PATH, which does not
	# always include ~/.local/bin. Bake in the absolute path instead.
	sed 's|^Exec=voltaic$$|Exec=$(BIN_DIR)/voltaic|' packaging/voltaic.desktop \
	    > $(DESKTOP_DIR)/voltaic.desktop
	chmod 0644 $(DESKTOP_DIR)/voltaic.desktop
	-update-desktop-database $(DESKTOP_DIR) 2>/dev/null
	@echo
	@echo "Installed. Start it with:  $(BIN_DIR)/voltaic"
	@echo "or search for Voltaic in your application launcher."
	@echo "Enable 'Start at login' from the tray icon's right-click menu."

uninstall:
	rm -rf $(VENV_DIR)
	rm -f $(BIN_DIR)/voltaic
	rm -f $(DESKTOP_DIR)/voltaic.desktop
	rm -f $(HOME)/.config/autostart/voltaic.desktop
	rm -rf $(HOME)/.cache/voltaic
	-update-desktop-database $(DESKTOP_DIR) 2>/dev/null

run:
	python3 -m voltaic

list:
	@python3 -m voltaic --list

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
