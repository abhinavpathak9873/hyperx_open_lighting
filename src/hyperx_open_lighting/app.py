#!/usr/bin/env python3
"""Linux lighting control for selected HyperX keyboards, mice and microphones.

The protocol core uses the standard library; the GUI uses system GTK4/Adwaita.

Uses volatile lighting and mouse DPI reports, never firmware, key mapping or flash commands.
Protocol notes and hardware verification are in README.md alongside this file.
"""
import argparse
import colorsys
import fcntl
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
try:
    from . import mouse, mouse_ui, openrgb
except ImportError:
    import mouse, mouse_ui, openrgb

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'hyperx-rgb'
CONFIG = CONFIG_DIR / 'settings.json'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
STATUS = RUNTIME / 'hyperx-rgb-status.json'
MODES = ('Static', 'Breathing', 'Color cycle', 'Rainbow wave', 'Off')
DEVICES = {
    'keyboard': {'pid': 0x02A1, 'name': 'Alloy Rise 75', 'leds': 103},
    'mouse': {'pid': 0x0AB5, 'name': 'Pulsefire Haste 2 Core Wireless', 'leds': 1},
    'microphone': {'pid': 0x02B5, 'name': 'QuadCast 2 S', 'leds': 108},
}
LAYOUT = json.loads((ROOT / 'keyboard-layout.json').read_text())
DEFAULT = {key: {'color': '#ff0000', 'brightness': 100, 'mode': 'Static', 'speed': 40, 'enabled': True}
           for key in DEVICES}

DEFAULT['microphone']['mode'] = 'Follow OpenRGB'


