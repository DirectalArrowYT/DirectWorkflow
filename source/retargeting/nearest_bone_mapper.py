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
    ('throw', None, None),
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


def _bone_world_head(armature_obj, bone):
    if isinstance(bone, str):
        bone = armature_obj.data.bones.get(bone)
    if not bone:
        return None
    return armature_obj.matrix_world @ bone.head_local


def _bone_world_mid(armature_obj, bone):
    if isinstance(bone, str):
        bone = armature_obj.data.bones.get(bone)
    if not bone:
        return None
    head = armature_obj.matrix_world @ bone.head_local
    tail = armature_obj.matrix_world @ bone.tail_local
    return (head + tail) * 0.5


def _bone_length(bone):
    length = (bone.tail_local - bone.head_local).length
    if length > 1e-6:
        return length
    if bone.parent:
        return (bone.head_local - bone.parent.head_local).length
    return 0.0


def _armature_world_extent(armature_obj):
    """Rough world-space size of an armature from bone head positions."""
    points = []
    for bone in armature_obj.data.bones:
        head = _bone_world_head(armature_obj, bone)
        if head is not None:
            points.append(head)
    if len(points) < 2:
        return 0.0
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    zs = [p.z for p in points]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    dz = max(zs) - min(zs)
    return max(dx, dy, dz)


