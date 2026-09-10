#!/usr/bin/env python3
"""Install this release for the current desktop user. Run with system Python."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
APP = DATA / 'hyperx-rgb'
BIN = Path.home() / '.local/bin'
UNIT = CONFIG / 'systemd/user/hyperx-rgb.service'
APP_ID = 'local.hyperx.RGB'


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def dependencies():
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Gtk
        assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10)
    except (ImportError, ValueError, AssertionError):
        raise SystemExit('Needs system Python 3.10+, PyGObject, GTK 4.10+ and libadwaita. See README dependency commands. Use /usr/bin/python3 install.py.')
    if sys.version_info < (3, 10) or not shutil.which('systemctl'):
        raise SystemExit('Python 3.10+ and a systemd desktop session are required.')


def permissions():
    target = Path('/etc/udev/rules.d/60-hyperx-rgb.rules')
    source = ROOT / 'packaging/60-hyperx-rgb.rules'
    if target.exists() and target.read_bytes() == source.read_bytes():
        return
    # This script only elevates for the two devices' udev rule, never the app.
    commands = ' && '.join(shlex.join(c) for c in [
        ['install', '-m', '644', str(source), str(target)],
        ['udevadm', 'control', '--reload-rules'],
        ['udevadm', 'trigger', '--subsystem-match=hidraw'],
        ['udevadm', 'settle'],
    ])
    privilege = 'sudo' if sys.stdin.isatty() else 'pkexec'
    print('Installing USB access for the two supported devices; administrator authentication may appear.')
    run([privilege, '/bin/sh', '-c', commands])


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'


def install(args):
    dependencies()
    if not args.skip_permissions:
        permissions()
    if not args.no_service:
        run(['systemctl', '--user', 'stop', 'hyperx-rgb.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if UNIT.exists() else None
    APP.mkdir(parents=True, exist_ok=True)
    for item in (ROOT / 'src/hyperx_open_lighting').iterdir():
        if item.suffix in ('.py', '.json'):
            shutil.copy2(item, APP / item.name)
    for name in ('README.md', 'LICENSE', 'CHANGELOG.md'):
        shutil.copy2(ROOT / name, APP / name)
    # Keep compatibility with the initial local app without two controllers.
    old = APP / 'hyperx_rgb.py'
    old.write_text('from app import main\n\nif __name__ == "__main__":\n    main()\n')
    BIN.mkdir(parents=True, exist_ok=True)
    for name in ('hyperx-open-lighting', 'hyperx-rgb'):
        launcher = BIN / name
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(APP / 'app.py')) + ' "$@"\n')
        launcher.chmod(0o755)
    for suffix, folder in [('svg', 'scalable'), ('png', '256x256')]:
        dest = DATA / f'icons/hicolor/{folder}/apps'
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f'assets/icons/{APP_ID}.{suffix}', dest / f'{APP_ID}.{suffix}')
    applications = DATA / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    desktop = (ROOT / f'packaging/{APP_ID}.desktop').read_text().replace('Exec=hyperx-open-lighting', 'Exec=' + desktop_quote(BIN / 'hyperx-open-lighting'))
    (applications / f'{APP_ID}.desktop').write_text(desktop)
    (applications / 'hyperx-rgb.desktop').unlink(missing_ok=True)
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    service = (ROOT / 'packaging/hyperx-rgb.service').read_text().replace('/usr/share/hyperx-open-lighting/app.py', unit_quote(APP / 'app.py'))
    UNIT.write_text(service)
    for cmd in [['gtk-update-icon-cache', '--force', '--ignore-theme-index', str(DATA / 'icons/hicolor')],
                ['update-desktop-database', str(applications)]]:
        if shutil.which(cmd[0]):
            run(cmd, stdout=subprocess.DEVNULL)
    if not args.no_service:
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'enable', '--now', 'hyperx-rgb.service'])
    print('Installed HyperX Open Lighting. Open it from the application launcher.')
    print('Saved colors are preserved. Lighting runs in the background at login.')


def uninstall(args):
    if not args.no_service:
        subprocess.run(['systemctl', '--user', 'disable', '--now', 'hyperx-rgb.service'], check=False)
    for name in ('hyperx-open-lighting', 'hyperx-rgb'):
        (BIN / name).unlink(missing_ok=True)
    UNIT.unlink(missing_ok=True)
    (DATA / f'applications/{APP_ID}.desktop').unlink(missing_ok=True)
    for folder in ('scalable', '32x32', '48x48', '64x64', '128x128', '256x256'):
        for suffix in ('svg', 'png'):
            (DATA / f'icons/hicolor/{folder}/apps/{APP_ID}.{suffix}').unlink(missing_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    if not args.no_service:
        run(['systemctl', '--user', 'daemon-reload'])
    print('App removed. Your saved settings and the shared USB permission rule were retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--skip-permissions', action='store_true', help='Do not install the udev rule')
    parser.add_argument('--no-service', action='store_true', help='Stage files without touching the user service')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit('Run this installer as your normal desktop user, not root.')
    (uninstall if args.uninstall else install)(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Installation failed: {exc}')
