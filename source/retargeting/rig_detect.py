"""
Work out which armature is which, and set the retarget tab up accordingly.

Retargeting needs three things named before it can do anything: which rig the
animation comes from, which rig it goes to, and what each rig's bones are
called. The last one used to be a name check - an armature called
"smush_blender_import" got the Smash preset and everything else got nothing -
so a character's source rig arrived with no mapping at all and had to be filled
in by hand, forty-odd bones at a time.

Bones are what actually identify a rig, so that is what is read here. Every
installed preset is scored against an armature by how many of its bones exist,
and the best score wins: the Smash starter rig matches Smash.py outright, an
All Justice rip matches AllJustice.py, a Mixamo download matches Mixamo.py.
Which of the pair is the target is decided the same way - by bones, not names -
since the Smash rig is the one carrying Trans/Rot/Hip/Bust.

CUSTOM BONES
  A preset only covers the humanoid slots. Everything else - swing chains,
  accessory bones, spare twist bones - has no slot to live in, and expy_kit
  pairs those through `custom` entries matched by identifier. Both skeletons
  need an entry under the SAME identifier or conversion_map quietly drops the
  pair, which is why link_custom_bones() always writes both sides. After the
  Rig Combiner the two rigs share bone positions exactly, so the leftovers pair
  off cleanly: AC_LB_slirt_A on the source to S_LB_slirt_A - or to S_SkirtLB1,
  once the swing tools have renamed it, which is why position is the last
  resort rather than the name.
"""

import os
import re

import bpy

# Bone names in a preset file: skeleton.spine.head = 'Head'
_PRESET_VALUE = re.compile(r"=\s*'([^']+)'")

# Assignments that name something other than a bone.
_NOT_BONES = ('deform_preset', 'active_preset', '.name =', 'super_copy')

# The vanilla fighter skeleton, for telling the Smash rig from the source rig
# when neither matched a preset well enough to be sure.
SMASH_MARKERS = ('Trans', 'Rot', 'Throw', 'Hip', 'Waist', 'Bust', 'Neck', 'Head',
                 'ClavicleL', 'ShoulderL', 'ArmL', 'HandL', 'LegL', 'KneeL',
                 'FootL', 'ToeL', 'FingerL11')

# A preset whose file name starts with this describes a Smash rig.
SMASH_PRESETS = ('smash',)


def preset_dirs():
    """Where preset files live: the user's copy first, then the bundled set."""
    dirs = []
    try:
        from ...expy_kit import preset_handler
        user_dir = preset_handler.get_retarget_dir()
        if os.path.isdir(user_dir):
            dirs.append(user_dir)
    except Exception:
        pass
    addon_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bundled = os.path.join(addon_root, 'expy_kit', 'rig_mapping', 'presets')
    if os.path.isdir(bundled):
        dirs.append(bundled)
    return dirs


def preset_bone_names(path):
    """Every bone name a preset file assigns."""
    names = set()
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            for line in handle:
                if any(token in line for token in _NOT_BONES):
                    continue
                match = _PRESET_VALUE.search(line)
                if match:
                    names.add(match.group(1))
    except OSError:
        pass
    return names


def installed_presets():
    """{preset filename: {bone names}}, the user's copy shadowing the bundled one."""
    presets = {}
    for directory in preset_dirs():
        try:
            filenames = sorted(os.listdir(directory))
        except OSError:
            continue
        for filename in filenames:
            if not filename.endswith('.py') or filename in presets:
                continue
            names = preset_bone_names(os.path.join(directory, filename))
            if names:
                presets[filename] = names
    return presets


def score_preset(armature, preset_bones):
    """(matched, total) for one preset against one armature."""
    if not preset_bones:
        return 0, 0
    bones = {bone.name for bone in armature.data.bones}
    return sum(1 for name in preset_bones if name in bones), len(preset_bones)


def detect_preset(armature, minimum=0.5):
    """Best preset for this armature as (filename, matched, total).

    `minimum` is the share of a preset's bones that must exist before it counts
    as a match, so an unrelated rig is told nothing was recognised instead of
    being handed whichever preset happened to share three bone names.
    """
    if armature is None or armature.type != 'ARMATURE':
        return None, 0, 0
    best_name, best_matched, best_total = None, 0, 0
    best_key = (0.0, 0)
    for filename, preset_bones in installed_presets().items():
        matched, total = score_preset(armature, preset_bones)
        if not total or matched / total < minimum:
            continue
        # Ratio first, then absolute count, so a fuller preset wins a tie.
        key = (matched / total, matched)
        if key > best_key:
            best_key = key
            best_name, best_matched, best_total = filename, matched, total
    return best_name, best_matched, best_total


