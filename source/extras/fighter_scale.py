"""Scale a whole fighter, and correct its animations to match.

Shrinking a fighter is not just a model edit. A .nuanmb Transform track stores
every bone's local translation on every frame, and those values override the
skeleton's rest pose completely. Scale the skeleton alone and the animations
drag the rig straight back to its original size the moment one plays. So a size
change has to touch four things together:

    model.nusktb   each bone's local translation
    model.numshb   every vertex position
    *.nuanmb       every Transform track's translation
    swing.prc      collision radii, offsets, distances

and must leave four things alone: rotations, non-uniform bone scale values,
normals/tangents (directions, unaffected by a uniform positive scale), and the
Visibility and Material tracks in an animation.

model.nuhlpb is deliberately not in the list. Its constraints are quaternions,
interpolation weights and degree ranges - no lengths. Across this project's mod
tree all 770 orient constraints and 0 aim constraints were checked; there is
nothing in them a scale would change.

Everything is done through ssbh_data_py and pyprc directly, so 1399 animations
do not have to be round-tripped through Blender's importer.
"""

import json
import os
import shutil
import time

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import PointerProperty

from ...dependencies import pyprc, ssbh_data_py

MANIFEST_NAME = '.fighter_scale.json'

# Fields in a swing.prc that are lengths, by list name. Everything omitted is
# either a direction (a plane's nx/ny/nz normal), an angle (an ellipsoid's
# rx/ry/rz, a swing bone's min/max angles) or a dimensionless rate, and must not
# be touched. Names come from the addon's own swing exporter.
SWING_LENGTH_FIELDS = {
    'spheres':     ('cx', 'cy', 'cz', 'radius'),
    'ovals':       ('radius', 'start_offset_x', 'start_offset_y', 'start_offset_z',
                    'end_offset_x', 'end_offset_y', 'end_offset_z'),
    'ellipsoids':  ('cx', 'cy', 'cz', 'sx', 'sy', 'sz'),
    'capsules':    ('start_offset_x', 'start_offset_y', 'start_offset_z',
                    'end_offset_x', 'end_offset_y', 'end_offset_z',
                    'start_radius', 'end_radius'),
    'planes':      ('distance',),
    'connections': ('radius', 'length'),
}
# Per swing bone, inside swingbones[].params[].
SWING_BONE_LENGTH_FIELDS = ('collisionsizetip', 'collisionsizeroot')

# The bone carrying world travel. Scaling it makes the fighter cover
# proportionally less ground, which is what a genuinely smaller character does;
# leaving it alone keeps the moveset's existing spacing at the cost of foot
# sliding. Exposed as a toggle because that is a design call, not a fact.
DEFAULT_ROOT_BONES = 'Trans'


# ---------------------------------------------------------------------------
# Hash label resolution for prc files
# ---------------------------------------------------------------------------

_LABELS = None


def _labels():
    """hash40 -> name, read from the ParamLabels.csv shipped with pyprc."""
    global _LABELS
    if _LABELS is None:
        import csv
        _LABELS = {}
        path = os.path.join(os.path.dirname(pyprc.__file__), 'ParamLabels.csv')
        try:
            with open(path, newline='', encoding='utf-8') as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2:
                        try:
                            _LABELS[int(row[0], 16)] = row[1]
                        except ValueError:
                            pass
        except OSError:
            pass
    return _LABELS


def _hash_name(h):
    try:
        value = h.value
    except Exception:
        try:
            value = int(h)
        except Exception:
            return ''
    return _labels().get(value, '')


def _as_dict(struct):
    return {_hash_name(k): v for k, v in struct}


# ---------------------------------------------------------------------------
# Per-format scaling. Each returns a count of values changed.
# ---------------------------------------------------------------------------

