"""Mouse page; hardware updates go through the existing single-owner daemon."""
import threading
try:
    from . import mouse
except ImportError:
    import mouse


def page(Gtk, Adw, GLib, read_status, save_json):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    for edge in ('start', 'end', 'top', 'bottom'):
        getattr(box, 'set_margin_' + edge)(24)

    def text(value, style=None):
        item = Gtk.Label(label=value, xalign=0, wrap=True)
        if style:
            item.add_css_class(style)
        return item

    def row(group, title, control, subtitle=None):
        r = Adw.ActionRow(title=title, subtitle=subtitle or '')
        control.set_valign(Gtk.Align.CENTER)
        r.add_suffix(control)
        group.add(r)
        return r

    try:
        saved = mouse.load()
        error = None
    except (OSError, ValueError) as exc:
        saved, error = dict(mouse.DEFAULT), str(exc)
    actual = read_status().get('devices', {}).get('mouse', {}).get('dpi')
    initial = saved['dpi'] or actual or {'stages': [400, 800, 1600, 3200], 'active': 1, 'polling_hz': 1000}
    box.append(text('Pulsefire Haste 2 Core Wireless', 'title-2'))
    status = text('Reading mouse settings…', 'dim-label')
    box.append(status)
    hardware = Adw.PreferencesGroup(title='Hardware DPI', description='50–12,000 DPI in steps of 50. Changes affect desktop and games.')
    box.append(hardware)
    manage = Gtk.Switch(active=saved['dpi'] is not None)
    row(hardware, 'Customize hardware DPI', manage, 'Saved settings restore at login and reconnect. Turning off leaves current hardware values in place.')
    spins = []
    locked = Gtk.Switch(active=saved['fixed_dpi'])
    row(hardware, 'Keep a single DPI', locked, 'Edits stay synchronized across stages; the DPI button cannot change speed.')
    for i, value in enumerate(initial['stages']):
        spin = Gtk.SpinButton.new_with_range(50, 12000, 50)
        spin.set_value(value)
        spin.set_snap_to_ticks(True)
        spin.set_numeric(True)
        spins.append(spin)
        row(hardware, f'Stage {i + 1}', spin)
    active = Gtk.DropDown.new_from_strings(['Stage 1', 'Stage 2', 'Stage 3', 'Stage 4'])
    active.set_selected(initial['active'])
    row(hardware, 'Selected stage', active, 'The mouse’s DPI button can still cycle through the four stages.')
    rates = list(mouse.RATES)
    polling = Gtk.DropDown.new_from_strings([f'{v:,} Hz' for v in rates])
    polling.set_selected(rates.index(initial['polling_hz']))
    row(hardware, 'Polling rate', polling, 'Higher rates can use more battery.')
    syncing = False
    def spin_changed(changed):
        nonlocal syncing
        manage.set_active(True)
        if syncing or not locked.get_active():
            return
        syncing = True
        try:
            for spin in spins:
                spin.set_value(changed.get_value())
        finally:
            syncing = False
    for spin in spins:
        spin.connect('value-changed', spin_changed)
    locked.connect('notify::active', lambda *_: spin_changed(spins[active.get_selected()]))
    for dropdown in (active, polling):
        dropdown.connect('notify::selected', lambda *_: manage.set_active(True))
    load_button = Gtk.Button(label='Use current mouse values')
    box.append(load_button)

    def load_actual(_):
        now = read_status().get('devices', {}).get('mouse', {}).get('dpi')
        if not now:
            hint.set_label('Wake or reconnect the mouse, then try again.')
            return
        for spin, value in zip(spins, now['stages']):
            spin.set_value(value)
        active.set_selected(now['active'])
        polling.set_selected(rates.index(now['polling_hz']))
        hint.set_label('Current hardware values loaded. Apply to save your changes.')
    load_button.connect('clicked', load_actual)
    fixed = Gtk.Button(label='Use one DPI for all stages')
    box.append(fixed)
    def fixed_dpi(_):
        locked.set_active(True)
        selected = spins[active.get_selected()]
        selected.update()
        value = selected.get_value_as_int()
        for spin in spins:
            spin.set_value(value)
        manage.set_active(True)
        hint.set_label(f'Prepared {value:,} DPI for every stage. Apply to prevent the DPI button changing speed.')
    fixed.connect('clicked', fixed_dpi)

    pointer = Adw.PreferencesGroup(title='Desktop pointer', description='Applies only to this HyperX mouse in Hyprland. Games using raw input may ignore these controls.')
    box.append(pointer)
    can_pointer = mouse.available()
    pointer.set_sensitive(can_pointer)
    p = saved['pointer'] or {'sensitivity': 0., 'acceleration': 'adaptive', 'scroll_factor': 1., 'natural_scroll': False}
    override = Gtk.Switch(active=saved['pointer'] is not None)
    row(pointer, 'Customize desktop pointer', override, 'Turn off and Apply to return to your desktop configuration.')
    acceleration = Gtk.DropDown.new_from_strings(['Off · consistent movement', 'Adaptive · faster when moved quickly'])
    acceleration.set_selected(0 if p['acceleration'] == 'flat' else 1)
    row(pointer, 'Mouse acceleration', acceleration)
    sensitivity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, .05)
    sensitivity.set_size_request(260, -1)
    sensitivity.set_digits(2)
    sensitivity.set_draw_value(True)
    sensitivity.set_value_pos(Gtk.PositionType.RIGHT)
    sensitivity.set_value(p['sensitivity'])
    row(pointer, 'Pointer sensitivity', sensitivity, '−1 slower · 0 neutral · +1 faster; independent of hardware DPI')
    scroll = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, .1, 5, .1)
    scroll.set_size_request(260, -1)
    scroll.set_digits(1)
    scroll.set_draw_value(True)
    scroll.set_value_pos(Gtk.PositionType.RIGHT)
    scroll.set_value(p['scroll_factor'])
    row(pointer, 'Scroll speed multiplier', scroll, '1.0 is normal speed; apps may apply their own scrolling behavior.')
    natural = Gtk.Switch(active=p['natural_scroll'])
    row(pointer, 'Natural scrolling', natural, 'Reverse the scroll direction.')
    acceleration.connect('notify::selected', lambda *_: override.set_active(True))
    natural.connect('notify::active', lambda *_: override.set_active(True))
    for slider in (sensitivity, scroll):
        slider.connect('value-changed', lambda _: override.set_active(True))
    if not can_pointer:
        box.append(text('Desktop controls are available in a Hyprland session. Hardware DPI works independently.', 'dim-label'))

    presets = Gtk.Box(spacing=12)
    gaming = Gtk.Button(label='Gaming starting point')
    normal = Gtk.Button(label='Desktop defaults')
    presets.append(gaming)
    presets.append(normal)
    box.append(presets)
    def gaming_preset(_):
        locked.set_active(True)
        for spin, value in zip(spins, [800] * 4):
            spin.set_value(value)
        active.set_selected(1)
        polling.set_selected(rates.index(1000))
        manage.set_active(True)
        override.set_active(True)
        acceleration.set_selected(0)
        sensitivity.set_value(0)
        scroll.set_value(1)
        natural.set_active(False)
        hint.set_label('Prepared: 800 DPI, 1,000 Hz, no acceleration, neutral pointer speed. Apply when ready.')
    gaming.connect('clicked', gaming_preset)
    def desktop_defaults(_):
        override.set_active(False)
        hint.set_label('Apply to remove this app’s desktop pointer overrides. Hardware DPI is unchanged.')
    normal.connect('clicked', desktop_defaults)
    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for edge in ('start', 'end', 'top', 'bottom'):
        getattr(footer, 'set_margin_' + edge)(16)
    hint = text(error or 'Adjust values, then Apply. Lighting and wallpaper sync are saved separately.', 'dim-label')
    footer.append(hint)
    apply_button = Gtk.Button(label='Apply mouse settings')
    apply_button.add_css_class('suggested-action')
    footer.append(apply_button)

    def apply_clicked(_):
        try:
            for spin in spins:
                spin.update()
            data = {'dpi': {'stages': [s.get_value_as_int() for s in spins],
                            'active': active.get_selected(), 'polling_hz': rates[polling.get_selected()]}
                    if manage.get_active() else None,
                    'pointer': {'sensitivity': round(sensitivity.get_value(), 2),
                                'acceleration': ('flat', 'adaptive')[acceleration.get_selected()],
                                'scroll_factor': round(scroll.get_value(), 1),
                                'natural_scroll': natural.get_active()}
                    if override.get_active() and can_pointer else None,
                    'fixed_dpi': locked.get_active()}
            if not can_pointer:
                data['pointer'] = mouse.load()['pointer']
            data = mouse.validate(data)
        except (ValueError, OSError) as exc:
            hint.set_label(str(exc))
            return
        apply_button.set_sensitive(False)
        hint.set_label('Applying…')
        def complete(message, persisted=None):
            nonlocal saved
            if persisted is not None:
                saved = persisted
            hint.set_label(message)
            apply_button.set_sensitive(True)
            return GLib.SOURCE_REMOVE
        def work():
            previous = None
            changed_pointer = False
            try:
                previous = mouse.check_snapshot(saved)
                if can_pointer and (data['pointer'] is not None or previous['pointer'] is not None):
                    mouse.apply_pointer(data['pointer'])
                    changed_pointer = True
                save_json(mouse.CONFIG, data)
                message = 'Saved. Hardware status below confirms applied DPI; sleeping mice update when they wake.'
            except (OSError, ValueError, RuntimeError, mouse.subprocess.SubprocessError) as exc:
                message = str(exc)
                if changed_pointer:
                    try:
                        mouse.apply_pointer(previous['pointer'])
                    except Exception as rollback:
                        message += '; pointer rollback failed: ' + str(rollback)
                GLib.idle_add(complete, message)
                return
            GLib.idle_add(complete, message, data)
        threading.Thread(target=work).start()
    apply_button.connect('clicked', apply_clicked)
    confirmation = text('', 'dim-label')
    footer.append(confirmation)

    def refresh():
        if not box.get_root():
            return GLib.SOURCE_REMOVE
        state = read_status()
        info = state.get('devices', {}).get('mouse', {})
        dpi = info.get('dpi')
        if dpi:
            status.set_label(f'Hardware: {dpi["stages"][dpi["active"]]:,} DPI · stage {dpi["active"] + 1} · {dpi["polling_hz"]:,} Hz')
        else:
            status.set_label(info.get('state', 'Waiting for mouse') + ' · DPI not read yet')
        problem = state.get('mouse_config_error') or info.get('dpi_error')
        if problem:
            confirmation.set_label('Could not apply mouse settings: ' + problem)
        else:
            try:
                current_saved = mouse.load()
                desired = current_saved['dpi']
                if desired and dpi:
                    same_speed = current_saved['fixed_dpi'] and dpi['stages'] == desired['stages'] and dpi['polling_hz'] == desired['polling_hz']
                    confirmation.set_label('Single DPI verified on every stage.' if same_speed else
                        'Saved DPI table verified on mouse.' if dpi == desired else
                        'Current mouse stage/table differs from saved settings. Apply to restore your selection.')
                elif desired:
                    confirmation.set_label('Saved DPI settings are waiting for the mouse.')
                else:
                    confirmation.set_label('Using the mouse’s current DPI settings.')
            except (OSError, ValueError):
                pass
        return GLib.SOURCE_CONTINUE
    GLib.timeout_add(1000, refresh)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_child(box)
    # Wheel navigation must not silently edit a hovered DPI/acceleration control.
    def wheel_navigation(_controller, _dx, dy):
        adj = scroller.get_vadjustment()
        adj.set_value(adj.get_value() + dy * 45)
        return True
    for widget in [*spins, active, polling, acceleration, sensitivity, scroll]:
        controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect('scroll', wheel_navigation)
        widget.add_controller(controller)
    root.append(scroller)
    root.append(footer)
    return root
