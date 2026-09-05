"""
Runtime shims so the same addon codebase runs on Blender 4 and Blender 5.

Prefer feature detection (hasattr) over hard version cuts when both APIs
exist. Blender 5 support is kept; nothing here removes 5.x code paths.
"""

import bpy


def blender_version():
    return bpy.app.version


def is_blender_5_or_newer():
    return bpy.app.version >= (5, 0, 0)


def uses_legacy_action_fcurves(action=None):
    """
    True when action.fcurves still exists.

    Blender 4.0–4.3: legacy only.
    Blender 4.4–4.5: legacy wrapper plus layered slots.
    Blender 5.0+: layered only; action.fcurves is gone.
    """
    if action is not None:
        return getattr(action, 'fcurves', None) is not None
    return bpy.app.version < (5, 0, 0)


def slot_display_name(slot):
    """Return the human-readable slot name (Blender 5.x uses name_display)."""
    if slot is None:
        return ""
    name = getattr(slot, 'name_display', None)
    if name:
        return name
    return getattr(slot, 'name', '') or ''


def slot_id_type(slot):
    id_type = getattr(slot, 'target_id_type', None)
    if id_type and id_type != 'UNSPECIFIED':
        return id_type
    return (
        getattr(slot, 'type', None)
        or getattr(slot, 'id_type', None)
        or id_type
    )


def _slot_id_type(slot):
    return slot_id_type(slot)


def id_type_for_id_data(id_data):
    """Return the layered-action slot type for an animated ID datablock."""
    if isinstance(id_data, bpy.types.Armature):
        return 'ARMATURE'
    if isinstance(id_data, bpy.types.Light):
        return 'LIGHT'
    if isinstance(id_data, bpy.types.Camera):
        return 'CAMERA'
    return 'OBJECT'


def ensure_action_slot(action, id_data, id_type=None):
    """
    Ensure an action has a slot named after id_data.

    Blender matches slots by name when switching actions, so every imported
    animation for the same armature/object should share that name.
    """
    if action is None or id_data is None:
        return None
    slots = getattr(action, 'slots', None)
    if slots is None:
        return None
    if id_type is None:
        id_type = id_type_for_id_data(id_data)
    slot_name = id_data.name
    for slot in slots:
        if _slot_id_type(slot) == id_type and slot_display_name(slot) == slot_name:
            return slot
    try:
        return slots.new(id_type, name=slot_name)
    except (AttributeError, TypeError, RuntimeError):
        return None


def _find_action_slot(animation_data, action):
    """Find the slot on action that matches this animation_data owner."""
    if action is None or not hasattr(animation_data, 'action_slot'):
        return None
    owner = animation_data.id_data
    owner_name = owner.name
    id_type = id_type_for_id_data(owner)

    slots = getattr(action, 'slots', None)
    if slots:
        for slot in slots:
            if _slot_id_type(slot) == id_type and slot_display_name(slot) == owner_name:
                return slot

    if hasattr(animation_data, 'action_suitable_slots'):
        for slot in animation_data.action_suitable_slots:
            if slot_display_name(slot) == owner_name:
                return slot
        if animation_data.action_suitable_slots:
            return animation_data.action_suitable_slots[0]

    if slots:
        for slot in slots:
            if _slot_id_type(slot) == id_type:
                return slot
        if len(slots) > 0:
            return slots[0]
    return None


def assign_action(animation_data, action):
    """Assign an action and, on 4.4+/5.x, a slot compatible with the animated ID."""
    if animation_data is None:
        return
    animation_data.action = action
    if action is None:
        return
    ensure_action_slot(action, animation_data.id_data)
    slot = _find_action_slot(animation_data, action)
    if slot is not None:
        try:
            animation_data.action_slot = slot
        except (AttributeError, TypeError, RuntimeError):
            pass


