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


def assign_action(animation_data, action):
    """Assign an action and, on 4.4+/5.x, the first action slot."""
    if animation_data is None:
        return
    animation_data.action = action
    if action is None:
        return
    slots = getattr(action, 'slots', None)
    if slots and len(slots) > 0 and hasattr(animation_data, 'action_slot'):
        try:
            animation_data.action_slot = slots[0]
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
