"""
Analytic two-bone IK for Smash Ultimate armatures ("IK v2").

Replaces the Blender IK constraints that used to sit directly on ArmL / KneeL.
Those had two problems that do not show while matching an existing animation
(the solver starts from the FK keys underneath, so it lands back on them) but
do as soon as a pose is animated in IK:

  * The pole did not decide the bend. A straight Smash T-pose arm gives the
    solver nothing to go on, so the hard-coded pole angles (-90 / 0) only held
    for one pose - measured on a test fighter, bending the arm dropped the elbow straight
    down while ArmIKL sat behind it.
  * The result depended on the FK keys under the IK, so the same control pose
    could solve differently in two actions.

Here the limb is solved with a law-of-cosines driver and Damped/Locked Track
constraints on mechanism bones instead:

    BL_MCH_IKRoot_<upper>   at the shoulder/hip, aims at the target and rolls
                            towards the pole (the pole decides the plane, always)
    BL_MCH_IK_<upper>       bent away from the aim by the exact angle for the
                            bone lengths and the target distance
    BL_MCH_IK_<lower>       aims at the target
    BL_IKTwist_<bone>       keyable twist rings (children of the MCH bones, with
                            the deform bones' own rest orientation); the deform
                            bones copy their rotation

It is exact for straight limbs, deterministic, and never depends on the FK
keys. The control bones keep their names (HandIK/ArmIK/FootIK/KneeIK) and the
sub_use_ik_arms / sub_use_ik_legs switches keep driving the influence, so the
rest of the IK tooling keeps working. Every new bone starts with BL_ and has
"IK" in its name, so the animation exporter skips it and Bake & Remove IK
deletes it.

The legs also get a foot roll (BL_IKFootRoll: rotate it forward to roll onto
the ball of the foot while the toes stay on the floor, back to lift the toes)
and a toe control (BL_IKToe).
"""

import json
import math
import re

import bpy
from mathutils import Matrix, Vector

from .create_animation_rig import canonical_bone_name, bone_name_suffix

INFO_KEY = 'sub_ik_v2'
CONSTRAINT_PREFIX = 'SUB_IK'
MCH = 'BL_MCH_IK'
FOOT_ROLL_ANGLE = math.radians(90.0)

# Smash characters face -Y with Z up and their left on +X.
FORWARD = Vector((0.0, -1.0, 0.0))
UP = Vector((0.0, 0.0, 1.0))
LEFT = Vector((1.0, 0.0, 0.0))

_UPPER_RE = re.compile(r'^(Shoulder|Leg)([LR])(\d*)$')


# ---------------------------------------------------------------------------
# Info
# ---------------------------------------------------------------------------

