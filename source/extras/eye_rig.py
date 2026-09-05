"""Eye look control bone that writes CustomVector31.

Smash eyes are aimed by sliding UV layer 2 (CustomVector31 Z/W). Export reads
keyframes on mat_tracks, not drivers, so Bake writes real keyframes.
"""

import array
import re

import bpy
from bpy.props import EnumProperty, FloatProperty
from bpy.types import Operator
from mathutils import Vector

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..anim.anim_data import SUB_PG_sub_anim_data


EYE_TRACKS = ('EyeL', 'EyeR')
CV31 = 'CustomVector31'
CV31_DEFAULT = (1.0, 1.0, 0.0, 0.0)
EYE_MATERIAL_ANIM_ITEMS = (
    ('MATCH', "Match Material Anim",
     "Copy the look onto the eye bone, clean baked interpolation keys, and delete EyeL/EyeR CustomVector31 keyframes"),
    ('REPLACE', "Replace Material Anim",
     "Delete EyeL/EyeR CustomVector31 keyframes and add an empty eye bone with no animation"),
)
EYE_CTRL_BONE = 'BL_EyeLook'
EYE_OPT_INVERT_X = 'BL_Eye_InvertX'
EYE_OPT_INVERT_Y = 'BL_Eye_InvertY'
EYE_OPT_PUPIL = 'BL_Eye_Pupil'
EYE_OPT_MEASURE = 'BL_Eye_Measure'
EYE_OPTION_BONES = (
    (EYE_OPT_INVERT_X, 'THEME01', 'X'),
    (EYE_OPT_INVERT_Y, 'THEME04', 'Y'),
)
EYE_TOGGLE_TRAVEL_KEY = 'sub_eye_toggle_travel'
# Workbench (Solid + Texture) samples the active Image Texture with the active UV
# map and never evaluates CustomVector31. A viewport-only UV Warp modifier
# applies the same scale/translate so eye look is visible there too.
SOLID_UV_NAME = '.sub_eye_solid'
SOLID_UV_FALLBACK = '_sub_eye_solid'
UV_WARP_NAME = 'SUB_EyeSolidWarp'
EYE_UV_FROM = 'BL_Eye_UVFrom'
EYE_UV_SPHERE = 'BL_Eye_UVSphere'
EYE_UV_TO_L = 'BL_Eye_UVToL'
EYE_UV_TO_R = 'BL_Eye_UVToR'
EYE_UV_HELPERS = (EYE_UV_FROM, EYE_UV_SPHERE, EYE_UV_TO_L, EYE_UV_TO_R)
EYE_UV_COLLECTION = 'Eye UV Warp'
EYE_UV_CONSTRAINT = 'SUB Eye UV'
_PREV_UV_KEY = 'sub_eye_prev_uv'
_PREV_TEX_KEY = 'sub_eye_prev_tex'
_PREV_EXT_KEY = 'sub_eye_prev_ext'
_PREV_EXT_NODE_KEY = 'sub_eye_ext_node'
_last_solid_uv = {}
_rest_uv_cache = {}
_solid_preview_engaged = False
_eye_preview_running = False


def is_eye_control_bone(name):
    from .create_animation_rig import canonical_bone_name
    base = canonical_bone_name(name)
    return base == EYE_CTRL_BONE or base.startswith('BL_Eye_')


def is_eye_uv_helper_bone(name):
    from .create_animation_rig import canonical_bone_name
    return canonical_bone_name(name) in EYE_UV_HELPERS


def is_eye_look_widget_bone(name):
    from .create_animation_rig import canonical_bone_name
    base = canonical_bone_name(name)
    return base == EYE_CTRL_BONE or base in {
        EYE_OPT_INVERT_X, EYE_OPT_INVERT_Y, EYE_OPT_PUPIL, EYE_OPT_MEASURE,
    }


def _eye_toggle_travel(arma):
    return float(arma.data.get(EYE_TOGGLE_TRAVEL_KEY, 0.05))


def _eye_toggle_on(arma, bone_name):
    pbone = arma.pose.bones.get(bone_name)
    if pbone is None:
        return None
    return pbone.location.y > _eye_toggle_travel(arma) * 0.35


def _resolve_eye_bool(arma, bone_name, fallback):
    value = _eye_toggle_on(arma, bone_name)
    return fallback if value is None else value


def eye_meshes(arma):
    from ..model.export_model import trim_name
    found = []
    for child in arma.children:
        if child.type != 'MESH' or not child.material_slots:
            continue
        mat = child.material_slots[0].material
        if mat is not None and trim_name(mat.name) in EYE_TRACKS:
            found.append(child)
    return found


def face_forward(arma, anchor_bone):
    meshes = eye_meshes(arma)
    if meshes and anchor_bone is not None:
        centre = Vector((0.0, 0.0, 0.0))
        n = 0
        for mesh in meshes:
            local = arma.matrix_world.inverted() @ mesh.matrix_world
            for corner in mesh.bound_box:
                centre += local @ Vector(corner)
                n += 1
        if n:
            centre /= n
            direction = centre - anchor_bone.head
            direction.z = 0.0
            if direction.length > 1e-6:
                return direction.normalized()
    return Vector((0.0, -1.0, 0.0))


def control_offset_armature_space(pbone):
    return pbone.bone.matrix_local.to_3x3() @ pbone.location


def look_values_from_control(pbone, sensitivity, clamp, invert_x=False, invert_y=False,
                             sensitivity_y=None):
    offset = control_offset_armature_space(pbone)
    if sensitivity_y is None:
        sensitivity_y = sensitivity
    dx = max(-clamp, min(clamp, offset.x * sensitivity))
    dz = max(-clamp, min(clamp, offset.z * sensitivity_y))
    if invert_x:
        dx = -dx
    if invert_y:
        dz = -dz
    return dx, -dx, dz


def head_frame(arma):
    pbone = None
    for candidate in ('Head', 'Face', 'Neck'):
        pbone = arma.pose.bones.get(candidate)
        if pbone is not None:
            break
    if pbone is None:
        return None

    rest = pbone.bone.matrix_local.to_3x3()
    delta = pbone.matrix.to_3x3() @ rest.inverted()
    forward = (delta @ face_forward(arma, pbone.bone)).normalized()
    up = (delta @ Vector((0.0, 0.0, 1.0))).normalized()
    right = forward.cross(up).normalized()
    up = right.cross(forward).normalized()
    return pbone.matrix.translation.copy(), right, up, forward


def look_at_values(arma, ctrl_pbone, gain, clamp, invert_x=False, invert_y=False,
                   gain_y=None):
    frame = head_frame(arma)
    if frame is None:
        return 0.0, 0.0, 0.0
    origin, right, up, _forward = frame
    direction = ctrl_pbone.matrix.translation - origin
    if direction.length < 1e-6:
        return 0.0, 0.0, 0.0
    direction.normalize()
    if gain_y is None:
        gain_y = gain
    du = max(-clamp, min(clamp, direction.dot(right) * gain))
    dv = max(-clamp, min(clamp, direction.dot(up) * gain_y))
    if invert_x:
        du = -du
    if invert_y:
        dv = -dv
    return du, -du, dv


_pupil_centre_cache = {}
_last_preview_values = {}
_last_idle_preview_key = None


def resolve_pupil_centre(arma, ssp):
    if not ssp.eye_pupil_centre_auto:
        return Vector((ssp.eye_pupil_centre[0], ssp.eye_pupil_centre[1]))
    key = arma.as_pointer()
    cached = _pupil_centre_cache.get(key)
    if cached is not None:
        return cached.copy()
    centre = eye_uv_centre(arma)
    _pupil_centre_cache[key] = centre.copy()
    return centre


def eye_uv_centre(arma):
    total = Vector((0.0, 0.0))
    n = 0
    for mesh_obj in eye_meshes(arma):
        me = mesh_obj.data
        uv_layer = me.uv_layers.active
        if uv_layer is None:
            continue
        for loop in me.loops:
            uv = uv_layer.data[loop.index].uv
            total += Vector((uv[0], uv[1]))
            n += 1
    if n == 0:
        return Vector((0.5, 0.5))
    return total / n


def compensate_scale_about_pupil(look_u, look_v, scale, uv_centre):
    if abs(scale) < 1e-6:
        return look_u, look_v
    return (
        uv_centre.x - (uv_centre.x - look_u) / scale,
        uv_centre.y - (uv_centre.y - look_v) / scale,
    )


def pupil_scale_from_control(pbone, min_cv=0.1, max_cv=10.0):
    average = (pbone.scale.x + pbone.scale.y + pbone.scale.z) / 3.0
    average = max(1e-3, average)
    return max(min_cv, min(max_cv, 1.0 / average))


def compute_cv31(arma, pbone, ssp):
    invert_x = _resolve_eye_bool(arma, EYE_OPT_INVERT_X, ssp.eye_look_invert_x)
    invert_y = _resolve_eye_bool(arma, EYE_OPT_INVERT_Y, ssp.eye_look_invert_y)
    pupil_from_scale = bool(getattr(ssp, 'eye_look_pupil_from_scale', False))
    if ssp.eye_look_mode == 'LOOK_AT':
        left_u, right_u, v = look_at_values(
            arma, pbone, ssp.eye_look_gain, ssp.eye_look_clamp,
            invert_x, invert_y, ssp.eye_look_gain_y)
    else:
        left_u, right_u, v = look_values_from_control(
            pbone, ssp.eye_look_sensitivity, ssp.eye_look_clamp,
            invert_x, invert_y, ssp.eye_look_sensitivity_y)

    scale = pupil_scale_from_control(pbone) if pupil_from_scale else None
    if scale is not None and ssp.eye_look_scale_about_pupil:
        centre = resolve_pupil_centre(arma, ssp)
        left_u, v_l = compensate_scale_about_pupil(left_u, v, scale, centre)
        right_u, v_r = compensate_scale_about_pupil(right_u, v, scale, centre)
        v = v_l if abs(v_l - v_r) < 1e-9 else (v_l + v_r) * 0.5
    return left_u, right_u, v, scale


def apply_look_to_tracks(arma, ssp):
    pbone = arma.pose.bones.get(EYE_CTRL_BONE)
    if pbone is None:
        return False
    sap = arma.data.sub_anim_properties
    left_u, right_u, v, scale = compute_cv31(arma, pbone, ssp)
    changed = False
    for name, u in (('EyeL', left_u), ('EyeR', right_u)):
        track = sap.mat_tracks.get(name)
        prop = track.properties.get(CV31) if track else None
        if prop is None:
            continue
        if abs(prop.custom_vector[2] - u) > 1e-7 or abs(prop.custom_vector[3] - v) > 1e-7:
            prop.custom_vector[2] = u
            prop.custom_vector[3] = v
            changed = True
        if scale is not None and (
            abs(prop.custom_vector[0] - scale) > 1e-7
            or abs(prop.custom_vector[1] - scale) > 1e-7
        ):
            prop.custom_vector[0] = scale
            prop.custom_vector[1] = scale
            changed = True
    return changed