def scale_skel_file(path, factor, dry_run=False):
    """Scale every bone's local translation.

    ssbh stores the bone transform as a row-major 4x4 with the translation in
    row 3. Rows 0-2 hold rotation and scale and are left exactly as they were.

    ssbh_data_py hands these back as numpy arrays and will not accept a list of
    lists on the way in, so the edit goes through numpy rather than plain
    Python sequences.
    """
    import numpy as np

    skel = ssbh_data_py.skel_data.read_skel(path)
    touched = 0
    for bone in skel.bones:
        transform = np.array(bone.transform, dtype=np.float32)
        if np.any(transform[3][:3] != 0.0):
            touched += 1
        transform[3][:3] *= factor
        bone.transform = transform
    if not dry_run:
        skel.save(path)
    return touched


def scale_mesh_file(path, factor, dry_run=False):
    """Scale every vertex position.

    Normals, tangents and binormals are directions. A uniform positive scale
    leaves them pointing the same way and still unit length, so they are not
    touched - rescaling them would be wrong, not merely redundant.
    """
    import numpy as np

    mesh = ssbh_data_py.mesh_data.read_mesh(path)
    verts = 0
    for obj in mesh.objects:
        for attr in obj.positions:
            data = np.array(attr.data, dtype=np.float32)
            data *= factor
            attr.data = data
            verts += len(data)
    if not dry_run:
        mesh.save(path)
    return verts


def scale_anim_file(path, factor, scale_root=True, root_bones=('Trans',),
                    dry_run=False):
    """Scale the translation of every Transform track.

    Only the Transform group is touched. Visibility tracks are booleans and
    Material tracks are shader parameters - a CustomVector is not a distance,
    and scaling one would change how the fighter looks rather than how big it
    is. Rotation and scale inside a Transform are left alone too: a uniform
    resize does not reorient anything, and local scale factors stay relative.

    Returns (nodes_touched, keys_touched).
    """
    anim = ssbh_data_py.anim_data.read_anim(path)
    nodes = keys = 0
    for group in anim.groups:
        if group.group_type.name != 'Transform':
            continue
        for node in group.nodes:
            if not scale_root and node.name in root_bones:
                continue
            for track in node.tracks:
                values = track.values
                if not values or not hasattr(values[0], 'translation'):
                    continue
                for value in values:
                    t = value.translation
                    value.translation = [t[0] * factor, t[1] * factor, t[2] * factor]
                    keys += 1
                track.values = values
            nodes += 1
    if not dry_run:
        anim.save(path)
    return nodes, keys


def scale_swing_file(path, factor, dry_run=False):
    """Scale collision sizes, offsets and distances in a swing.prc."""
    root = pyprc.param(path)
    top = _as_dict(root)
    touched = 0

    for list_name, fields in SWING_LENGTH_FIELDS.items():
        node = top.get(list_name)
        if node is None:
            continue
        try:
            items = list(node)
        except Exception:
            continue
        for item in items:
            entry = _as_dict(item)
            for field in fields:
                param = entry.get(field)
                if param is None:
                    continue
                try:
                    param.value = param.value * factor
                    touched += 1
                except Exception:
                    pass

    chains = top.get('swingbones')
    if chains is not None:
        try:
            chain_list = list(chains)
        except Exception:
            chain_list = []
        for chain in chain_list:
            params = _as_dict(chain).get('params')
            if params is None:
                continue
            try:
                bones = list(params)
            except Exception:
                continue
            for bone in bones:
                entry = _as_dict(bone)
                for field in SWING_BONE_LENGTH_FIELDS:
                    param = entry.get(field)
                    if param is None:
                        continue
                    try:
                        param.value = param.value * factor
                        touched += 1
                    except Exception:
                        pass

    if not dry_run:
        root.save(path)
    return touched


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def collect_targets(root, want_skel=True, want_mesh=True, want_anim=True,
                    want_swing=True, subfolders=None):
    """Walk `root` and bucket the files this module knows how to scale.

    `subfolders` optionally restricts to paths containing one of those
    fragments, so a run can be limited to particular costume slots.
    """
    found = {'skel': [], 'mesh': [], 'anim': [], 'swing': []}
    for dirpath, dirnames, filenames in os.walk(root):
        if '.git' in dirnames:
            dirnames.remove('.git')
        # Never walk into a backup. These tools write originals somewhere before editing, and
        # a second run that found those copies would happily edit them too - destroying the one
        # copy of the untouched files. fighter_scale writes to a sibling of the root so this
        # cannot bite it, but swing_axis writes inside the animation folder.
        for name in list(dirnames):
            lowered = name.lower()
            if lowered.startswith('_scale_backup') or lowered.endswith('_backup'):
                dirnames.remove(name)
        norm = dirpath.replace('\\', '/')
        if subfolders and not any(f in norm for f in subfolders):
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            lower = name.lower()
            if want_skel and lower.endswith('.nusktb'):
                found['skel'].append(full)
            elif want_mesh and lower.endswith('.numshb'):
                found['mesh'].append(full)
            elif want_anim and lower.endswith('.nuanmb'):
                found['anim'].append(full)
            elif want_swing and lower == 'swing.prc':
                found['swing'].append(full)
    for key in found:
        found[key].sort()
    return found


