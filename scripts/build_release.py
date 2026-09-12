#!/usr/bin/env python3
"""Build a source/installer archive and an architecture-independent Debian package."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
VERSION = '0.3.0'
DEB_VERSION = '0.3.0'
EPOCH = int(os.environ.get('SOURCE_DATE_EPOCH', '1788998400'))


def archive(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as tar:
        directories = {str(parent) for name, _, _ in files for parent in Path(name).parents if str(parent) != '.'}
        for name in sorted(directories, key=lambda x: (x.count('/'), x)):
            info = tarfile.TarInfo(name + '/')
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, EPOCH
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            tar.addfile(info)
        for name, data, mode in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, EPOCH
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=EPOCH)


def build():
    DIST.mkdir(exist_ok=True)
    excluded = {'.git', 'dist', '__pycache__', '.venv', 'build'}
    files = []
    for path in ROOT.rglob('*'):
        if not path.is_file() or any(x in excluded or x.endswith('.egg-info') for x in path.relative_to(ROOT).parts):
            continue
        name = path.relative_to(ROOT).as_posix()
        files.append((f'hyperx_open_lighting-{VERSION}/{name}', path.read_bytes(), 0o644))
    source = DIST / f'hyperx_open_lighting-{VERSION}.tar.gz'
    source.write_bytes(archive(files))
    data = []
    for path in (ROOT / 'src/hyperx_open_lighting').iterdir():
        if path.suffix in ('.py', '.json'):
            data.append((f'usr/share/hyperx-open-lighting/{path.name}', path.read_bytes(), 0o644))
    data.extend([
        ('usr/bin/hyperx-open-lighting', b'#!/bin/sh\nexec /usr/bin/python3 /usr/share/hyperx-open-lighting/app.py "$@"\n', 0o755),
        ('usr/share/applications/local.hyperx.RGB.desktop', (ROOT / 'packaging/local.hyperx.RGB.desktop').read_bytes(), 0o644),
        ('usr/lib/systemd/user/hyperx-rgb.service', (ROOT / 'packaging/hyperx-rgb.service').read_bytes(), 0o644),
        ('usr/lib/udev/rules.d/60-hyperx-rgb.rules', (ROOT / 'packaging/60-hyperx-rgb.rules').read_bytes(), 0o644),
        ('usr/share/doc/hyperx-open-lighting/copyright', (ROOT / 'LICENSE').read_bytes(), 0o644),
        ('usr/share/doc/hyperx-open-lighting/README.md', (ROOT / 'README.md').read_bytes(), 0o644),
    ])
    for suffix, folder in [('svg','scalable'), ('png','256x256')]:
        data.append((f'usr/share/icons/hicolor/{folder}/apps/local.hyperx.RGB.{suffix}', (ROOT / f'assets/icons/local.hyperx.RGB.{suffix}').read_bytes(), 0o644))
    control = f'''Package: hyperx-open-lighting
Version: {DEB_VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: HyperX Open Lighting contributors <noreply@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1, libgtk-4-1 (>= 4.10), libadwaita-1-0, systemd, udev
Homepage: https://github.com/abhinavpathak9873/hyperx_open_lighting
Description: HyperX RGB lighting control for Linux
 Native GTK4 desktop interface and a user background service for
 selected HyperX keyboard and mouse lighting with independent colors,
 effects, brightness and automatic startup at desktop login.
'''.encode()
    postinst = b'''#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
'''
    parts = DIST / 'deb-parts'
    parts.mkdir(exist_ok=True)
    (parts / 'debian-binary').write_bytes(b'2.0\n')
    (parts / 'control.tar.gz').write_bytes(archive([('control',control,0o644), ('postinst',postinst,0o755)]))
    (parts / 'data.tar.gz').write_bytes(archive(data))
    deb = DIST / f'hyperx-open-lighting_{DEB_VERSION}_all.deb'
    subprocess.run(['ar','rcD',str(deb),'debian-binary','control.tar.gz','data.tar.gz'],cwd=parts,check=True)
    (DIST / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in [source,deb]))
    print(source)
    print(deb)


if __name__ == '__main__':
    build()
