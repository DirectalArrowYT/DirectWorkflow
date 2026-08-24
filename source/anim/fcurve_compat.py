"""
Compatibility layer for bpy.types.Action F-Curves on Blender 4 and 5.

Blender 4.0–4.3 expose the legacy `action.fcurves` / `action.groups` API.
Blender 4.4 introduced layered Actions (layers, strips, slots, channelbags)
and kept `action.fcurves` as a first-slot wrapper.
Blender 5.0 removed that wrapper. Code that used `action.fcurves.new(...)`
must now go through a channelbag.

These helpers feature-detect the available API so one codebase works on both.
"""

import bpy

from ..blender_compat import uses_legacy_action_fcurves


def _get_or_create_first_slot(action: bpy.types.Action, id_type: str = 'OBJECT'):
    if len(action.slots) == 0:
        return action.slots.new(id_type, name="Slot")
    return action.slots[0]


def _get_or_create_channelbag(action: bpy.types.Action, id_type: str = 'OBJECT', ensure: bool = True):
    if not ensure:
        if len(action.slots) == 0 or len(action.layers) == 0 or len(action.layers[0].strips) == 0:
            return None
        slot = action.slots[0]
        strip = action.layers[0].strips[0]
        return strip.channelbag(slot, ensure=False)

    slot = _get_or_create_first_slot(action, id_type)

    if len(action.layers) == 0:
        layer = action.layers.new("Layer")
    else:
        layer = action.layers[0]

    if len(layer.strips) == 0:
        strip = layer.strips.new(type='KEYFRAME')
    else:
        strip = layer.strips[0]

    return strip.channelbag(slot, ensure=True)


def get_fcurves(action: bpy.types.Action, id_type: str = 'OBJECT'):
    """
    Equivalent of the old `action.fcurves` for the action's first slot.
    This is read-only: it does NOT create a layer/strip/slot/channelbag if
    none exist yet. Use new_fcurve() to create F-Curves.
    """
    if action is None:
        return []
    if uses_legacy_action_fcurves(action):
        return action.fcurves
    channelbag = _get_or_create_channelbag(action, id_type, ensure=False)
    return channelbag.fcurves if channelbag is not None else []


def get_or_create_fcurves(action: bpy.types.Action, id_type: str = 'OBJECT'):
    """
    Like get_fcurves(), but ensures the layer/strip/slot/channelbag exist
    on Blender 5. On Blender 4 this is simply action.fcurves.
    """
    if action is None:
        return []
    if uses_legacy_action_fcurves(action):
        return action.fcurves
    channelbag = _get_or_create_channelbag(action, id_type, ensure=True)
    return channelbag.fcurves


def new_fcurve(action: bpy.types.Action, data_path: str, index: int = 0, action_group: str = '', id_type: str = 'OBJECT') -> bpy.types.FCurve:
    """Equivalent of the old `action.fcurves.new(...)`."""
    if uses_legacy_action_fcurves(action):
        return action.fcurves.new(data_path, index=index, action_group=action_group)
    channelbag = _get_or_create_channelbag(action, id_type, ensure=True)
    return channelbag.fcurves.new(data_path, index=index, group_name=action_group)


def find_fcurve(action: bpy.types.Action, data_path: str, index: int = 0, id_type: str = 'OBJECT'):
    """Equivalent of the old `action.fcurves.find(...)`."""
    if uses_legacy_action_fcurves(action):
        return action.fcurves.find(data_path, index=index)
    channelbag = _get_or_create_channelbag(action, id_type, ensure=False)
    if channelbag is None:
        return None
    return channelbag.fcurves.find(data_path, index=index)


def remove_fcurve(action: bpy.types.Action, fcurve: bpy.types.FCurve, id_type: str = 'OBJECT'):
    """Equivalent of the old `action.fcurves.remove(fcurve)`."""
    if uses_legacy_action_fcurves(action):
        action.fcurves.remove(fcurve)
        return
    channelbag = _get_or_create_channelbag(action, id_type, ensure=False)
    if channelbag is not None:
        channelbag.fcurves.remove(fcurve)


def get_id_action_fcurves(id_data):
    """
    Return the mutable F-Curve collection for id_data.animation_data.action,
    or None if the ID has no action. Safe on Blender 4 and 5.
    """
    try:
        action = id_data.animation_data.action
    except AttributeError:
        return None
    if action is None:
        return None
    return get_or_create_fcurves(action)
