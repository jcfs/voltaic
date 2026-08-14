# Contributing

Thanks for looking. Bug reports about devices I cannot test are especially
useful — Voltaic speaks two protocols with a lot of vendor variation, and the
only way to cover more hardware is for people who own it to say what happened.

## Reporting a bug

Please include:

- the output of `voltaic --list` (it contains no personal data — device
  product names only)
- the output of `make check`
- your distribution, desktop environment, and session type (`echo $XDG_SESSION_TYPE`)
- for a Logitech problem: the receiver model (Unifying, Bolt, Nano) and
  whether Solaar is running
- for an AirPods problem: the model, and whether the accessory was connected
  at the time

## Development setup

```sh
git clone https://github.com/jcfs/voltaic
cd voltaic
make check    # confirms GTK, pycairo and a HID++ node are present
make run      # runs from the checkout without installing
```

There is nothing to install for development — Voltaic has no Python
dependencies. The GTK stack comes from your distribution; see the README's
install section for the package names.

## Tests

```sh
make test     # headless: no display, no receiver, no GTK required
make verify   # tray hover and click behaviour; needs a real desktop
```

`make test` is what CI runs, deliberately on a machine with **no GTK
installed**. That is a design constraint, not an accident:

- `model.py`, `state.py`, `hidpp.py` and `airpods.py` must import cleanly
  with no GTK present. `airpods.py` imports `gi` lazily, inside the function
  that needs D-Bus, precisely so the module stays importable without it.
- A top-level `import gi` in any of those modules will fail CI.

New protocol parsing should come with a test built from a real captured
frame. `tests/test_units.py` has examples for both HID++ report descriptors
and AAP battery frames.

## Style

`ruff check .` must pass; the configuration is in `pyproject.toml`. Beyond
that, match the surrounding code — comments here explain *why* something is
done, particularly where a protocol or a desktop API behaves unexpectedly.
Those comments have saved real debugging time and are worth keeping in that
spirit.

## Things worth knowing before you dig in

- **Only one HID++ client can poll a node at a time.** Two readers steal each
  other's replies and both report devices at random. Voltaic takes an
  abstract-socket lock to enforce a single instance; running it alongside
  Solaar mostly works but each may miss notifications.
- **udev rules only apply at enumeration.** After changing them you must
  replug the receiver or `make rebind`, or you will be testing against the
  old ACL.
- **The rules file must sort before `73-seat-late.rules`.** That is where
  systemd turns the `uaccess` tag into an ACL. This is why the file is named
  `60-voltaic.rules`.
