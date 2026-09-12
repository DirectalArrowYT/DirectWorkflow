"""
Bake straight from a folder of PSA files, without importing them all first.

A fighter's animation set is hundreds of sequences - Dabi ships 223 PSAs across
at/, co/, dm/ and the rest. Importing the lot to retarget them leaves every one
of those actions in the .blend forever, and every later bake then walks the
whole pile again.

This takes one PSA at a time: import it onto the source rig, bake it onto the
Smash rig through the constraints that are already there, then throw the
imported action away. Only the baked results stay in the file.

Give it an export folder as well and the baked action is written straight out
as .nuanmb through the addon's own animation exporter and then dropped too, so
the round trip - import, bake, export - leaves the .blend exactly as it started
rather than several hundred actions heavier.

Sub-folders are walked, because that is how the game ships them - one Animation
folder with a folder per category underneath.
"""

import os

import bpy

PSA_EXTENSION = '.psa'


def iter_psa_files(folder, recursive=True):
    """Every .psa under `folder`, sorted, sub-folders included."""
    folder = bpy.path.abspath(folder or '')
    if not folder or not os.path.isdir(folder):
        return []

    found = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            found.extend(os.path.join(root, name) for name in files
                         if name.lower().endswith(PSA_EXTENSION))
    else:
        found.extend(os.path.join(folder, name) for name in os.listdir(folder)
                     if name.lower().endswith(PSA_EXTENSION))
    return sorted(found)


def _psa_import_operator():
    """The PSA import operator this Blender actually has, as (module, name).

    The PSK/PSA addon ships both as a legacy addon and as an extension, and the
    two register different operators - and, in a machine that has both, the
    stale copy's internals do not match the property group the enabled one
    installed. Going through the operator sidesteps that entirely: whichever
    version is enabled reads its own settings.
    """
    psa_ops = getattr(bpy.ops, 'psa', None)
    if psa_ops is not None and 'import_all' in dir(psa_ops):
        return psa_ops.import_all, 'psa.import_all'
    legacy = getattr(bpy.ops, 'psa_import', None)
    if legacy is not None and 'import_multiple' in dir(legacy):
        return legacy.import_multiple, 'psa_import.import_multiple'
    return None, ''


def _run_psa_import(context, armature, filepath):
    """Import one PSA onto `armature`, whatever the addon version calls it."""
    operator, name = _psa_import_operator()
    if operator is None:
        raise RuntimeError('no PSA import operator found - enable the PSK/PSA addon')

    view_layer = context.view_layer
    previous_active = view_layer.objects.active
    was_hidden = armature.hide_get()
    # Both operators import onto the ACTIVE object, and neither runs from pose mode.
    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    armature.hide_set(False)
    view_layer.objects.active = armature
    try:
        if name == 'psa.import_all':
            return operator(filepath=filepath)
        return operator(directory=os.path.dirname(filepath) + os.sep,
                        files=[{'name': os.path.basename(filepath)}])
    finally:
        armature.hide_set(was_hidden)
        if previous_active is not None:
            view_layer.objects.active = previous_active


def import_psa_actions(context, armature, filepath):
    """Import every sequence in one PSA onto `armature`.

    Returns (new actions, warnings). The importer reports only warnings, never
    what it created, so the new actions are found by comparing bpy.data.actions
    either side of the call.
    """
    before = {action.name for action in bpy.data.actions}
    warnings = []
    result = _run_psa_import(context, armature, filepath)
    if result and 'CANCELLED' in result:
        warnings.append('the import operator cancelled')
    new_actions = [action for action in bpy.data.actions if action.name not in before]
    if not new_actions and not warnings:
        warnings.append('imported nothing - do the PSA bone names match this rig?')
    return new_actions, warnings


class _Reporter:
    """Stands in for the operator that export_model_anim_fast reports through."""

    def __init__(self):
        self.messages = []

    def report(self, level, message):
        self.messages.append((next(iter(level)) if level else 'INFO', message))


