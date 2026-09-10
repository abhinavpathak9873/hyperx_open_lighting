"""Haste 2 Core DPI protocol and per-device Hyprland pointer settings."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

DEVICE_NAME = 'hp--inc-hyperx-pulsefire-haste-2-core-wireless'
RATES = {125: 64, 250: 32, 500: 16, 1000: 8}
BASE = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
CONFIG = BASE / 'hyperx-rgb/mouse.json'
DEFAULT = {'dpi': None, 'pointer': None, 'fixed_dpi': False}


def validate(data):
    if not isinstance(data, dict) or not {'dpi', 'pointer'} <= set(data) or set(data) - set(DEFAULT):
        raise ValueError('Mouse settings must contain dpi and pointer')
    data = json.loads(json.dumps({**DEFAULT, **data}))
    if type(data['fixed_dpi']) is not bool:
        raise ValueError('fixed_dpi must be true or false')
    dpi, pointer = data['dpi'], data['pointer']
    if dpi is not None:
        if not isinstance(dpi, dict) or set(dpi) != {'stages', 'active', 'polling_hz'}:
            raise ValueError('Invalid DPI settings')
        if (not isinstance(dpi['stages'], list) or len(dpi['stages']) != 4 or
                any(type(v) is not int or not 50 <= v <= 12000 or v % 50 for v in dpi['stages'])):
            raise ValueError('Four DPI stages are required: 50–12,000 in steps of 50')
        if type(dpi['active']) is not int or not 0 <= dpi['active'] < 4:
            raise ValueError('Choose DPI stage 1–4')
        if type(dpi['polling_hz']) is not int or dpi['polling_hz'] not in RATES:
            raise ValueError('Polling rate must be 125, 250, 500 or 1,000 Hz')
        if data['fixed_dpi']:
            dpi['stages'] = [dpi['stages'][dpi['active']]] * 4
    if pointer is not None:
        if not isinstance(pointer, dict) or set(pointer) != {'sensitivity', 'acceleration', 'scroll_factor', 'natural_scroll'}:
            raise ValueError('Invalid pointer settings')
        for key, low, high in [('sensitivity', -1, 1), ('scroll_factor', .1, 5)]:
            value = pointer[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{key} must be between {low} and {high}')
        if pointer['acceleration'] not in ('flat', 'adaptive') or type(pointer['natural_scroll']) is not bool:
            raise ValueError('Invalid acceleration or scroll direction')
    return json.loads(json.dumps(data))


def load():
    return validate(json.loads(CONFIG.read_text())) if CONFIG.exists() else dict(DEFAULT)


def check_snapshot(expected):
    current = load()
    if current != expected:
        raise RuntimeError('Mouse settings changed outside this window. Reopen the app to load them before applying.')
    return current


def decode(report):
    if len(report) != 64 or report[:2] != b'\x33\x02':
        raise ValueError('Invalid DPI response')
    if report[2] not in RATES.values() or report[3] != 15 or report[4] > 3:
        raise ValueError('Unsupported DPI table; leaving hardware unchanged')
    result = {'stages': [(int.from_bytes(report[5 + i * 5:7 + i * 5], 'little') + 1) * 50
                         for i in range(4)],
              'active': report[4], 'polling_hz': next(k for k, v in RATES.items() if v == report[2])}
    validate({'dpi': result, 'pointer': None})
    return result


def build(report, settings):
    decode(report)
    validate({'dpi': settings, 'pointer': None})
    # Copy the live table, including stage colors and inactive/reserved records.
    # ApplyProfiles=0 means volatile live settings, not onboard flash.
    data = bytearray(b'\x32\x01\x00\x00' + report[2:62])
    data[4], data[6] = RATES[settings['polling_hz']], settings['active']
    for i, dpi in enumerate(settings['stages']):
        data[7 + i * 5:9 + i * 5] = (dpi // 50 - 1).to_bytes(2, 'little')
    return bytes(data)


def read(device):
    replies = device.exchange(bytes([0x32, 2]).ljust(64, b'\0'), response=(0x33, 2))
    report = next(b for b in replies if b[:2] == b'\x33\x02')
    return report, decode(report)


def apply(device, settings, heartbeat=None):
    before, actual = read(device)
    if actual == settings:
        device.dpi_stage = actual['active']
        return actual
    if heartbeat:
        heartbeat()
    device.exchange(build(before, settings))
    if heartbeat:
        heartbeat()
    _, actual = read(device)
    if actual != settings:
        raise RuntimeError('Mouse DPI readback does not match requested settings')
    device.dpi_stage = actual['active']
    return actual


def run(*args):
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=12)
    return result.stdout.strip()


def available():
    return bool(shutil.which('hyprctl') and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'))


def pointer_text(pointer, lua=True):
    if pointer is None:
        return '-- Desktop pointer defaults.\n' if lua else '# Desktop pointer defaults.\n'
    validate({'dpi': None, 'pointer': pointer})
    fields = {'name': DEVICE_NAME, 'sensitivity': pointer['sensitivity'],
              'accel_profile': pointer['acceleration'], 'scroll_factor': pointer['scroll_factor'],
              'natural_scroll': pointer['natural_scroll']}
    if lua:
        return '-- Managed by HyperX Open Lighting.\nhl.device({\n' + ''.join(
            f'  {key} = {json.dumps(value)},\n' for key, value in fields.items()) + '})\n'
    return '# Managed by HyperX Open Lighting.\ndevice {\n' + ''.join(
        f'  {key} = {str(value).lower() if isinstance(value, bool) else value}\n'
        for key, value in fields.items()) + '}\n'


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def apply_pointer(pointer):
    """Install one guarded, per-device include; rollback on compositor errors."""
    if not available():
        raise RuntimeError('Desktop pointer controls require a running Hyprland session')
    folder = BASE / 'hypr'
    lua = (folder / 'hyprland.lua').exists()
    main = folder / ('hyprland.lua' if lua else 'hyprland.conf')
    if not main.exists():
        raise RuntimeError('No standard Hyprland configuration found')
    errors = run('hyprctl', 'configerrors')
    if errors:
        raise RuntimeError('Resolve existing Hyprland config errors before applying pointer settings: ' + errors)
    generated = CONFIG.parent / ('pointer.lua' if lua else 'pointer.conf')
    if lua:
        quoted = json.dumps(str(generated), ensure_ascii=False)
        include = f'\n-- HyperX Open Lighting pointer settings\ndo local p = {quoted}; local f = io.open(p, "r"); if f then f:close(); dofile(p) end end\n'
    else:
        include = f'\n# HyperX Open Lighting pointer settings\nsource = {generated}\n'
    before = main.read_text()
    previous = generated.read_text() if generated.exists() else None
    new_main = before if include.strip() in before else before + include
    if new_main != before:
        shutil.copy2(main, main.with_name(main.name + f'.hyperx-backup-{time.time_ns()}'))
    try:
        write_text(generated, pointer_text(pointer, lua))
        if new_main != before:
            write_text(main, new_main)
        run('hyprctl', 'reload')
        errors = run('hyprctl', 'configerrors')
        if errors:
            raise RuntimeError('Hyprland rejected pointer settings: ' + errors)
        if pointer is not None:
            devices = json.loads(run('hyprctl', 'devices', '-j'))['mice']
            target = next((d for d in devices if d['name'] == DEVICE_NAME), None)
            # Hyprland's defaultSpeed is the libinput factory default, not the
            # configured sensitivity. Only scrollFactor is a live readback.
            if target and abs(target['scrollFactor'] - pointer['scroll_factor']) > .011:
                raise RuntimeError('Pointer settings readback failed')
    except Exception:
        if new_main != before:
            write_text(main, before)
        if previous is None:
            generated.unlink(missing_ok=True)
        else:
            write_text(generated, previous)
        run('hyprctl', 'reload')
        raise
