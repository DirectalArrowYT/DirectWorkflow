"""
Reading the effect calls out of a move's effect script (ACMD), so a move's effects can be
placed in a shot the way the game plays them.

Scripts come as smashline Rust (`macros::EFFECT(agent, Hash40::new("x"), ...)`,
`frame(agent.lua_state_agent, 9.0)`) or as dumped scripts in the same shape without the
Rust around it; both read the same here. Only what places, tints, times or stops an
effect is read; everything else (conditions, loops, other modules) is skipped, so a
script that branches plays every branch.
"""

import re

# What each call's arguments are, after the agent. `bone` is a Hash40 bone name.
# frame: when it happens; follow: 'all' (moves and turns with the bone), 'pos' (moves
# with it, keeps its turn), None (placed once).
_PLACE = {
    'EFFECT': None,
    'EFFECT_FOLLOW': 'all',
    'EFFECT_FOLLOW_NO_STOP': 'all',
    'EFFECT_FOLLOW_FLIP': 'all',
    'EFFECT_FOLLOW_ALPHA': 'all',
    'EFFECT_FLW_POS': 'pos',
    'EFFECT_FLW_POS_NO_STOP': 'pos',
    'EFFECT_FLIP': None,
    'EFFECT_ALPHA': None,
    'LANDING_EFFECT': None,
    'LANDING_EFFECT_FLIP': None,
    'FOOT_EFFECT': None,
    'FOOT_EFFECT_FLIP': None,
}
# Placed on the ground under the bone, as the game's landing and foot effects are.
_GROUNDED = {'LANDING_EFFECT', 'LANDING_EFFECT_FLIP', 'FOOT_EFFECT', 'FOOT_EFFECT_FLIP'}

_CALL = re.compile(r'(?:macros::)?\b([A-Z][A-Z0-9_]*|frame|wait)\s*\(')
_HASH = re.compile(r'Hash40::new\(\s*"([^"]*)"\s*\)|hash40\(\s*"([^"]*)"\s*\)|"([^"]*)"')
_NUMBER = re.compile(r'^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$', re.I)


def _arguments(text, at):
    """The top-level arguments of the call whose '(' is at text[at-1], and where it ends."""
    depth, start, args = 1, at, []
    i = at
    while i < len(text) and depth:
        c = text[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth == 0:
                args.append(text[start:i].strip())
        elif c == ',' and depth == 1:
            args.append(text[start:i].strip())
            start = i + 1
        elif c == '"':
            i = text.index('"', i + 1)
        i += 1
    return args, i


def _value(arg):
    """A number, a quoted or Hash40 name, true/false, or the text itself."""
    m = _HASH.fullmatch(arg.strip())
    if m:
        return next(g for g in m.groups() if g is not None)
    a = arg.strip().rstrip('f')
    a = re.sub(r'^Some\((.*)\)$', r'\1', a)
    if _NUMBER.match(a):
        return float(a)
    if a in ('true', 'false'):
        return a == 'true'
    return arg.strip()


def function_body(text, name):
    """The body of the script function `name` (`effect_attack11`), or the whole text when it
    isn't there (a file holding one dumped script)."""
    m = re.search(r'\bfn\s+' + re.escape(name) + r'\s*\(', text) or re.search(r'\b' + re.escape(name) + r'\b', text)
    if m is None:
        return None
    brace = text.find('{', m.end())
    if brace < 0:
        return text[m.end():]
    depth, i = 0, brace
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[brace + 1:i]
        i += 1
    return text[brace + 1:]


def scripts(text):
    """The effect script functions a file defines."""
    return re.findall(r'\bfn\s+(effect_[A-Za-z0-9_]+)\s*\(', text)


def read(body):
    """The effects a script body plays: [{frame, name, bone, offset, rotation, size,
    follow, grounded, rate, colour, alpha, stop, end}], frames counted as the game's
    motion frames."""
    frame = 0.0
    effects = []
    last = None
    for m in _CALL.finditer(body):
        call = m.group(1)
        args, _ = _arguments(body, m.end())
        values = [_value(a) for a in args]
        numbers = [v for v in values if isinstance(v, float)]
        if call == 'frame':
            frame = numbers[-1] if numbers else frame
        elif call == 'wait':
            frame += numbers[-1] if numbers else 0.0
        elif call in _PLACE:
            # agent, name, bone, x y z, rx ry rz, size, ...
            names = [v for v in values[1:3] if isinstance(v, str)]
            nums = [v for v in values[3:] if isinstance(v, float)]
            if len(names) < 2 or len(nums) < 7:
                continue
            last = {
                'frame': frame,
                'call': call,
                'name': names[0],
                'bone': names[1],
                'offset': nums[0:3],
                'rotation': nums[3:6],
                'size': nums[6],
                'follow': _PLACE[call],
                'grounded': call in _GROUNDED,
                'rate': 1.0,
                'colour': [1.0, 1.0, 1.0, 1.0],
                'stop': None,
            }
            effects.append(last)
        elif last is not None and call == 'LAST_EFFECT_SET_RATE' and numbers:
            last['rate'] = numbers[0]
        elif last is not None and call in ('LAST_EFFECT_SET_COLOR', 'LAST_PARTICLE_SET_COLOR') and len(numbers) >= 3:
            last['colour'][:3] = numbers[:3]
        elif last is not None and call == 'LAST_EFFECT_SET_ALPHA' and numbers:
            last['colour'][3] = numbers[0]
        elif call in ('EFFECT_OFF_KIND', 'EFFECT_DETACH_KIND'):
            name = values[1] if len(values) > 1 and isinstance(values[1], str) else None
            for e in effects:
                if name and e['name'].lower() == name.lower() and e['stop'] is None and e['frame'] <= frame:
                    e['stop'] = frame
    return effects
