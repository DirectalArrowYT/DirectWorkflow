"""Map retarget bone slots by nearest world-space position between armatures."""

_LIMB_GROUPS = (
    'right_arm', 'left_arm', 'right_arm_ik', 'left_arm_ik',
    'right_leg', 'left_leg', 'right_leg_ik', 'left_leg_ik',
)

_LIMB_SLOTS = (
    'shoulder', 'arm', 'arm_twist', 'arm_twist_02',
    'forearm', 'forearm_twist', 'forearm_twist_02', 'hand',
    'upleg', 'upleg_twist', 'upleg_twist_02',
    'leg', 'leg_twist', 'leg_twist_02', 'foot', 'toe',
)

_SPINE_SLOTS = ('head', 'neck', 'spine2', 'spine1', 'spine', 'hips')

_FACE_SLOTS = ('jaw', 'left_eye', 'right_eye', 'left_upLid', 'right_upLid')

_FINGER_GROUPS = ('left_fingers', 'right_fingers')
_FINGER_NAMES = ('thumb', 'index', 'middle', 'ring', 'pinky')
_FINGER_SLOTS = ('meta', 'a', 'b', 'c')

# Process larger / structural bones first so they claim the best matches.
_SLOT_PRIORITY = [
    ('root', None, None),
    ('spine', None, 'hips'),
    ('spine', None, 'spine'),
    ('spine', None, 'spine1'),
    ('spine', None, 'spine2'),
    ('spine', None, 'neck'),
    ('spine', None, 'head'),
]
for _group in _LIMB_GROUPS:
    for _slot in _LIMB_SLOTS:
        _SLOT_PRIORITY.append((_group, None, _slot))
for _group in _FINGER_GROUPS:
    for _finger in _FINGER_NAMES:
        for _slot in _FINGER_SLOTS:
            _SLOT_PRIORITY.append((_group, _finger, _slot))
for _slot in _FACE_SLOTS:
    _SLOT_PRIORITY.append(('face', None, _slot))


def _bone_world_head(armature_obj, bone_name):
    bone = armature_obj.data.bones.get(bone_name)
    if not bone:
        return None
    return armature_obj.matrix_world @ bone.head_local


def _bone_length(bone):
    length = (bone.tail_local - bone.head_local).length
    if length > 1e-6:
        return length
    if bone.parent:
        return (bone.head_local - bone.parent.head_local).length
    return 0.0