def export_action(context, armature, action, folder):
    """Write one action out as .nuanmb, the same way the Animation Exporter does.

    Returns the file path. The action has to be the one on the rig when the
    exporter walks the frames, so it is assigned first.
    """
    from ..anim.export_anim import (ensure_nuanmb_filename, export_model_anim_fast,
                                    sanitize_filename)
    from ..blender_compat import assign_action

    directory = bpy.path.abspath(folder)
    os.makedirs(directory, exist_ok=True)

    if armature.animation_data is None:
        armature.animation_data_create()
    assign_action(armature.animation_data, action)

    start, end = action.frame_range
    first_frame = int(round(start))
    last_frame = max(int(round(end)), first_frame)

    filepath = os.path.join(directory,
                            ensure_nuanmb_filename(sanitize_filename(action.name)))

    scene_properties = context.scene.sub_scene_properties
    override_bones = [item.name for item in
                      getattr(scene_properties, 'anim_override_bone_list', ())]
    use_exclude_list = getattr(scene_properties, 'anim_override_use_exclude_list', True)

    reporter = _Reporter()
    export_model_anim_fast(
        context, reporter, armature, filepath,
        True, True, True,            # transform, material and visibility tracks
        first_frame, last_frame,
        False, False, False, False, False,
        override_bones, use_exclude_list)
    return filepath, [message for _level, message in reporter.messages]


def discard_action(armature, action):
    """Delete an imported action, unhooking it first so nothing keeps it alive."""
    animation_data = armature.animation_data
    if animation_data is not None and animation_data.action is action:
        animation_data.action = None
    try:
        action.use_fake_user = False
        bpy.data.actions.remove(action)
    except (ReferenceError, RuntimeError):
        pass


def bake_psa_folder(context, driver, constrained, folder, recursive=True,
                    discard_imported=True, fake_user_new=True, exclude_deform=False,
                    keep_ik_bones=True, limit=0, export_folder='', discard_baked=True):
    """Import, bake and (optionally) export every PSA under `folder`.

    `driver` is the rig the PSAs are authored for (the source rig the Smash rig
    is bound to) and `constrained` is the rig that gets the baked actions.
    `limit` above zero stops after that many files, for a trial run. With an
    `export_folder`, each baked action is written out as .nuanmb and then
    dropped when `discard_baked` is set, so nothing accumulates in the .blend.

    Returns a summary dict.
    """
    from ...expy_kit.operators import (select_bones_for_visual_bake,
                                       bake_one_constrained_action)

    files = iter_psa_files(folder, recursive)
    if limit > 0:
        files = files[:limit]
    summary = {'files': len(files), 'baked': [], 'exported': [], 'skipped': [], 'warnings': []}
    if not files:
        summary['warnings'].append('no .psa files found in {}'.format(folder))
        return summary

    bone_names = select_bones_for_visual_bake(
        driver, constrained, use_deform=not exclude_deform, keep_ik_bones=keep_ik_bones)
    if not bone_names:
        summary['warnings'].append(
            'no constrained bones on {} - bind it to {} first'.format(
                constrained.name, driver.name))
        return summary

    for index, path in enumerate(files, start=1):
        filename = os.path.basename(path)
        try:
            actions, warnings = import_psa_actions(context, driver, path)
        except Exception as error:
            summary['warnings'].append('{}: import failed ({})'.format(filename, error))
            continue
        summary['warnings'].extend('{}: {}'.format(filename, w) for w in warnings)

        if not actions:
            summary['skipped'].append(filename)
            continue

        for action in actions:
            source_name = action.name
            baked = None
            try:
                baked = bake_one_constrained_action(
                    context, driver, constrained, action, bone_names,
                    fake_user_new=fake_user_new, use_retarget_clean=True)
            except Exception as error:
                summary['warnings'].append('{}: bake failed ({})'.format(source_name, error))

            if baked is None:
                summary['warnings'].append('{}: nothing baked'.format(source_name))
            else:
                summary['baked'].append(baked.name)
                line = '  [{}/{}] {} -> {}'.format(index, len(files), filename, baked.name)

                if export_folder:
                    try:
                        filepath, messages = export_action(
                            context, constrained, baked, export_folder)
                    except Exception as error:
                        summary['warnings'].append(
                            '{}: export failed ({})'.format(baked.name, error))
                    else:
                        summary['exported'].append(filepath)
                        summary['warnings'].extend(
                            '{}: {}'.format(baked.name, m) for m in messages)
                        line += ' -> {}'.format(os.path.basename(filepath))
                        # Exported and on disk, so the .blend does not need it.
                        if discard_baked:
                            discard_action(constrained, baked)
                print(line)

            # The bake renames the imported action to <name>_old; this is the
            # same datablock, so it goes whether it was renamed or not.
            if discard_imported:
                discard_action(driver, action)

    return summary