def _cv31_look_from_stored(arma, ssp, left_u, right_u, v, scale):
    invert_x = _resolve_eye_bool(arma, EYE_OPT_INVERT_X, ssp.eye_look_invert_x)
    invert_y = _resolve_eye_bool(arma, EYE_OPT_INVERT_Y, ssp.eye_look_invert_y)
    look_l, look_r, look_v = left_u, right_u, v
    if (
        scale is not None
        and bool(getattr(ssp, 'eye_look_pupil_from_scale', False))
        and bool(getattr(ssp, 'eye_look_scale_about_pupil', True))
        and abs(float(scale) - 1.0) > 1e-4
    ):
        centre = resolve_pupil_centre(arma, ssp)
        if look_l is not None:
            look_l = centre.x - scale * (centre.x - look_l)
        if look_r is not None:
            look_r = centre.x - scale * (centre.x - look_r)
        if look_v is not None:
            look_v = centre.y - scale * (centre.y - look_v)
    if look_l is not None and look_r is not None:
        # Vanilla Smash often stores the same Z on both eyes. This rig mirrors
        # them (left = dx, right = -dx). Pick whichever reading matches the data.
        if abs(look_l + look_r) < abs(look_l - look_r):
            dx = (look_l - look_r) * 0.5
        else:
            dx = (look_l + look_r) * 0.5
    elif look_l is not None:
        dx = look_l
    elif look_r is not None:
        dx = -look_r
    else:
        dx = 0.0
    dz = look_v if look_v is not None else 0.0
    if invert_x:
        dx = -dx
    if invert_y:
        dz = -dz
    return dx, dz


def _offset_location_vector(pbone, ssp, dx, dz):
    sensitivity = max(float(ssp.eye_look_sensitivity), 1e-6)
    sensitivity_y = max(float(getattr(ssp, 'eye_look_sensitivity_y', sensitivity)), 1e-6)
    offset = Vector((dx / sensitivity, 0.0, dz / sensitivity_y))
    return pbone.bone.matrix_local.to_3x3().inverted() @ offset


def _set_offset_location(pbone, ssp, dx, dz):
    pbone.location = _offset_location_vector(pbone, ssp, dx, dz)


def _set_bone_armature_translation(pbone, arma_pos):
    """Set location so the posed bone sits at arma_pos. Do not assign pbone.matrix."""
    parent = pbone.parent
    if parent is not None:
        rest_posed = parent.matrix @ pbone.bone.matrix
    else:
        rest_posed = pbone.bone.matrix_local.copy()
    try:
        pbone.location = rest_posed.inverted() @ arma_pos
    except ValueError:
        pbone.location = (0.0, 0.0, 0.0)


def _set_look_at_location(arma, pbone, ssp, dx, dz):
    frame = head_frame(arma)
    if frame is None:
        _set_offset_location(pbone, ssp, dx, dz)
        return
    origin, right, up, forward = frame
    gain = max(float(ssp.eye_look_gain), 1e-6)
    gain_y = max(float(getattr(ssp, 'eye_look_gain_y', gain)), 1e-6)
    sx = max(-0.999, min(0.999, dx / gain))
    sy = max(-0.999, min(0.999, dz / gain_y))
    fwd_amt = 1.0 - sx * sx - sy * sy
    fwd_amt = (fwd_amt ** 0.5) if fwd_amt > 0.0 else 0.0
    direction = right * sx + up * sy + forward * fwd_amt
    if direction.length < 1e-6:
        direction = forward.copy()
    else:
        direction.normalize()
    rest_ctrl = pbone.bone.matrix_local.to_translation()
    rest_head = None
    for name in ('Head', 'Face', 'Neck'):
        bone = arma.data.bones.get(name)
        if bone is not None:
            rest_head = bone.matrix_local.to_translation()
            break
    distance = max((rest_ctrl - rest_head).length, 0.05) if rest_head is not None else max(rest_ctrl.length, 0.05)
    _set_bone_armature_translation(pbone, origin + direction * distance)


def apply_cv31_to_control(arma, pbone, ssp, left_u, right_u, v, scale):
    dx, dz = _cv31_look_from_stored(arma, ssp, left_u, right_u, v, scale)
    if ssp.eye_look_mode == 'LOOK_AT':
        _set_look_at_location(arma, pbone, ssp, dx, dz)
    else:
        _set_offset_location(pbone, ssp, dx, dz)
    if scale is not None and bool(getattr(ssp, 'eye_look_pupil_from_scale', False)):
        bone_scale = 1.0 / max(float(scale), 1e-3)
        pbone.scale = (bone_scale, bone_scale, bone_scale)
    else:
        pbone.scale = (1.0, 1.0, 1.0)


def _eye_look_key_frames(arma):
    animation_data = getattr(arma, 'animation_data', None)
    action = getattr(animation_data, 'action', None) if animation_data else None
    if action is None:
        return set()
    from ..anim.fcurve_compat import get_all_action_fcurves
    prefix = f'pose.bones["{EYE_CTRL_BONE}"]'
    alt = f"pose.bones['{EYE_CTRL_BONE}']"
    frames = set()
    for fcurve in get_all_action_fcurves(action, id_type='OBJECT'):
        path = fcurve.data_path or ''
        if not (path.startswith(prefix) or path.startswith(alt)):
            continue
        if 'location' not in path and 'scale' not in path:
            continue
        for keyframe in fcurve.keyframe_points:
            frames.add(round(float(keyframe.co[0]), 5))
    return frames


def _clear_eye_look_keys(arma):
    animation_data = getattr(arma, 'animation_data', None)
    action = getattr(animation_data, 'action', None) if animation_data else None
    if action is None:
        return
    from ..anim.fcurve_compat import (
        get_all_action_fcurves,
        is_eye_control_fcurve,
        remove_fcurve,
    )
    for fcurve in list(get_all_action_fcurves(action, id_type='OBJECT')):
        if is_eye_control_fcurve(fcurve):
            remove_fcurve(action, fcurve, id_type='OBJECT')


_CV31_PATH = re.compile(
    r'sub_anim_properties\.mat_tracks\[(\d+)\]\.properties\[(\d+)\]\.custom_vector(?:\[(\d+)\])?$'
)


def _iter_action_fcurves(action):
    """Yield every fcurve on a Blender 4 or 5 action, including all slots."""
    if action is None:
        return
    seen = set()
    legacy = getattr(action, 'fcurves', None)
    if legacy is not None:
        try:
            for fcurve in legacy:
                key = (fcurve.data_path, fcurve.array_index)
                if key not in seen:
                    seen.add(key)
                    yield fcurve
            if seen:
                return
        except Exception:
            pass
    layers = getattr(action, 'layers', None) or []
    slots = list(getattr(action, 'slots', None) or [])
    for layer in layers:
        for strip in layer.strips:
            bags = getattr(strip, 'channelbags', None)
            if bags:
                for bag in bags:
                    for fcurve in bag.fcurves:
                        key = (fcurve.data_path, fcurve.array_index)
                        if key not in seen:
                            seen.add(key)
                            yield fcurve
            for slot in slots:
                try:
                    bag = strip.channelbag(slot, ensure=False)
                except Exception:
                    bag = None
                if bag is None:
                    continue
                for fcurve in bag.fcurves:
                    key = (fcurve.data_path, fcurve.array_index)
                    if key not in seen:
                        seen.add(key)
                        yield fcurve


def _is_eye_track_name(name):
    base = (name or '').split('.')[0]
    return base in EYE_TRACKS or (name or '') in EYE_TRACKS


def _sap_action_for_arma(arma):
    """Return the SAP action that holds material keys, assigning it if needed."""
    from ..blender_compat import assign_action
    data_ad = getattr(arma.data, 'animation_data', None)
    if data_ad is not None and data_ad.action is not None:
        return data_ad.action
    obj_ad = getattr(arma, 'animation_data', None)
    obj_action = getattr(obj_ad, 'action', None) if obj_ad else None
    if obj_action is not None:
        expected = f"{arma.name} {obj_action.name} SAP Data"
        sap = bpy.data.actions.get(expected)
        if sap is not None:
            if data_ad is None:
                data_ad = arma.data.animation_data_create()
            assign_action(data_ad, sap)
            return sap
    prefix = f"{arma.name} "
    for action in bpy.data.actions:
        if action.name.startswith(prefix) and action.name.endswith(" SAP Data"):
            if data_ad is None:
                data_ad = arma.data.animation_data_create()
            assign_action(data_ad, action)
            return action
    return None


def _cv31_fcurves(arma):
    sap = arma.data.sub_anim_properties
    action = _sap_action_for_arma(arma)
    if action is None:
        return {}, None
    curves = {}

    def remember(name, axis, fcurve):
        if _is_eye_track_name(name) and fcurve is not None:
            base = name.split('.')[0] if name.split('.')[0] in EYE_TRACKS else name
            if base not in EYE_TRACKS:
                if name.startswith('EyeL'):
                    base = 'EyeL'
                elif name.startswith('EyeR'):
                    base = 'EyeR'
                else:
                    return
            curves[(base, int(axis))] = fcurve

    for fcurve in _iter_action_fcurves(action):
        path = fcurve.data_path or ''
        match = _CV31_PATH.search(path)
        if match is None:
            continue
        track_index = int(match.group(1))
        prop_index = int(match.group(2))
        axis_in_path = match.group(3)
        axis = int(axis_in_path) if axis_in_path is not None else int(fcurve.array_index)
        track_name = None
        if track_index < len(sap.mat_tracks):
            track = sap.mat_tracks[track_index]
            prop_ok = (
                prop_index < len(track.properties)
                and track.properties[prop_index].name == CV31
            )
            if _is_eye_track_name(track.name) and (prop_ok or 'custom_vector' in path):
                track_name = track.name
        if track_name is None:
            group = getattr(fcurve, 'group', None)
            gname = getattr(group, 'name', '') or ''
            group_match = re.match(r'Material \((Eye[LR][^)]*)\)', gname)
            if group_match:
                track_name = group_match.group(1)
        if track_name is None:
            continue
        remember(track_name, axis, fcurve)
    return curves, action


def _set_eye_cv31_fcurves_retired(arma, retired):
    """Mute and hide EyeL/EyeR CustomVector31 so they do not fight BL_EyeLook."""
    curves, _action = _cv31_fcurves(arma)
    hidden = bool(retired)
    for fcurve in curves.values():
        try:
            fcurve.mute = hidden
        except Exception:
            pass
        try:
            fcurve.hide = hidden
        except Exception:
            pass


def _delete_eye_cv31_keys(arma):
    """Permanently remove EyeL/EyeR CustomVector31 fcurves from the SAP action."""
    from ..anim.fcurve_compat import id_type_for_id_data, remove_fcurve
    curves, action = _cv31_fcurves(arma)
    if action is None:
        return 0
    id_type = id_type_for_id_data(arma.data)
    removed = 0
    for fcurve in list(dict.fromkeys(curves.values())):
        try:
            remove_fcurve(action, fcurve, id_type=id_type)
            removed += 1
        except Exception:
            pass
    return removed


def _finish_eye_material_after_match(arma, delete_material):
    if delete_material:
        _delete_eye_cv31_keys(arma)
    else:
        _set_eye_cv31_fcurves_retired(arma, True)


def armature_has_eye_material_keys(arma):
    if arma is None or getattr(arma, 'type', None) != 'ARMATURE':
        return False
    curves, _action = _cv31_fcurves(arma)
    for (_name, axis), fcurve in curves.items():
        if axis not in {2, 3} or fcurve is None:
            continue
        try:
            if len(fcurve.keyframe_points) > 0:
                return True
        except Exception:
            continue
    return False


def _clean_redundant_eye_look_keys(arma, threshold=1e-3):
    from ..anim.fcurve_compat import (
        clean_fcurve_redundant_keys,
        get_all_action_fcurves,
        is_eye_control_fcurve,
        style_eye_control_action,
    )
    animation_data = getattr(arma, 'animation_data', None)
    action = getattr(animation_data, 'action', None) if animation_data else None
    if action is None:
        return 0
    removed = 0
    for fcurve in get_all_action_fcurves(action, id_type='OBJECT'):
        if is_eye_control_fcurve(fcurve):
            removed += clean_fcurve_redundant_keys(fcurve, threshold=threshold)
    try:
        style_eye_control_action(action)
    except Exception:
        pass
    return removed


