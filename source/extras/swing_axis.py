"""Put swing bones on the axis the game's swing solver expects, and clear their tracks.

Every one of the 1135 swing bones across the 61 vanilla fighters in the dump has its child
sitting on its own **+X**. Not a majority - all of them. The solver reads a bone's angle limits
(minanglez/maxanglez, minangley/maxangley) in that bone's local frame, so a rig whose chain runs
down -X has those limits applied to inverted geometry and the chain fights itself, which reads
in game as the hair vibrating.

Two steps, either runnable alone:

    Reorient   turn each offending bone's rest frame so its child lands on +X.
    Tracks     either strip the swing bones' animation tracks, or carry them through the same
               turn so their authored motion survives.

Why the tracks have to be touched at all: a bone's animation track fully replaces its rest
transform, so reorienting the skeleton alone changes nothing in game. On Shigaraki 47 of his 55
swing bones have *constant* tracks, which still override, so "it has no animation" is not a way
out.

On stripping: vanilla does animate its swing bones - 119 of 125 sampled animations across Ike,
Sephiroth, Peach, Pyra and Lucina carry tracks for them, and those tracks vary. They are the
pose `goalstrength` pulls toward, not something physics replaces. Stripping is still a coherent
choice, and the cheaper one, because the goal then becomes the rest pose and the chain hangs
from it under physics; it just gives up whatever authored motion the tracks held. Animations
with no swing tracks do occur in vanilla, so the files stay valid either way.
"""

import os
import shutil

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from ...dependencies import pyprc, ssbh_data_py
from .fighter_scale import backup_files, collect_targets

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

# A near-reversal, not just an exact one, has to take the deterministic branch: the minimal
# rotation turns about cross(+X, d), which vanishes as d approaches -X, so its direction would be
# decided by whatever noise sits in the last decimals. Shigaraki's hair is at
# (-1.304, -0.0, -0.0) - reversed to within 1e-6 - and the minimal path there would set 22
# bones' roll from float dust.
NEAR_FLIP = 1e-6


def correction(child_offset, flip_axis='Z'):
    """Rotation F (3x3, row-vector convention) whose +X maps onto `child_offset`.

    ssbh stores transforms row-major with the translation in row 3, so points are row vectors
    and F's first row is the unit child direction.
    """
    import numpy as np

    v = np.array(child_offset, dtype=np.float64)
    length = float(np.linalg.norm(v))
    if length < 1e-9:
        return np.eye(3)
    d = v / length
    x = np.array([1.0, 0.0, 0.0])
    dot = float(np.dot(x, d))
    if dot > 1.0 - 1e-9:
        return np.eye(3)
    if dot < -1.0 + NEAR_FLIP:
        # A half circle. About Z: +X -> -X, +Y -> -Y, +Z kept. Z is the default because the
        # bone's Z is the axis minanglez/maxanglez are written against, so keeping it leaves
        # those limits meaning what they did. If a chain ends up swinging in the wrong plane,
        # this is the knob.
        if flip_axis == 'Y':
            return np.array([[-1.0, 0.0, 0.0],
                             [0.0, 1.0, 0.0],
                             [0.0, 0.0, -1.0]])
        return np.array([[-1.0, 0.0, 0.0],
                         [0.0, -1.0, 0.0],
                         [0.0, 0.0, 1.0]])
    axis = np.cross(x, d)
    axis /= np.linalg.norm(axis)
    angle = float(np.arccos(max(-1.0, min(1.0, dot))))
    K = np.array([[0.0, -axis[2], axis[1]],
                  [axis[2], 0.0, -axis[0]],
                  [-axis[1], axis[0], 0.0]])
    # Rodrigues for column vectors, transposed for the row-vector convention.
    return (np.eye(3) + np.sin(angle) * K + (1.0 - np.cos(angle)) * (K @ K)).T


def _as4(r3):
    import numpy as np
    m = np.eye(4)
    m[:3, :3] = r3
    return m


# ---------------------------------------------------------------------------
# Reading the rig
# ---------------------------------------------------------------------------

