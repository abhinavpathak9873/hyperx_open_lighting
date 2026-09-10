# Contributing

Use system Python 3.10+; the controller and unit tests need only its standard
library. The GUI also needs PyGObject, GTK 4.10+ and libadwaita.

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
PYTHONPATH=src /usr/bin/python3 -m hyperx_open_lighting --doctor
```

Tests use simulated devices and never change hardware. The streaming regression
checks that static/off frames continue while a simulated mouse times out. Device
permissions must target only the RGB control interface, not all HID devices.

For hardware reports, include model, USB ID, firmware, distribution, connection
mode, effect, and reproduction steps. Do not attach full input logs, device
serials, Windows executables, firmware or decompiled vendor source. Say which
observations are physical and which are only protocol acknowledgements.

Hardware changes should be checked with physical key press/release tests,
including Ctrl/Shift, sustained lighting and device reconnect. Command ACKs
alone do not establish correct keyboard input.

## Release

Update version constants in pyproject.toml, app.py, scripts/build_release.py,
README examples and CHANGELOG. Run tests and the release builder:

```sh
/usr/bin/python3 scripts/build_release.py
```

This creates a native installer archive, an architecture-independent `.deb`, and
SHA256SUMS. The artifacts require system Python/GTK; they are not standalone
AppImages. Push a `v*` tag to run CI and attach packages to a GitHub release.
Tags containing alpha, beta or rc produce prereleases. A container can validate Debian installation and GTK loading, but
cannot replace real-device or desktop-session tests.