def _timed_point_deviation_vec(point, start, end):
    span = float(end[0]) - float(start[0])
    if abs(span) < 1e-12:
        return (point[1] - start[1]).length
    factor = (float(point[0]) - float(start[0])) / span
    expected = start[1].lerp(end[1], factor)
    return (point[1] - expected).length


def _timed_point_deviation_scalar(point, start, end):
    span = float(end[0]) - float(start[0])
    if abs(span) < 1e-12:
        return abs(point[1] - start[1])
    factor = (float(point[0]) - float(start[0])) / span
    expected = start[1] + (end[1] - start[1]) * factor
    return abs(point[1] - expected)


def _drop_consecutive_timed_keys(points, almost_same):
    if len(points) < 2:
        return list(points)
    kept = [points[0]]
    last = points[-1]
    for item in points[1:-1]:
        if not almost_same(kept[-1], item):
            kept.append(item)
    if kept[-1][0] != last[0]:
        kept.append(last)
    else:
        kept[-1] = last
    return kept


def _simplify_timed_keys(points, deviation, epsilon, almost_same=None):
    """Drop baked-interpolation samples. Keeps corners, start, and end."""
    if almost_same is not None:
        points = _drop_consecutive_timed_keys(points, almost_same)
    if len(points) < 3:
        return list(points)
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        start = points[lo]
        end = points[hi]
        worst_i = -1
        worst = 0.0
        for index in range(lo + 1, hi):
            dist = deviation(points[index], start, end)
            if dist > worst:
                worst = dist
                worst_i = index
        if worst_i >= 0 and worst > epsilon:
            keep.add(worst_i)
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [points[index] for index in sorted(keep)]


def _eval_cv31_axis(curves, name, axis, frame, default):
    fcurve = curves.get((name, axis))
    if fcurve is None:
        return default
    return float(fcurve.evaluate(frame))


def _ensure_pose_fcurve(action, data_path, index, group):
    from ..anim.fcurve_compat import find_fcurve, new_fcurve
    fcurve = find_fcurve(action, data_path, index=index, id_type='OBJECT')
    if fcurve is None:
        fcurve = new_fcurve(
            action, data_path, index=index, action_group=group, id_type='OBJECT',
        )
    return fcurve


def _write_fcurve_keys(fcurve, frames_and_values):
    points = fcurve.keyframe_points
    try:
        points.clear()
    except Exception:
        while points:
            points.remove(points[0])
    count = len(frames_and_values)
    if count == 0:
        return
    points.add(count)
    coords = []
    for frame, value in frames_and_values:
        coords.extend([float(frame), float(value)])
    points.foreach_set('co', coords)
    for keyframe in points:
        keyframe.interpolation = 'LINEAR'
    fcurve.update()


def _write_eye_look_location_keys(arma, locations, scales):
    """Write BL_EyeLook loc/scale fcurves directly so Blender 5 slots cannot drop them."""
    from ..blender_compat import assign_action, ensure_action_slot
    from ..anim.fcurve_compat import style_eye_control_action
    if arma.animation_data is None:
        arma.animation_data_create()
    action = arma.animation_data.action
    if action is None:
        stem = "EyeLook"
        obj_name = arma.name
        action = bpy.data.actions.new(f"{obj_name} {stem}")
        assign_action(arma.animation_data, action)
    else:
        ensure_action_slot(action, arma)
        slot = getattr(arma.animation_data, 'action_slot', None)
        if slot is None:
            assign_action(arma.animation_data, action)

    loc_path = f'pose.bones["{EYE_CTRL_BONE}"].location'
    scale_path = f'pose.bones["{EYE_CTRL_BONE}"].scale'
    for axis in range(3):
        fcurve = _ensure_pose_fcurve(action, loc_path, axis, EYE_CTRL_BONE)
        _write_fcurve_keys(fcurve, [(frame, loc[axis]) for frame, loc in locations])
    if scales:
        for axis in range(3):
            fcurve = _ensure_pose_fcurve(action, scale_path, axis, EYE_CTRL_BONE)
            _write_fcurve_keys(fcurve, [(frame, value) for frame, value in scales])
    try:
        style_eye_control_action(action)
    except Exception:
        pass
    return len(locations)


def match_eye_look_from_material(arma, *, overwrite=False, delete_material=False):
    """Translate EyeL/EyeR CustomVector31 Z/W keys into BL_EyeLook location keys."""
    if arma is None or arma.type != 'ARMATURE':
        return 0
    pbone = arma.pose.bones.get(EYE_CTRL_BONE)
    if pbone is None:
        return 0
    ssp = getattr(bpy.context.scene, 'sub_scene_properties', None)
    if ssp is None:
        return 0
    curves, _action = _cv31_fcurves(arma)
    look_curves = [fc for (name, axis), fc in curves.items() if axis in {2, 3}]
    if not look_curves:
        return 0
    if not overwrite and len(_eye_look_key_frames(arma)) > 1:
        _finish_eye_material_after_match(arma, delete_material)
        return 0
    frames = set()
    for fcurve in look_curves:
        for keyframe in fcurve.keyframe_points:
            frames.add(round(float(keyframe.co[0]), 5))
    if not frames:
        return 0

    looks = []
    scales = []
    max_look = 0.0
    for frame in sorted(frames):
        left_u = _eval_cv31_axis(curves, 'EyeL', 2, frame, None)
        right_u = _eval_cv31_axis(curves, 'EyeR', 2, frame, None)
        left_v = _eval_cv31_axis(curves, 'EyeL', 3, frame, None)
        right_v = _eval_cv31_axis(curves, 'EyeR', 3, frame, None)
        if left_v is not None and right_v is not None:
            v = (left_v + right_v) * 0.5
        else:
            v = left_v if left_v is not None else right_v
        scale_x = _eval_cv31_axis(curves, 'EyeL', 0, frame, None)
        if scale_x is None:
            scale_x = _eval_cv31_axis(curves, 'EyeR', 0, frame, None)
        scale_y = _eval_cv31_axis(curves, 'EyeL', 1, frame, None)
        if scale_y is None:
            scale_y = _eval_cv31_axis(curves, 'EyeR', 1, frame, None)
        if scale_x is None and scale_y is None:
            scale = None
        else:
            sx = 1.0 if scale_x is None else scale_x
            sy = 1.0 if scale_y is None else scale_y
            scale = (sx + sy) * 0.5
        dx, dz = _cv31_look_from_stored(arma, ssp, left_u, right_u, v, scale)
        max_look = max(max_look, abs(dx), abs(dz))
        looks.append((frame, Vector((dx, dz))))
        if scale is not None and bool(getattr(ssp, 'eye_look_pupil_from_scale', False)):
            bone_scale = 1.0 / max(float(scale), 1e-3)
            scales.append((frame, bone_scale))

    if max_look < 1e-8:
        if not delete_material:
            _set_eye_cv31_fcurves_retired(arma, True)
        return 0

    prev_live = bool(getattr(ssp, 'eye_look_live_preview', False))
    ssp.eye_look_live_preview = False
    keyed = 0
    try:
        if overwrite:
            _clear_eye_look_keys(arma)
        looks = _simplify_timed_keys(
            looks,
            _timed_point_deviation_vec,
            0.004,
            almost_same=lambda a, b: (a[1] - b[1]).length < 1e-6,
        )
        locations = [
            (frame, _offset_location_vector(pbone, ssp, look.x, look.y))
            for frame, look in looks
        ]
        if scales:
            scales = _simplify_timed_keys(
                scales,
                _timed_point_deviation_scalar,
                0.01,
                almost_same=lambda a, b: abs(a[1] - b[1]) < 1e-5,
            )
        keyed = _write_eye_look_location_keys(arma, locations, scales)
        current = bpy.context.scene.frame_current if bpy.context.scene else locations[0][0]
        best = min(locations, key=lambda item: abs(item[0] - current))
        pbone.location = best[1]
        if scales:
            scale_val = min(scales, key=lambda item: abs(item[0] - current))[1]
            pbone.scale = (scale_val, scale_val, scale_val)
        _clean_redundant_eye_look_keys(arma, threshold=1e-3)
        _finish_eye_material_after_match(arma, delete_material)
    finally:
        ssp.eye_look_live_preview = prev_live
    if prev_live:
        ensure_eye_live_preview()
    return keyed


_live_sync_running = False
_shader_preview_engaged = False


def _iter_view3d_shading():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                shading = getattr(space, 'shading', None)
                if shading is not None:
                    yield shading


def _solid_texture_viewport_open():
    for shading in _iter_view3d_shading():
        if shading.type == 'SOLID' and getattr(shading, 'color_type', None) == 'TEXTURE':
            return True
    return False


def _shader_viewport_open():
    """Material Preview only — not Rendered.

    Live CV31/UV drivers write material sockets every depsgraph tick. That is
    fine for Material shading, but it restarts EEVEE/Cycles sampling forever in
    Rendered views. Bake/SAP keys still drive eyes during final renders.
    """
    for shading in _iter_view3d_shading():
        if shading.type == 'MATERIAL':
            return True
    return False


def _rendered_viewport_open():
    for shading in _iter_view3d_shading():
        if shading.type == 'RENDERED':
            return True
    return False