def smash_score(armature):
    """How much of the vanilla fighter skeleton an armature has, 0-1."""
    if armature is None or armature.type != 'ARMATURE':
        return 0.0
    bones = {bone.name for bone in armature.data.bones}
    return sum(1 for name in SMASH_MARKERS if name in bones) / len(SMASH_MARKERS)


def looks_like_smash(armature, preset_name=None):
    if preset_name and os.path.splitext(preset_name)[0].lower().startswith(SMASH_PRESETS):
        return True
    return smash_score(armature) >= 0.75


def candidate_armatures(context):
    """Armatures to consider: the selection when it holds two or more, else the scene."""
    selected = [o for o in context.selected_objects if o.type == 'ARMATURE']
    if len(selected) >= 2:
        return selected
    return [o for o in context.view_layer.objects if o.type == 'ARMATURE']


def detect_pair(armatures):
    """Pick (source, target, notes) out of the armatures in the file.

    The target is the Smash rig - that is the one being animated. The source is
    whichever other rig recognises a preset best, so a file holding the Smash
    rig, the All Justice rig and a couple of leftovers still resolves.
    """
    notes = []
    scored = []
    for armature in armatures:
        preset, matched, total = detect_preset(armature)
        scored.append({
            'armature': armature, 'preset': preset, 'matched': matched,
            'total': total, 'ratio': (matched / total) if total else 0.0,
            'smash': smash_score(armature),
        })

    targets = [s for s in scored if looks_like_smash(s['armature'], s['preset'])]
    if not targets:
        return None, None, ['no Smash rig found - none of these armatures carry the '
                            'vanilla bones (Trans, Hip, Bust, ...)']
    targets.sort(key=lambda s: (-s['smash'], -s['ratio']))
    target = targets[0]
    if len(targets) > 1:
        notes.append('{} rigs look like Smash rigs; using {}'.format(
            len(targets), target['armature'].name))

    sources = [s for s in scored if s['armature'] is not target['armature']
               and not looks_like_smash(s['armature'], s['preset'])]
    if not sources:
        return None, target['armature'], notes + ['no source rig found beside the Smash rig']
    # Prefer a recognised preset, then the rig with the most bones - a source
    # rig carries its face and accessories, a stray control rig does not.
    sources.sort(key=lambda s: (-s['ratio'], -len(s['armature'].data.bones)))
    source = sources[0]
    if len(sources) > 1:
        notes.append('{} rigs could be the source; using {} - select the two rigs '
                     'you want and run again to choose'.format(
                         len(sources), source['armature'].name))
    if source['preset'] is None:
        notes.append('{} matched no preset; map it by proximity or save one'
                     .format(source['armature'].name))
    return source['armature'], target['armature'], notes


def _slot_bone_names(settings):
    """Bone names already claimed by preset slots or existing custom entries."""
    from .nearest_bone_mapper import _SLOT_PRIORITY, _get_slot_bone_name
    names = set()
    for group_name, finger_name, slot_name in _SLOT_PRIORITY:
        name = _get_slot_bone_name(settings, group_name, finger_name, slot_name)
        if name:
            names.add(name)
    settings.custom.migrate_legacy_bones()
    for _identifier, bone in settings.custom.get_bones():
        if bone:
            names.add(bone)
    return names


def _slot_pairs(source_settings, target_settings):
    """src bone -> trg bone for every slot both presets fill, as parent context."""
    from .nearest_bone_mapper import _SLOT_PRIORITY, _get_slot_bone_name
    pairs = {}
    for group_name, finger_name, slot_name in _SLOT_PRIORITY:
        src = _get_slot_bone_name(source_settings, group_name, finger_name, slot_name)
        trg = _get_slot_bone_name(target_settings, group_name, finger_name, slot_name)
        if src and trg:
            pairs[src] = trg
    return pairs


def facial_bones(armature):
    """Face bones on a rig: the subtree under a bone called 'face', plus anything
    named like facial rigging.

    Kept out of custom pairing by default. A Smash rig has a dozen face bones
    against an All Justice rig's ninety, so a leftover eyelid has nothing
    sensible to pair with - and position alone will happily marry it to
    whatever swing bone happens to sit nearby (measured: RT_eyelid_D onto the
    front hair bone). Expressions are the VIS face bake's job, not the
    retarget's.
    """
    try:
        from ..exo.rig_combiner import FACIAL_RE, _descendants
    except Exception:
        return set()
    names = set()
    for bone in armature.data.bones:
        if bone.name.lower() == 'face':
            names.update(child.name for child in _descendants(bone))
        elif FACIAL_RE.search(bone.name):
            names.add(bone.name)
    return names


