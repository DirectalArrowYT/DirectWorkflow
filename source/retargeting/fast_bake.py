"""Bulk visual action baking for the Ultimate bake dialog."""

import bpy

from ...expy_kit.operators import (
    SourceObjectBakeLock,
    bake_one_constrained_action,
    resolve_bake_armature_pair,
    select_bones_for_visual_bake,
)


def bake_visible_actions(
    context,
    source_armature,
    dest_armature,
    actions_to_bake,
    fake_user_new=True,
    clear_users_old=True,
    keep_ik_bones=True,
):
    """Visually bake a list of actions from source_armature onto dest_armature."""
    if not actions_to_bake:
        return 0

    action_armature, bake_armature = resolve_bake_armature_pair(dest_armature)
    if action_armature is None or bake_armature is None:
        action_armature = source_armature
        bake_armature = dest_armature

    if bake_armature.animation_data is None:
        bake_armature.animation_data_create()
    if action_armature.animation_data is None:
        action_armature.animation_data_create()

    baked_count = 0
    use_retarget_clean = action_armature != bake_armature
    lock_source = use_retarget_clean

    try:
        context.window.cursor_modal_set('WAIT')

        def _bake_actions():
            nonlocal baked_count
            for action in list(actions_to_bake):
                bone_names = select_bones_for_visual_bake(
                    action_armature,
                    bake_armature,
                    use_deform=True,
                    keep_ik_bones=keep_ik_bones,
                )
                if not bone_names:
                    continue

                original_name = action.name
                baked_action = bake_one_constrained_action(
                    context,
                    action_armature,
                    bake_armature,
                    action,
                    bone_names,
                    fake_user_new=fake_user_new,
                    use_retarget_clean=use_retarget_clean,
                    for_visible_bake=True,
                    lock_source_object=not lock_source,
                )
                if baked_action is None:
                    continue

                if clear_users_old:
                    old_action = bpy.data.actions.get(f"{original_name}_old")
                    if old_action and old_action.users > 0:
                        old_action.user_clear()

                baked_count += 1

        if lock_source:
            with SourceObjectBakeLock(action_armature):
                _bake_actions()
        else:
            _bake_actions()
    finally:
        context.window.cursor_modal_restore()

    return baked_count