def read_manifest(root):
    path = os.path.join(root, MANIFEST_NAME)
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_manifest(root, factor, counts, scale_root):
    """Record what was applied, so a second run can warn instead of compounding.

    Applying 0.8 twice leaves the fighter at 0.64, and nothing in the files
    themselves says a scale already happened.
    """
    path = os.path.join(root, MANIFEST_NAME)
    existing = read_manifest(root)
    cumulative = factor * (existing.get('cumulative_factor', 1.0) if existing else 1.0)
    data = {
        'cumulative_factor': cumulative,
        'history': (existing.get('history', []) if existing else []) + [{
            'factor': factor,
            'scaled_root_motion': scale_root,
            'when': time.strftime('%Y-%m-%d %H:%M:%S'),
            'counts': counts,
        }],
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
    return cumulative


def backup_files(paths, root, backup_root):
    """Copy originals into `backup_root`, preserving their relative layout."""
    copied = 0
    for path in paths:
        rel = os.path.relpath(path, root)
        dest = os.path.join(backup_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1
    return copied


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class SUB_PG_fighter_scale(PropertyGroup):
    root_path: StringProperty(
        name='Mod Folder',
        subtype='DIR_PATH',
        default='',
        description='Folder to walk. Point it at the fighter folder to cover '
                    'every costume slot, or at one slot to limit the run')
    factor: FloatProperty(
        name='Scale',
        default=0.8, min=0.01, max=10.0, precision=4,
        description='New size as a fraction of the current one. 0.8 shrinks '
                    'the fighter to 80%')
    scale_skeleton: BoolProperty(name='Skeleton (.nusktb)', default=True)
    scale_mesh: BoolProperty(name='Mesh (.numshb)', default=True)
    scale_animations: BoolProperty(name='Animations (.nuanmb)', default=True)
    scale_swing: BoolProperty(name='Swing (swing.prc)', default=True)
    scale_root_motion: BoolProperty(
        name='Scale Root Motion (Trans)', default=True,
        description='Scale the Trans bone too, so the fighter travels '
                    'proportionally less far. Turn off to keep existing '
                    'travel distances, at the cost of foot sliding')
    root_bones: StringProperty(
        name='Root Bones', default=DEFAULT_ROOT_BONES,
        description='Comma separated bones treated as world motion')
    subfolders: StringProperty(
        name='Limit To', default='',
        description='Comma separated path fragments, e.g. "c80,c81". Blank '
                    'means every folder under the mod folder')
    make_backup: BoolProperty(
        name='Back Up Originals', default=True,
        description='Copy every file about to change into a timestamped '
                    'folder beside the mod folder before writing')
    dry_run: BoolProperty(
        name='Dry Run', default=False,
        description='Do all the work but write nothing, to see the counts first')
    last_report: StringProperty(default='')


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _settings(context):
    return context.scene.sub_fighter_scale


def _resolve(settings):
    root = bpy.path.abspath(settings.root_path)
    return os.path.normpath(root) if root else ''


def _subfolder_list(settings):
    return [f.strip() for f in settings.subfolders.split(',') if f.strip()]


class SUB_OP_fighter_scale_scan(Operator):
    bl_idname = 'sub.fighter_scale_scan'
    bl_label = 'Scan Folder'
    bl_description = 'Count the files a scale would touch. Nothing is modified'
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = _settings(context)
        root = _resolve(settings)
        if not root or not os.path.isdir(root):
            self.report({'ERROR'}, 'Set a valid mod folder first')
            return {'CANCELLED'}

        found = collect_targets(
            root, settings.scale_skeleton, settings.scale_mesh,
            settings.scale_animations, settings.scale_swing,
            _subfolder_list(settings))
        parts = ['{} {}'.format(len(v), k) for k, v in found.items() if v]
        settings.last_report = ', '.join(parts) if parts else 'nothing found'

        manifest = read_manifest(root)
        if manifest:
            self.report({'WARNING'},
                        'This folder was already scaled (cumulative {:.4g}). '
                        'Scaling again compounds: {} more would leave it at {:.4g}.'
                        .format(manifest.get('cumulative_factor', 1.0),
                                settings.factor,
                                manifest.get('cumulative_factor', 1.0) * settings.factor))
        else:
            self.report({'INFO'}, 'Found ' + settings.last_report)
        return {'FINISHED'}


class SUB_OP_fighter_scale_apply(Operator):
    bl_idname = 'sub.fighter_scale_apply'
    bl_label = 'Apply Scale'
    bl_description = ('Scale the skeleton, mesh, animations and swing data in '
                      'the mod folder')
    bl_options = {'REGISTER'}

    confirm: BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        settings = _settings(context)
        layout = self.layout
        root = _resolve(settings)
        layout.label(text='Scale everything in:', icon='FILE_FOLDER')
        layout.label(text=root or '(no folder set)')
        layout.label(text='by {:.4g}x'.format(settings.factor))

        manifest = read_manifest(root) if root else None
        if manifest:
            box = layout.box()
            box.alert = True
            box.label(text='Already scaled once - this compounds!', icon='ERROR')
            box.label(text='Cumulative would become {:.4g}'.format(
                manifest.get('cumulative_factor', 1.0) * settings.factor))
        if settings.dry_run:
            layout.label(text='Dry run: nothing will be written', icon='INFO')
        elif not settings.make_backup:
            box = layout.box()
            box.alert = True
            box.label(text='No backup. This rewrites files in place.', icon='ERROR')

    def execute(self, context):
        settings = _settings(context)
        root = _resolve(settings)
        if not root or not os.path.isdir(root):
            self.report({'ERROR'}, 'Set a valid mod folder first')
            return {'CANCELLED'}

        factor = settings.factor
        found = collect_targets(
            root, settings.scale_skeleton, settings.scale_mesh,
            settings.scale_animations, settings.scale_swing,
            _subfolder_list(settings))
        every = [p for group in found.values() for p in group]
        if not every:
            self.report({'WARNING'}, 'No matching files under that folder')
            return {'CANCELLED'}

        backup_root = ''
        if settings.make_backup and not settings.dry_run:
            backup_root = os.path.join(
                os.path.dirname(root.rstrip('\\/')),
                '_scale_backup_{}'.format(time.strftime('%Y%m%d_%H%M%S')))
            try:
                backup_files(every, root, backup_root)
            except OSError as e:
                self.report({'ERROR'}, 'Backup failed, nothing written: {}'.format(e))
                return {'CANCELLED'}

        root_bones = tuple(b.strip() for b in settings.root_bones.split(',') if b.strip())
        counts = {'skel_bones': 0, 'mesh_verts': 0, 'anim_nodes': 0,
                  'anim_keys': 0, 'swing_values': 0}
        failures = []

        for path in found['skel']:
            try:
                counts['skel_bones'] += scale_skel_file(path, factor, settings.dry_run)
            except Exception as e:
                failures.append((path, e))
        for path in found['mesh']:
            try:
                counts['mesh_verts'] += scale_mesh_file(path, factor, settings.dry_run)
            except Exception as e:
                failures.append((path, e))
        for path in found['anim']:
            try:
                nodes, keys = scale_anim_file(
                    path, factor, settings.scale_root_motion, root_bones,
                    settings.dry_run)
                counts['anim_nodes'] += nodes
                counts['anim_keys'] += keys
            except Exception as e:
                failures.append((path, e))
        for path in found['swing']:
            try:
                counts['swing_values'] += scale_swing_file(path, factor, settings.dry_run)
            except Exception as e:
                failures.append((path, e))

        summary = ('{} skeletons ({} bones), {} meshes ({} verts), {} anims '
                   '({} keys), {} swing ({} values)'.format(
                       len(found['skel']), counts['skel_bones'],
                       len(found['mesh']), counts['mesh_verts'],
                       len(found['anim']), counts['anim_keys'],
                       len(found['swing']), counts['swing_values']))
        settings.last_report = summary

        if settings.dry_run:
            self.report({'INFO'}, 'Dry run, nothing written: ' + summary)
            return {'FINISHED'}

        cumulative = write_manifest(root, factor, counts, settings.scale_root_motion)
        message = 'Scaled by {:.4g} (cumulative {:.4g}): {}'.format(
            factor, cumulative, summary)
        if backup_root:
            message += '. Backup: {}'.format(backup_root)
        if failures:
            for path, err in failures[:5]:
                print('fighter_scale FAILED {}: {}'.format(path, err))
            self.report({'WARNING'},
                        '{} file(s) failed, see console. {}'.format(len(failures), message))
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class SUB_PT_fighter_scale(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Fighter Scale'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        settings = _settings(context)
        layout = self.layout

        layout.prop(settings, 'root_path')
        layout.prop(settings, 'factor')
        layout.prop(settings, 'subfolders')

        col = layout.column(align=True)
        col.label(text='Scale these:')
        col.prop(settings, 'scale_skeleton')
        col.prop(settings, 'scale_mesh')
        col.prop(settings, 'scale_animations')
        col.prop(settings, 'scale_swing')

        box = layout.box()
        box.prop(settings, 'scale_root_motion')
        if settings.scale_root_motion:
            box.label(text='Travels proportionally less far.', icon='INFO')
        else:
            box.label(text='Keeps travel distances; feet may slide.', icon='INFO')
            box.prop(settings, 'root_bones')

        col = layout.column(align=True)
        col.prop(settings, 'make_backup')
        col.prop(settings, 'dry_run')

        layout.operator('sub.fighter_scale_scan', icon='VIEWZOOM')
        row = layout.row()
        row.scale_y = 1.3
        row.operator('sub.fighter_scale_apply', icon='FULLSCREEN_EXIT')

        if settings.last_report:
            info = layout.box()
            info.label(text=settings.last_report, icon='CHECKMARK')

        note = layout.box()
        note.label(text='.nuhlpb needs no scaling: its', icon='INFO')
        note.label(text='constraints hold no lengths.')


classes = (
    SUB_PG_fighter_scale,
    SUB_OP_fighter_scale_scan,
    SUB_OP_fighter_scale_apply,
    SUB_PT_fighter_scale,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    bpy.types.Scene.sub_fighter_scale = PointerProperty(type=SUB_PG_fighter_scale)


def unregister():
    try:
        del bpy.types.Scene.sub_fighter_scale
    except AttributeError:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