def _iris_image_node(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return None
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    named = nodes.get('Texture1')
    if named is not None and named.type == 'TEX_IMAGE' and named.image:
        return named
    for node in nodes:
        if node.type != 'TEX_IMAGE' or not node.image:
            continue
        for link in links:
            if link.to_node is not node:
                continue
            from_node = link.from_node
            uv_map = getattr(from_node, 'uv_map', None)
            if uv_map == 'uvSet':
                return node
            if from_node.type == 'GROUP' or 'uv_transform' in (from_node.name or '').lower():
                for uv_link in links:
                    if uv_link.to_node is not from_node:
                        continue
                    if getattr(uv_link.from_node, 'uv_map', None) == 'uvSet':
                        return node
    return None


def _source_uv_layer(mesh):
    src = mesh.uv_layers.get('uvSet')
    if src is not None:
        return src
    active = mesh.uv_layers.active
    if active is not None and not _is_solid_uv_layer(active.name):
        return active
    for layer in mesh.uv_layers:
        if not _is_solid_uv_layer(layer.name):
            return layer
    return None


def _is_solid_uv_layer(name):
    return name in {SOLID_UV_NAME, SOLID_UV_FALLBACK}


def _solid_uv_layer(mesh):
    return mesh.uv_layers.get(SOLID_UV_NAME) or mesh.uv_layers.get(SOLID_UV_FALLBACK)


def _ensure_solid_uv_layer(mesh):
    src = _source_uv_layer(mesh)
    if src is None:
        return None, None
    dst = _solid_uv_layer(mesh)
    if dst is None:
        dst = mesh.uv_layers.new(name=SOLID_UV_NAME)
        if not _is_solid_uv_layer(dst.name):
            try:
                dst.name = SOLID_UV_FALLBACK
            except Exception:
                pass
    return src, dst


def _write_solid_uv(mesh, scale_x, scale_y, trans_x, trans_y):
    src, dst = _ensure_solid_uv_layer(mesh)
    if src is None or dst is None:
        return False
    key = mesh.as_pointer()
    count = len(src.data)
    state = (scale_x, scale_y, trans_x, trans_y, count, dst.name, 'clamp')
    if _last_solid_uv.get(key) == state:
        return False
    rest_key = (key, src.name, count)
    rest = _rest_uv_cache.get(rest_key)
    if rest is None:
        rest = array.array('f', [0.0]) * (count * 2)
        src.data.foreach_get('uv', rest)
        _rest_uv_cache[rest_key] = rest
    try:
        import numpy as np
        buf = np.frombuffer(rest, dtype=np.float32).copy()
        buf[0::2] = np.clip((buf[0::2] - trans_x) * scale_x, 0.0, 1.0)
        buf[1::2] = np.clip((buf[1::2] - trans_y) * scale_y, 0.0, 1.0)
        dst.data.foreach_set('uv', buf)
    except Exception:
        buf = array.array('f', rest)
        for i in range(0, len(buf), 2):
            u = (buf[i] - trans_x) * scale_x
            v = (buf[i + 1] - trans_y) * scale_y
            buf[i] = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
            buf[i + 1] = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
        dst.data.foreach_set('uv', buf)
    _last_solid_uv[key] = state
    mesh.update_tag()
    return True


def _remember_active_uv(mesh):
    if _PREV_UV_KEY in mesh:
        return
    active = mesh.uv_layers.active
    if active is not None and not _is_solid_uv_layer(active.name):
        mesh[_PREV_UV_KEY] = active.name


def _set_active_uv(mesh, name):
    layer = mesh.uv_layers.get(name) if name else _solid_uv_layer(mesh)
    if layer is None:
        return
    active = mesh.uv_layers.active
    if active is not None and active.name == layer.name:
        return
    mesh.uv_layers.active = layer


def _remember_active_texture(material):
    if material is None or _PREV_TEX_KEY in material:
        return
    nodes = material.node_tree.nodes if material.use_nodes and material.node_tree else None
    if nodes is None or nodes.active is None:
        return
    material[_PREV_TEX_KEY] = nodes.active.name


def _set_active_image_node(material, node):
    if material is None or node is None or not material.use_nodes:
        return
    nodes = material.node_tree.nodes
    if nodes.active is node:
        return
    node.select = True
    nodes.active = node


def _apply_iris_solid_sampling(material):
    node = _iris_image_node(material)
    if node is None or not hasattr(node, 'extension'):
        return
    _remember_active_texture(material)
    if _PREV_EXT_KEY not in material:
        material[_PREV_EXT_KEY] = node.extension
        material[_PREV_EXT_NODE_KEY] = node.name
    if node.extension != 'CLIP':
        node.extension = 'CLIP'
    _set_active_image_node(material, node)


def _cv31_indices_for_mat(arma, mat):
    sap = getattr(arma.data, 'sub_anim_properties', None)
    if sap is None or mat is None:
        return None
    from ..model.export_model import trim_name
    name = trim_name(mat.name)
    track = sap.mat_tracks.get(name)
    if track is None or track.properties.get(CV31) is None:
        return None
    return sap.mat_tracks.find(name), track.properties.find(CV31)


def _strip_uv_warp_drivers(mesh_obj):
    for path in (
        f'modifiers["{UV_WARP_NAME}"].scale',
        f'modifiers["{UV_WARP_NAME}"].offset',
    ):
        for index in (0, 1, None):
            try:
                if index is None:
                    mesh_obj.driver_remove(path)
                else:
                    mesh_obj.driver_remove(path, index)
            except Exception:
                pass


def _apply_cv31_to_warp(mod, sx, sy, tx, ty):
    """Match Smash uv' = scale * (uv - trans). UV Warp with center 0 is scale * uv + offset."""
    if mod is None or mod.type != 'UV_WARP':
        return
    ox, oy = -sx * tx, -sy * ty
    try:
        cur_s = mod.scale
        cur_o = mod.offset
        if (
            abs(cur_s[0] - sx) < 1e-6 and abs(cur_s[1] - sy) < 1e-6
            and abs(cur_o[0] - ox) < 1e-6 and abs(cur_o[1] - oy) < 1e-6
        ):
            return
        mod.scale = (sx, sy)
        mod.offset = (ox, oy)
    except Exception:
        pass


def _sync_solid_warps(arma, ssp):
    needs_setup = False
    meshes = eye_meshes(arma)
    if not meshes:
        return
    for mesh_obj in meshes:
        marker = str(mesh_obj.get('sub_eye_warp', ''))
        if not marker.startswith('cv31:') or mesh_obj.modifiers.get(UV_WARP_NAME) is None:
            needs_setup = True
            break
    if needs_setup:
        _ensure_eye_uv_warps(arma)
    pbone = arma.pose.bones.get(EYE_CTRL_BONE)
    live = ssp is not None and getattr(ssp, 'eye_look_live_preview', False)
    from_bone = pbone is not None and live
    left_u = right_u = v = None
    sx = sy = 1.0
    if from_bone:
        left_u, right_u, v, scale = compute_cv31(arma, pbone, ssp)
        sx = sy = 1.0 if scale is None else float(scale)
    from ..model.export_model import trim_name
    for mesh_obj in meshes:
        mod = mesh_obj.modifiers.get(UV_WARP_NAME)
        if mod is None or mod.type != 'UV_WARP':
            continue
        mat = mesh_obj.material_slots[0].material if mesh_obj.material_slots else None
        if from_bone:
            side = trim_name(mat.name) if mat else ''
            tx = left_u if side == 'EyeL' else right_u
            _apply_cv31_to_warp(mod, sx, sy, tx, v)
            continue
        cv = _cv31_for_eye_mesh(arma, mat)
        if cv is None:
            continue
        _apply_cv31_to_warp(mod, cv[0], cv[1], cv[2], cv[3])


def _head_anchor_name(arma):
    for name in ('Head', 'Face', 'Neck'):
        if arma.data.bones.get(name) is not None:
            return name
    return None


def _rest_look_distance(arma):
    ctrl = arma.data.bones.get(EYE_CTRL_BONE)
    anchor = arma.data.bones.get(_head_anchor_name(arma) or '')
    if ctrl is None or anchor is None:
        return 1.0
    return max((ctrl.head_local - anchor.head_local).length, 0.05)


def _uv_warp_influence(arma, ssp):
    if ssp is not None and getattr(ssp, 'eye_look_mode', 'LOOK_AT') == 'LOOK_AT':
        gain = max(float(getattr(ssp, 'eye_look_gain', 0.35)), 1e-6)
        return min(2.0, max(0.001, gain / _rest_look_distance(arma)))
    sensitivity = 0.05 if ssp is None else float(getattr(ssp, 'eye_look_sensitivity', 0.05))
    return min(2.0, max(0.001, sensitivity))


def _uv_warp_settings_key(arma, ssp):
    invert_x = _resolve_eye_bool(
        arma, EYE_OPT_INVERT_X,
        bool(getattr(ssp, 'eye_look_invert_x', False)) if ssp else False,
    )
    invert_y = _resolve_eye_bool(
        arma, EYE_OPT_INVERT_Y,
        bool(getattr(ssp, 'eye_look_invert_y', False)) if ssp else False,
    )
    mode = 'LOOK_AT' if ssp is None else str(getattr(ssp, 'eye_look_mode', 'LOOK_AT'))
    return '|'.join((
        mode,
        f"{_uv_warp_influence(arma, ssp):.4f}",
        f"{int(bool(invert_x))}",
        f"{int(bool(invert_y))}",
    ))


def _clear_uv_constraints(pose_bone):
    if pose_bone is None:
        return
    for constraint in list(pose_bone.constraints):
        if constraint.name.startswith(EYE_UV_CONSTRAINT) or constraint.name.startswith('SUB Eye UV'):
            pose_bone.constraints.remove(constraint)


def _hide_eye_uv_helpers(arma):
    from ..blender_compat import ensure_bone_collection, isolate_bone_in_collection
    collection = ensure_bone_collection(arma.data, EYE_UV_COLLECTION)
    if collection is not None and hasattr(collection, 'is_visible'):
        collection.is_visible = False
    for name in EYE_UV_HELPERS:
        bone = arma.data.bones.get(name)
        if bone is None:
            continue
        isolate_bone_in_collection(collection, bone)
        bone.hide = True
        if hasattr(bone, 'hide_select'):
            bone.hide_select = True
        pbone = arma.pose.bones.get(name)
        if pbone is not None:
            pbone.lock_location = (True, True, True)
            pbone.lock_rotation = (True, True, True)
            pbone.lock_scale = (True, True, True)
            if hasattr(pbone, 'lock_rotation_w'):
                pbone.lock_rotation_w = True


def _configure_eye_uv_helpers(arma, ssp=None, force=False):
    if arma.pose.bones.get(EYE_CTRL_BONE) is None:
        return False
    missing = [name for name in EYE_UV_HELPERS if arma.pose.bones.get(name) is None]
    if missing:
        return False
    key = _uv_warp_settings_key(arma, ssp)
    if not force and arma.data.get('sub_eye_uv_warp') == key:
        return True
    look_at = ssp is None or getattr(ssp, 'eye_look_mode', 'LOOK_AT') == 'LOOK_AT'
    invert_x = _resolve_eye_bool(arma, EYE_OPT_INVERT_X, bool(getattr(ssp, 'eye_look_invert_x', False)) if ssp else False)
    invert_y = _resolve_eye_bool(arma, EYE_OPT_INVERT_Y, bool(getattr(ssp, 'eye_look_invert_y', False)) if ssp else False)
    influence = _uv_warp_influence(arma, ssp)
    anchor = _head_anchor_name(arma)
    source = EYE_UV_SPHERE if look_at else EYE_CTRL_BONE

    for name in EYE_UV_HELPERS:
        _clear_uv_constraints(arma.pose.bones.get(name))

    sphere = arma.pose.bones.get(EYE_UV_SPHERE)
    if look_at and sphere is not None:
        copy_loc = sphere.constraints.new('COPY_LOCATION')
        copy_loc.name = EYE_UV_CONSTRAINT
        copy_loc.target = arma
        copy_loc.subtarget = EYE_CTRL_BONE
        copy_loc.target_space = 'WORLD'
        copy_loc.owner_space = 'WORLD'
        if anchor:
            limit = sphere.constraints.new('LIMIT_DISTANCE')
            limit.name = f'{EYE_UV_CONSTRAINT} Clamp'
            limit.target = arma
            limit.subtarget = anchor
            limit.distance = _rest_look_distance(arma)
            limit.limit_mode = 'LIMITDIST_ONSURFACE'

    to_l = arma.pose.bones.get(EYE_UV_TO_L)
    copy_l = to_l.constraints.new('COPY_LOCATION')
    copy_l.name = EYE_UV_CONSTRAINT
    copy_l.target = arma
    copy_l.subtarget = source
    copy_l.use_x = True
    copy_l.use_y = True
    copy_l.use_z = True
    copy_l.invert_x = invert_x
    copy_l.invert_y = False
    copy_l.invert_z = invert_y
    copy_l.target_space = 'LOCAL'
    copy_l.owner_space = 'LOCAL'
    copy_l.influence = influence

    to_r = arma.pose.bones.get(EYE_UV_TO_R)
    copy_r = to_r.constraints.new('COPY_LOCATION')
    copy_r.name = EYE_UV_CONSTRAINT
    copy_r.target = arma
    copy_r.subtarget = EYE_UV_TO_L
    copy_r.use_x = True
    copy_r.use_y = True
    copy_r.use_z = True
    copy_r.invert_x = True
    copy_r.invert_y = False
    copy_r.invert_z = False
    copy_r.target_space = 'LOCAL'
    copy_r.owner_space = 'LOCAL'

    _hide_eye_uv_helpers(arma)
    arma.data['sub_eye_uv_warp'] = key
    return True


def _ensure_uv_warp(mesh_obj, arma, side):
    marker = f'cv31:{side}'
    mod = mesh_obj.modifiers.get(UV_WARP_NAME)
    if (
        mesh_obj.get('sub_eye_warp') == marker
        and mod is not None
        and mod.type == 'UV_WARP'
        and not getattr(mod, 'object_from', None)
        and not getattr(mod, 'object_to', None)
    ):
        return True
    if mod is None:
        try:
            mod = mesh_obj.modifiers.new(UV_WARP_NAME, 'UV_WARP')
        except Exception:
            return False
    if mod is None or mod.type != 'UV_WARP':
        return False
    _strip_uv_warp_drivers(mesh_obj)
    src = _source_uv_layer(mesh_obj.data)
    if src is not None:
        try:
            mod.uv_layer = src.name
        except Exception:
            pass
    try:
        mod.center = (0.0, 0.0)
    except Exception:
        pass
    mod.offset = (0.0, 0.0)
    mod.scale = (1.0, 1.0)
    try:
        mod.rotation = 0.0
    except Exception:
        pass
    try:
        mod.axis_u = 'X'
        mod.axis_v = 'Y'
    except TypeError:
        try:
            mod.axis_u = 0
            mod.axis_v = 1
        except Exception:
            pass
    try:
        mod.object_from = None
        mod.bone_from = ''
        mod.object_to = None
        mod.bone_to = ''
    except Exception:
        pass
    # Avoid rewriting RNA when the flag is already correct (restarts viewport samples).
    want_vp = _solid_texture_viewport_open()
    if bool(getattr(mod, "show_viewport", False)) != bool(want_vp):
        mod.show_viewport = want_vp
    if bool(getattr(mod, "show_render", True)):
        mod.show_render = False
    if hasattr(mod, 'show_in_editmode'):
        mod.show_in_editmode = False
    mesh_obj['sub_eye_warp'] = marker
    return True


def _ensure_eye_uv_warps(arma):
    ssp = getattr(getattr(bpy.context, 'scene', None), 'sub_scene_properties', None)
    _configure_eye_uv_helpers(arma, ssp)
    any_ok = False
    from ..model.export_model import trim_name
    for mesh_obj in eye_meshes(arma):
        mat = mesh_obj.material_slots[0].material if mesh_obj.material_slots else None
        side = trim_name(mat.name) if mat else ''
        if side not in EYE_TRACKS:
            continue
        if _ensure_uv_warp(mesh_obj, arma, side):
            src = _source_uv_layer(mesh_obj.data)
            if src is not None:
                _remember_active_uv(mesh_obj.data)
                _set_active_uv(mesh_obj.data, src.name)
            _apply_iris_solid_sampling(mat)
            any_ok = True
    return any_ok


def _set_eye_uv_warps_viewport(scene, enabled):
    if scene is None:
        return
    enabled = bool(enabled)
    for arma in _iter_solid_eye_armatures(scene):
        for mesh_obj in eye_meshes(arma):
            mod = mesh_obj.modifiers.get(UV_WARP_NAME)
            if mod is not None and mod.type == 'UV_WARP':
                if bool(getattr(mod, "show_viewport", False)) != enabled:
                    mod.show_viewport = enabled
                if bool(getattr(mod, "show_render", True)):
                    mod.show_render = False


def _remove_stale_uv_warps(arma):
    for mesh_obj in eye_meshes(arma):
        mod = mesh_obj.modifiers.get(UV_WARP_NAME)
        if mod is not None:
            _strip_uv_warp_drivers(mesh_obj)
            mesh_obj.modifiers.remove(mod)
        if 'sub_eye_warp' in mesh_obj:
            del mesh_obj['sub_eye_warp']


def _restore_solid_eye_preview(scene):
    _set_eye_uv_warps_viewport(scene, False)
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        prev = mesh.get(_PREV_UV_KEY)
        if prev:
            _set_active_uv(mesh, prev)
            del mesh[_PREV_UV_KEY]
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            nodes = mat.node_tree.nodes if mat.use_nodes and mat.node_tree else None
            if _PREV_EXT_KEY in mat:
                if nodes is not None:
                    ext_node = nodes.get(mat.get(_PREV_EXT_NODE_KEY, ''))
                    if ext_node is not None and hasattr(ext_node, 'extension'):
                        ext_node.extension = mat[_PREV_EXT_KEY]
                del mat[_PREV_EXT_KEY]
                if _PREV_EXT_NODE_KEY in mat:
                    del mat[_PREV_EXT_NODE_KEY]
            if _PREV_TEX_KEY not in mat:
                continue
            prev_tex = mat.get(_PREV_TEX_KEY)
            if nodes is not None and prev_tex:
                node = nodes.get(prev_tex)
                if node is not None:
                    nodes.active = node
                    node.select = True
            del mat[_PREV_TEX_KEY]


def _cv31_from_nodes(mat):
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes
    if nodes.get(f'{CV31}_Z') is None and nodes.get(f'{CV31}_W') is None:
        return None

    def axis(name, default):
        node = nodes.get(f'{CV31}_{name}')
        if node is None:
            return default
        return float(node.outputs[0].default_value)

    return (axis('X', 1.0), axis('Y', 1.0), axis('Z', 0.0), axis('W', 0.0))


def _cv31_for_eye_mesh(arma, mat):
    sap = getattr(arma.data, 'sub_anim_properties', None)
    if sap is not None and mat is not None:
        from ..model.export_model import trim_name
        track = sap.mat_tracks.get(trim_name(mat.name))
        prop = track.properties.get(CV31) if track else None
        if prop is not None:
            cv = prop.custom_vector
            return (cv[0], cv[1], cv[2], cv[3])
    return _cv31_from_nodes(mat)


def apply_solid_eye_preview(arma):
    _ensure_eye_uv_warps(arma)


def is_eye_preview_running():
    return _eye_preview_running


_DRIVER_CV31 = 'sub_eye_cv31'
_DRIVER_WARP = 'sub_eye_warp'
_driver_pose_cache = {}


def _register_eye_driver_namespace():
    bpy.app.driver_namespace[_DRIVER_CV31] = _ns_eye_cv31
    bpy.app.driver_namespace[_DRIVER_WARP] = _ns_eye_warp


def _unregister_eye_driver_namespace():
    bpy.app.driver_namespace.pop(_DRIVER_CV31, None)
    bpy.app.driver_namespace.pop(_DRIVER_WARP, None)


def _eye_cv31_for_driver(arma):
    """Same look values SSBH would upload as CustomVector31 uniforms."""
    try:
        if arma is None or arma.type != 'ARMATURE':
            return (0.0, 0.0, 0.0, 1.0, False)
        scene = getattr(bpy.context, 'scene', None)
        ssp = getattr(scene, 'sub_scene_properties', None) if scene is not None else None
        pbone = arma.pose.bones.get(EYE_CTRL_BONE)
        if pbone is None or ssp is None:
            return (0.0, 0.0, 0.0, 1.0, False)
        loc = pbone.location
        sc = pbone.scale
        frame = int(getattr(scene, 'frame_current', 0))
        token = (
            arma.as_pointer(),
            frame,
            round(float(loc.x), 5),
            round(float(loc.y), 5),
            round(float(loc.z), 5),
            round(float(sc.x), 5),
            round(float(sc.y), 5),
            round(float(sc.z), 5),
        )
        cached = _driver_pose_cache.get(token)
        if cached is not None:
            return cached
        left_u, right_u, v, scale = compute_cv31(arma, pbone, ssp)
        sx = 1.0 if scale is None else float(scale)
        result = (float(left_u), float(right_u), float(v), sx, scale is not None)
        if len(_driver_pose_cache) > 12:
            _driver_pose_cache.clear()
        _driver_pose_cache[token] = result
        return result
    except Exception:
        return (0.0, 0.0, 0.0, 1.0, False)


def _ns_eye_cv31(arma_name, side, axis, _dep=0.0):
    arma = bpy.data.objects.get(arma_name)
    left_u, right_u, v, sx, has_scale = _eye_cv31_for_driver(arma)
    tx = left_u if int(side) == 0 else right_u
    axis = int(axis)
    if axis in (0, 1):
        return sx if has_scale else 1.0
    if axis == 2:
        return tx
    return v


def _ns_eye_warp(arma_name, side, which, _dep=0.0):
    arma = bpy.data.objects.get(arma_name)
    left_u, right_u, v, sx, _has_scale = _eye_cv31_for_driver(arma)
    tx = left_u if int(side) == 0 else right_u
    which = int(which)
    if which in (0, 1):
        return sx
    if which == 2:
        return -sx * tx
    return -sx * v


def _live_driver_dep_sum(driver, arma):
    """Pose/scene deps so Blender re-evaluates when the look actually changes."""
    parts = []

    def add_bone(name, bone, transform_type, space='LOCAL_SPACE'):
        if arma.pose.bones.get(bone) is None:
            return
        var = driver.variables.new()
        var.name = name
        var.type = 'TRANSFORMS'
        target = var.targets[0]
        target.id = arma
        target.bone_target = bone
        target.transform_type = transform_type
        target.transform_space = space
        parts.append(name)

    add_bone('lx', EYE_CTRL_BONE, 'LOC_X')
    add_bone('ly', EYE_CTRL_BONE, 'LOC_Y')
    add_bone('lz', EYE_CTRL_BONE, 'LOC_Z')
    add_bone('sx', EYE_CTRL_BONE, 'SCALE_X')
    add_bone('sy', EYE_CTRL_BONE, 'SCALE_Y')
    add_bone('sz', EYE_CTRL_BONE, 'SCALE_Z')
    add_bone('ix', EYE_OPT_INVERT_X, 'LOC_Y')
    add_bone('iy', EYE_OPT_INVERT_Y, 'LOC_Y')
    add_bone('hx', 'Head', 'LOC_X', 'WORLD_SPACE')
    add_bone('hy', 'Head', 'LOC_Y', 'WORLD_SPACE')
    add_bone('hz', 'Head', 'LOC_Z', 'WORLD_SPACE')
    scene = getattr(bpy.context, 'scene', None)
    if scene is not None:
        for index, path in enumerate((
            'sub_scene_properties.eye_look_gain',
            'sub_scene_properties.eye_look_gain_y',
            'sub_scene_properties.eye_look_sensitivity',
            'sub_scene_properties.eye_look_sensitivity_y',
            'sub_scene_properties.eye_look_clamp',
        )):
            try:
                var = driver.variables.new()
                var.name = f'p{index}'
                var.type = 'SINGLE_PROP'
                target = var.targets[0]
                target.id_type = 'SCENE'
                target.id = scene
                target.data_path = path
                parts.append(var.name)
            except Exception:
                pass
    return '+'.join(parts) if parts else '0'


def _arma_driver_name(arma):
    return arma.name.replace('\\', '\\\\').replace("'", "\\'")


def _add_sap_cv31_var(driver, arma, name, track_index, prop_index, axis):
    var = driver.variables.new()
    var.name = name
    var.type = 'SINGLE_PROP'
    target = var.targets[0]
    target.id_type = 'ARMATURE'
    target.id = arma.data
    target.data_path = (
        f'sub_anim_properties.mat_tracks[{track_index}]'
        f'.properties[{prop_index}].custom_vector[{axis}]'
    )


def _attach_sap_cv31_node_driver(node, arma, track_index, prop_index, axis_index):
    try:
        node.outputs[0].driver_remove('default_value')
    except Exception:
        pass
    if track_index < 0 or prop_index < 0:
        return
    try:
        fcurve = node.outputs[0].driver_add('default_value')
        var = fcurve.driver.variables.new()
        var.name = 'var'
        target = var.targets[0]
        target.id_type = 'ARMATURE'
        target.id = arma.data
        target.data_path = (
            f'sub_anim_properties.mat_tracks[{track_index}]'
            f'.properties[{prop_index}].custom_vector[{axis_index}]'
        )
        fcurve.driver.expression = 'var'
    except Exception:
        pass


def _attach_sap_uv_warp_drivers(arma, mesh_obj, track_index, prop_index):
    """UV Warp = Smash uv' = scale * (uv - trans), driven by EyeL/EyeR CustomVector31 keys."""
    if track_index < 0 or prop_index < 0 or mesh_obj.modifiers.get(UV_WARP_NAME) is None:
        return
    scale_path = f'modifiers["{UV_WARP_NAME}"].scale'
    offset_path = f'modifiers["{UV_WARP_NAME}"].offset'
    for index, axis in ((0, 0), (1, 1)):
        try:
            fcurve = mesh_obj.driver_add(scale_path, index)
            driver = fcurve.driver
            driver.type = 'SCRIPTED'
            _add_sap_cv31_var(driver, arma, 's', track_index, prop_index, axis)
            driver.expression = 's'
        except Exception:
            pass
    for index, scale_axis, trans_axis in ((0, 0, 2), (1, 1, 3)):
        try:
            fcurve = mesh_obj.driver_add(offset_path, index)
            driver = fcurve.driver
            driver.type = 'SCRIPTED'
            _add_sap_cv31_var(driver, arma, 's', track_index, prop_index, scale_axis)
            _add_sap_cv31_var(driver, arma, 't', track_index, prop_index, trans_axis)
            driver.expression = '-s * t'
        except Exception:
            pass


def _attach_bone_uv_warp_drivers(arma, mesh_obj, side, safe_name):
    if mesh_obj.modifiers.get(UV_WARP_NAME) is None:
        return
    scale_path = f'modifiers["{UV_WARP_NAME}"].scale'
    offset_path = f'modifiers["{UV_WARP_NAME}"].offset'
    for index, which in ((0, 0), (1, 1), (0, 2), (1, 3)):
        path = scale_path if which < 2 else offset_path
        try:
            fcurve = mesh_obj.driver_add(path, index)
            driver = fcurve.driver
            driver.type = 'SCRIPTED'
            dep = _live_driver_dep_sum(driver, arma)
            driver.expression = f"{_DRIVER_WARP}('{safe_name}', {side}, {which}, {dep})"
        except Exception:
            pass


def _set_eye_cv31_drivers_for_preview(arma, live):
    """Drive eye look in the viewport without writing SAP every frame.

    Bone present: shader + Solid UV Warp follow BL_EyeLook.
    No bone: shader keeps the imported CustomVector31 drivers, and Solid UV Warp
    follows those same EyeL/EyeR keys.
    """
    if arma is None or arma.type != 'ARMATURE':
        return
    sap = getattr(arma.data, 'sub_anim_properties', None)
    if sap is None:
        return
    _register_eye_driver_namespace()
    has_bone = arma.pose.bones.get(EYE_CTRL_BONE) is not None
    if live:
        _ensure_eye_uv_warps(arma)
    from ..model.export_model import trim_name
    mats = {}
    for child in arma.children:
        if child.type != 'MESH':
            continue
        for slot in child.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            name = trim_name(mat.name)
            if name in EYE_TRACKS:
                mats[name] = mat
    safe = _arma_driver_name(arma)
    for name, mat in mats.items():
        track = sap.mat_tracks.get(name)
        if track is None:
            continue
        prop_index = track.properties.find(CV31)
        track_index = sap.mat_tracks.find(name)
        side = 0 if name == 'EyeL' else 1
        for axis_index, axis in enumerate(('X', 'Y', 'Z', 'W')):
            node = mat.node_tree.nodes.get(f'{CV31}_{axis}')
            if node is None:
                continue
            if live and has_bone:
                try:
                    node.outputs[0].driver_remove('default_value')
                except Exception:
                    pass
                try:
                    fcurve = node.outputs[0].driver_add('default_value')
                    driver = fcurve.driver
                    driver.type = 'SCRIPTED'
                    dep = _live_driver_dep_sum(driver, arma)
                    driver.expression = (
                        f"{_DRIVER_CV31}('{safe}', {side}, {axis_index}, {dep})"
                    )
                except Exception:
                    pass
            else:
                _attach_sap_cv31_node_driver(node, arma, track_index, prop_index, axis_index)

    for mesh_obj in eye_meshes(arma):
        _strip_uv_warp_drivers(mesh_obj)
        if not live:
            continue
        mat = mesh_obj.material_slots[0].material if mesh_obj.material_slots else None
        side_name = trim_name(mat.name) if mat else ''
        if side_name not in EYE_TRACKS:
            continue
        track = sap.mat_tracks.get(side_name)
        prop_index = track.properties.find(CV31) if track else -1
        track_index = sap.mat_tracks.find(side_name) if track else -1
        if has_bone:
            side = 0 if side_name == 'EyeL' else 1
            _attach_bone_uv_warp_drivers(arma, mesh_obj, side, safe)
        else:
            _attach_sap_uv_warp_drivers(arma, mesh_obj, track_index, prop_index)


def _set_all_eye_cv31_drivers_for_preview(scene, live):
    if scene is None:
        return
    for arma in _iter_solid_eye_armatures(scene):
        _set_eye_cv31_drivers_for_preview(arma, live)


def _ssp_preview_key(ssp):
    if ssp is None:
        return None
    return (
        str(getattr(ssp, 'eye_look_mode', '')),
        round(float(getattr(ssp, 'eye_look_gain', 0.0)), 4),
        round(float(getattr(ssp, 'eye_look_gain_y', 0.0)), 4),
        round(float(getattr(ssp, 'eye_look_sensitivity', 0.0)), 4),
        round(float(getattr(ssp, 'eye_look_sensitivity_y', 0.0)), 4),
        round(float(getattr(ssp, 'eye_look_clamp', 0.0)), 4),
        bool(getattr(ssp, 'eye_look_invert_x', False)),
        bool(getattr(ssp, 'eye_look_invert_y', False)),
        bool(getattr(ssp, 'eye_look_pupil_from_scale', False)),
        bool(getattr(ssp, 'eye_look_scale_about_pupil', False)),
        bool(getattr(ssp, 'eye_pupil_centre_auto', False)),
        round(float(ssp.eye_pupil_centre[0]), 4) if ssp.eye_pupil_centre else 0.0,
        round(float(ssp.eye_pupil_centre[1]), 4) if ssp.eye_pupil_centre else 0.0,
    )


def start_eye_preview(scene=None):
    global _eye_preview_running, _last_preview_values, _last_idle_preview_key
    if scene is None:
        scene = _scene_for_preview()
    if _eye_preview_running:
        _prepare_live_preview(scene)
        _ensure_preview_timer()
        return
    _eye_preview_running = True
    _last_preview_values.clear()
    _prepare_live_preview(scene)
    _ensure_preview_timer()


def stop_eye_preview(scene=None):
    global _eye_preview_running, _solid_preview_engaged, _shader_preview_engaged
    global _last_idle_preview_key
    if scene is None:
        scene = _scene_for_preview()
    if _eye_preview_running:
        _set_all_eye_cv31_drivers_for_preview(scene, False)
    _eye_preview_running = False
    _shader_preview_engaged = False
    _last_preview_values.clear()
    _last_idle_preview_key = None
    if scene is not None:
        if _solid_preview_engaged:
            _restore_solid_eye_preview(scene)
        else:
            _set_eye_uv_warps_viewport(scene, False)
    _solid_preview_engaged = False
    _ensure_preview_timer()


def ensure_eye_live_preview(scene=None):
    """Turn Live Preview on for the current model/anim if the checkbox is on.

    The scene property defaults to on, so RNA update never fires. Preview also
    starts before a model or anim exists. Call this after import or rig setup.
    """
    global _last_idle_preview_key
    if scene is None:
        scene = _scene_for_preview()
    ssp = getattr(scene, 'sub_scene_properties', None) if scene is not None else None
    if ssp is None or not bool(ssp.eye_look_live_preview):
        return False
    _last_idle_preview_key = None
    start_eye_preview(scene)
    return True


def _iter_eye_look_armatures(scene):
    for obj in scene.objects:
        if obj.type == 'ARMATURE' and EYE_CTRL_BONE in getattr(obj.pose, 'bones', {}):
            yield obj


def _armature_has_eye_cv31(arma):
    sap = getattr(arma.data, 'sub_anim_properties', None)
    if sap is None:
        return False
    return sap.mat_tracks.get('EyeL') is not None or sap.mat_tracks.get('EyeR') is not None


def _iter_solid_eye_armatures(scene):
    for obj in scene.objects:
        if obj.type != 'ARMATURE':
            continue
        pose = getattr(obj, 'pose', None)
        if pose is not None and EYE_CTRL_BONE in pose.bones:
            yield obj
            continue
        if _armature_has_eye_cv31(obj) or eye_meshes(obj):
            yield obj


def _prepare_live_preview(scene):
    if scene is None:
        return
    _register_eye_driver_namespace()
    _sync_preview_modes(scene)
    # Only keep live material drivers while Solid Texture / Material Preview
    # needs them. Leaving them on during Rendered shading restarts sampling.
    live_needed = bool(_solid_preview_engaged or _shader_preview_engaged)
    _set_all_eye_cv31_drivers_for_preview(scene, live_needed)


def _sync_preview_modes(scene):
    global _solid_preview_engaged, _shader_preview_engaged
    ssp = getattr(scene, 'sub_scene_properties', None)
    live = ssp is not None and getattr(ssp, 'eye_look_live_preview', False)
    want_solid = _eye_preview_running and _solid_texture_viewport_open()
    want_shader = _eye_preview_running and live and _shader_viewport_open()
    if want_solid:
        for obj in _iter_solid_eye_armatures(scene):
            sap = getattr(obj.data, 'sub_anim_properties', None)
            has_tracks = sap is not None and (
                sap.mat_tracks.get('EyeL') is not None
                or sap.mat_tracks.get('EyeR') is not None
            )
            if not has_tracks and not eye_meshes(obj):
                continue
            apply_solid_eye_preview(obj)
        _set_eye_uv_warps_viewport(scene, True)
        if not _solid_preview_engaged:
            _solid_preview_engaged = True
            if live:
                _set_all_eye_cv31_drivers_for_preview(scene, True)
    elif _solid_preview_engaged:
        _restore_solid_eye_preview(scene)
        _solid_preview_engaged = False
    _shader_preview_engaged = want_shader


def sync_solid_eye_preview(scene):
    _sync_preview_modes(scene)


def sync_shader_eye_preview(scene, *, transforming=False, pending=False):
    _sync_preview_modes(scene)


_solid_preview_pending = False
_live_look_pending = False
_applying_eye_look = False
_last_preview_frame = None
_was_transforming_eye = False
_last_preview_values = {}
_last_idle_preview_key = None


_uv_warps_cleaned = False


def _cleanup_scene_uv_warps(scene):
    global _uv_warps_cleaned
    if _uv_warps_cleaned or scene is None:
        return
    _uv_warps_cleaned = True
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.modifiers.get(UV_WARP_NAME) is None:
            continue
        marker = str(obj.get('sub_eye_warp', ''))
        if marker.startswith('cv31:') or marker.startswith('bone:'):
            continue
        _strip_uv_warp_drivers(obj)
        if 'sub_eye_warp' in obj:
            del obj['sub_eye_warp']


def _eye_content_key(scene):
    if scene is None:
        return None
    parts = [len(scene.objects)]
    for obj in scene.objects:
        if obj.type != 'ARMATURE':
            continue
        sap = getattr(obj.data, 'sub_anim_properties', None)
        sap_action = None
        try:
            sap_action = obj.data.animation_data.action
        except Exception:
            pass
        has_bone = False
        try:
            has_bone = EYE_CTRL_BONE in obj.pose.bones
        except Exception:
            pass
        has_eye_track = False
        if sap is not None:
            has_eye_track = (
                sap.mat_tracks.get('EyeL') is not None
                or sap.mat_tracks.get('EyeR') is not None
            )
        parts.append((
            obj.name,
            has_bone,
            has_eye_track,
            getattr(sap_action, 'name', ''),
            len(eye_meshes(obj)),
        ))
    return tuple(parts)


def _solid_preview_timer():
    global _last_idle_preview_key
    scene = _scene_for_preview()
    if scene is not None and not _uv_warps_cleaned:
        _cleanup_scene_uv_warps(scene)
    ssp = getattr(scene, 'sub_scene_properties', None) if scene is not None else None
    want = bool(ssp is not None and ssp.eye_look_live_preview)
    if not want:
        if _eye_preview_running:
            stop_eye_preview(scene)
        return 1.0
    if scene is None:
        return 1.0
    solid_open = _solid_texture_viewport_open()
    material_open = _shader_viewport_open()
    rendered_open = _rendered_viewport_open()
    # Rendered-only: tear down live drivers/UV warps so EEVEE/Cycles can finish.
    if rendered_open and not solid_open and not material_open:
        if _eye_preview_running or _solid_preview_engaged or _shader_preview_engaged:
            stop_eye_preview(scene)
        return 1.0
    idle_key = (
        _ssp_preview_key(ssp),
        _eye_content_key(scene),
        solid_open,
        material_open,
    )
    if not _eye_preview_running or idle_key != _last_idle_preview_key:
        _last_idle_preview_key = idle_key
        _driver_pose_cache.clear()
        start_eye_preview(scene)
        return 0.5
    # Modes already match — do not re-touch materials/modifiers every tick.
    return 0.5


def _handle_eye_measure_toggle(arma, ssp):
    if not _eye_toggle_on(arma, EYE_OPT_MEASURE):
        return
    centre = eye_uv_centre(arma)
    _pupil_centre_cache[arma.as_pointer()] = centre.copy()
    ssp.eye_pupil_centre = (centre.x, centre.y)
    ssp.eye_pupil_centre_auto = False
    pbone = arma.pose.bones.get(EYE_OPT_MEASURE)
    if pbone is not None:
        pbone.location.y = 0.0


@bpy.app.handlers.persistent
def _eye_look_load_post(_dummy):
    global _uv_warps_cleaned, _solid_preview_engaged, _shader_preview_engaged
    global _last_preview_frame, _was_transforming_eye, _live_look_pending
    global _eye_preview_running, _last_idle_preview_key
    _uv_warps_cleaned = False
    _solid_preview_engaged = False
    _shader_preview_engaged = False
    _eye_preview_running = False
    _last_preview_frame = None
    _was_transforming_eye = False
    _live_look_pending = False
    _last_idle_preview_key = None
    _last_solid_uv.clear()
    _rest_uv_cache.clear()
    _pupil_centre_cache.clear()
    _last_preview_values.clear()
    _driver_pose_cache.clear()
    _ensure_preview_timer()
    _schedule_eye_preview_sync()


def _scene_for_preview():
    scene = getattr(bpy.context, 'scene', None)
    if scene is not None:
        return scene
    wm = getattr(bpy.context, 'window_manager', None)
    if wm is not None:
        for window in wm.windows:
            if getattr(window, 'scene', None) is not None:
                return window.scene
    if bpy.data.scenes:
        return bpy.data.scenes[0]
    return None


def _schedule_eye_preview_sync():
    global _sync_preview_attempts
    _sync_preview_attempts = 0
    if bpy.app.timers.is_registered(_sync_eye_preview_from_scene):
        bpy.app.timers.unregister(_sync_eye_preview_from_scene)
    bpy.app.timers.register(_sync_eye_preview_from_scene, first_interval=0.15)


_sync_preview_attempts = 0


def _sync_eye_preview_from_scene():
    global _sync_preview_attempts
    scene = _scene_for_preview()
    if scene is None:
        _sync_preview_attempts += 1
        return 0.2 if _sync_preview_attempts < 20 else None
    ssp = getattr(scene, 'sub_scene_properties', None)
    if ssp is None:
        _sync_preview_attempts += 1
        return 0.2 if _sync_preview_attempts < 20 else None
    _sync_preview_attempts = 0
    _ensure_preview_timer()
    if ssp.eye_look_live_preview:
        start_eye_preview(scene)
    else:
        stop_eye_preview(scene)
    return None


def _ensure_preview_timer():
    if not bpy.app.timers.is_registered(_solid_preview_timer):
        bpy.app.timers.register(_solid_preview_timer, persistent=True, first_interval=0.05)


def _register_live_handler():
    _register_eye_driver_namespace()
    if _eye_look_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_eye_look_load_post)
    _ensure_preview_timer()
    _schedule_eye_preview_sync()


