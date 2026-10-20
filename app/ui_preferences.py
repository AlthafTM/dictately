"""Shared position labels and persistent desktop microphone identities."""
POSITIONS = {
    'Bottom left': 'bottom_left', 'Bottom center': 'bottom_center', 'Bottom right': 'bottom_right',
    'Top left': 'top_left', 'Top center': 'top_center', 'Top right': 'top_right',
}


def position_name(value):
    return next((label for label, code in POSITIONS.items() if code == value), 'Bottom center')


def overlay_origin(bounds, size, margin, position):
    left, top, width, height = bounds
    panel_width, panel_height = size
    right = max(left, left+width-panel_width-margin)
    bottom = max(top, top+height-panel_height-margin)
    x = min(left+margin, right) if position.endswith('_left') else right if position.endswith('_right') else left+max(0, width-panel_width)//2
    y = min(top+margin, bottom) if position.startswith('top_') else bottom
    return x, y


def desktop_microphones(sounddevice):
    hosts = sounddevice.query_hostapis()
    return [dict(index=index, name=device['name'], host=hosts[device['hostapi']]['name'])
            for index, device in enumerate(sounddevice.query_devices()) if device['max_input_channels'] > 0]


def resolve_microphone(preference, devices):
    if not preference:
        return None
    matches = [device for device in devices if device['name'] == preference.get('name') and device['host'] == preference.get('host')]
    if len(matches) != 1:
        raise RuntimeError('Your preferred microphone is unavailable or ambiguous. Choose a microphone in Audio & appearance')
    return matches[0]['index']