def get_info(armature_obj):
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return None
    raw = armature_obj.data.get(INFO_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def has_ik_v2(armature_obj):
    return get_info(armature_obj) is not None


def iter_limbs(info, kinds=('ARMS', 'LEGS')):
    for limb in (info or {}).get('limbs', []):
        if limb['kind'] in kinds:
            yield limb


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _frame(head, y, z_hint=None, x_hint=None):
    """A 4x4 bone matrix at `head` with its Y axis along `y`."""
    y = y.normalized()
    if z_hint is not None:
        z = z_hint - y * z_hint.dot(y)
        if z.length < 1e-6:
            z = UP - y * UP.dot(y) if abs(y.dot(UP)) < 0.9 else FORWARD - y * FORWARD.dot(y)
        z.normalize()
        x = y.cross(z)
    else:
        hint = x_hint if x_hint is not None else LEFT
        x = hint - y * hint.dot(y)
        if x.length < 1e-6:
            x = FORWARD - y * FORWARD.dot(y)
        x.normalize()
        z = x.cross(y)
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = head
    return m


def bend_direction(a, b, c, fallback):
    """Unit vector from the line a->c towards the joint b."""
    line = c - a
    if line.length < 1e-9:
        return fallback.normalized()
    t = (b - a).dot(line) / line.length_squared
    bend = b - (a + line * t)
    if bend.length < line.length * 1e-3:
        return fallback.normalized()
    return bend.normalized()


def _new_edit_bone(edit_bones, name, matrix, length, parent):
    bone = edit_bones.get(name)
    if bone is None:
        bone = edit_bones.new(name)
    bone.head = matrix.translation
    bone.tail = matrix.translation + Vector((0.0, max(length, 1e-3), 0.0))
    bone.matrix = matrix
    bone.length = max(length, 1e-3)
    bone.use_deform = False
    bone.use_connect = False
    bone.parent = parent
    return bone


# ---------------------------------------------------------------------------
# Limb discovery
# ---------------------------------------------------------------------------

def find_limbs(armature_obj):
    """Every arm (Shoulder/Arm/Hand, including extra arms like ShoulderL2) and
    leg (Leg/Knee/Foot) chain in the armature, including .001 copies."""
    bones = armature_obj.data.bones
    limbs = []
    for bone in bones:
        base, suffix = canonical_bone_name(bone.name), bone_name_suffix(bone.name)
        match = _UPPER_RE.match(base)
        if not match:
            continue
        part, side, digits = match.groups()
        tag = f'{side}{digits}{suffix}'
        if part == 'Shoulder':
            kind, names = 'ARMS', (f'Shoulder{tag}', f'Arm{tag}', f'Hand{tag}')
            target, pole = f'HandIK{tag}', f'ArmIK{tag}'
        else:
            kind, names = 'LEGS', (f'Leg{tag}', f'Knee{tag}', f'Foot{tag}')
            target, pole = f'FootIK{tag}', f'KneeIK{tag}'
        upper, lower, end = (bones.get(n) for n in names)
        if upper is None or lower is None or end is None:
            continue
        if lower.parent != upper or end.parent != lower:
            continue
        limbs.append({
            'kind': kind, 'side': side, 'tag': tag,
            'upper': upper.name, 'lower': lower.name, 'end': end.name,
            'target': target, 'pole': pole,
            'toe': f'Toe{tag}' if kind == 'LEGS' and bones.get(f'Toe{tag}') is not None else None,
        })
    return limbs


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _remove_old_ik(armature_obj, limbs):
    """Remove the Blender IK constraints and control-copying constraints of the
    old IK setup from the limb bones."""
    names = set()
    for limb in limbs:
        names.update(n for n in (limb['upper'], limb['lower'], limb['end'], limb.get('toe')) if n)
    for name in names:
        pose_bone = armature_obj.pose.bones.get(name)
        if pose_bone is None:
            continue
        for constraint in list(pose_bone.constraints):
            subtarget = getattr(constraint, 'subtarget', '') or ''
            if constraint.type == 'IK' or constraint.name.startswith(CONSTRAINT_PREFIX) or (
                    constraint.type == 'COPY_ROTATION' and 'IK' in subtarget):
                try:
                    constraint.driver_remove('influence')
                except (TypeError, RuntimeError):
                    pass
                pose_bone.constraints.remove(constraint)


def build(context, armature_obj):
    """Build (or rebuild) the analytic IK rig. Returns the number of limbs."""
    armature = armature_obj.data
    limbs = find_limbs(armature_obj)
    if not limbs:
        return 0

    previous_mode = armature_obj.mode
    context.view_layer.objects.active = armature_obj
    if armature_obj.mode != 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
    _remove_old_ik(armature_obj, limbs)

    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature.edit_bones
    for limb in limbs:
        suffix_trans = 'Trans' + bone_name_suffix(limb['upper'])
        trans = edit_bones.get(suffix_trans) or edit_bones.get('Trans')
        upper, lower, end = edit_bones[limb['upper']], edit_bones[limb['lower']], edit_bones[limb['end']]
        a, b, c = upper.head.copy(), lower.head.copy(), end.head.copy()
        chain_length = (b - a).length + (c - b).length
        is_arm = limb['kind'] == 'ARMS'
        # Arms fold backwards, knees forwards.
        bend = bend_direction(a, b, c, -FORWARD if is_arm else FORWARD)

        # Controls (kept if they already exist, so their keys stay valid).
        if edit_bones.get(limb['pole']) is None:
            position = b + bend * max((b - a).length, 0.1)
            _new_edit_bone(edit_bones, limb['pole'], _frame(position, bend, z_hint=UP),
                           (b - a).length * 0.2, trans)
        if is_arm:
            if edit_bones.get(limb['target']) is None:
                _new_edit_bone(edit_bones, limb["target"], end.matrix.copy(), end.length * 1.5, trans)
        else:
            toe = edit_bones.get(limb['toe']) if limb['toe'] else None
            toe_position = toe.head.copy() if toe is not None else c + FORWARD * chain_length * 0.12
            foot_forward = toe_position - c
            foot_forward = foot_forward - UP * foot_forward.dot(UP)
            if foot_forward.length < 1e-4:
                foot_forward = FORWARD.copy()
            foot_forward.normalize()
            foot_length = max((toe_position - c).length, chain_length * 0.05)
            floor = min(0.0, toe_position.z) if toe is not None else 0.0
            ankle_height = c.z - floor
            if edit_bones.get(limb['target']) is None:
                _new_edit_bone(edit_bones, limb['target'], _frame(c, foot_forward, z_hint=UP),
                               foot_length * 0.8, trans)
            foot_ik = edit_bones[limb['target']]
            heel = c - UP * ankle_height - foot_forward * foot_length * 0.25
            flat = lambda p: _frame(p, foot_forward, z_hint=UP)
            names = {
                'heel': f'{MCH}Heel_{limb["tag"]}', 'ball': f'{MCH}Ball_{limb["tag"]}',
                'foot_target': f'{MCH}FootTarget_{limb["tag"]}', 'roll': f'BL_IKFootRoll_{limb["tag"]}',
                'toe_ctrl': f'BL_IKToe_{limb["tag"]}' if toe is not None else None,
            }
            heel_bone = _new_edit_bone(edit_bones, names['heel'], flat(heel), foot_length * 0.2, foot_ik)
            ball_bone = _new_edit_bone(edit_bones, names['ball'], flat(toe_position), foot_length * 0.2, heel_bone)
            _new_edit_bone(edit_bones, names['foot_target'], end.matrix.copy(), end.length, ball_bone)
            if toe is not None:
                _new_edit_bone(edit_bones, names['toe_ctrl'], toe.matrix.copy(), toe.length, heel_bone)
            roll_position = heel - foot_forward * foot_length * 0.35
            _new_edit_bone(edit_bones, names['roll'], flat(roll_position), foot_length * 0.3, foot_ik)
            limb.update(names)
            limb['solve_target'] = names['foot_target']

        target_name = limb.get('solve_target', limb['target'])
        pole = edit_bones[limb['pole']]
        root_matrix = _frame(a, c - a, z_hint=pole.head - a)
        root = _new_edit_bone(edit_bones, f'{MCH}Root_{limb["upper"]}', root_matrix,
                              (c - a).length * 0.25, upper.parent)
        mch_upper = _new_edit_bone(edit_bones, f'{MCH}_{limb["upper"]}', _frame(a, b - a, z_hint=bend),
                                   (b - a).length, root)
        mch_lower = _new_edit_bone(edit_bones, f'{MCH}_{limb["lower"]}', _frame(b, c - b, z_hint=bend),
                                   (c - b).length, mch_upper)
        _new_edit_bone(edit_bones, f'BL_IKTwist_{limb["upper"]}', upper.matrix.copy(), upper.length, mch_upper)
        _new_edit_bone(edit_bones, f'BL_IKTwist_{limb["lower"]}', lower.matrix.copy(), lower.length, mch_lower)

        root_y, root_z = root_matrix.col[1].xyz.normalized(), root_matrix.col[2].xyz.normalized()
        upper_y = (b - a).normalized()
        limb.update({
            'mch_root': root.name, 'mch_upper': mch_upper.name, 'mch_lower': mch_lower.name,
            'twist_upper': f'BL_IKTwist_{limb["upper"]}', 'twist_lower': f'BL_IKTwist_{limb["lower"]}',
            'solve_target': target_name,
            'length_a': (b - a).length, 'length_b': (c - b).length,
            'rest_angle': math.atan2(upper_y.dot(root_z), upper_y.dot(root_y)),
            'pole_distance': max((b - a).length, 0.1),
        })

    bpy.ops.object.mode_set(mode='POSE')
    pose = armature_obj.pose.bones
    for limb in limbs:
        _setup_limb_pose(armature_obj, pose, limb)

    armature[INFO_KEY] = json.dumps({'version': 1, 'limbs': limbs})
    _organize(armature_obj, limbs)
    if previous_mode not in {'POSE', 'EDIT'}:
        bpy.ops.object.mode_set(mode='OBJECT')
    return len(limbs)


def _constraint(pose_bone, type_, name):
    constraint = pose_bone.constraints.new(type_)
    constraint.name = f'{CONSTRAINT_PREFIX} {name}'
    return constraint


def _setup_limb_pose(armature_obj, pose, limb):
    kind_label = 'Arm' if limb['kind'] == 'ARMS' else 'Leg'
    root = pose[limb['mch_root']]
    c = _constraint(root, 'DAMPED_TRACK', 'Aim')
    c.target, c.subtarget = armature_obj, limb['solve_target']
    c = _constraint(root, 'LOCKED_TRACK', 'Pole')
    c.target, c.subtarget = armature_obj, limb['pole']
    c.lock_axis, c.track_axis = 'LOCK_Y', 'TRACK_Z'

    mch_upper = pose[limb['mch_upper']]
    mch_upper.rotation_mode = 'XYZ'
    fcurve = mch_upper.driver_add('rotation_euler', 0)
    for modifier in list(fcurve.modifiers):
        fcurve.modifiers.remove(modifier)
    driver = fcurve.driver
    driver.type = 'SCRIPTED'
    while driver.variables:
        driver.variables.remove(driver.variables[0])
    var = driver.variables.new()
    var.name, var.type = 'd', 'LOC_DIFF'
    var.targets[0].id, var.targets[0].bone_target = armature_obj, limb['mch_root']
    var.targets[1].id, var.targets[1].bone_target = armature_obj, limb['solve_target']
    var = driver.variables.new()
    var.name, var.type = 's', 'TRANSFORMS'
    var.targets[0].id = armature_obj
    var.targets[0].transform_type = 'SCALE_X'
    var.targets[0].transform_space = 'WORLD_SPACE'
    la, lb = limb['length_a'], limb['length_b']
    # Law of cosines. Beyond reach the limb goes straight (clamped), like IK without stretch.
    driver.expression = (f'acos(min(max(({la:.6f}**2 + (d/s)**2 - {lb:.6f}**2) / '
                         f'max(2.0 * {la:.6f} * (d/s), 1e-9), -1.0), 1.0)) - {limb["rest_angle"]:.9f}')

    c = _constraint(pose[limb['mch_lower']], 'DAMPED_TRACK', 'Aim')
    c.target, c.subtarget = armature_obj, limb['solve_target']

    for deform, source in ((limb['upper'], limb['twist_upper']), (limb['lower'], limb['twist_lower'])):
        c = _constraint(pose[deform], 'COPY_ROTATION', kind_label)
        c.target, c.subtarget = armature_obj, source
    end_source = limb['target'] if limb['kind'] == 'ARMS' else limb['foot_target']
    c = _constraint(pose[limb['end']], 'COPY_ROTATION', f'{kind_label} End')
    c.target, c.subtarget = armature_obj, end_source

    for name in (limb['twist_upper'], limb['twist_lower']):
        twist = pose[name]
        twist.rotation_mode = 'YXZ'
        twist.lock_location = (True, True, True)
        twist.lock_rotation = (True, False, True)
        twist.lock_scale = (True, True, True)
    pole = pose[limb['pole']]
    pole.lock_rotation = (True, True, True)
    pole.lock_rotation_w = True

    if limb['kind'] == 'LEGS':
        for name in (limb['heel'], limb['ball']):
            pose[name].rotation_mode = 'XYZ'
        roll = pose[limb['roll']]
        roll.rotation_mode = 'XYZ'
        roll.lock_location = (True, True, True)
        roll.lock_rotation = (False, True, True)
        roll.lock_scale = (True, True, True)
        # Pivot bones point forward with Z up, so +X rotation lifts the front. Rolling the control
        # forward (+X) lifts the heel around the ball; back (-X) lifts the toes around the heel.
        for pivot, lo, hi, to_lo, to_hi in ((limb['ball'], 0.0, FOOT_ROLL_ANGLE, 0.0, -FOOT_ROLL_ANGLE),
                                            (limb['heel'], -FOOT_ROLL_ANGLE, 0.0, FOOT_ROLL_ANGLE, 0.0)):
            c = _constraint(pose[pivot], 'TRANSFORM', 'Foot Roll')
            c.target, c.subtarget = armature_obj, limb['roll']
            c.owner_space = c.target_space = 'LOCAL'
            c.map_from, c.map_to = 'ROTATION', 'ROTATION'
            c.from_min_x_rot, c.from_max_x_rot = lo, hi
            c.to_min_x_rot, c.to_max_x_rot = to_lo, to_hi
            c.map_to_x_from = 'X'
            c.mix_mode_rot = 'REPLACE'
        if limb.get('toe_ctrl'):
            c = _constraint(pose[limb['toe']], 'COPY_ROTATION', 'Leg Toe')
            c.target, c.subtarget = armature_obj, limb['toe_ctrl']


def iter_ik_constraints(armature_obj, limbs='BOTH'):
    """(pose_bone, constraint, kind) for the constraints that the IK/FK switch drives."""
    for pose_bone in armature_obj.pose.bones:
        for constraint in pose_bone.constraints:
            if not constraint.name.startswith(CONSTRAINT_PREFIX) or constraint.type != 'COPY_ROTATION':
                continue
            kind = 'ARMS' if ' Arm' in constraint.name else 'LEGS'
            if limbs == 'BOTH' or limbs == kind:
                yield pose_bone, constraint, kind


def _organize(armature_obj, limbs):
    from ..blender_compat import assign_bone_to_collection, ensure_bone_collection
    armature = armature_obj.data
    ik = ensure_bone_collection(armature, "IK Bones")
    mechanism = ensure_bone_collection(armature, "IK Mechanism")
    for limb in limbs:
        controls = [limb['target'], limb['pole'], limb['twist_upper'], limb['twist_lower']]
        if limb['kind'] == 'LEGS':
            controls += [limb['roll']] + ([limb['toe_ctrl']] if limb.get('toe_ctrl') else [])
        mechanism_bones = [limb['mch_root'], limb['mch_upper'], limb['mch_lower']]
        if limb['kind'] == 'LEGS':
            mechanism_bones += [limb['heel'], limb['ball'], limb['foot_target']]
        for name in controls:
            bone = armature.bones.get(name)
            if bone is not None and ik is not None:
                assign_bone_to_collection(ik, bone)
        for name in mechanism_bones:
            bone = armature.bones.get(name)
            if bone is None:
                continue
            bone.hide = True
            if mechanism is not None:
                assign_bone_to_collection(mechanism, bone)
    if mechanism is not None and hasattr(mechanism, 'is_visible'):
        mechanism.is_visible = False


# ---------------------------------------------------------------------------
# Matching (FK -> IK) and snapping (IK -> FK)
# ---------------------------------------------------------------------------

class MutedIK:
    """Temporarily mutes the IK v2 constraints, exposing the FK pose."""
    def __init__(self, armature_obj):
        self.armature_obj = armature_obj
        self.saved = []

    def __enter__(self):
        for pose_bone in self.armature_obj.pose.bones:
            for constraint in pose_bone.constraints:
                if constraint.name.startswith(CONSTRAINT_PREFIX) and constraint.type == 'COPY_ROTATION':
                    self.saved.append((constraint, constraint.mute))
                    constraint.mute = True
        return self

    def __exit__(self, *args):
        for constraint, mute in self.saved:
            constraint.mute = mute


def _rest(armature_obj, name):
    return armature_obj.data.bones[name].matrix_local


def _basis(armature_obj, name, pose_matrix, parent_pose_matrix):
    bone = armature_obj.data.bones[name]
    if bone.parent is None or parent_pose_matrix is None:
        basis = bone.matrix_local.inverted() @ pose_matrix
    else:
        local_rest = bone.parent.matrix_local.inverted() @ bone.matrix_local
        basis = local_rest.inverted() @ parent_pose_matrix.inverted() @ pose_matrix
    location, rotation, scale = basis.decompose()
    return Matrix.LocRotScale(location, rotation, scale)


def _follow(pose_matrix, rest_matrix, other_rest):
    return pose_matrix @ rest_matrix.inverted() @ other_rest


def control_bases_from_fk(armature_obj, limbs):
    """Local transforms for the IK controls so that they match the current FK pose.
    The IK v2 constraints must be muted."""
    pose = armature_obj.pose.bones
    bases, computed = {}, {}

    def parent_matrix(name):
        bone = armature_obj.data.bones[name]
        if bone.parent is None:
            return None
        return computed.get(bone.parent.name, pose[bone.parent.name].matrix)

    def solve(name, matrix):
        computed[name] = matrix
        bases[name] = _basis(armature_obj, name, matrix, parent_matrix(name))

    for limb in limbs:
        a, b, c = pose[limb['upper']].head, pose[limb['lower']].head, pose[limb['end']].head
        end_matrix = pose[limb['end']].matrix
        end_rest = _rest(armature_obj, limb['end'])
        target_matrix = _follow(end_matrix, end_rest, _rest(armature_obj, limb['target']))
        solve(limb['target'], target_matrix)
        if limb['kind'] == 'LEGS':
            # The foot roll mechanism stays at rest.
            heel = _follow(target_matrix, _rest(armature_obj, limb['target']), _rest(armature_obj, limb['heel']))
            computed[limb['heel']] = heel
            bases[limb['roll']] = Matrix.Identity(4)
            if limb.get('toe_ctrl'):
                toe_matrix = pose[limb['toe']].matrix
                solve(limb['toe_ctrl'], _follow(toe_matrix, _rest(armature_obj, limb['toe']),
                                                 _rest(armature_obj, limb['toe_ctrl'])))
        # Pole: in the plane of the FK limb. For a straight limb, the rest bend direction relative
        # to the upper bone.
        upper_rest = _rest(armature_obj, limb['upper'])
        rest_bend = _rest(armature_obj, limb['pole']).translation - _rest(armature_obj, limb['lower']).translation
        fallback = pose[limb['upper']].matrix.to_3x3() @ upper_rest.to_3x3().inverted() @ rest_bend
        bend = bend_direction(a, b, c, fallback)
        parent = parent_matrix(limb['pole'])
        pole_rest = _rest(armature_obj, limb['pole'])
        bone = armature_obj.data.bones[limb['pole']]
        matrix = _follow(parent, bone.parent.matrix_local, pole_rest) if parent is not None else pole_rest.copy()
        matrix.translation = b + bend * limb['pole_distance']
        solve(limb['pole'], matrix)
        bases[limb['twist_upper']] = Matrix.Identity(4)
        bases[limb['twist_lower']] = Matrix.Identity(4)
    return bases


def twist_bases(armature_obj, limbs, fk_matrices):
    """Twist ring transforms so the IK chains match the FK twist. IK must be evaluated
    with the twist rings at rest."""
    pose = armature_obj.pose.bones
    bases = {}
    for limb in limbs:
        for twist, mch, deform in ((limb['twist_upper'], limb['mch_upper'], limb['upper']),
                                   (limb['twist_lower'], limb['mch_lower'], limb['lower'])):
            if deform not in fk_matrices:
                continue
            # The twist ring rotates about the deform bone's own axis; measure the difference in
            # its frame (the ring's rest pose follows the MCH bone rigidly).
            ring = _follow(pose[mch].matrix, _rest(armature_obj, mch), _rest(armature_obj, twist))
            q = ring.to_quaternion().inverted() @ fk_matrices[deform].to_quaternion()
            angle = 2.0 * math.atan2(q.y, q.w)
            angle = math.atan2(math.sin(angle), math.cos(angle))
            bases[twist] = Matrix.Rotation(angle, 4, 'Y')
    return bases


def _set_basis(pose_bone, basis):
    location, rotation, scale = basis.decompose()
    pose_bone.location = location
    if pose_bone.rotation_mode == 'QUATERNION':
        pose_bone.rotation_quaternion = rotation
    elif pose_bone.rotation_mode == 'AXIS_ANGLE':
        axis, angle = rotation.to_axis_angle()
        pose_bone.rotation_axis_angle = (angle, *axis)
    else:
        pose_bone.rotation_euler = rotation.to_euler(pose_bone.rotation_mode, pose_bone.rotation_euler)
    pose_bone.scale = scale


def _key(pose_bone, frame):
    group = pose_bone.name
    pose_bone.keyframe_insert('location', frame=frame, group=group)
    if pose_bone.rotation_mode == 'QUATERNION':
        pose_bone.keyframe_insert('rotation_quaternion', frame=frame, group=group)
    elif pose_bone.rotation_mode == 'AXIS_ANGLE':
        pose_bone.keyframe_insert('rotation_axis_angle', frame=frame, group=group)
    else:
        pose_bone.keyframe_insert('rotation_euler', frame=frame, group=group)
    pose_bone.keyframe_insert('scale', frame=frame, group=group)


def match_ik_to_fk(context, armature_obj, frames=None, limbs_kind='BOTH', insert_keys=True):
    """Pose (and key) the IK controls so that the IK reproduces the FK pose, on the given
    frames (the current frame if None). Exact, including the twist of each segment."""
    info = get_info(armature_obj)
    if info is None:
        return 0
    kinds = ('ARMS', 'LEGS') if limbs_kind == 'BOTH' else (limbs_kind,)
    limbs = list(iter_limbs(info, kinds))
    scene = context.scene
    saved_frame = scene.frame_current
    single = frames is None
    frames = [scene.frame_current] if single else list(frames)
    pose = armature_obj.pose.bones
    fk_released = _release_fk_curves(armature_obj, True)
    count = 0
    try:
        if single and fk_released:
            scene.frame_set(scene.frame_current)
        for frame in frames:
            if not single:
                scene.frame_set(frame)
            with MutedIK(armature_obj):
                context.view_layer.update()
                bases = control_bases_from_fk(armature_obj, limbs)
                fk = {n: pose[n].matrix.copy() for limb in limbs for n in (limb['upper'], limb['lower'])}
            for name, basis in bases.items():
                _set_basis(pose[name], basis)
            context.view_layer.update()
            twists = twist_bases(armature_obj, limbs, fk)
            for name, basis in twists.items():
                _set_basis(pose[name], basis)
                bases[name] = basis
            if insert_keys:
                for name in bases:
                    _key(pose[name], frame)
            count += 1
    finally:
        _release_fk_curves(armature_obj, False, fk_released)
        if not single:
            scene.frame_set(saved_frame)
    return count


def _release_fk_curves(armature_obj, release, saved=None):
    """While IK is on, the rig mutes the limb FK curves (so the IK does not fight them).
    Matching must read the FK keys, so unmute them for the duration and restore after."""
    from .create_animation_rig import _HELD_FK_MUTE_KEY, _set_ik_driven_fcurves_muted
    if release:
        was_muted = bool(armature_obj.data.get(_HELD_FK_MUTE_KEY))
        if was_muted:
            _set_ik_driven_fcurves_muted(armature_obj, False)
        return was_muted
    if saved:
        _set_ik_driven_fcurves_muted(armature_obj, True)
    return saved


def snap_fk_to_ik(context, armature_obj, limbs_kind='BOTH', insert_keys=False):
    """Pose the FK limb bones like the current IK result (on the current frame)."""
    info = get_info(armature_obj)
    if info is None:
        return 0
    kinds = ('ARMS', 'LEGS') if limbs_kind == 'BOTH' else (limbs_kind,)
    pose = armature_obj.pose.bones
    names = []
    for limb in iter_limbs(info, kinds):
        names += [limb['upper'], limb['lower'], limb['end']]
        if limb.get('toe_ctrl'):
            names.append(limb['toe'])
    context.view_layer.update()
    ordered = [b.name for b in armature_obj.data.bones if b.name in set(names)]
    visual = {n: pose[n].matrix.copy() for n in ordered}
    bases = {}
    for name in ordered:
        bone = armature_obj.data.bones[name]
        parent = visual.get(bone.parent.name, pose[bone.parent.name].matrix) if bone.parent else None
        bases[name] = _basis(armature_obj, name, visual[name], parent)
    for name, basis in bases.items():
        _set_basis(pose[name], basis)
        if insert_keys:
            _key(pose[name], context.scene.frame_current)
    return len(bases)