def _labels():
    import csv
    out = {}
    path = os.path.join(os.path.dirname(pyprc.__file__), 'ParamLabels.csv')
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            for row in csv.reader(fh):
                if len(row) >= 2:
                    try:
                        out[int(row[0], 16)] = row[1]
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def _hash_name(handle, labels):
    """Resolve a prc hash to its label.

    Two levels of unwrapping, not one: a `param` holding a hash gives the hash object from
    `.value`, and the 40-bit integer only appears on that object's own `.value`. Peeling a
    single layer leaves a hash object, which is not an int, and silently produced empty bone
    names - every chain then failed to match the rig and the whole scan came back empty.
    """
    value = handle
    for _ in range(3):
        if isinstance(value, int):
            break
        try:
            value = value.value
        except Exception:
            break
    if not isinstance(value, int):
        try:
            value = int(value)
        except Exception:
            return ''
    return labels.get(value, '0x%010x' % value)


def _struct_dict(struct, labels):
    return {_hash_name(key, labels): value for key, value in struct}


def swing_chains(swing_path, skel, labels):
    """[(chain_name, [bone names root..tip], null_terminal_or_None)] for a swing.prc."""
    bones = list(skel.bones)
    lower = {b.name.lower(): b for b in bones}
    children = {}
    for bone in bones:
        if bone.parent_index is not None:
            children.setdefault(bones[bone.parent_index].name, []).append(bone.name)

    out = []
    top = _struct_dict(pyprc.param(swing_path), labels)
    for entry in list(top['swingbones']):
        chain = _struct_dict(entry, labels)
        count = len(list(chain['params']))
        name = _hash_name(chain['name'], labels)
        start = _hash_name(chain['start_bonename'], labels)
        current = lower.get(start.lower())
        walked = []
        while current is not None and len(walked) < count:
            walked.append(current.name)
            kids = children.get(current.name, [])
            current = lower.get(kids[0].lower()) if len(kids) == 1 else None
        terminal = current.name if current is not None else None
        if walked:
            out.append((name, walked, terminal))
    return out


def child_offset(skel, bone_name):
    """The single child's local translation, or None if the bone does not have exactly one."""
    bones = list(skel.bones)
    index = {b.name: i for i, b in enumerate(bones)}
    if bone_name not in index:
        return None
    kids = [b for b in bones if b.parent_index == index[bone_name]]
    if len(kids) != 1:
        return None
    t = kids[0].transform
    return (t[3][0], t[3][1], t[3][2]), kids[0].name


def plan(skel_path, swing_path, flip_axis='Z'):
    """Work out which swing bones need turning.

    Returns (corrections, parent_of, report) where `corrections` maps bone name -> 3x3 F for
    the bones that are off-axis, `parent_of` maps every swing bone (and terminal) to its parent
    name, and `report` is a list of per-chain lines for the UI.
    """
    import numpy as np

    labels = _labels()
    skel = ssbh_data_py.skel_data.read_skel(skel_path)
    bones = list(skel.bones)
    parent_of = {}
    for bone in bones:
        parent_of[bone.name] = (bones[bone.parent_index].name
                                if bone.parent_index is not None else None)

    corrections = {}
    report = []
    for chain_name, walked, terminal in swing_chains(swing_path, skel, labels):
        axes = []
        for bone_name in walked:
            found = child_offset(skel, bone_name)
            if found is None:
                axes.append('?')
                continue
            offset, _child = found
            magnitude = sum(c * c for c in offset) ** 0.5
            if magnitude < 1e-6:
                axes.append('0')
                continue
            dominant = max(range(3), key=lambda k: abs(offset[k]))
            label = ('+' if offset[dominant] >= 0 else '-') + 'XYZ'[dominant]
            axes.append(label)
            if label != '+X':
                F = correction(offset, flip_axis)
                if not np.allclose(F, np.eye(3)):
                    corrections[bone_name] = F
        bad = sum(1 for a in axes if a not in ('+X', '0'))
        report.append((chain_name, walked, terminal, axes, bad))
    return corrections, parent_of, report


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def apply_to_skel(skel_path, corrections, dry_run=False):
    """Turn each corrected bone's rest frame, and absorb the inverse into its children.

    new_local = F_self @ old_local @ inv(F_parent)

    Every bone's WORLD transform comes out unchanged - only its axes turn - so the mesh's bind
    is untouched. ssbh_data_py hands transforms back as numpy arrays and will not take a list of
    lists, so the edit goes through numpy.
    """
    import numpy as np

    skel = ssbh_data_py.skel_data.read_skel(skel_path)
    bones = list(skel.bones)
    parent_name = {}
    for bone in bones:
        parent_name[bone.name] = (bones[bone.parent_index].name
                                  if bone.parent_index is not None else None)

    eye = np.eye(4)
    mats = {name: _as4(F) for name, F in corrections.items()}
    touched = 0
    for bone in bones:
        f_self = mats.get(bone.name, eye)
        parent = parent_name.get(bone.name)
        f_parent = mats.get(parent, eye) if parent else eye
        if f_self is eye and f_parent is eye:
            continue
        old = np.array(bone.transform, dtype=np.float64)
        new = f_self @ old @ np.linalg.inv(f_parent)
        bone.transform = new.astype(np.float32)
        touched += 1
    if not dry_run:
        skel.save(skel_path)
    return touched