def _compute_proximity_threshold(reference_armature_obj, target_armature_obj):
    """Return max head-to-head distance for a bone pair to count as 'close'."""
    lengths = []
    for armature_obj in (reference_armature_obj, target_armature_obj):
        for bone in armature_obj.data.bones:
            length = _bone_length(bone)
            if length > 1e-6:
                lengths.append(length)

    if not lengths:
        return 0.05

    lengths.sort()
    median = lengths[len(lengths) // 2]
    # Nearly touching / slightly separated relative to typical bone length.
    return max(0.005, min(median * 1.5, 0.15))


def _find_nearest_bone(world_pos, target_armature_obj, used_bones, max_distance):
    best_name = ""
    best_dist = max_distance

    for bone in target_armature_obj.data.bones:
        if bone.name in used_bones:
            continue
        bone_pos = target_armature_obj.matrix_world @ bone.head_local
        dist = (bone_pos - world_pos).length
        if dist < best_dist:
            best_dist = dist
            best_name = bone.name

    return best_name if best_name else ""


def _collect_close_bone_pairs(reference_armature_obj, target_armature_obj, max_distance):
    """Return sorted (distance, ref_bone, target_bone) for all close head pairs."""
    pairs = []

    for ref_bone in reference_armature_obj.data.bones:
        ref_pos = _bone_world_head(reference_armature_obj, ref_bone.name)
        if ref_pos is None:
            continue

        for target_bone in target_armature_obj.data.bones:
            target_pos = target_armature_obj.matrix_world @ target_bone.head_local
            dist = (target_pos - ref_pos).length
            if dist <= max_distance:
                pairs.append((dist, ref_bone.name, target_bone.name))

    pairs.sort(key=lambda item: item[0])
    return pairs


def _get_slot_bone_name(settings, group_name, finger_name, slot_name):
    if group_name == 'root':
        return settings.root or ""

    group = getattr(settings, group_name)
    if finger_name:
        finger = getattr(group, finger_name)
        return getattr(finger, slot_name, "") or ""

    return getattr(group, slot_name, "") or ""


def _set_slot_bone_name(settings, group_name, finger_name, slot_name, bone_name):
    if group_name == 'root':
        settings.root = bone_name
        return

    group = getattr(settings, group_name)
    if finger_name:
        setattr(getattr(group, finger_name), slot_name, bone_name)
    else:
        setattr(group, slot_name, bone_name)


def _collect_reference_preset_bones(ref_settings):
    """Bone names already assigned to preset slots on the reference armature."""
    bones = set()

    for group_name, finger_name, slot_name in _SLOT_PRIORITY:
        ref_bone = _get_slot_bone_name(ref_settings, group_name, finger_name, slot_name)
        if ref_bone:
            bones.add(ref_bone)

    ref_settings.custom.migrate_legacy_bones()
    for _identifier, ref_bone in ref_settings.custom.get_bones():
        if ref_bone:
            bones.add(ref_bone)

    if ref_settings.custom.name:
        bones.add(ref_settings.custom.name)

    return bones


def map_bones_by_proximity(reference_armature_obj, target_armature_obj):
    """Fill target retarget settings using nearby bone head positions.

    Compares all bone heads between the reference and target armatures. When
    heads are close (nearly touching or slightly separated), they are paired.
    Preset slots from the reference mapping are filled first, then any other
    close pairs are added as custom bones.

    Returns (mapped_count, custom_count).
    """
    ref_settings = reference_armature_obj.data.expykit_retarget
    target_settings = target_armature_obj.data.expykit_retarget
    max_distance = _compute_proximity_threshold(reference_armature_obj, target_armature_obj)
    used_targets = set()
    mapped_ref_bones = set()
    mapped_count = 0
    custom_count = 0
    ref_has_preset = ref_settings.has_settings()

    if ref_has_preset:
        for group_name, finger_name, slot_name in _SLOT_PRIORITY:
            ref_bone = _get_slot_bone_name(ref_settings, group_name, finger_name, slot_name)
            if not ref_bone:
                continue

            world_pos = _bone_world_head(reference_armature_obj, ref_bone)
            if world_pos is None:
                continue

            nearest = _find_nearest_bone(
                world_pos, target_armature_obj, used_targets, max_distance
            )
            if not nearest:
                continue

            _set_slot_bone_name(target_settings, group_name, finger_name, slot_name, nearest)
            used_targets.add(nearest)
            mapped_ref_bones.add(ref_bone)
            mapped_count += 1

        ref_settings.custom.migrate_legacy_bones()
        for identifier, ref_bone in ref_settings.custom.get_bones():
            world_pos = _bone_world_head(reference_armature_obj, ref_bone)
            if world_pos is None:
                continue

            nearest = _find_nearest_bone(
                world_pos, target_armature_obj, used_targets, max_distance
            )
            if not nearest:
                continue

            target_settings.custom.add_bone(identifier, nearest)
            used_targets.add(nearest)
            mapped_ref_bones.add(ref_bone)
            custom_count += 1

        if ref_settings.custom.name:
            ref_bone = ref_settings.custom.name
            world_pos = _bone_world_head(reference_armature_obj, ref_bone)
            if world_pos is not None:
                nearest = _find_nearest_bone(
                    world_pos, target_armature_obj, used_targets, max_distance
                )
                if nearest:
                    from ...expy_kit.properties import _clean_custom_identifier
                    identifier = _clean_custom_identifier(ref_bone)
                    target_settings.custom.add_bone(identifier, nearest)
                    used_targets.add(nearest)
                    mapped_ref_bones.add(ref_bone)
                    custom_count += 1
    else:
        mapped_ref_bones = _collect_reference_preset_bones(ref_settings)

    from ...expy_kit.properties import _clean_custom_identifier

    for dist, ref_bone, target_bone in _collect_close_bone_pairs(
        reference_armature_obj, target_armature_obj, max_distance
    ):
        if ref_bone in mapped_ref_bones or target_bone in used_targets:
            continue

        identifier = _clean_custom_identifier(ref_bone)
        if not identifier:
            continue

        target_settings.custom.add_bone(identifier, target_bone)
        used_targets.add(target_bone)
        mapped_ref_bones.add(ref_bone)
        custom_count += 1

    target_settings.custom.sync_all_dynamic_props()
    return mapped_count, custom_count