def _unregister_live_handler():
    global _uv_warps_cleaned, _solid_preview_engaged, _shader_preview_engaged
    stop_eye_preview()
    _unregister_eye_driver_namespace()
    _uv_warps_cleaned = False
    _solid_preview_engaged = False
    _shader_preview_engaged = False
    if _eye_look_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_eye_look_load_post)
    if bpy.app.timers.is_registered(_solid_preview_timer):
        bpy.app.timers.unregister(_solid_preview_timer)
    if bpy.app.timers.is_registered(_sync_eye_preview_from_scene):
        bpy.app.timers.unregister(_sync_eye_preview_from_scene)


def get_or_create_track(sap, name):
    track = sap.mat_tracks.get(name)
    if track is None:
        track = sap.mat_tracks.add()
        track.name = name
        return track, True
    return track, False


def get_or_create_cv31(track):
    prop = track.properties.get(CV31)
    if prop is not None:
        return prop, False
    prop = track.properties.add()
    prop.sub_type = 'VECTOR'
    prop.name = CV31
    prop.custom_vector = CV31_DEFAULT
    return prop, True


def setup_eye_cv31_tracks(arma, context):
    sap = arma.data.sub_anim_properties
    made_tracks, made_props, already = [], [], []
    for name in EYE_TRACKS:
        track, new_track = get_or_create_track(sap, name)
        if new_track:
            made_tracks.append(name)
        _prop, new_prop = get_or_create_cv31(track)
        if new_prop:
            made_props.append(name)
        elif not new_track:
            already.append(name)

    from ..anim.anim_data import refresh_material_drivers
    refresh_material_drivers(context)

    missing_nodes = []
    from ..model.export_model import trim_name
    mats = {
        slot.material for child in arma.children if child.type == 'MESH'
        for slot in child.material_slots if slot.material
    }
    by_trimmed = {trim_name(m.name): m for m in mats}
    for name in EYE_TRACKS:
        mat = by_trimmed.get(name)
        if mat is None:
            missing_nodes.append(f"{name} (no material assigned to a mesh)")
        elif not mat.use_nodes or mat.node_tree.nodes.get(f'{CV31}_Z') is None:
            missing_nodes.append(f"{name} (material has no {CV31}_Z value node)")
    return made_tracks, made_props, already, missing_nodes