def strip_anim_tracks(anim_path, bone_names, dry_run=False):
    """Remove the Transform nodes for `bone_names`, leaving them to the rest pose and physics.

    Only the Transform group is touched: a swing bone has no business in the Visibility or
    Material groups, and removing something there would change how the fighter looks.
    """
    anim = ssbh_data_py.anim_data.read_anim(anim_path)
    removed = 0
    for group in anim.groups:
        if group.group_type.name != 'Transform':
            continue
        keep = [node for node in group.nodes if node.name not in bone_names]
        removed += len(group.nodes) - len(keep)
        if removed:
            group.nodes = keep
    if removed and not dry_run:
        anim.save(anim_path)
    return removed


def transform_anim_tracks(anim_path, corrections, parent_of, dry_run=False):
    """Carry each keyframe through the same turn the rest pose got.

    new = F_self @ old @ inv(F_parent), exactly as for the skeleton, so the posed result is
    identical to before while the bone's local axes now run down +X.

    A Transform is (scale, rotation quaternion, translation) rather than a matrix, so each key
    is composed to a matrix, turned, and decomposed back. Scale is asserted uniform-free rather
    than silently mangled: every swing bone in the corpus has identity scale, and a sheared or
    non-uniformly scaled key would not survive being pulled back through a quaternion.
    """
    import numpy as np

    anim = ssbh_data_py.anim_data.read_anim(anim_path)
    eye = np.eye(4)
    mats = {name: _as4(F) for name, F in corrections.items()}
    touched = keys = skipped_scale = 0

    for group in anim.groups:
        if group.group_type.name != 'Transform':
            continue
        for node in group.nodes:
            f_self = mats.get(node.name, eye)
            parent = parent_of.get(node.name)
            f_parent = mats.get(parent, eye) if parent else eye
            if f_self is eye and f_parent is eye:
                continue
            f_parent_inv = np.linalg.inv(f_parent)
            for track in node.tracks:
                values = track.values
                if not values or not hasattr(values[0], 'translation'):
                    continue
                for value in values:
                    scale = list(value.scale)
                    if any(abs(s - 1.0) > 1e-4 for s in scale):
                        skipped_scale += 1
                        continue
                    x, y, z, w = value.rotation
                    # Row-vector rotation matrix from the quaternion.
                    R = np.array([
                        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
                        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
                        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
                    ])
                    old = np.eye(4)
                    old[:3, :3] = R
                    old[3, :3] = list(value.translation)
                    new = f_self @ old @ f_parent_inv
                    value.translation = [float(c) for c in new[3, :3]]
                    value.rotation = _quat_from_row_matrix(new[:3, :3])
                    keys += 1
                track.values = values
            touched += 1
    if touched and not dry_run:
        anim.save(anim_path)
    return touched, keys, skipped_scale


