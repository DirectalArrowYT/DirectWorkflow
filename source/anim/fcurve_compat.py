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


def get_all_action_fcurves(action: bpy.types.Action, id_type: str = 'OBJECT'):
    """Return every fcurve on an action across all layered slots."""
    if action is None:
        return []
    if uses_legacy_action_fcurves(action):
        return list(get_fcurves(action))

    fcurves = []
    seen = set()
    for channelbag in _iter_channelbags(action):
        for fc in channelbag.fcurves:
            key = (fc.data_path, fc.array_index)
            if key not in seen:
                seen.add(key)
                fcurves.append(fc)
    if not fcurves:
        for slot_type in ('ARMATURE', 'OBJECT', id_type):
            for fc in get_fcurves(action, id_type=slot_type):
                key = (fc.data_path, fc.array_index)
                if key not in seen:
                    seen.add(key)
                    fcurves.append(fc)
    return fcurves


def collect_actions_for_armatures(armatures):
    """
    Return actions that resolve on any of the given armatures and are eligible
    for retarget baking (skips SAP Data and _old backups).
    """
    armatures = _expand_bake_armatures(armatures)
    if not armatures:
        return []

    armature_names = {ob.name for ob in armatures}
    results = []
    seen = set()
    for action in bpy.data.actions:
        name = action.name
        if name in seen or _is_bake_backup_action(action):
            continue
        if any(_action_valid_for_armature(action, ob) for ob in armatures):
            seen.add(name)
            results.append(action)
            continue
        if not action_has_pose_fcurves(action):
            continue
        sap_armature = sap_armature_name_for_action(name)
        if sap_armature and sap_armature in armature_names:
            seen.add(name)
            results.append(action)
    return results