def keyframe_eye_look_controls(arma, frame):
    """Insert loc/rot/scale keys on BL_EyeLook so the channel exists without hitting I."""
    if arma is None or arma.type != 'ARMATURE':
        return
    pbone = arma.pose.bones.get(EYE_CTRL_BONE)
    if pbone is None:
        return
    if arma.animation_data is None:
        arma.animation_data_create()
    action = arma.animation_data.action
    if action is not None:
        try:
            from ..blender_compat import ensure_action_slot
            ensure_action_slot(action, arma)
        except Exception:
            pass
    group = pbone.name
    try:
        pbone.keyframe_insert('location', frame=frame, group=group)
        pbone.keyframe_insert('scale', frame=frame, group=group)
        if pbone.rotation_mode == 'QUATERNION':
            pbone.keyframe_insert('rotation_quaternion', frame=frame, group=group)
        elif pbone.rotation_mode == 'AXIS_ANGLE':
            pbone.keyframe_insert('rotation_axis_angle', frame=frame, group=group)
        else:
            pbone.keyframe_insert('rotation_euler', frame=frame, group=group)
    except RuntimeError:
        return
    try:
        from ..anim.fcurve_compat import style_eye_control_action
        animation_data = arma.animation_data
        if animation_data is not None:
            style_eye_control_action(animation_data.action)
    except Exception:
        pass