def _combiner_name(source_name):
    """What the Rig Combiner would have called this bone on the Smash rig."""
    try:
        from ..exo.rig_combiner import merged_name
    except Exception:
        return None
    return merged_name(source_name, 'S_', 'H_')


def link_custom_bones(source, target, radius_scale=1.0, include_face=False):
    """Pair leftover bones between two rigs as custom entries on BOTH skeletons.

    Anything a preset has no slot for - swing chains, accessories, spare twist
    bones - only retargets if each skeleton carries a custom entry under the
    same identifier, so both sides are always written together.

    Returns a list of (source bone, target bone, how it was matched).
    """
    from ...expy_kit.properties import _clean_custom_identifier
    from .nearest_bone_mapper import _compute_proximity_threshold, _find_best_target

    source_settings = source.data.expykit_retarget
    target_settings = target.data.expykit_retarget

    claimed_source = _slot_bone_names(source_settings)
    claimed_target = _slot_bone_names(target_settings)
    parent_map = _slot_pairs(source_settings, target_settings)

    # H_ bones are helper bones: the nuhlpb drives them in game and constraints
    # drive them in Blender, so a retarget constraint on one only fights that.
    target_bones = {bone.name for bone in target.data.bones
                    if not bone.name.startswith('H_')}
    skip_source = set() if include_face else facial_bones(source)
    free_source = [b for b in source.data.bones
                   if b.use_deform and b.name not in claimed_source
                   and b.name not in skip_source]
    # Longest first: a chain root claims its match before its own children can.
    free_source.sort(key=lambda b: -b.length)

    threshold = _compute_proximity_threshold(source, target, radius_scale=radius_scale)
    linked = []
    for bone in free_source:
        match = None
        how = ''
        for candidate, label in ((bone.name, 'same name'),
                                 (_combiner_name(bone.name), 'combiner name')):
            if candidate and candidate in target_bones and candidate not in claimed_target:
                match, how = candidate, label
                break
        if match is None:
            match = _find_best_target(source, bone.name, target, claimed_target,
                                      parent_map, threshold)
            how = 'position'
            if match and match not in target_bones:
                match = None  # a helper bone; leave it to the nuhlpb
        if not match:
            continue

        identifier = _clean_custom_identifier(bone.name)
        if not identifier:
            continue
        source_settings.custom.add_bone(identifier, bone.name)
        target_settings.custom.add_bone(identifier, match)
        claimed_source.add(bone.name)
        claimed_target.add(match)
        parent_map[bone.name] = match
        linked.append((bone.name, match, how))

    source_settings.custom.sync_all_dynamic_props()
    target_settings.custom.sync_all_dynamic_props()
    return linked


def apply_preset(armature, preset_name):
    """Load a preset onto one armature, whichever object happens to be active."""
    from . import load_preset_with_custom_bones, set_armature_active_preset

    previous = bpy.context.view_layer.objects.active
    hidden = armature.hide_get()
    armature.hide_set(False)
    bpy.context.view_layer.objects.active = armature
    try:
        if load_preset_with_custom_bones(preset_name, armature):
            set_armature_active_preset(armature, preset_name)
            return True
    finally:
        armature.hide_set(hidden)
        if previous is not None:
            bpy.context.view_layer.objects.active = previous
    return False


def auto_setup(context, link_customs=True, radius_scale=1.0, include_face=False):
    """Detect both rigs, load their presets, pair the leftovers. Returns a report."""
    source, target, notes = detect_pair(candidate_armatures(context))
    report = {'source': source, 'target': target, 'notes': notes,
              'presets': {}, 'linked': []}
    if source is None or target is None:
        return report

    for armature in (source, target):
        preset, matched, total = detect_preset(armature)
        report['presets'][armature.name] = (preset, matched, total)
        if preset:
            apply_preset(armature, preset)

    if link_customs:
        report['linked'] = link_custom_bones(source, target, radius_scale, include_face)

    scene = context.scene
    if hasattr(scene, 'expykit_bind_to'):
        scene.expykit_bind_to = target
    # Binding reads the source off the active object.
    if source.name not in context.view_layer.objects:
        report['notes'].append(
            '{} is not in this view layer - its collection is excluded, so binding '
            'cannot make it active'.format(source.name))
    else:
        for obj in context.selected_objects:
            obj.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source
    return report