def _compute_proximity_threshold(reference_armature_obj, target_armature_obj, radius_scale=1.0):
    """Return max head distance for a bone pair to count as a candidate match."""
    lengths = []
    for armature_obj in (reference_armature_obj, target_armature_obj):
        for bone in armature_obj.data.bones:
            length = _bone_length(bone)
            if length > 1e-6:
                lengths.append(length)

    extent = max(
        _armature_world_extent(reference_armature_obj),
        _armature_world_extent(target_armature_obj),
        0.0,
    )

    if not lengths:
        base = max(0.05, extent * 0.04)
    else:
        lengths.sort()
        median = lengths[len(lengths) // 2]
        # Scale with the skeleton. A hard 0.10m-style cap made Smash-sized
        # overlapping armatures fail even when heads visually touch.
        base = max(median * 0.65, extent * 0.035, 0.02)
        if extent > 1e-6:
            base = min(base, extent * 0.20)

    scale = max(0.1, float(radius_scale) if radius_scale else 1.0)
    return base * scale


def _match_score(ref_armature, ref_bone, target_armature, target_bone, parent_map, max_distance):
    """Lower is better. Gate on head distance; use mid/length/parent for ranking."""
    ref_head = _bone_world_head(ref_armature, ref_bone)
    trg_head = _bone_world_head(target_armature, target_bone)
    ref_mid = _bone_world_mid(ref_armature, ref_bone)
    trg_mid = _bone_world_mid(target_armature, target_bone)
    if ref_head is None or trg_head is None or ref_mid is None or trg_mid is None:
        return None

    head_dist = (trg_head - ref_head).length
    # Users judge "touching" by heads. Different proportions make midpoints diverge
    # even when heads sit on top of each other.
    if head_dist > max_distance:
        return None

    mid_dist = (trg_mid - ref_mid).length
    dist = (head_dist * 0.80) + (mid_dist * 0.20)

    ref_len = _bone_length(ref_bone)
    trg_len = _bone_length(target_bone)
    if ref_len > 1e-6 and trg_len > 1e-6:
        ratio = min(ref_len, trg_len) / max(ref_len, trg_len)
        dist *= 1.0 + ((1.0 - ratio) * 0.45)

    if ref_bone.parent and ref_bone.parent.name in parent_map:
        expected = parent_map[ref_bone.parent.name]
        if target_bone.parent and target_bone.parent.name == expected:
            dist *= 0.45
        else:
            dist *= 1.15

    return dist


def _find_best_target(ref_armature, ref_bone, target_armature, used_bones, parent_map, max_distance):
    """Return the unique best target bone, or '' when the match is ambiguous."""
    if isinstance(ref_bone, str):
        ref_bone = ref_armature.data.bones.get(ref_bone)
    if not ref_bone:
        return ""

    best_name = ""
    best_score = None
    second_score = None

    for bone in target_armature.data.bones:
        if bone.name in used_bones:
            continue
        score = _match_score(ref_armature, ref_bone, target_armature, bone, parent_map, max_distance)
        if score is None:
            continue
        if best_score is None or score < best_score:
            second_score = best_score
            best_score = score
            best_name = bone.name
        elif second_score is None or score < second_score:
            second_score = score

    if not best_name:
        return ""
    # Drop near-ties only when the winner is not already very close. Perfect
    # overlaps used to lose every slot because helper/twist bones sat nearby.
    if (
        second_score is not None
        and best_score > max_distance * 0.12
        and second_score <= best_score * 1.08
    ):
        return ""
    return best_name


def _collect_close_bone_pairs(reference_armature_obj, target_armature_obj, parent_map, max_distance):
    """Return sorted (score, ref_bone, target_bone) for close scored pairs."""
    pairs = []

    for ref_bone in reference_armature_obj.data.bones:
        for target_bone in target_armature_obj.data.bones:
            score = _match_score(
                reference_armature_obj, ref_bone,
                target_armature_obj, target_bone,
                parent_map, max_distance,
            )
            if score is None:
                continue
            pairs.append((score, ref_bone.name, target_bone.name))

    pairs.sort(key=lambda item: item[0])
    return pairs


def _get_slot_bone_name(settings, group_name, finger_name, slot_name):
    if group_name == 'root':
        return settings.root or ""
    if group_name == 'throw':
        return getattr(settings, 'throw', '') or ""

    group = getattr(settings, group_name)
    if finger_name:
        finger = getattr(group, finger_name)
        return getattr(finger, slot_name, "") or ""

    return getattr(group, slot_name, "") or ""


def _set_slot_bone_name(settings, group_name, finger_name, slot_name, bone_name):
    if group_name == 'root':
        settings.root = bone_name
        return
    if group_name == 'throw':
        settings.throw = bone_name
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


def map_bones_by_proximity(reference_armature_obj, target_armature_obj, radius_scale=1.0):
    """Fill target retarget settings using scored nearby bone positions.

    Compares bone heads and midpoints, prefers similar lengths and matching
    parents, and skips pairs that are too close to call. `radius_scale`
    multiplies the auto distance threshold (1.0 is the default).

    Returns (mapped_count, custom_count).
    """
    ref_settings = reference_armature_obj.data.expykit_retarget
    target_settings = target_armature_obj.data.expykit_retarget
    max_distance = _compute_proximity_threshold(
        reference_armature_obj, target_armature_obj, radius_scale=radius_scale
    )
    used_targets = set()
    mapped_ref_bones = set()
    parent_map = {}
    mapped_count = 0
    custom_count = 0
    ref_has_preset = ref_settings.has_settings()

    if ref_has_preset:
        for group_name, finger_name, slot_name in _SLOT_PRIORITY:
            ref_bone_name = _get_slot_bone_name(ref_settings, group_name, finger_name, slot_name)
            if not ref_bone_name:
                continue

            nearest = _find_best_target(
                reference_armature_obj,
                ref_bone_name,
                target_armature_obj,
                used_targets,
                parent_map,
                max_distance,
            )
            if not nearest:
                continue

            _set_slot_bone_name(target_settings, group_name, finger_name, slot_name, nearest)
            used_targets.add(nearest)
            mapped_ref_bones.add(ref_bone_name)
            parent_map[ref_bone_name] = nearest
            mapped_count += 1

        ref_settings.custom.migrate_legacy_bones()
        for identifier, ref_bone in ref_settings.custom.get_bones():
            nearest = _find_best_target(
                reference_armature_obj,
                ref_bone,
                target_armature_obj,
                used_targets,
                parent_map,
                max_distance,
            )
            if not nearest:
                continue

            target_settings.custom.add_bone(identifier, nearest)
            used_targets.add(nearest)
            mapped_ref_bones.add(ref_bone)
            parent_map[ref_bone] = nearest
            custom_count += 1

        if ref_settings.custom.name:
            ref_bone = ref_settings.custom.name
            nearest = _find_best_target(
                reference_armature_obj,
                ref_bone,
                target_armature_obj,
                used_targets,
                parent_map,
                max_distance,
            )
            if nearest:
                from ...expy_kit.properties import _clean_custom_identifier
                identifier = _clean_custom_identifier(ref_bone)
                target_settings.custom.add_bone(identifier, nearest)
                used_targets.add(nearest)
                mapped_ref_bones.add(ref_bone)
                parent_map[ref_bone] = nearest
                custom_count += 1
    else:
        mapped_ref_bones = _collect_reference_preset_bones(ref_settings)

    from ...expy_kit.properties import _clean_custom_identifier

    for dist, ref_bone, target_bone in _collect_close_bone_pairs(
        reference_armature_obj, target_armature_obj, parent_map, max_distance
    ):
        if ref_bone in mapped_ref_bones or target_bone in used_targets:
            continue

        identifier = _clean_custom_identifier(ref_bone)
        if not identifier:
            continue

        target_settings.custom.add_bone(identifier, target_bone)
        used_targets.add(target_bone)
        mapped_ref_bones.add(ref_bone)
        parent_map[ref_bone] = target_bone
        custom_count += 1

    target_settings.custom.sync_all_dynamic_props()
    return mapped_count, custom_count