def add_eye_look_control_bone(
    arma, distance=3.0, *, include_invert_sliders=False, material_anim='MATCH',
):
    prev_mode = arma.mode
    parent_name = None
    for candidate in ('Head', 'Face', 'Neck'):
        if candidate in arma.data.bones:
            parent_name = candidate
            break

    bpy.ops.object.mode_set(mode='EDIT')
    try:
        ebs = arma.data.edit_bones
        stale = [b for b in ebs if is_eye_control_bone(b.name)]
        for bone in reversed(stale):
            ebs.remove(bone)
        anchor = ebs.get(parent_name) if parent_name else None
        if anchor is not None:
            base = anchor.head.copy()
            size = max((anchor.head - anchor.tail).length, 0.1)
        else:
            base = ebs[0].head.copy() if len(ebs) else None
            size = 1.0
        if base is None:
            return False, "Armature has no bones to anchor the control to"
        forward = face_forward(arma, anchor)
        eyes = eye_meshes(arma)
        if eyes:
            zs = []
            for mesh in eyes:
                local = arma.matrix_world.inverted() @ mesh.matrix_world
                zs += [(local @ Vector(c)).z for c in mesh.bound_box]
            base.z = sum(zs) / len(zs)
        offset = forward * (distance * size)
        ctrl = ebs.new(EYE_CTRL_BONE)
        ctrl.head = base + offset
        ctrl.tail = base + offset + forward * size
        ctrl.roll = 0.0
        ctrl.use_deform = False
        if anchor is not None:
            ctrl.parent = anchor
            ctrl.use_connect = False

        if include_invert_sliders:
            side = Vector((0.0, 0.0, 1.0)).cross(forward)
            if side.length < 1e-6:
                side = Vector((1.0, 0.0, 0.0))
            else:
                side.normalize()
            up = forward.cross(side)
            if up.length < 1e-6:
                up = Vector((0.0, 0.0, 1.0))
            else:
                up.normalize()
            hip = ebs.get('Hip')
            char_scale = (
                (anchor.head - hip.head).length
                if (anchor is not None and hip is not None)
                else size * 6.0
            )
            box = max(char_scale * 0.18, 0.06)
            mid = ctrl.head + forward * (max(ctrl.length, 1e-6) * 0.5)
            top = mid + up * (box * 0.52)
            half_w = box * 0.28
            opt_len = max(box * 0.08, 0.012)
            for index, (name, _color, _label) in enumerate(EYE_OPTION_BONES):
                bone = ebs.new(name)
                pos = top + side * ((index * 2 - 1) * half_w)
                bone.head = pos
                bone.tail = pos + forward * opt_len
                bone.use_deform = False
                bone.use_connect = False
                bone.parent = ctrl
                try:
                    bone.align_roll(up)
                except (TypeError, ValueError, RuntimeError):
                    bone.roll = ctrl.roll
        for name in EYE_UV_HELPERS:
            bone = ebs.new(name)
            bone.head = ctrl.head.copy()
            bone.tail = ctrl.tail.copy()
            bone.roll = ctrl.roll
            bone.use_deform = False
            bone.use_connect = False
            bone.parent = ctrl.parent
    finally:
        target = prev_mode if prev_mode != 'EDIT' else 'OBJECT'
        if target not in {'OBJECT', 'POSE', 'EDIT'}:
            target = 'POSE'
        bpy.ops.object.mode_set(mode=target)

    parent_note = f" parented to {parent_name}" if parent_name else " (no Head bone found to parent to)"
    matched = 0
    try:
        ssp = getattr(bpy.context.scene, 'sub_scene_properties', None)
        scene = getattr(bpy.context, 'scene', None)
        frame = scene.frame_current if scene is not None else 1
        if material_anim == 'REPLACE':
            _delete_eye_cv31_keys(arma)
        else:
            matched = match_eye_look_from_material(
                arma,
                overwrite=True,
                delete_material=True,
            )
            if not matched:
                keyframe_eye_look_controls(arma, frame)
        _configure_eye_uv_helpers(arma, ssp, force=True)
        _ensure_eye_uv_warps(arma)
        ensure_eye_live_preview(scene)
        _last_preview_values.pop(arma.as_pointer(), None)
    except Exception as ex:
        print(f"[eye look] {ex}")
    extra = ""
    if material_anim == 'REPLACE':
        extra = ", replaced EyeL/EyeR CustomVector31 with an empty eye bone"
    elif matched:
        extra = (
            f", matched {matched} cleaned eye keys onto {EYE_CTRL_BONE} "
            "and deleted EyeL/EyeR CustomVector31 keys"
        )
    return True, f"Added {EYE_CTRL_BONE}{parent_note}{extra}"