def modes_for(key):
    return MODES + ('Follow OpenRGB',) if key == 'microphone' else MODES


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(obj, stream, indent=2)
            stream.write('\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validate(data):
    if not isinstance(data, dict) or set(data) not in (set(DEVICES), {'keyboard', 'mouse'}):
        raise ValueError('Settings must contain keyboard, mouse and optionally microphone')
    # Migrate existing installations without altering either device or DPI settings.
    data = {**data, 'microphone': data.get('microphone', dict(DEFAULT['microphone']))}
    result = {}
    for key in DEVICES:
        entry = data[key]
        if not isinstance(entry, dict):
            raise ValueError('Device settings must be an object')
        color = entry.get('color', '')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Color must be a six-digit hex color such as #ff0000')
        if entry.get('mode') not in modes_for(key):
            raise ValueError('Unknown lighting mode')
        for field in ('brightness', 'speed'):
            if type(entry.get(field)) is not int or not 0 <= entry[field] <= 100:
                raise ValueError(f'{field} must be an integer from 0 to 100')
        enabled = entry.get('enabled', True)
        if type(enabled) is not bool:
            raise ValueError('enabled must be true or false')
        result[key] = {f: entry[f] for f in ('color', 'brightness', 'mode', 'speed')}
        result[key]['enabled'] = enabled
    return result


def load_settings():
    if not CONFIG.exists():
        return json.loads(json.dumps(DEFAULT))
    return validate(json.loads(CONFIG.read_text()))


def find_device(key):
    if key == 'microphone':
        return None  # OpenRGB owns this USB device; never open a competing HID reader.
    target = DEVICES[key]
    for path in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
        try:
            props = dict(line.split('=', 1) for line in
                         (path / 'device/uevent').read_text().splitlines() if '=' in line)
            parts = props.get('HID_ID', '').split(':')
            if len(parts) != 3 or [int(n, 16) for n in parts] != [3, 0x03F0, target['pid']]:
                continue
            if not props.get('HID_PHYS', '').endswith('/input2'):
                continue
            descriptor = (path / 'device/report_descriptor').read_bytes()
            if b'\x85\x44' not in descriptor or b'\x85\x40' not in descriptor:
                continue
            return '/dev/' + path.name
        except (OSError, ValueError):
            continue
    return None


def packet(prefix):
    if len(prefix) > 64:
        raise ValueError('HID packet too long')
    return bytes(prefix).ljust(64, b'\0')


def frame_packets(colors):
    count = (len(colors) + 19) // 20
    packets = [packet([0x44, 1, count, 0])]
    for part in range(count):
        rgb = [component for color in colors[part * 20:(part + 1) * 20] for component in color]
        packets.append(packet([0x44, 2, part, 0] + rgb))
    return packets


class Device:
    def __init__(self, key, path):
        self.key, self.path = key, path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.acks = 0
        self.dpi_stage = None
        try:
            response = self.exchange(packet([0x10, 1]), response=(0x11, 1))
            info = next(b for b in response if b[:2] == b'\x11\x01')
            pid = int.from_bytes(info[41:43], 'little')
            vid = int.from_bytes(info[35:37], 'little')
            if vid != 0x03F0 or pid != DEVICES[key]['pid'] or info[54] != DEVICES[key]['leds']:
                raise RuntimeError('Device identity/LED count does not match this controller')
            self.info = {'firmware': f'{int.from_bytes(info[43:45], "little"):04x}',
                         'leds': info[54], 'layout': info[52]}
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def observe_stage(self, data):
        if self.key == 'mouse' and len(data) >= 3 and data[:2] == b'\xfb\x08' and data[2] < 4:
            self.dpi_stage = data[2]

    def exchange(self, data, response=None):
        # Drop stale notifications/acks before each transaction.
        while select.select([self.fd], [], [], 0)[0]:
            stale = os.read(self.fd, 64)
            if not stale:
                raise OSError('Device disconnected')
            self.observe_stage(stale)
        if os.write(self.fd, data) != 64:
            raise OSError('Incomplete HID write')
        deadline = time.monotonic() + (.25 if self.key == 'mouse' else .6)
        received = []
        while time.monotonic() < deadline:
            if not select.select([self.fd], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            item = os.read(self.fd, 64)
            if len(item) != 64:
                raise OSError('Incomplete HID response')
            # Keyboard notifications can contain key events. Never retain them.
            if item[0] == 0xFB:
                # Only retain the mouse DPI-stage notification, never key events.
                self.observe_stage(item)
                continue
            received.append(item)
            if item[:2] == b'\xff\x01' and item[14] in (data[0], data[0] + 1) and item[15] == data[1]:
                if item[16] != 0:
                    raise RuntimeError(f'Device rejected command {data[0]:02x}/{data[1]:02x}')
                self.acks += 1
                if response is None:
                    return received
            if response and item[:2] == bytes(response):
                return received
        raise TimeoutError('Device did not acknowledge; it may be asleep or disconnected')

    def brightness(self, value):
        level = value * 255 // 100
        self.exchange(packet([0x40, 1, 0, 0, level]))
        replies = self.exchange(packet([0x40, 2]), response=(0x41, 2))
        actual = next(b[2] for b in replies if b[:2] == b'\x41\x02')
        # Firmware converts through integer percentages, losing up to one step.
        if abs(actual - level) > 3:
            raise RuntimeError(f'Brightness verification failed: {actual} != {level}')
        return actual

    def frame(self, colors):
        if len(colors) != DEVICES[self.key]['leds']:
            raise ValueError('Wrong LED count')
        for data in frame_packets(colors):
            self.exchange(data)


def render(key, settings, now):
    count = DEVICES[key]['leds']
    mode = settings['mode']
    base = tuple(int(settings['color'][i:i + 2], 16) for i in (1, 3, 5))
    phase = now / (18 - settings['speed'] * .15)
    if mode == 'Off':
        return [(0, 0, 0)] * count
    if mode == 'Static':
        return [base] * count
    if mode == 'Breathing':
        factor = .08 + .92 * (.5 - .5 * math.cos(phase * 2 * math.pi))
        return [tuple(round(c * factor) for c in base)] * count
    offsets = [i / count if mode == 'Rainbow wave' and key == 'microphone' else 0. for i in range(count)]
    if mode == 'Rainbow wave' and key == 'keyboard':
        for item in LAYOUT:
            offsets[item['led'] - 1] = item['col'] / 17
    return [tuple(round(c * 255) for c in colorsys.hsv_to_rgb((phase + offset) % 1, 1, 1))
            for offset in offsets]


def take_lock():
    lock = open(RUNTIME / 'hyperx-rgb.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('RGB controller is already running')
    return lock


def device_worker(key, shared, guard, stopping):
    """Keep this device's direct-lighting session alive independently of others."""
    if key == 'microphone':
        return openrgb.worker(shared, guard, stopping, render)
    device = None
    applied = None
    frames = 0
    previous_frame = None
    max_gap = 0.0
    dpi_applied = None
    dpi_actual = None
    dpi_checked = 0
    dpi_error = None
    dpi_next = 0
    dpi_failures = 0
    reconnects = 0
    frame_failures = 0

    def mouse_heartbeat():
        nonlocal frames, previous_frame, max_gap
        if not settings['enabled']:
            return
        device.frame(render('mouse', settings, time.monotonic()))
        completed = time.monotonic()
        frames += 1
        if previous_frame is not None:
            max_gap = max(max_gap, completed - previous_frame)
        previous_frame = completed
    try:
        while not stopping.is_set():
            started = time.monotonic()
            with guard:
                settings = shared['settings'][key].copy()
                dpi_desired = shared.get('mouse_settings', {}).get('dpi') if key == 'mouse' else None
                dpi_request = (dpi_desired, shared.get('mouse_revision', 0))
            if not settings['enabled'] and dpi_desired is None:
                if device is not None:
                    device.close()
                    device = None
                with guard:
                    shared['devices'][key] = {'state': 'Paused', 'frames_sent': frames}
                stopping.wait(.1)
                continue
            try:
                if device is None:
                    path = find_device(key)
                    if path is None:
                        with guard:
                            shared['devices'][key] = {'state': 'Waiting for device'}
                        stopping.wait(.5 if key == 'mouse' else 2)
                        continue
                    device = Device(key, path)
                    reconnects += 1
                    applied = None
                    if key != 'mouse':
                        previous_frame = None
                    # Keep the last observed stage through transient RF failures.
                    # A reconnect must not jump back to an older saved stage.
                    dpi_checked, dpi_next = 0, 0
                dpi_due = key == 'mouse' and started >= dpi_next and (dpi_request != dpi_applied or dpi_checked == 0 or dpi_error is not None)
                if dpi_due:
                    # Restore RGB first; optional sensor queries cannot hold up
                    # the first frame after wake or reconnect.
                    mouse_heartbeat()
                if dpi_due:
                    try:
                        if dpi_desired is not None and dpi_request != dpi_applied:
                            dpi_actual = mouse.apply(device, dpi_desired, heartbeat=mouse_heartbeat)
                            dpi_applied = dpi_request
                        else:
                            _, current_dpi = mouse.read(device)
                            if dpi_desired is not None and (current_dpi['stages'] != dpi_desired['stages'] or
                                                           current_dpi['polling_hz'] != dpi_desired['polling_hz']):
                                restore = {**dpi_desired, 'active': (dpi_actual or dpi_desired)['active']}
                                mouse_heartbeat()
                                dpi_actual = mouse.apply(device, restore, heartbeat=mouse_heartbeat)
                            else:
                                dpi_actual = current_dpi
                            dpi_applied = dpi_request
                        dpi_error = None
                        dpi_next = started
                    except TimeoutError:
                        # A lost optional query is not a lost lighting session.
                        dpi_error = 'DPI query timed out; lighting continues, retrying shortly'
                        dpi_failures += 1
                        dpi_next = time.monotonic() + 5
                    except (ValueError, RuntimeError) as exc:
                        dpi_error = str(exc)
                        # Retry rejected settings only after a new request or reconnect.
                        dpi_applied = dpi_request
                        dpi_next = time.monotonic() + 30
                    dpi_checked = started
                if not settings['enabled']:
                    with guard:
                        shared['devices'][key] = {'state': 'Lighting paused', 'dpi': dpi_actual,
                                                 'dpi_error': dpi_error, **device.info}
                    stopping.wait(.25)
                    continue
                if applied is None or settings['brightness'] != applied['brightness']:
                    device.brightness(settings['brightness'])
                # Even identical static/off frames must be streamed: otherwise
                # firmware resumes its onboard lighting after host activity stops.
                device.frame(render(key, settings, started))
                frame_failures = 0
                if key == 'mouse' and dpi_actual and type(getattr(device, 'dpi_stage', None)) is int:
                    dpi_actual = {**dpi_actual, 'active': device.dpi_stage}
                completed = time.monotonic()
                frames += 1
                if previous_frame is not None:
                    max_gap = max(max_gap, completed - previous_frame)
                previous_frame = completed
                applied = settings
                with guard:
                    shared['devices'][key] = {
                        'state': 'Connected', 'path': device.path,
                        'mode': settings['mode'], 'color': settings['color'],
                        'brightness': settings['brightness'],
                        'acknowledged_commands': device.acks, 'frames_sent': frames,
                        'max_frame_gap_ms': round(max_gap * 1000, 1),
                        'target_fps': 20, **device.info,
                        **({'dpi': dpi_actual, 'dpi_error': dpi_error,
                            'dpi_query_failures': dpi_failures, 'connections': reconnects} if key == 'mouse' else {}),
                    }
            except (OSError, RuntimeError, TimeoutError, StopIteration) as exc:
                frame_failures += 1
                keep_mouse_session = key == 'mouse' and isinstance(exc, TimeoutError) and frame_failures < 3
                if device is not None and not keep_mouse_session:
                    device.close()
                    device = None
                message = str(exc)
                if key == 'mouse' and isinstance(exc, TimeoutError):
                    message = 'Waiting for mouse to wake or reconnect'
                with guard:
                    shared['devices'][key] = {'state': message, 'frames_sent': frames}
                stopping.wait((.05 if device is not None else .25) if key == 'mouse' else 3)
                continue
            stopping.wait(max(0, .05 - (time.monotonic() - started)))
    finally:
        if device is not None:
            device.close()


def daemon():
    lock = take_lock()
    stopping = threading.Event()
    guard = threading.Lock()
    # An invalid file must not cause a restart loop or re-enable hardware.
    try:
        initial = load_settings()
    except (OSError, ValueError, TypeError):
        initial = {key: {**value, 'enabled': False} for key, value in DEFAULT.items()}
    shared = {'settings': initial, 'devices': {}, 'mouse_settings': dict(mouse.DEFAULT)}

    def stop(*_):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    workers = [threading.Thread(target=device_worker, args=(key, shared, guard, stopping),
                                name=f'hyperx-{key}') for key in DEVICES]
    for worker in workers:
        worker.start()
    try:
        while not stopping.is_set():
            config_error = None
            try:
                settings = load_settings()
                with guard:
                    shared['settings'] = settings
            except (OSError, ValueError, KeyError, TypeError) as exc:
                config_error = str(exc)
            mouse_error = None
            try:
                with guard:
                    shared['mouse_settings'] = mouse.load()
                    shared['mouse_revision'] = mouse.CONFIG.stat().st_mtime_ns if mouse.CONFIG.exists() else 0
            except (OSError, ValueError, KeyError, TypeError) as exc:
                mouse_error = str(exc)
            with guard:
                status = dict(shared['devices'])
            atomic_json(STATUS, {'updated': time.time(), 'devices': status,
                                 'config_error': config_error, 'mouse_config_error': mouse_error})
            stopping.wait(.25)
    finally:
        stopping.set()
        for worker in workers:
            worker.join()
        STATUS.unlink(missing_ok=True)
        lock.close()


def read_status():
    try:
        data = json.loads(STATUS.read_text())
        if time.time() - data['updated'] > 10:
            return {'devices': {}, 'config_error': 'Controller is not responding'}
        return data
    except (OSError, ValueError, KeyError):
        return {'devices': {}, 'config_error': 'Controller is starting'}


def ensure_service():
    subprocess.run(['systemctl', '--user', 'enable', '--now', 'hyperx-rgb.service'],
                   check=True, capture_output=True, text=True, timeout=15)


def gui(default_page='lighting'):
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw, Gdk, GLib

    app = Adw.Application(application_id='local.hyperx.RGB')

    def activate(application):
        if application.get_active_window():
            application.get_active_window().present()
            return
        service_error = None
        try:
            ensure_service()
        except (OSError, subprocess.SubprocessError):
            service_error = 'Background service unavailable. Run the installer, then reopen the app.'
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        window = Adw.ApplicationWindow(application=application, title='HyperX Open Lighting')
        window.set_icon_name('local.hyperx.RGB')
        window.set_default_size(820, 720)
        window.set_resizable(True)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title='HyperX Open Lighting', subtitle='Lighting & mouse control'))
        about = Gtk.Button(icon_name='help-about-symbolic')
        about.set_tooltip_text('About HyperX Open Lighting')
        def show_about(_button):
            dialog = Adw.AboutWindow(transient_for=window, modal=True,
                                    application_name='HyperX Open Lighting',
                                    application_icon='local.hyperx.RGB',
                                    developer_name='HyperX Open Lighting contributors',
                                    version='0.3.0',
                                    website='https://github.com/abhinavpathak9873/hyperx_open_lighting',
                                    issue_url='https://github.com/abhinavpathak9873/hyperx_open_lighting/issues',
                                    license_type=Gtk.License.MIT_X11)
            dialog.present()
        about.connect('clicked', show_about)
        header.pack_start(about)
        outer.append(header)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for method in ('set_margin_start', 'set_margin_end', 'set_margin_top', 'set_margin_bottom'):
            getattr(content, method)(24)
        stack = Gtk.Stack()
        stack.set_vexpand(True)
        switcher = Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER)
        outer.append(switcher)
        outer.append(stack)
        lighting_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        lighting_scroll.set_child(content)
        stack.add_titled(lighting_scroll, 'lighting', 'Lighting')
        stack.add_titled(mouse_ui.page(Gtk, Adw, GLib, read_status, atomic_json), 'mouse', 'Mouse')
        stack.set_visible_child_name(default_page)
        cards = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=16, row_spacing=16, homogeneous=True)
        cards.set_max_children_per_line(3)
        cards.set_min_children_per_line(1)
        content.append(cards)
        try:
            settings = load_settings()
        except (OSError, ValueError, TypeError):
            settings = {k: {**v, 'enabled': False} for k, v in DEFAULT.items()}
            service_error = 'Invalid saved settings. Choose your settings and Apply & save to repair.'
        controls, labels = {}, {}

        def label(text, css=None):
            widget = Gtk.Label(label=text, xalign=0)
            if css:
                widget.add_css_class(css)
            return widget

        for key, spec in DEVICES.items():
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            frame.add_css_class('card')
            cards.insert(frame, -1)
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for method in ('set_margin_start', 'set_margin_end', 'set_margin_top', 'set_margin_bottom'):
                getattr(card, method)(20)
            frame.append(card)
            card.append(label(key.upper(), 'caption'))
            title = label(spec['name'], 'heading')
            title.set_wrap(True)
            title.set_max_width_chars(28)
            card.append(title)
            state = label('Connecting…', 'dim-label')
            state.set_wrap(True)
            state.set_max_width_chars(32)
            labels[key] = state
            card.append(state)
            enabled = Gtk.Switch(active=settings[key]['enabled'])
            enable_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            enable_label = label('Enable lighting')
            enable_label.set_hexpand(True)
            enable_row.append(enable_label)
            enable_row.append(enabled)
            card.append(enable_row)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_top(8)
            color_dialog = Gtk.ColorDialog()
            color_dialog.set_with_alpha(False)
            color = Gtk.ColorDialogButton(dialog=color_dialog)
            rgba = Gdk.RGBA()
            rgba.parse(settings[key]['color'])
            color.set_rgba(rgba)
            color.set_tooltip_text('Choose a color')
            color.set_size_request(62, 42)
            entry = Gtk.Entry(text=settings[key]['color'])
            entry.set_hexpand(True)
            entry.set_max_length(7)
            entry.set_width_chars(8)
            entry.set_placeholder_text('#ff0000')
            entry.set_tooltip_text('Hex RGB color')
            row.append(color)
            row.append(entry)
            card.append(row)

            def color_changed(button, _param, target=entry):
                rgba = button.get_rgba()
                target.set_text('#' + ''.join(f'{round(v * 255):02x}' for v in (rgba.red, rgba.green, rgba.blue)))
            color.connect('notify::rgba', color_changed)
            card.append(label('Effect', 'dim-label'))
            mode = Gtk.DropDown.new_from_strings(modes_for(key))
            mode.set_selected(modes_for(key).index(settings[key]['mode']))
            card.append(mode)
            values = {'color': entry, 'swatch': color, 'mode': mode, 'enabled': enabled}
            for field, text in [('brightness', 'Brightness'), ('speed', 'Effect speed')]:
                card.append(label(text, 'dim-label'))
                slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                slider.set_value(settings[key][field])
                slider.set_draw_value(True)
                slider.set_digits(0)
                slider.set_value_pos(Gtk.PositionType.RIGHT)
                card.append(slider)
                values[field] = slider
            if key == 'microphone':
                note = label('Follow OpenRGB uses its colors, effects and profiles. Manual effects override it until you select Follow OpenRGB again.', 'dim-label')
                note.set_wrap(True)
                note.set_max_width_chars(32)
                card.append(note)
                def follow_changed(dropdown, _param, widgets=values):
                    follow = modes_for('microphone')[dropdown.get_selected()] == 'Follow OpenRGB'
                    for field in ('color', 'swatch', 'brightness', 'speed'):
                        widgets[field].set_sensitive(not follow)
                mode.connect('notify::selected', follow_changed)
                follow_changed(mode, None)
            controls[key] = values
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hint = label('Keeps running when this window closes.', 'dim-label')
        hint.set_hexpand(True)
        hint.set_wrap(True)
        footer.append(hint)
        button = Gtk.Button(label='Apply & save')
        button.add_css_class('suggested-action')
        button.add_css_class('pill')
        footer.append(button)
        footer.set_margin_start(24)
        footer.set_margin_end(24)
        outer.append(footer)
        pause = Gtk.Button(label='Pause app control')
        pause.set_margin_start(24)
        pause.set_margin_end(24)
        pause.set_margin_bottom(16)
        outer.append(pause)

        def pause_all(_button):
            try:
                current = load_settings()
                for key in DEVICES:
                    current[key]['enabled'] = False
                    controls[key]['enabled'].set_active(False)
                atomic_json(CONFIG, current)
                hint.set_label('App control paused. OpenRGB continues managing the microphone.')
            except (OSError, ValueError) as exc:
                hint.set_label(str(exc))
        pause.connect('clicked', pause_all)

        def apply(_button):
            try:
                data = {key: {'color': c['color'].get_text(),
                              'mode': modes_for(key)[c['mode'].get_selected()],
                              'brightness': round(c['brightness'].get_value()),
                              'speed': round(c['speed'].get_value()),
                              'enabled': c['enabled'].get_active()}
                        for key, c in controls.items()}
                data = validate(data)
                atomic_json(CONFIG, data)
                for key in DEVICES:
                    rgba = Gdk.RGBA()
                    rgba.parse(data[key]['color'])
                    controls[key]['swatch'].set_rgba(rgba)
                hint.set_label('Saved. Lighting stays active when closed.')
            except (ValueError, OSError) as exc:
                hint.set_label(str(exc))
        button.connect('clicked', apply)

        def refresh():
            if not window.get_visible():
                return GLib.SOURCE_REMOVE
            current = read_status()
            for key in DEVICES:
                info = current.get('devices', {}).get(key, {})
                state = info.get('state', 'Connecting…')
                if state == 'Connected':
                    state = ('Connected · Follow OpenRGB · 108 LEDs' if info['mode'] == 'Follow OpenRGB' else f'Connected · {info["mode"]} · {info["brightness"]}%')
                labels[key].set_label(state)
            if service_error or current.get('config_error'):
                hint.set_label(service_error or current['config_error'])
            return GLib.SOURCE_CONTINUE
        window.set_content(outer)
        window.present()
        GLib.timeout_add(800, refresh)

    app.connect('activate', activate)
    app.run([sys.argv[0]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='HyperX Open Lighting 0.3.0')
    parser.add_argument('--doctor', action='store_true', help='Show dependency and device access checks')
    switch = parser.add_mutually_exclusive_group()
    switch.add_argument('--enable', action='store_true', help='Enable selected devices')
    switch.add_argument('--disable', action='store_true', help='Pause selected devices immediately')
    parser.add_argument('--daemon', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--mouse-settings', action='store_true', help='Open the Mouse tab')
    parser.add_argument('--device', choices=('keyboard', 'mouse', 'microphone', 'both', 'all'), default='both')
    parser.add_argument('--color', help='Hex RGB color, e.g. ff0000')
    parser.add_argument('--brightness', type=int)
    parser.add_argument('--mode', choices=MODES + ('Follow OpenRGB',))
    parser.add_argument('--dpi', type=int, help='Set active mouse stage DPI (50–12000, steps of 50)')
    parser.add_argument('--polling-rate', type=int, choices=tuple(mouse.RATES), help='Mouse polling rate in Hz')
    args = parser.parse_args()
    if args.doctor:
        result = {}
        for key in DEVICES:
            if key == 'microphone':
                sdk = None
                try:
                    sdk = openrgb.SDK()
                    found = any(name == openrgb.NAME and len(colors) == 432 for _, name, colors in sdk.devices())
                    result[key] = {'connected': found, 'backend': 'OpenRGB SDK at 127.0.0.1:6742'}
                except (OSError, RuntimeError, ValueError) as exc:
                    result[key] = {'connected': False, 'error': str(exc)}
                finally:
                    if sdk:
                        sdk.close()
                continue
            path = find_device(key)
            result[key] = {'connected': path is not None, 'control_interface': path,
                           'accessible': bool(path and os.access(path, os.R_OK | os.W_OK))}
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            gi.require_version('Adw', '1')
            from gi.repository import Gtk
            result['gui_dependencies'] = Gtk.get_minor_version() >= 10
        except (ImportError, ValueError):
            result['gui_dependencies'] = False
        result['service'] = read_status()
        print(json.dumps(result, indent=2))
    elif args.daemon:
        daemon()
    elif args.status:
        print(json.dumps(read_status(), indent=2))
    elif args.dpi is not None or args.polling_rate is not None:
        settings = mouse.load()
        actual = read_status().get('devices', {}).get('mouse', {}).get('dpi')
        dpi = settings['dpi'] or actual
        if dpi is None:
            raise RuntimeError('Wake the mouse and wait for its DPI status before changing it')
        dpi = json.loads(json.dumps(dpi))
        if args.dpi is not None:
            dpi['stages'][dpi['active']] = args.dpi
        if args.polling_rate is not None:
            dpi['polling_hz'] = args.polling_rate
        settings['dpi'] = dpi
        atomic_json(mouse.CONFIG, mouse.validate(settings))
        ensure_service()
        print('Saved mouse settings. Check --status for hardware readback.')
    elif args.color is not None or args.brightness is not None or args.mode is not None or args.enable or args.disable:
        settings = load_settings()
        for key in (tuple(DEVICES) if args.device == 'all' else ('keyboard', 'mouse') if args.device == 'both' else (args.device,)):
            if args.enable or args.disable:
                settings[key]['enabled'] = args.enable
            if args.color is not None:
                settings[key]['color'] = '#' + args.color.lstrip('#')
                if key == 'microphone' and args.mode is None:
                    settings[key]['mode'] = 'Static'
            if args.brightness is not None:
                settings[key]['brightness'] = args.brightness
            if args.mode is not None:
                settings[key]['mode'] = args.mode
        atomic_json(CONFIG, validate(settings))
        ensure_service()
        print('Saved settings.')
    else:
        gui('mouse' if args.mouse_settings else 'lighting')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        print(f'HyperX Open Lighting: {exc}', file=sys.stderr)
        sys.exit(1)