def ensure_bone_collection(armature_data, name):
    """Armature bone collections exist on Blender 4.0+ (replacing bone layers)."""
    collections = getattr(armature_data, 'collections', None)
    if collections is None:
        return None
    existing = collections.get(name) if hasattr(collections, 'get') else (
        collections[name] if name in collections else None
    )
    if existing is not None:
        return existing
    try:
        return collections.new(name)
    except TypeError:
        return collections.new(name=name)


def set_pose_bone_select(pose_bone, selected=True):
    """Select a pose bone on Blender 4 (Bone.select) and Blender 5 (PoseBone.select)."""
    if pose_bone is None:
        return
    if hasattr(pose_bone, 'select'):
        try:
            pose_bone.select = selected
            return
        except (AttributeError, TypeError):
            pass
    inner = getattr(pose_bone, 'bone', None)
    if inner is not None and hasattr(inner, 'select'):
        inner.select = selected


def is_pose_bone_selected(pose_bone):
    if pose_bone is None:
        return False
    if hasattr(pose_bone, 'select'):
        try:
            return bool(pose_bone.select)
        except (AttributeError, TypeError):
            pass
    inner = getattr(pose_bone, 'bone', None)
    return bool(getattr(inner, 'select', False))


def is_armature_bone_selected(armature_obj, bone):
    """True if an armature data Bone is selected (Blender 4 Bone.select / 5 PoseBone.select)."""
    if bone is None:
        return False
    if hasattr(bone, 'select'):
        try:
            return bool(bone.select)
        except (AttributeError, TypeError):
            pass
    if armature_obj is not None:
        pose_bones = getattr(getattr(armature_obj, 'pose', None), 'bones', None)
        if pose_bones is not None and bone.name in pose_bones:
            return is_pose_bone_selected(pose_bones[bone.name])
    return False


def assign_bone_to_collection(collection, bone):
    if collection is None or bone is None:
        return
    try:
        collection.assign(bone)
    except (TypeError, AttributeError):
        inner = getattr(bone, 'bone', None)
        if inner is not None:
            collection.assign(inner)


def unassign_bone_from_collection(collection, bone):
    if collection is None or bone is None:
        return
    try:
        collection.unassign(bone)
        return
    except (TypeError, AttributeError, RuntimeError):
        pass
    inner = getattr(bone, 'bone', None)
    if inner is not None:
        try:
            collection.unassign(inner)
        except (TypeError, AttributeError, RuntimeError):
            pass


def isolate_bone_in_collection(collection, bone):
    """Put the bone only in this collection.

    Pose Mode visibility in Blender 4+ is the union of a bone's collections.
    Bone.hide only affects Edit Mode on Blender 5, so hiding one collection
    does nothing if the bone is still in a visible collection such as
    Standard Bones.
    """
    if collection is None or bone is None:
        return
    assign_bone_to_collection(collection, bone)
    memberships = getattr(bone, 'collections', None)
    if memberships is None:
        inner = getattr(bone, 'bone', None)
        memberships = getattr(inner, 'collections', None) if inner is not None else None
    if memberships is None:
        return
    for other in list(memberships):
        if other == collection:
            continue
        unassign_bone_from_collection(other, bone)


def draw_progress(layout, factor, text=None, progress_type='BAR'):
    """UILayout.progress exists on 4.1+; fall back to a label on 4.0."""
    row = layout.row()
    progress = getattr(row, 'progress', None)
    if progress is None:
        row.label(text=text or f"{int(max(0.0, min(1.0, factor)) * 100)}%")
        return row
    try:
        if text:
            progress(factor=factor, type=progress_type, text=text)
        else:
            progress(factor=factor, type=progress_type)
    except TypeError:
        progress(factor=factor, type=progress_type)
        if text:
            layout.label(text=text)
    return row


def register_node_categories(identifier, categories):
    try:
        import nodeitems_utils
        nodeitems_utils.register_node_categories(identifier, categories)
        return True
    except Exception as error:
        print(f"Could not register node categories '{identifier}': {error}")
        return False


def unregister_node_categories(identifier):
    try:
        import nodeitems_utils
        nodeitems_utils.unregister_node_categories(identifier)
    except Exception:
        pass