def remove_eye_look_control_bone(arma):
    if arma is None or arma.type != 'ARMATURE':
        return False
    if not any(is_eye_control_bone(b.name) for b in arma.data.bones):
        return False
    try:
        _set_eye_cv31_fcurves_retired(arma, False)
    except Exception:
        pass
    prev_mode = arma.mode
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        ebs = arma.data.edit_bones
        stale = [b for b in ebs if is_eye_control_bone(b.name)]
        for bone in reversed(stale):
            ebs.remove(bone)
    finally:
        target = prev_mode if prev_mode != 'EDIT' else 'OBJECT'
        if target not in {'OBJECT', 'POSE', 'EDIT'}:
            target = 'POSE'
        bpy.ops.object.mode_set(mode=target)
    if EYE_TOGGLE_TRAVEL_KEY in arma.data:
        del arma.data[EYE_TOGGLE_TRAVEL_KEY]
    if 'sub_eye_uv_warp' in arma.data:
        del arma.data['sub_eye_uv_warp']
    try:
        _remove_stale_uv_warps(arma)
    except Exception:
        pass
    return True


class SUB_OT_setup_eye_cv31(Operator):
    bl_idname = "sub.setup_eye_cv31"
    bl_label = "Set Up Eye Look (CustomVector31)"
    bl_description = (
        "Create the EyeL/EyeR material tracks and CustomVector31 so aiming the eyes works"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        arma = context.object
        try:
            made_tracks, made_props, already, missing_nodes = setup_eye_cv31_tracks(arma, context)
        except Exception as ex:
            self.report({'ERROR'}, f"Tracks created but driver setup failed: {ex}")
            return {'CANCELLED'}

        msg = (
            f"Eye look ready. Tracks created: {made_tracks or 'none'}; "
            f"{CV31} added to: {made_props or 'none'}; already set up: {already or 'none'}"
        )
        if missing_nodes:
            msg += (
                f". NOTE - these can't be driven yet: {', '.join(missing_nodes)}. "
                "Re-import the material for that eye so its value nodes exist"
            )
        self.report({'WARNING'} if missing_nodes else {'INFO'}, msg)
        return {'FINISHED'}


class SUB_OT_add_eye_look_control(Operator):
    bl_idname = "sub.add_eye_look_control"
    bl_label = "Add Eye Look Control Bone"
    bl_description = (
        "Add a regular control bone in front of the head. Move it in Pose Mode to aim the eyes, "
        "then Bake to write CustomVector31 keyframes. The animation rig uses a box widget instead"
    )
    bl_options = {"REGISTER", "UNDO"}

    distance: FloatProperty(
        name="Distance In Front",
        description="How far in front of the head the control sits, in Blender units",
        default=3.0, min=0.01,
    )
    material_anim: EnumProperty(
        name="Existing Eye Material Anim",
        description="What to do with EyeL/EyeR CustomVector31 keys already on this animation",
        items=EYE_MATERIAL_ANIM_ITEMS,
        default='MATCH',
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE'

    def invoke(self, context, event):
        if armature_has_eye_material_keys(context.object):
            return context.window_manager.invoke_props_dialog(self, width=380)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="This animation has EyeL/EyeR CustomVector31 keys.")
        layout.prop(self, "material_anim", expand=True)

    def execute(self, context):
        arma = context.object
        ok, message = add_eye_look_control_bone(
            arma, self.distance, material_anim=self.material_anim,
        )
        if not ok:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        self.report({'INFO'}, message + ". Move it in Pose Mode, then Bake Eye Look.")
        return {'FINISHED'}


class SUB_OT_match_eye_look_from_material(Operator):
    bl_idname = "sub.match_eye_look_from_material"
    bl_label = "Match Eye Bone from Material"
    bl_description = (
        "Copy existing EyeL/EyeR CustomVector31 look keys onto BL_EyeLook, "
        "clean baked interpolation, and delete those material keyframes"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (
            obj is not None
            and obj.type == 'ARMATURE'
            and obj.pose.bones.get(EYE_CTRL_BONE) is not None
        )

    def execute(self, context):
        keyed = match_eye_look_from_material(
            context.object, overwrite=True, delete_material=True,
        )
        if not keyed:
            self.report(
                {'WARNING'},
                "No EyeL/EyeR CustomVector31 look keys found on this animation's SAP data",
            )
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            f"Matched {keyed} cleaned keys onto {EYE_CTRL_BONE} and deleted EyeL/EyeR CustomVector31",
        )
        return {'FINISHED'}


def bake_eye_look_keys(context, arma, progress=None, progress_start=0.0, progress_end=1.0):
    sap = arma.data.sub_anim_properties
    tracks = {}
    for name in EYE_TRACKS:
        track = sap.mat_tracks.get(name)
        prop = track.properties.get(CV31) if track else None
        if prop is not None:
            tracks[name] = (sap.mat_tracks.find(name), track, track.properties.find(CV31))
    if not tracks:
        return 0
    if arma.data.animation_data is None:
        arma.data.animation_data_create()
    pbone = arma.pose.bones.get(EYE_CTRL_BONE)
    if pbone is None:
        return 0
    try:
        _set_eye_cv31_fcurves_retired(arma, False)
    except Exception:
        pass
    scene = context.scene
    start, end = scene.frame_start, scene.frame_end
    original_frame = scene.frame_current
    ssp = scene.sub_scene_properties
    prev_live = ssp.eye_look_live_preview
    ssp.eye_look_live_preview = False
    baked = 0
    total = max(1, int(end) - int(start) + 1)
    try:
        for index, frame in enumerate(range(start, end + 1)):
            scene.frame_set(frame)
            left_u, right_u, v, pupil = compute_cv31(arma, pbone, ssp)
            for name, (track_index, track, prop_index) in tracks.items():
                prop = track.properties[prop_index]
                prop.custom_vector[2] = left_u if name == 'EyeL' else right_u
                prop.custom_vector[3] = v
                if pupil is not None:
                    prop.custom_vector[0] = pupil
                    prop.custom_vector[1] = pupil
                arma.data.keyframe_insert(
                    data_path=(
                        f'sub_anim_properties.mat_tracks[{track_index}]'
                        f'.properties[{prop_index}].custom_vector'
                    ),
                    frame=frame,
                    group=f'Material ({name})',
                )
                baked += 1
            if progress is not None:
                factor = progress_start + ((index + 1) / total) * (progress_end - progress_start)
                progress.update(factor)
    finally:
        scene.frame_set(original_frame)
        ssp.eye_look_live_preview = prev_live
        from ..anim.fcurve_compat import style_visibility_action
        animation_data = arma.data.animation_data
        if animation_data is not None:
            style_visibility_action(animation_data.action)
        _clear_eye_look_keys(arma)
    return baked


class SUB_OT_bake_eye_look(Operator):
    bl_idname = "sub.bake_eye_look"
    bl_label = "Bake Eye Look to CustomVector31"
    bl_description = (
        "Sample the control bone across the scene frame range, write CustomVector31 "
        "keyframes for both eyes so they export, then remove the eye look bone"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or obj.type != 'ARMATURE':
            return False
        return EYE_CTRL_BONE in obj.pose.bones

    def execute(self, context):
        arma = context.object
        sap = arma.data.sub_anim_properties
        ready = any(
            (t := sap.mat_tracks.get(n)) is not None and t.properties.get(CV31) is not None
            for n in EYE_TRACKS
        )
        if not ready:
            self.report(
                {'ERROR'},
                "No EyeL/EyeR CustomVector31 to bake into - run Set Up Eye Look first",
            )
            return {'CANCELLED'}
        if arma.data.animation_data is None or arma.data.animation_data.action is None:
            self.report(
                {'ERROR'},
                "The armature DATA has no action to hold the keyframes - import or create an animation first",
            )
            return {'CANCELLED'}
        baked = bake_eye_look_keys(context, arma)
        if baked <= 0:
            self.report({'ERROR'}, f"Nothing baked into {CV31}")
            return {'CANCELLED'}
        removed = remove_eye_look_control_bone(arma)
        extra = f" and removed {EYE_CTRL_BONE}" if removed else ""
        self.report(
            {'INFO'},
            f"Baked {baked} frame(s) of eye look into {CV31}{extra}. These are real keyframes, so they will export",
        )
        return {'FINISHED'}


class SUB_OT_measure_pupil_centre(Operator):
    bl_idname = "sub.measure_pupil_centre"
    bl_label = "Measure From Mesh"
    bl_description = "Set the pupil centre to the eye meshes' average UV"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        centre = eye_uv_centre(context.object)
        ssp.eye_pupil_centre = (centre.x, centre.y)
        ssp.eye_pupil_centre_auto = False
        self.report(
            {'INFO'},
            f"Pupil centre set to ({centre.x:.4f}, {centre.y:.4f})",
        )
        return {'FINISHED'}


classes = (
    SUB_OT_setup_eye_cv31,
    SUB_OT_measure_pupil_centre,
    SUB_OT_add_eye_look_control,
    SUB_OT_match_eye_look_from_material,
    SUB_OT_bake_eye_look,
)


def register():
    _register_live_handler()


def unregister():
    _unregister_live_handler()