def _quat_from_row_matrix(R):
    """(x, y, z, w) from a row-vector rotation matrix, the inverse of the build above."""
    import numpy as np

    m = np.array(R, dtype=np.float64)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0.0:
        s = (trace + 1.0) ** 0.5 * 2.0
        w = 0.25 * s
        x = (m[1, 2] - m[2, 1]) / s
        y = (m[2, 0] - m[0, 2]) / s
        z = (m[0, 1] - m[1, 0]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = (1.0 + m[0, 0] - m[1, 1] - m[2, 2]) ** 0.5 * 2.0
        w = (m[1, 2] - m[2, 1]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[2, 0] + m[0, 2]) / s
    elif m[1, 1] > m[2, 2]:
        s = (1.0 + m[1, 1] - m[0, 0] - m[2, 2]) ** 0.5 * 2.0
        w = (m[2, 0] - m[0, 2]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = (1.0 + m[2, 2] - m[0, 0] - m[1, 1]) ** 0.5 * 2.0
        w = (m[0, 1] - m[1, 0]) / s
        x = (m[2, 0] + m[0, 2]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w], dtype=np.float64)
    q /= np.linalg.norm(q)
    return [float(c) for c in q]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class SUB_PG_swing_axis(PropertyGroup):
    skel_path: StringProperty(
        name='model.nusktb', subtype='FILE_PATH', default='',
        description='The rig whose swing bones should be measured and turned')
    swing_path: StringProperty(
        name='swing.prc', subtype='FILE_PATH', default='',
        description='Which bones are swing bones is read from here')
    anim_root: StringProperty(
        name='Animation Folder', subtype='DIR_PATH', default='',
        description='Every .nuanmb under this folder is treated as belonging to that rig')
    track_mode: EnumProperty(
        name='Tracks',
        items=[
            ('STRIP', 'Remove swing tracks',
             'Delete the swing bones\' Transform tracks. Physics then hangs the chain from the '
             'rest pose. Cheaper and cannot get the maths wrong, but gives up whatever motion '
             'the tracks held - and vanilla does animate these bones'),
            ('TRANSFORM', 'Turn swing tracks',
             'Carry every keyframe through the same turn as the rest pose, so the posed result '
             'is unchanged and the authored motion survives'),
            ('NONE', 'Leave tracks alone',
             'Reorient the skeleton only. Included for inspection: constant tracks still '
             'override the rest pose, so in game this will look like nothing happened'),
        ],
        default='STRIP')
    flip_axis: EnumProperty(
        name='Half-turn About',
        items=[('Z', 'Z (keeps bone Z)', 'Keeps the axis minanglez/maxanglez are written against'),
               ('Y', 'Y (keeps bone Y)', 'Try this if a chain swings in the wrong plane')],
        default='Z')
    include_terminals: BoolProperty(
        name='Include _null Terminals', default=True,
        description='Strip or turn the terminal bone\'s track too. It is not simulated, but it '
                    'is a child of a bone that is, so its track has to move with it')
    make_backup: BoolProperty(
        name='Back Up First', default=True,
        description='Copy every file about to change into a sibling folder')
    dry_run: BoolProperty(
        name='Dry Run', default=True,
        description='Report what would change without writing anything')
    scanned: StringProperty(default='')
    scanned_count: IntProperty(default=0)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _settings(context):
    return context.scene.sub_swing_axis


def _resolve(path):
    return bpy.path.abspath(path) if path else ''


class SUB_OP_swing_axis_scan(Operator):
    bl_idname = 'sub.swing_axis_scan'
    bl_label = 'Measure Swing Bone Axes'
    bl_description = ('Report which swing bones do not have their child on +X. Nothing is '
                      'written')
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = _settings(context)
        skel_path, swing_path = _resolve(settings.skel_path), _resolve(settings.swing_path)
        if not (os.path.isfile(skel_path) and os.path.isfile(swing_path)):
            self.report({'ERROR'}, 'Set both model.nusktb and swing.prc first')
            return {'CANCELLED'}
        try:
            corrections, _parent_of, report = plan(skel_path, swing_path, settings.flip_axis)
        except Exception as exc:
            self.report({'ERROR'}, 'Could not read the rig: %s' % exc)
            return {'CANCELLED'}

        lines = []
        for chain_name, walked, terminal, axes, bad in report:
            lines.append('%-14s %-6s %s' % (chain_name, 'BAD' if bad else 'ok', ' '.join(axes)))
        settings.scanned = '\n'.join(lines)
        settings.scanned_count = len(corrections)
        self.report({'INFO'}, '%d chain(s), %d bone(s) need turning'
                    % (len(report), len(corrections)))
        return {'FINISHED'}


class SUB_OP_swing_axis_apply(Operator):
    bl_idname = 'sub.swing_axis_apply'
    bl_label = 'Fix Swing Bone Axes'
    bl_description = ('Turn the off-axis swing bones onto +X and deal with their animation '
                      'tracks')
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = _settings(context)
        skel_path = _resolve(settings.skel_path)
        swing_path = _resolve(settings.swing_path)
        anim_root = _resolve(settings.anim_root)
        if not (os.path.isfile(skel_path) and os.path.isfile(swing_path)):
            self.report({'ERROR'}, 'Set both model.nusktb and swing.prc first')
            return {'CANCELLED'}

        try:
            corrections, parent_of, report = plan(skel_path, swing_path, settings.flip_axis)
        except Exception as exc:
            self.report({'ERROR'}, 'Could not read the rig: %s' % exc)
            return {'CANCELLED'}
        # Removing tracks does not depend on there being anything to reorient. An early return
        # here refused to strip on a rig whose axes were already correct, which is the common
        # case once the skeleton has been fixed - the tracks still override the rest pose and
        # still have to go.
        if not corrections and settings.track_mode in ('NONE', 'TRANSFORM'):
            self.report({'INFO'}, 'Every swing bone is already on +X; nothing to reorient '
                                  'and no tracks to turn')
            return {'FINISHED'}

        # The terminal is not simulated, but it is a child of a bone that is, so its track has
        # to travel with the parent's turn or it will be left behind.
        swing_names = set()
        for _name, walked, terminal, _axes, _bad in report:
            swing_names.update(walked)
            if terminal and settings.include_terminals:
                swing_names.add(terminal)

        anims = []
        if anim_root and os.path.isdir(anim_root) and settings.track_mode != 'NONE':
            anims = collect_targets(anim_root, want_skel=False, want_mesh=False,
                                    want_anim=True, want_swing=False)['anim']

        if settings.make_backup and not settings.dry_run:
            root = os.path.dirname(skel_path)
            stamp = 'swing_axis_backup'
            try:
                backup_files([skel_path], root, os.path.join(root, stamp))
                if anims and anim_root:
                    backup_files(anims, anim_root, os.path.join(anim_root, stamp))
            except OSError as exc:
                self.report({'ERROR'}, 'Backup failed, nothing written: %s' % exc)
                return {'CANCELLED'}

        bones_turned = apply_to_skel(skel_path, corrections, dry_run=settings.dry_run)

        anim_files = tracks = keys = scale_skips = 0
        for path in anims:
            try:
                if settings.track_mode == 'STRIP':
                    n = strip_anim_tracks(path, swing_names, dry_run=settings.dry_run)
                    tracks += n
                    anim_files += 1 if n else 0
                else:
                    t, k, s = transform_anim_tracks(path, corrections, parent_of,
                                                    dry_run=settings.dry_run)
                    tracks += t
                    keys += k
                    scale_skips += s
                    anim_files += 1 if t else 0
            except Exception as exc:
                self.report({'WARNING'}, '%s: %s' % (os.path.basename(path), exc))

        verb = 'would turn' if settings.dry_run else 'turned'
        msg = '%s %d bone(s) in the rig' % (verb, bones_turned)
        if settings.track_mode == 'STRIP':
            msg += '; %d track(s) removed across %d animation(s)' % (tracks, anim_files)
        elif settings.track_mode == 'TRANSFORM':
            msg += '; %d track(s)/%d key(s) turned across %d animation(s)' % (
                tracks, keys, anim_files)
        if scale_skips:
            msg += '; %d key(s) skipped for non-identity scale' % scale_skips
        if settings.dry_run:
            msg += ' (dry run, nothing written)'
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class SUB_PT_swing_axis(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Swing Bone Axis'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        col = layout.column(align=True)
        col.prop(settings, 'skel_path')
        col.prop(settings, 'swing_path')
        col.prop(settings, 'anim_root')
        layout.operator('sub.swing_axis_scan', icon='VIEWZOOM')

        if settings.scanned:
            box = layout.box()
            for line in settings.scanned.split('\n')[:24]:
                box.label(text=line)
            box.label(text='%d bone(s) off +X' % settings.scanned_count,
                      icon='ERROR' if settings.scanned_count else 'CHECKMARK')

        layout.separator()
        col = layout.column(align=True)
        col.prop(settings, 'track_mode')
        col.prop(settings, 'flip_axis')
        col.prop(settings, 'include_terminals')
        col.prop(settings, 'make_backup')
        col.prop(settings, 'dry_run')
        layout.operator('sub.swing_axis_apply',
                        icon='CHECKMARK' if not settings.dry_run else 'INFO')

        info = layout.box()
        info.label(text='All 1135 vanilla swing bones put the child', icon='INFO')
        info.label(text='on +X. A chain running -X has its angle')
        info.label(text='limits applied inverted, which reads as')
        info.label(text='the chain vibrating in game.')


classes = (
    SUB_PG_swing_axis,
    SUB_OP_swing_axis_scan,
    SUB_OP_swing_axis_apply,
    SUB_PT_swing_axis,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    bpy.types.Scene.sub_swing_axis = PointerProperty(type=SUB_PG_swing_axis)


def unregister():
    try:
        del bpy.types.Scene.sub_swing_axis
    except AttributeError:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
