import ctypes
import ctypes.wintypes
import math
import os
import re
import sys
import traceback
from pathlib import Path

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from mathutils import Matrix, Vector


TOML_NAME = "face_picker.toml"
IMAGE_DIR_NAME = "face_picker"
DEFAULT_CAMERA_SIZE = 500
HEAD_BONE_NAMES = ("HeadN", "Head", "FaceN", "NeckN")
FACE_MESH_HINTS = (
    "eye", "mouth", "face", "blink", "head", "brow",
    "tooth", "tongue", "jaw", "lip", "openblink",
)
FACIAL_TRACK_HINTS = (
    "mouth", "blink", "eye", "face", "brow", "jaw",
    "lip", "tooth", "tongue", "openblink",
)


def get_armature(context):
    obj = getattr(context, "object", None)
    if obj is not None and obj.type == "ARMATURE":
        return obj
    for selected in getattr(context, "selected_objects", []) or []:
        if selected.type == "ARMATURE":
            return selected
    if obj is not None and obj.type == "MESH":
        armature = obj.find_armature()
        if armature is not None:
            return armature
    return None


_suppress_apply = False
FACE_PICKER_SCREEN_KEY = "sub_face_picker_window"
FACE_PICKER_HWND_KEY = "sub_face_picker_hwnd"
_topmost_timer_running = False
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def get_picker(arma):
    if arma is None or arma.type != "ARMATURE":
        return None
    return getattr(arma.data, "sub_face_picker", None)


def get_sap(arma):
    if arma is None:
        return None
    return getattr(arma.data, "sub_anim_properties", None)


def armature_object_from_data(arma_data):
    if arma_data is None:
        return None
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE" and obj.data == arma_data:
            return obj
    return None


def peek_model_folder(context, arma):
    picker = get_picker(arma)
    if picker and picker.source_folder and os.path.isdir(picker.source_folder):
        return picker.source_folder
    ssp = getattr(context.scene, "sub_scene_properties", None)
    if ssp is not None:
        for attr in ("last_imported_model_path", "model_import_folder_path"):
            folder = getattr(ssp, attr, "")
            if folder and os.path.isdir(folder):
                return folder
    return ""


def armature_has_face_picker_menu(context):
    arma = get_armature(context)
    picker = get_picker(arma)
    if picker is None:
        return False
    if len(picker.expressions) > 0:
        return True
    folder = peek_model_folder(context, arma)
    return bool(folder) and toml_path_for(folder).exists()


def sanitize_id(name):
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", (name or "").strip())
    cleaned = cleaned.strip("_")
    return cleaned or "expression"


def unique_expression_id(picker, base_id):
    candidate = base_id
    existing = {expr.expression_id for expr in picker.expressions}
    index = 1
    while candidate in existing:
        index += 1
        candidate = f"{base_id}_{index:03d}"
    return candidate


def toml_string(value):
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )
    return f'"{escaped}"'


def parse_toml_value(raw):
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        values = []
        current = []
        in_string = False
        escape = False
        for char in inner:
            if escape:
                current.append(char)
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if char == "," and not in_string:
                values.append("".join(current).strip())
                current = []
                continue
            current.append(char)
        if current:
            values.append("".join(current).strip())
        return values
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return bytes(text[1:-1], "utf-8").decode("unicode_escape")
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text.strip('"')


def parse_simple_toml(text):
    result = {"expressions": []}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[expressions]]":
            current = {}
            result["expressions"].append(current)
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed = parse_toml_value(value)
        if current is not None:
            current[key.strip()] = parsed
        else:
            result[key.strip()] = parsed
    return result


def read_toml_file(path):
    raw = Path(path).read_bytes()
    try:
        import tomllib
        return tomllib.loads(raw.decode("utf-8"))
    except ImportError:
        return parse_simple_toml(raw.decode("utf-8"))
    except Exception:
        return parse_simple_toml(raw.decode("utf-8"))


def write_toml_file(path, data):
    lines = [
        f"version = {int(data.get('version', 1))}",
        f"camera_size = {int(data.get('camera_size', DEFAULT_CAMERA_SIZE))}",
        "",
    ]
    for expr in data.get("expressions", []):
        lines.append("[[expressions]]")
        lines.append(f"id = {toml_string(expr.get('id', ''))}")
        lines.append(f"name = {toml_string(expr.get('name', ''))}")
        lines.append(f"image = {toml_string(expr.get('image', ''))}")
        tracks = ", ".join(toml_string(track) for track in expr.get("tracks", []))
        lines.append(f"tracks = [{tracks}]")
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def preview_icon(image):
    if image is None:
        return 0
    try:
        image.preview_ensure()
    except Exception:
        pass
    preview = getattr(image, "preview", None)
    if preview is None:
        return 0
    return preview.icon_id


def expression_image_name(arma, expression_id):
    return f"FacePicker_{arma.name}_{expression_id}"


def load_preview_image(filepath, image_name):
    path = Path(filepath)
    if not path.exists():
        return None
    existing = bpy.data.images.get(image_name)
    if existing is not None:
        bpy.data.images.remove(existing)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = image_name
    try:
        image.pack()
    except Exception:
        pass
    try:
        image.preview_ensure()
    except Exception:
        pass
    return image


def resolve_source_folder(context, arma):
    picker = get_picker(arma)
    if picker and picker.source_folder and os.path.isdir(picker.source_folder):
        return picker.source_folder
    ssp = getattr(context.scene, "sub_scene_properties", None)
    if ssp is not None:
        for attr in ("last_imported_model_path", "model_import_folder_path"):
            folder = getattr(ssp, attr, "")
            if folder and os.path.isdir(folder):
                if picker is not None:
                    picker.source_folder = folder
                return folder
    return ""


def toml_path_for(folder):
    return Path(folder) / TOML_NAME


def image_dir_for(folder):
    return Path(folder) / IMAGE_DIR_NAME


def iter_armature_meshes(arma):
    for child in arma.children:
        if child.type == "MESH":
            yield child
        for grandchild in child.children:
            if grandchild.type == "MESH":
                yield grandchild


def estimate_face_target(arma):
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    found_mesh = False
    for mesh in iter_armature_meshes(arma):
        name = mesh.name.lower()
        if not any(hint in name for hint in FACE_MESH_HINTS):
            continue
        for corner in mesh.bound_box:
            world = mesh.matrix_world @ Vector(corner)
            mins.x = min(mins.x, world.x)
            mins.y = min(mins.y, world.y)
            mins.z = min(mins.z, world.z)
            maxs.x = max(maxs.x, world.x)
            maxs.y = max(maxs.y, world.y)
            maxs.z = max(maxs.z, world.z)
            found_mesh = True
    if found_mesh:
        center = (mins + maxs) * 0.5
        size = max((maxs - mins).length, 0.2)
        return center, size * 0.9

    pose_bones = arma.pose.bones
    for bone_name in HEAD_BONE_NAMES:
        bone = pose_bones.get(bone_name)
        if bone is None:
            continue
        return arma.matrix_world @ bone.head, 0.35
    return arma.matrix_world.translation + Vector((0.0, 0.0, 1.6)), 0.4


def find_view3d(context):
    screen = getattr(context, "screen", None)
    if screen is not None:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    return area, region
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    return area, region
    return None, None


def managed_track_names(picker, extra_names=None):
    names = set(extra_names or [])
    for expr in picker.expressions:
        for track in expr.tracks:
            if track.name:
                names.add(track.name)
    return names


def vis_entry_by_name(sap, name):
    for entry in sap.vis_track_entries:
        if entry.name == name:
            return entry
    return None


def vis_entry_index(sap, name):
    for index, entry in enumerate(sap.vis_track_entries):
        if entry.name == name:
            return index
    return -1


def set_visibility_tracks(arma, enable_names, managed_names, insert_keyframes=False):
    sap = get_sap(arma)
    if sap is None:
        return 0
    changed = 0
    for index, entry in enumerate(sap.vis_track_entries):
        if entry.name not in managed_names:
            continue
        new_value = entry.name in enable_names
        if entry.value != new_value:
            entry.value = new_value
            changed += 1
        if insert_keyframes:
            arma.data.keyframe_insert(
                data_path=f"sub_anim_properties.vis_track_entries[{index}].value",
                group="Visibility",
            )
    return changed


def apply_expression(context, arma, expression, insert_keyframes=False):
    global _suppress_apply
    picker = get_picker(arma)
    sap = get_sap(arma)
    if picker is None or sap is None:
        return 0, []
    enable = [track.name for track in expression.tracks if track.name]
    missing = [name for name in enable if vis_entry_by_name(sap, name) is None]
    managed = managed_track_names(picker, enable)
    should_key = insert_keyframes or context.scene.tool_settings.use_keyframe_insert_auto
    changed = set_visibility_tracks(arma, set(enable), managed, insert_keyframes=should_key)
    picker.active_expression_id = expression.expression_id
    new_index = next(
        (i for i, expr in enumerate(picker.expressions) if expr.expression_id == expression.expression_id),
        picker.active_expression_index,
    )
    if picker.active_expression_index != new_index:
        _suppress_apply = True
        try:
            picker.active_expression_index = new_index
        finally:
            _suppress_apply = False
    return changed, missing


def _on_active_expression_index_update(self, context):
    if _suppress_apply:
        return
    if not self.expressions:
        return
    index = self.active_expression_index
    if index < 0 or index >= len(self.expressions):
        return
    expression = self.expressions[index]
    if expression.expression_id == self.active_expression_id:
        return
    arma = armature_object_from_data(self.id_data) or get_armature(context)
    if arma is None:
        return
    apply_expression(context, arma, expression, self.insert_keyframes)


def refresh_track_choices(arma):
    picker = get_picker(arma)
    sap = get_sap(arma)
    if picker is None or sap is None:
        return 0
    selected = {item.name: item.selected for item in picker.track_choices}
    picker.track_choices.clear()
    for entry in sap.vis_track_entries:
        item = picker.track_choices.add()
        item.name = entry.name
        item.selected = selected.get(entry.name, False)
    picker.track_choices_index = min(picker.track_choices_index, max(len(picker.track_choices) - 1, 0))
    return len(picker.track_choices)


def ensure_track_choices(arma):
    picker = get_picker(arma)
    sap = get_sap(arma)
    if picker is None or sap is None:
        return
    current = [item.name for item in picker.track_choices]
    sap_names = [entry.name for entry in sap.vis_track_entries]
    if current != sap_names:
        refresh_track_choices(arma)


def selected_track_names(picker):
    return [item.name for item in picker.track_choices if item.selected]


def find_expression(picker, expression_id):
    for expr in picker.expressions:
        if expr.expression_id == expression_id:
            return expr
    return None


def capture_thumbnail(context, arma, filepath, size):
    picker = get_picker(arma)
    camera = picker.camera if picker is not None else None
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    scene = context.scene
    render = scene.render
    old = {
        "filepath": render.filepath,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "file_format": render.image_settings.file_format,
        "camera": scene.camera,
    }
    area, region = find_view3d(context)
    space = area.spaces.active if area is not None else None
    old_view = None
    old_overlays = None
    if space is not None:
        old_view = space.region_3d.view_perspective
        old_overlays = space.overlay.show_overlays

    try:
        render.filepath = str(path)
        render.resolution_x = size
        render.resolution_y = size
        render.resolution_percentage = 100
        render.image_settings.file_format = "PNG"
        if camera is not None:
            scene.camera = camera
        if space is not None and camera is not None:
            space.region_3d.view_perspective = "CAMERA"
            space.overlay.show_overlays = False
        if area is not None and region is not None:
            with context.temp_override(window=context.window, area=area, region=region):
                bpy.ops.render.opengl(write_still=True)
        else:
            bpy.ops.render.render(write_still=True)
    finally:
        render.filepath = old["filepath"]
        render.resolution_x = old["resolution_x"]
        render.resolution_y = old["resolution_y"]
        render.resolution_percentage = old["resolution_percentage"]
        render.image_settings.file_format = old["file_format"]
        scene.camera = old["camera"]
        if space is not None:
            if old_view is not None:
                space.region_3d.view_perspective = old_view
            if old_overlays is not None:
                space.overlay.show_overlays = old_overlays

    if not path.exists():
        raise RuntimeError("Thumbnail was not written")
    return path


def clear_expressions(picker):
    for expr in list(picker.expressions):
        if expr.image is not None:
            image = expr.image
            expr.image = None
            if image.users <= 1:
                bpy.data.images.remove(image)
    picker.expressions.clear()
    picker.active_expression_index = 0
    picker.active_expression_id = ""


def add_tracks_to_expression(expr, track_names):
    expr.tracks.clear()
    for name in track_names:
        if not name:
            continue
        track = expr.tracks.add()
        track.name = name


def serialize_picker(picker):
    expressions = []
    for expr in picker.expressions:
        image_rel = expr.image_path or f"{IMAGE_DIR_NAME}/{expr.expression_id}.png"
        expressions.append({
            "id": expr.expression_id,
            "name": expr.name,
            "image": image_rel.replace("\\", "/"),
            "tracks": [track.name for track in expr.tracks],
        })
    return {
        "version": 1,
        "camera_size": picker.camera_size or DEFAULT_CAMERA_SIZE,
        "expressions": expressions,
    }


def load_picker_from_folder(arma, folder):
    global _suppress_apply
    picker = get_picker(arma)
    if picker is None:
        return 0
    path = toml_path_for(folder)
    if not path.exists():
        return 0
    data = read_toml_file(path)
    _suppress_apply = True
    try:
        clear_expressions(picker)
    finally:
        _suppress_apply = False
    # clear_expressions already ran; avoid a second clear
    picker.source_folder = folder
    picker.camera_size = int(data.get("camera_size", DEFAULT_CAMERA_SIZE) or DEFAULT_CAMERA_SIZE)
    loaded = 0
    _suppress_apply = True
    try:
        for item in data.get("expressions", []):
            expr = picker.expressions.add()
            expr.expression_id = item.get("id") or unique_expression_id(picker, sanitize_id(item.get("name", "expression")))
            expr.name = item.get("name") or expr.expression_id
            rel_image = item.get("image") or f"{IMAGE_DIR_NAME}/{expr.expression_id}.png"
            expr.image_path = rel_image.replace("\\", "/")
            add_tracks_to_expression(expr, item.get("tracks") or [])
            abs_image = Path(folder) / expr.image_path
            image = load_preview_image(abs_image, expression_image_name(arma, expr.expression_id))
            if image is not None:
                expr.image = image
            loaded += 1
        picker.active_expression_index = 0
        picker.active_expression_id = ""
    finally:
        _suppress_apply = False
    refresh_track_choices(arma)
    return loaded


def save_picker_to_folder(arma, folder):
    picker = get_picker(arma)
    if picker is None:
        raise RuntimeError("No face picker data on armature")
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)
    images_dir = image_dir_for(folder)
    images_dir.mkdir(parents=True, exist_ok=True)

    for expr in picker.expressions:
        dest = images_dir / f"{expr.expression_id}.png"
        if expr.image is not None:
            try:
                expr.image.filepath_raw = str(dest)
                expr.image.file_format = "PNG"
                expr.image.save()
            except Exception:
                if expr.image.packed_file is not None:
                    expr.image.save_render(str(dest))
        elif expr.image_path:
            src = Path(folder) / expr.image_path
            if src.exists() and src.resolve() != dest.resolve():
                dest.write_bytes(src.read_bytes())
        expr.image_path = f"{IMAGE_DIR_NAME}/{expr.expression_id}.png".replace("\\", "/")
        if dest.exists():
            image = load_preview_image(dest, expression_image_name(arma, expr.expression_id))
            if image is not None:
                expr.image = image

    picker.source_folder = str(folder_path)
    write_toml_file(toml_path_for(folder), serialize_picker(picker))
    return toml_path_for(folder)


def on_model_imported(arma, folder):
    picker = get_picker(arma)
    if picker is None:
        return
    picker.source_folder = folder
    refresh_track_choices(arma)
    toml_file = toml_path_for(folder)
    if toml_file.exists():
        try:
            load_picker_from_folder(arma, folder)
        except Exception:
            print(f"Face picker auto-load failed:\n{traceback.format_exc()}")


def store_render_size(context, picker):
    render = context.scene.render
    if not picker.backup_resolution_stored:
        picker.backup_resolution_x = render.resolution_x
        picker.backup_resolution_y = render.resolution_y
        picker.backup_resolution_stored = True


def apply_square_render(context, picker):
    store_render_size(context, picker)
    size = picker.camera_size or DEFAULT_CAMERA_SIZE
    render = context.scene.render
    render.resolution_x = size
    render.resolution_y = size
    render.resolution_percentage = 100
    render.pixel_aspect_x = 1.0
    render.pixel_aspect_y = 1.0


def restore_render_size(context, picker):
    if not picker.backup_resolution_stored:
        return
    render = context.scene.render
    render.resolution_x = picker.backup_resolution_x
    render.resolution_y = picker.backup_resolution_y
    picker.backup_resolution_stored = False


def configure_square_camera(cam_data, ortho_scale):
    cam_data.sensor_fit = "AUTO"
    cam_data.sensor_width = 36.0
    cam_data.sensor_height = 36.0
    cam_data.lens = 85.0
    cam_data.clip_start = 0.01
    cam_data.clip_end = 100.0
    cam_data.display_size = 0.2
    cam_data.show_passepartout = True
    cam_data.passepartout_alpha = 0.6
    cam_data.type = "PERSP"
    if ortho_scale:
        cam_data.ortho_scale = ortho_scale


def _win32_user32():
    if sys.platform != "win32":
        return None
    return ctypes.windll.user32


def _win32_process_hwnds():
    user32 = _win32_user32()
    if user32 is None:
        return []
    pid = os.getpid()
    hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        process_id = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid:
            hwnds.append(int(hwnd))
        return True

    user32.EnumWindows(_enum, 0)
    return hwnds


def _hwnd_is_valid(hwnd):
    user32 = _win32_user32()
    return bool(hwnd) and user32 is not None and bool(user32.IsWindow(hwnd))


def _hwnd_from_screen(window):
    screen = getattr(window, "screen", None)
    if screen is None:
        return 0
    try:
        hwnd = int(screen.get(FACE_PICKER_HWND_KEY, 0) or 0)
    except Exception:
        return 0
    return hwnd if _hwnd_is_valid(hwnd) else 0


def _store_hwnd(window, hwnd):
    screen = getattr(window, "screen", None)
    if screen is None or not hwnd:
        return
    try:
        screen[FACE_PICKER_HWND_KEY] = str(int(hwnd))
    except Exception:
        pass


def _hwnd_window_size(hwnd):
    user32 = _win32_user32()
    if user32 is None:
        return 0, 0
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def _match_hwnd_to_blender_window(window, candidates=None):
    user32 = _win32_user32()
    if user32 is None or window is None:
        return 0
    hwnds = list(candidates) if candidates is not None else _win32_process_hwnds()
    best = 0
    best_score = None
    max_width = max(int(window.width * 1.8), window.width + 80)
    max_height = max(int(window.height * 1.8), window.height + 80)
    for hwnd in hwnds:
        if not _hwnd_is_valid(hwnd):
            continue
        width, height = _hwnd_window_size(hwnd)
        if width > max_width or height > max_height:
            continue
        score = abs(width - window.width) + abs(height - window.height)
        if best_score is None or score < best_score:
            best = hwnd
            best_score = score
    if best_score is None or best_score > 280:
        return 0
    return best


def _set_hwnd_always_on_top(hwnd):
    user32 = _win32_user32()
    if user32 is None or not _hwnd_is_valid(hwnd):
        return False
    user32.SetWindowPos(
        ctypes.c_void_p(hwnd),
        ctypes.c_void_p(_HWND_TOPMOST),
        0,
        0,
        0,
        0,
        _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
    )
    return True


def _pin_picker_window(window, new_hwnds=None):
    if window is None or sys.platform != "win32":
        return
    hwnd = _hwnd_from_screen(window)
    if not hwnd and new_hwnds:
        user32 = _win32_user32()
        foreground = int(user32.GetForegroundWindow() or 0) if user32 else 0
        if foreground in new_hwnds:
            hwnd = foreground
        elif len(new_hwnds) == 1:
            hwnd = next(iter(new_hwnds))
        else:
            hwnd = _match_hwnd_to_blender_window(window, candidates=new_hwnds)
    if not hwnd:
        hwnd = _match_hwnd_to_blender_window(window)
    if hwnd:
        _store_hwnd(window, hwnd)
        _set_hwnd_always_on_top(hwnd)


def _keep_picker_on_top():
    global _topmost_timer_running, _canvas_modal_running
    try:
        window = find_face_picker_window(bpy.context)
        if window is None:
            _topmost_timer_running = False
            _canvas_modal_running = False
            return None
        _pin_picker_window(window)
    except Exception:
        pass
    return 0.25


def _ensure_topmost_timer():
    global _topmost_timer_running
    if sys.platform != "win32":
        return
    if _topmost_timer_running:
        return
    try:
        bpy.app.timers.register(_keep_picker_on_top, first_interval=0.05, persistent=True)
        _topmost_timer_running = True
    except Exception:
        _topmost_timer_running = False


def _stop_topmost_timer():
    global _topmost_timer_running
    _topmost_timer_running = False
    try:
        if bpy.app.timers.is_registered(_keep_picker_on_top):
            bpy.app.timers.unregister(_keep_picker_on_top)
    except Exception:
        pass


def find_face_picker_window(context):
    for window in context.window_manager.windows:
        screen = window.screen
        if screen is not None and screen.get(FACE_PICKER_SCREEN_KEY):
            return window
    return None


def register():
    _ensure_draw_handler()


def unregister():
    _remove_draw_handler()
    _stop_topmost_timer()


_picker_armature_name = ""
_picker_scroll = 0.0
_picker_hits = []
_picker_hover = None
_draw_handler = None
_canvas_modal_running = False
_canvas_generation = 0


def _is_picker_screen(screen):
    return screen is not None and bool(screen.get(FACE_PICKER_SCREEN_KEY))


def _region_is_open(region):
    if region.type in {"HEADER", "TOOL_HEADER", "FOOTER"}:
        return region.height > 1
    return region.width > 1


def _hide_area_regions(area, window=None, region_types=()):
    window = window or getattr(bpy.context, "window", None)
    screen = getattr(window, "screen", None) if window is not None else None
    for region_type in region_types:
        for region in area.regions:
            if region.type != region_type or not _region_is_open(region):
                continue
            try:
                override = {"window": window, "area": area, "region": region}
                if screen is not None:
                    override["screen"] = screen
                with bpy.context.temp_override(**override):
                    bpy.ops.screen.region_toggle(region_type=region_type)
            except Exception:
                try:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.screen.region_toggle()
                except Exception:
                    pass
            break


def _ui_shader():
    try:
        return gpu.shader.from_builtin("UNIFORM_COLOR")
    except Exception:
        return gpu.shader.from_builtin("2D_UNIFORM_COLOR")


def _draw_rect(x, y, width, height, color):
    shader = _ui_shader()
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", color)
    batch = batch_for_shader(
        shader,
        "TRI_FAN",
        {
            "pos": (
                (x, y),
                (x + width, y),
                (x + width, y + height),
                (x, y + height),
            )
        },
    )
    batch.draw(shader)


def _draw_outline(x, y, width, height, color, thickness=2.0):
    _draw_rect(x, y, width, thickness, color)
    _draw_rect(x, y + height - thickness, width, thickness, color)
    _draw_rect(x, y, thickness, height, color)
    _draw_rect(x + width - thickness, y, thickness, height, color)


def _draw_label(text, x, y, width, height, size, color=(0.9, 0.9, 0.9, 1.0)):
    font = 0
    try:
        blf.size(font, size)
    except TypeError:
        blf.size(font, size, 72)
    blf.color(font, *color)
    text_w, text_h = blf.dimensions(font, text)
    blf.position(font, x + max((width - text_w) * 0.5, 2.0), y + max((height - text_h) * 0.5, 1.0), 0)
    blf.draw(font, text)


def _picker_editor_area(window):
    if window is None or window.screen is None:
        return None
    for area in window.screen.areas:
        if area.type not in {"TOPBAR", "STATUSBAR"}:
            return area
    return None


def _picker_window_region(area):
    if area is None:
        return None
    for region in area.regions:
        if region.type == "WINDOW":
            return region
    return None


def _canvas_armature():
    obj = bpy.data.objects.get(_picker_armature_name)
    if obj is not None and obj.type == "ARMATURE":
        return obj
    return get_armature(bpy.context)


def _hit_at(x, y):
    for hit in _picker_hits:
        if hit["x"] <= x <= hit["x"] + hit["w"] and hit["y"] <= y <= hit["y"] + hit["h"]:
            return hit
    return None


def _ensure_draw_handler():
    global _draw_handler
    if _draw_handler is not None:
        return
    try:
        _draw_handler = bpy.types.SpaceImageEditor.draw_handler_add(
            _draw_picker_overlay, (), "WINDOW", "POST_PIXEL"
        )
    except Exception:
        _draw_handler = None


def _remove_draw_handler():
    global _draw_handler
    if _draw_handler is None:
        return
    try:
        bpy.types.SpaceImageEditor.draw_handler_remove(_draw_handler, "WINDOW")
    except Exception:
        pass
    _draw_handler = None


def _draw_picker_overlay():
    context = bpy.context
    if not _is_picker_screen(getattr(context, "screen", None)):
        return
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "IMAGE_EDITOR" or region is None or region.type != "WINDOW":
        return
    arma = _canvas_armature()
    picker = get_picker(arma)
    _draw_picker_canvas(context, region, picker)


def _draw_picker_canvas(context, region, picker):
    global _picker_hits, _picker_scroll
    _picker_hits = []
    ui = context.preferences.system.ui_scale
    width = region.width
    height = region.height
    _draw_rect(0, 0, width, height, (0.18, 0.18, 0.18, 1.0))

    pad = 10.0 * ui
    gap = 8.0 * ui
    title_h = 24.0 * ui
    bar_h = 28.0 * ui
    name_h = 22.0 * ui
    font_size = 12.0 * ui
    y = height - pad

    _draw_label("Easy Facial Animation", pad, y - title_h, width - pad * 2, title_h, 14.0 * ui)
    y -= title_h + 4.0 * ui

    check = 16.0 * ui
    key_x = pad
    key_y = y - bar_h + (bar_h - check) * 0.5
    keyframes = bool(picker.insert_keyframes) if picker is not None else True
    _draw_rect(key_x, key_y, check, check, (0.12, 0.12, 0.12, 1.0))
    _draw_outline(key_x, key_y, check, check, (0.45, 0.45, 0.45, 1.0), 1.5)
    if keyframes:
        _draw_rect(key_x + 3.0 * ui, key_y + 3.0 * ui, check - 6.0 * ui, check - 6.0 * ui, (0.33, 0.55, 0.85, 1.0))
    _draw_label("Keyframes", key_x + check + 6.0 * ui, y - bar_h, 90.0 * ui, bar_h, font_size, (0.85, 0.85, 0.85, 1.0))
    _picker_hits.append({"kind": "keyframes", "x": key_x, "y": y - bar_h, "w": check + 96.0 * ui, "h": bar_h})

    columns = picker.columns if picker is not None else 3
    plus_w = 22.0 * ui
    col_w = 28.0 * ui
    right = width - pad
    plus_x = right - plus_w
    num_x = plus_x - col_w
    minus_x = num_x - plus_w
    _draw_rect(minus_x, y - bar_h, plus_w, bar_h, (0.28, 0.28, 0.28, 1.0))
    _draw_rect(plus_x, y - bar_h, plus_w, bar_h, (0.28, 0.28, 0.28, 1.0))
    _draw_label("-", minus_x, y - bar_h, plus_w, bar_h, font_size)
    _draw_label(str(columns), num_x, y - bar_h, col_w, bar_h, font_size)
    _draw_label("+", plus_x, y - bar_h, plus_w, bar_h, font_size)
    _draw_label("Columns", minus_x - 78.0 * ui, y - bar_h, 74.0 * ui, bar_h, font_size, (0.75, 0.75, 0.75, 1.0))
    _picker_hits.append({"kind": "columns_minus", "x": minus_x, "y": y - bar_h, "w": plus_w, "h": bar_h})
    _picker_hits.append({"kind": "columns_plus", "x": plus_x, "y": y - bar_h, "w": plus_w, "h": bar_h})

    y -= bar_h + gap
    clip_top = y
    clip_bottom = pad
    if picker is None:
        _draw_label("Select an imported armature", pad, height * 0.5, width - pad * 2, 24.0 * ui, font_size)
        return
    if not picker.expressions:
        _draw_label("No expressions yet. Use Setup in the Ultimate tab first.", pad, height * 0.5, width - pad * 2, 24.0 * ui, font_size)
        return

    cols = max(int(picker.columns), 1)
    inner_w = max(width - pad * 2, 1.0)
    cell_w = (inner_w - gap * (cols - 1)) / cols
    img_h = cell_w
    cell_h = img_h + name_h
    count = len(picker.expressions)
    rows = math.ceil(count / cols)
    content_h = rows * cell_h + max(rows - 1, 0) * gap
    visible_h = max(clip_top - clip_bottom, 1.0)
    max_scroll = max(content_h - visible_h, 0.0)
    _picker_scroll = min(max(_picker_scroll, 0.0), max_scroll)
    content_top = clip_top + _picker_scroll

    for index, expr in enumerate(picker.expressions):
        row, col = divmod(index, cols)
        cell_x = pad + col * (cell_w + gap)
        cell_top = content_top - row * (cell_h + gap)
        cell_y = cell_top - cell_h
        if cell_top < clip_bottom or cell_y > clip_top:
            continue
        is_active = expr.expression_id == picker.active_expression_id
        is_hover = _picker_hover == expr.expression_id
        name_y = cell_y
        img_y = name_y + name_h
        if is_active:
            _draw_outline(cell_x - 2.0, img_y - 2.0, cell_w + 4.0, img_h + 4.0, (0.33, 0.55, 0.85, 1.0), 2.5)
        elif is_hover:
            _draw_outline(cell_x - 1.0, img_y - 1.0, cell_w + 2.0, img_h + 2.0, (0.55, 0.55, 0.55, 1.0), 1.5)
        image = expr.image
        if image is not None:
            try:
                texture = gpu.texture.from_image(image)
                draw_texture_2d(texture, (cell_x, img_y), cell_w, img_h)
            except Exception:
                _draw_rect(cell_x, img_y, cell_w, img_h, (0.1, 0.1, 0.1, 1.0))
        else:
            _draw_rect(cell_x, img_y, cell_w, img_h, (0.1, 0.1, 0.1, 1.0))
        button_color = (0.25, 0.47, 0.75, 1.0) if is_active else (0.27, 0.27, 0.27, 1.0)
        _draw_rect(cell_x, name_y, cell_w, name_h, button_color)
        _draw_label(expr.name, cell_x, name_y, cell_w, name_h, font_size)
        _picker_hits.append(
            {
                "kind": "expression",
                "id": expr.expression_id,
                "x": cell_x,
                "y": cell_y,
                "w": cell_w,
                "h": cell_h,
            }
        )


def _apply_picker_hit(context, hit):
    arma = _canvas_armature()
    picker = get_picker(arma)
    if picker is None:
        return False
    kind = hit.get("kind")
    if kind == "keyframes":
        picker.insert_keyframes = not picker.insert_keyframes
        return True
    if kind == "columns_minus":
        picker.columns = max(picker.columns - 1, 1)
        return True
    if kind == "columns_plus":
        picker.columns = min(picker.columns + 1, 6)
        return True
    if kind == "expression":
        expression = find_expression(picker, hit.get("id"))
        if expression is None:
            return False
        apply_expression(context, arma, expression, picker.insert_keyframes)
        return True
    return False


def _start_picker_canvas(context, window, force=False):
    global _canvas_modal_running, _canvas_generation
    _ensure_draw_handler()
    if _canvas_modal_running and not force:
        return
    area = _picker_editor_area(window)
    region = _picker_window_region(area)
    if area is None or region is None:
        return
    _canvas_generation += 1
    try:
        with context.temp_override(window=window, area=area, region=region):
            bpy.ops.sub.face_picker_canvas("INVOKE_DEFAULT")
        _canvas_modal_running = True
    except Exception:
        try:
            bpy.ops.sub.face_picker_canvas("INVOKE_DEFAULT")
            _canvas_modal_running = True
        except Exception:
            pass


def _configure_picker_area(area, window=None):
    if area.type != "IMAGE_EDITOR":
        area.type = "IMAGE_EDITOR"
    space = area.spaces.active
    if space.type != "IMAGE_EDITOR":
        return
    try:
        space.image = None
    except Exception:
        pass
    for attr in (
        "show_region_header",
        "show_region_tool_header",
        "show_region_ui",
        "show_region_toolbar",
        "show_region_hud",
        "show_region_footer",
    ):
        if hasattr(space, attr):
            setattr(space, attr, False)
    _hide_area_regions(
        area,
        window,
        ("HEADER", "TOOL_HEADER", "TOOLS", "UI", "HUD", "FOOTER", "NAVIGATION_BAR"),
    )
    try:
        area.tag_redraw()
    except Exception:
        pass


_configure_retries_left = 0


def _schedule_picker_configure():
    global _configure_retries_left
    _configure_retries_left = 5
    try:
        if bpy.app.timers.is_registered(_deferred_configure_picker_window):
            return
        bpy.app.timers.register(_deferred_configure_picker_window, first_interval=0.02)
    except Exception:
        pass


def _deferred_configure_picker_window():
    global _configure_retries_left
    try:
        context = bpy.context
        window = find_face_picker_window(context)
        if window is None:
            _configure_retries_left = 0
            return None
        for area in window.screen.areas:
            if area.type not in {"TOPBAR", "STATUSBAR"}:
                _configure_picker_area(area, window)
        _pin_picker_window(window)
        _ensure_topmost_timer()
        _start_picker_canvas(context, window)
    except Exception:
        pass
    _configure_retries_left -= 1
    if _configure_retries_left > 0:
        return 0.08
    return None


def _new_window_after(before_ptrs, wm):
    for window in wm.windows:
        if window.as_pointer() not in before_ptrs:
            return window
    return None


def open_face_picker_window(context):
    global _picker_armature_name
    arma = get_armature(context)
    if arma is not None:
        _picker_armature_name = arma.name

    existing = find_face_picker_window(context)
    if existing is not None:
        for candidate in existing.screen.areas:
            if candidate.type not in {"TOPBAR", "STATUSBAR"}:
                _configure_picker_area(candidate, existing)
                break
        _pin_picker_window(existing)
        _ensure_topmost_timer()
        _start_picker_canvas(context, existing, force=True)
        return existing, False

    wm = context.window_manager
    picker = get_picker(arma)
    columns = picker.columns if picker is not None else 3
    window_width = max(460, int(columns) * 170)
    window_height = 820

    render = context.scene.render
    old_x = render.resolution_x
    old_y = render.resolution_y
    old_pct = render.resolution_percentage

    before_hwnds = set(_win32_process_hwnds())
    before = {window.as_pointer() for window in wm.windows}
    new_window = None
    try:
        render.resolution_x = window_width
        render.resolution_y = window_height
        render.resolution_percentage = 100
        bpy.ops.render.view_show("INVOKE_DEFAULT")
        new_window = _new_window_after(before, wm)
        if new_window is None:
            bpy.ops.render.view_show("INVOKE_DEFAULT")
            new_window = _new_window_after(before, wm)
    except Exception:
        new_window = None
    finally:
        render.resolution_x = old_x
        render.resolution_y = old_y
        render.resolution_percentage = old_pct

    if new_window is None:
        before = {window.as_pointer() for window in wm.windows}
        bpy.ops.wm.window_new()
        new_window = _new_window_after(before, wm) or wm.windows[-1]

    try:
        new_window.screen[FACE_PICKER_SCREEN_KEY] = True
    except Exception:
        pass

    for candidate in new_window.screen.areas:
        if candidate.type not in {"TOPBAR", "STATUSBAR"}:
            _configure_picker_area(candidate, new_window)
            break

    _schedule_picker_configure()

    new_hwnds = set(_win32_process_hwnds()) - before_hwnds
    _pin_picker_window(new_window, new_hwnds=new_hwnds)
    _ensure_topmost_timer()
    _start_picker_canvas(context, new_window, force=True)
    return new_window, True


class SUB_PG_face_picker_track(PropertyGroup):
    name: StringProperty(name="Track", default="")


class SUB_PG_face_picker_track_choice(PropertyGroup):
    name: StringProperty(name="Track", default="")
    selected: BoolProperty(name="Assign", default=False)


class SUB_PG_face_picker_expression(PropertyGroup):
    expression_id: StringProperty(name="ID", default="")
    name: StringProperty(name="Name", default="")
    image_path: StringProperty(name="Image Path", default="")
    image: PointerProperty(name="Thumbnail", type=bpy.types.Image)
    tracks: CollectionProperty(type=SUB_PG_face_picker_track)


class SUB_PG_face_picker_data(PropertyGroup):
    expressions: CollectionProperty(type=SUB_PG_face_picker_expression)
    active_expression_index: IntProperty(
        name="Active Expression",
        default=0,
        update=_on_active_expression_index_update,
    )
    active_expression_id: StringProperty(name="Active Expression ID", default="")
    source_folder: StringProperty(name="Model Folder", default="", subtype="DIR_PATH")
    camera: PointerProperty(name="Face Camera", type=bpy.types.Object)
    camera_size: IntProperty(
        name="Thumbnail Size",
        description="Square capture resolution in pixels",
        default=DEFAULT_CAMERA_SIZE,
        min=64,
        max=2048,
    )
    backup_resolution_x: IntProperty(default=0)
    backup_resolution_y: IntProperty(default=0)
    backup_resolution_stored: BoolProperty(default=False)
    new_expression_name: StringProperty(name="Expression Name", default="")
    track_choices: CollectionProperty(type=SUB_PG_face_picker_track_choice)
    track_choices_index: IntProperty(name="Track Choice Index", default=0)
    insert_keyframes: BoolProperty(
        name="Insert Keyframes",
        description="Insert visibility keyframes when applying an expression. Also happens when Auto Key is enabled",
        default=True,
    )
    columns: IntProperty(name="Columns", default=3, min=1, max=6)
    setup_expanded: BoolProperty(name="Setup Expanded", default=True)


class SUB_UL_face_picker_track_choices(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.prop(item, "selected", text="")
            row.label(text=item.name, icon="HIDE_OFF")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.prop(item, "selected", text="")


class SUB_UL_face_picker_expressions(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        icon_id = preview_icon(item.image)
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            if icon_id:
                layout.label(text=item.name, icon_value=icon_id)
            else:
                layout.label(text=item.name, icon="IMAGE_DATA")
            layout.label(text=str(len(item.tracks)))
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            col = layout.column(align=True)
            if icon_id:
                col.template_icon(icon_value=icon_id, scale=6.5)
            col.label(text=item.name, translate=False)


class SUB_OP_face_picker_apply(Operator):
    bl_idname = "sub.face_picker_apply"
    bl_label = "Apply Expression"
    bl_description = "Enable this expression's visibility tracks and disable the other picker expressions"
    bl_options = {"REGISTER", "UNDO"}

    expression_id: StringProperty()

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        if picker is None:
            self.report({"ERROR"}, "No face picker data on the armature")
            return {"CANCELLED"}
        expression = find_expression(picker, self.expression_id)
        if expression is None:
            self.report({"ERROR"}, "Expression was not found")
            return {"CANCELLED"}
        _changed, missing = apply_expression(context, arma, expression, picker.insert_keyframes)
        if missing:
            self.report({"WARNING"}, f"Missing vis tracks: {', '.join(missing)}")
        else:
            self.report({"INFO"}, f"Applied {expression.name}")
        return {"FINISHED"}


class SUB_OP_face_picker_canvas(Operator):
    bl_idname = "sub.face_picker_canvas"
    bl_label = "Face Picker Canvas"
    bl_options = {"INTERNAL"}

    def _stop(self, context):
        global _canvas_modal_running
        timer = getattr(self, "_timer", None)
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except Exception:
                pass
            self._timer = None
        if getattr(self, "_generation", None) == _canvas_generation:
            _canvas_modal_running = False
        return {"CANCELLED"}

    def invoke(self, context, _event):
        global _canvas_modal_running
        self._generation = _canvas_generation
        window = find_face_picker_window(context) or context.window
        self._window_ptr = window.as_pointer() if window is not None else 0
        host = context.window_manager.windows[0] if context.window_manager.windows else context.window
        try:
            self._timer = context.window_manager.event_timer_add(0.2, window=host)
        except Exception:
            self._timer = None
        _canvas_modal_running = True
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _picker_hover, _picker_scroll
        if getattr(self, "_generation", None) != _canvas_generation:
            return self._stop(context)
        window = find_face_picker_window(context)
        if window is None:
            return self._stop(context)
        if event.type == "TIMER":
            return {"PASS_THROUGH"}
        current = getattr(context, "window", None)
        if current is None or current.as_pointer() != window.as_pointer():
            return {"PASS_THROUGH"}
        area = _picker_editor_area(window)
        region = _picker_window_region(area)
        if area is None or region is None:
            return {"PASS_THROUGH"}
        mouse_x = event.mouse_x - region.x
        mouse_y = event.mouse_y - region.y
        inside = 0 <= mouse_x < region.width and 0 <= mouse_y < region.height
        if event.type == "MOUSEMOVE" and inside:
            hit = _hit_at(mouse_x, mouse_y)
            hover = hit.get("id") if hit and hit.get("kind") == "expression" else None
            if hover != _picker_hover:
                _picker_hover = hover
                area.tag_redraw()
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS" and inside:
            hit = _hit_at(mouse_x, mouse_y)
            if hit and _apply_picker_hit(context, hit):
                area.tag_redraw()
                return {"RUNNING_MODAL"}
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and inside:
            step = 48.0 * context.preferences.system.ui_scale
            if event.type == "WHEELUPMOUSE":
                _picker_scroll = max(_picker_scroll - step, 0.0)
            else:
                _picker_scroll += step
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


class SUB_OP_face_picker_popup(Operator):
    bl_idname = "sub.face_picker_popup"
    bl_label = "Easy Facial Animation"
    bl_description = "Open the expression picker in a separate window"

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        if picker is not None:
            ensure_track_choices(arma)
            if not picker.expressions:
                folder = resolve_source_folder(context, arma)
                if folder and toml_path_for(folder).exists():
                    try:
                        load_picker_from_folder(arma, folder)
                    except Exception as exc:
                        self.report({"WARNING"}, f"Could not load face_picker.toml: {exc}")
        window, created = open_face_picker_window(context)
        if window is None:
            self.report({"ERROR"}, "Could not open a new window")
            return {"CANCELLED"}
        if created:
            self.report({"INFO"}, "Opened Easy Facial Animation window")
        else:
            self.report({"INFO"}, "Easy Facial Animation window is already open")
        return {"FINISHED"}


class SUB_OP_face_picker_create_camera(Operator):
    bl_idname = "sub.face_picker_create_camera"
    bl_label = "Create Face Camera"
    bl_description = "Create a square face camera aimed at the character's head"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        if picker is None:
            self.report({"ERROR"}, "No face picker data on the armature")
            return {"CANCELLED"}

        size = picker.camera_size or DEFAULT_CAMERA_SIZE
        name = f"{arma.name}_FacePickerCam"
        cam_obj = picker.camera
        if cam_obj is None:
            cam_obj = bpy.data.objects.get(name)

        created = False
        if cam_obj is None or cam_obj.type != "CAMERA":
            cam_data = bpy.data.cameras.new(name)
            cam_obj = bpy.data.objects.new(name, cam_data)
            context.collection.objects.link(cam_obj)
            created = True

        cam_data = cam_obj.data
        target, distance = estimate_face_target(arma)
        configure_square_camera(cam_data, ortho_scale=max(distance * 0.9, 0.2))

        forward = (arma.matrix_world.to_quaternion() @ Vector((0.0, -1.0, 0.0))).normalized()
        location = target + forward * max(distance, 0.25)
        direction = target - location
        rotation = direction.to_track_quat("-Z", "Y") if direction.length > 0.0001 else cam_obj.matrix_world.to_quaternion()
        world_matrix = Matrix.LocRotScale(location, rotation, Vector((1.0, 1.0, 1.0)))
        cam_obj.parent = arma
        cam_obj.matrix_world = world_matrix

        picker.camera = cam_obj
        picker.camera_size = size
        apply_square_render(context, picker)

        area, _region = find_view3d(context)
        if area is not None:
            context.scene.camera = cam_obj
            area.spaces.active.region_3d.view_perspective = "CAMERA"

        self.report({"INFO"}, "Created face camera" if created else "Updated face camera")
        return {"FINISHED"}


class SUB_OP_face_picker_remove_camera(Operator):
    bl_idname = "sub.face_picker_remove_camera"
    bl_label = "Remove Face Camera"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and picker.camera is not None

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        camera = picker.camera
        picker.camera = None
        restore_render_size(context, picker)
        if camera is not None:
            cam_data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if cam_data is not None and cam_data.users == 0:
                bpy.data.cameras.remove(cam_data)
        self.report({"INFO"}, "Removed face camera")
        return {"FINISHED"}


class SUB_OP_face_picker_view_camera(Operator):
    bl_idname = "sub.face_picker_view_camera"
    bl_label = "View Face Camera"
    bl_description = "Look through the face picker camera"

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and picker.camera is not None

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        area, _region = find_view3d(context)
        if area is None:
            self.report({"ERROR"}, "No 3D Viewport found")
            return {"CANCELLED"}
        context.scene.camera = picker.camera
        apply_square_render(context, picker)
        area.spaces.active.region_3d.view_perspective = "CAMERA"
        return {"FINISHED"}


class SUB_OP_face_picker_refresh_tracks(Operator):
    bl_idname = "sub.face_picker_refresh_tracks"
    bl_label = "Refresh Vis Tracks"
    bl_description = "Reload visibility track names from Ultimate Animation Data"

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def execute(self, context):
        count = refresh_track_choices(get_armature(context))
        self.report({"INFO"}, f"Loaded {count} visibility tracks")
        return {"FINISHED"}


class SUB_OP_face_picker_match_enabled(Operator):
    bl_idname = "sub.face_picker_match_enabled"
    bl_label = "Match Currently Enabled"
    bl_description = "Assign whatever visibility tracks are currently checked on"

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.track_choices) > 0

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        sap = get_sap(arma)
        enabled = {entry.name for entry in sap.vis_track_entries if entry.value}
        matched = 0
        for item in picker.track_choices:
            item.selected = item.name in enabled
            if item.selected:
                matched += 1
        self.report({"INFO"}, f"Assigned {matched} currently enabled tracks")
        return {"FINISHED"}


class SUB_OP_face_picker_select_facial(Operator):
    bl_idname = "sub.face_picker_select_facial"
    bl_label = "Select Facial Names"
    bl_description = "Check vis tracks whose names look like eyes, blinks, or mouths"

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.track_choices) > 0

    def execute(self, context):
        picker = get_picker(get_armature(context))
        matched = 0
        for item in picker.track_choices:
            lower = item.name.lower()
            item.selected = any(hint in lower for hint in FACIAL_TRACK_HINTS)
            if item.selected:
                matched += 1
        self.report({"INFO"}, f"Selected {matched} facial tracks")
        return {"FINISHED"}


class SUB_OP_face_picker_clear_tracks(Operator):
    bl_idname = "sub.face_picker_clear_tracks"
    bl_label = "Clear Assigned Tracks"

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.track_choices) > 0

    def execute(self, context):
        picker = get_picker(get_armature(context))
        for item in picker.track_choices:
            item.selected = False
        return {"FINISHED"}


class SUB_OP_face_picker_preview(Operator):
    bl_idname = "sub.face_picker_preview"
    bl_label = "Preview Assigned Tracks"
    bl_description = "Turn on the assigned vis tracks and turn off other picker expression tracks"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and any(item.selected for item in picker.track_choices)

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        enable = selected_track_names(picker)
        managed = managed_track_names(picker, enable)
        set_visibility_tracks(arma, set(enable), managed, insert_keyframes=False)
        self.report({"INFO"}, f"Previewing {len(enable)} tracks")
        return {"FINISHED"}


class SUB_OP_face_picker_add(Operator):
    bl_idname = "sub.face_picker_add"
    bl_label = "Capture & Add Expression"
    bl_description = "Take a thumbnail of the current expression and store its vis tracks"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        ensure_track_choices(arma)
        name = picker.new_expression_name.strip()
        tracks = selected_track_names(picker)
        if not name:
            self.report({"ERROR"}, "Enter an expression name first")
            return {"CANCELLED"}
        if not tracks:
            self.report({"ERROR"}, "Assign at least one visibility track")
            return {"CANCELLED"}

        folder = resolve_source_folder(context, arma)
        expr_id = unique_expression_id(picker, sanitize_id(name))
        set_visibility_tracks(arma, set(tracks), managed_track_names(picker, tracks), insert_keyframes=False)

        image = None
        rel_path = f"{IMAGE_DIR_NAME}/{expr_id}.png"
        try:
            if folder:
                filepath = image_dir_for(folder) / f"{expr_id}.png"
            else:
                filepath = Path(bpy.app.tempdir) / f"{expr_id}.png"
            capture_thumbnail(context, arma, filepath, picker.camera_size or DEFAULT_CAMERA_SIZE)
            image = load_preview_image(filepath, expression_image_name(arma, expr_id))
            if folder:
                rel_path = f"{IMAGE_DIR_NAME}/{expr_id}.png"
            else:
                rel_path = str(filepath)
        except Exception as exc:
            self.report({"WARNING"}, f"Added without thumbnail: {exc}")

        expr = picker.expressions.add()
        expr.expression_id = expr_id
        expr.name = name
        expr.image_path = rel_path.replace("\\", "/")
        expr.image = image
        add_tracks_to_expression(expr, tracks)
        picker.active_expression_index = len(picker.expressions) - 1
        picker.active_expression_id = expr_id
        picker.new_expression_name = ""
        self.report({"INFO"}, f"Added expression '{name}'")
        return {"FINISHED"}


class SUB_OP_face_picker_remove(Operator):
    bl_idname = "sub.face_picker_remove"
    bl_label = "Remove Expression"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.expressions) > 0

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        index = picker.active_expression_index
        if index < 0 or index >= len(picker.expressions):
            return {"CANCELLED"}
        expr = picker.expressions[index]
        if picker.active_expression_id == expr.expression_id:
            picker.active_expression_id = ""
        if expr.image is not None:
            image = expr.image
            expr.image = None
            if image.users <= 1:
                bpy.data.images.remove(image)
        picker.expressions.remove(index)
        picker.active_expression_index = min(index, max(len(picker.expressions) - 1, 0))
        return {"FINISHED"}


class SUB_OP_face_picker_load_selected(Operator):
    bl_idname = "sub.face_picker_load_selected"
    bl_label = "Load Expression Into Setup"
    bl_description = "Copy the selected expression's tracks back into the setup checklist"

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.expressions) > 0

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        if not picker.expressions:
            return {"CANCELLED"}
        index = min(max(picker.active_expression_index, 0), len(picker.expressions) - 1)
        expr = picker.expressions[index]
        refresh_track_choices(arma)
        assigned = {track.name for track in expr.tracks}
        for item in picker.track_choices:
            item.selected = item.name in assigned
        picker.new_expression_name = expr.name
        picker.active_expression_id = expr.expression_id
        set_visibility_tracks(arma, assigned, managed_track_names(picker, assigned), insert_keyframes=False)
        return {"FINISHED"}


class SUB_OP_face_picker_recapture(Operator):
    bl_idname = "sub.face_picker_recapture"
    bl_label = "Recapture Thumbnail"
    bl_description = "Replace the selected expression's thumbnail and vis tracks with the current setup"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.expressions) > 0

    def execute(self, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        index = picker.active_expression_index
        if index < 0 or index >= len(picker.expressions):
            return {"CANCELLED"}
        expr = picker.expressions[index]
        tracks = selected_track_names(picker)
        if not tracks:
            tracks = [track.name for track in expr.tracks]
        if not tracks:
            self.report({"ERROR"}, "Assign at least one visibility track")
            return {"CANCELLED"}

        folder = resolve_source_folder(context, arma)
        set_visibility_tracks(arma, set(tracks), managed_track_names(picker, tracks), insert_keyframes=False)
        add_tracks_to_expression(expr, tracks)
        if picker.new_expression_name.strip():
            expr.name = picker.new_expression_name.strip()

        try:
            if folder:
                filepath = image_dir_for(folder) / f"{expr.expression_id}.png"
                expr.image_path = f"{IMAGE_DIR_NAME}/{expr.expression_id}.png"
            else:
                filepath = Path(bpy.app.tempdir) / f"{expr.expression_id}.png"
                expr.image_path = str(filepath)
            capture_thumbnail(context, arma, filepath, picker.camera_size or DEFAULT_CAMERA_SIZE)
            expr.image = load_preview_image(filepath, expression_image_name(arma, expr.expression_id))
        except Exception as exc:
            self.report({"WARNING"}, f"Updated tracks, but thumbnail failed: {exc}")
            return {"FINISHED"}

        picker.active_expression_id = expr.expression_id
        self.report({"INFO"}, f"Updated {expr.name}")
        return {"FINISHED"}


class SUB_OP_face_picker_save(Operator):
    bl_idname = "sub.face_picker_save"
    bl_label = "Save Expression Menu"
    bl_description = "Save the picker setup as face_picker.toml next to the loaded model"

    @classmethod
    def poll(cls, context):
        arma = get_armature(context)
        picker = get_picker(arma)
        return picker is not None and len(picker.expressions) > 0

    def execute(self, context):
        arma = get_armature(context)
        folder = resolve_source_folder(context, arma)
        if not folder:
            self.report({"ERROR"}, "Set the model folder first")
            return {"CANCELLED"}
        try:
            path = save_picker_to_folder(arma, folder)
        except Exception as exc:
            self.report({"ERROR"}, f"Save failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved {path}")
        return {"FINISHED"}


class SUB_OP_face_picker_load(Operator):
    bl_idname = "sub.face_picker_load"
    bl_label = "Load Expression Menu"
    bl_description = "Load face_picker.toml from the model folder"

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def execute(self, context):
        arma = get_armature(context)
        folder = resolve_source_folder(context, arma)
        if not folder:
            self.report({"ERROR"}, "Set the model folder first")
            return {"CANCELLED"}
        path = toml_path_for(folder)
        if not path.exists():
            self.report({"ERROR"}, f"No {TOML_NAME} in {folder}")
            return {"CANCELLED"}
        try:
            count = load_picker_from_folder(arma, folder)
        except Exception as exc:
            self.report({"ERROR"}, f"Load failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Loaded {count} expressions")
        return {"FINISHED"}


def draw_expression_grid(layout, picker, columns, scale=6.0):
    if not picker.expressions:
        layout.label(text="No expressions yet. Use Setup to add some.")
        return
    columns = max(int(columns), 1)
    grid = layout.grid_flow(
        row_major=True,
        columns=columns,
        even_columns=True,
        even_rows=True,
        align=True,
    )
    for expr in picker.expressions:
        is_active = expr.expression_id == picker.active_expression_id
        cell = grid.column(align=True)
        if is_active:
            cell = cell.box()
        icon_id = preview_icon(expr.image)
        if icon_id:
            cell.template_icon(icon_value=icon_id, scale=scale)
        op = cell.operator(
            SUB_OP_face_picker_apply.bl_idname,
            text=expr.name,
            depress=is_active,
        )
        op.expression_id = expr.expression_id


def draw_popup_picker_layout(layout, context):
    arma = get_armature(context)
    picker = get_picker(arma)
    if picker is None:
        layout.label(text="Select an imported armature")
        return
    row = layout.row(align=True)
    row.prop(picker, "insert_keyframes", text="Keyframes")
    row.prop(picker, "columns", text="Columns")
    if not picker.expressions:
        layout.label(text="No expressions yet.")
        layout.label(text="Use Setup in the Ultimate tab first.")
        return
    draw_expression_grid(layout, picker, picker.columns, scale=7.0)
    if picker.active_expression_id:
        active = find_expression(picker, picker.active_expression_id)
        if active is not None:
            layout.separator()
            layout.label(text=f"Active: {active.name}", icon="CHECKMARK")


def draw_face_picker_layout(layout, context, show_grid=True):
    arma = get_armature(context)
    picker = get_picker(arma)
    if arma is None or picker is None:
        layout.label(text="Select an armature to use Easy Facial Animation")
        return picker

    header = layout.row(align=True)
    header.operator(SUB_OP_face_picker_popup.bl_idname, icon="IMAGE_DATA", text="Open Expression Window")
    header.prop(picker, "insert_keyframes", text="Keyframes")

    if show_grid and picker.expressions:
        box = layout.box()
        row = box.row()
        row.prop(picker, "columns", text="Columns")
        draw_expression_grid(box, picker, picker.columns, scale=5.5)

    setup = layout.box()
    header_row = setup.row()
    header_row.prop(
        picker,
        "setup_expanded",
        icon="TRIA_DOWN" if picker.setup_expanded else "TRIA_RIGHT",
        icon_only=True,
        emboss=False,
    )
    header_row.label(text="Setup")
    if not picker.setup_expanded:
        return picker

    col = setup.column(align=True)
    col.prop(picker, "source_folder", text="Model Folder")
    row = col.row(align=True)
    row.operator(SUB_OP_face_picker_save.bl_idname, icon="FILE_TICK", text="Save TOML")
    row.operator(SUB_OP_face_picker_load.bl_idname, icon="FILE_REFRESH", text="Load TOML")

    col.separator()
    col.prop(picker, "camera_size", text="Capture Size")
    cam_row = col.row(align=True)
    cam_row.operator(SUB_OP_face_picker_create_camera.bl_idname, icon="CAMERA_DATA")
    cam_row.operator(SUB_OP_face_picker_view_camera.bl_idname, text="View")
    cam_row.operator(SUB_OP_face_picker_remove_camera.bl_idname, text="", icon="X")
    if picker.camera is not None:
        col.label(text=f"Camera: {picker.camera.name}", icon="CHECKMARK")
    else:
        col.label(text="No face camera yet", icon="INFO")

    col.separator()
    col.prop(picker, "new_expression_name", text="Name")
    col.label(text="Assigned Visibility Tracks")
    sap = get_sap(arma)
    if sap is None or len(sap.vis_track_entries) == 0:
        col.label(text="No vis tracks yet. Import an animation or auto-fill them first.", icon="INFO")
    elif len(picker.track_choices) != len(sap.vis_track_entries):
        col.label(text="Vis tracks changed. Refresh the list before assigning.", icon="ERROR")
    row = col.row()
    row.template_list(
        "SUB_UL_face_picker_track_choices",
        "",
        picker,
        "track_choices",
        picker,
        "track_choices_index",
        rows=8,
        maxrows=12,
    )
    track_col = row.column(align=True)
    track_col.operator(SUB_OP_face_picker_refresh_tracks.bl_idname, icon="FILE_REFRESH", text="")
    track_col.operator(SUB_OP_face_picker_match_enabled.bl_idname, icon="CHECKBOX_HLT", text="")
    track_col.operator(SUB_OP_face_picker_select_facial.bl_idname, icon="HIDE_OFF", text="")
    track_col.operator(SUB_OP_face_picker_clear_tracks.bl_idname, icon="X", text="")

    col.operator(SUB_OP_face_picker_preview.bl_idname, icon="HIDE_OFF")
    col.operator(SUB_OP_face_picker_add.bl_idname, icon="ADD")

    col.separator()
    col.label(text="Saved Expressions")
    row = col.row()
    row.template_list(
        "SUB_UL_face_picker_expressions",
        "",
        picker,
        "expressions",
        picker,
        "active_expression_index",
        rows=5,
        maxrows=8,
    )
    expr_col = row.column(align=True)
    expr_col.operator(SUB_OP_face_picker_remove.bl_idname, icon="REMOVE", text="")
    expr_col.operator(SUB_OP_face_picker_load_selected.bl_idname, icon="IMPORT", text="")
    expr_col.operator(SUB_OP_face_picker_recapture.bl_idname, icon="RENDER_STILL", text="")

    if picker.expressions and 0 <= picker.active_expression_index < len(picker.expressions):
        expr = picker.expressions[picker.active_expression_index]
        names = ", ".join(track.name for track in expr.tracks) or "(no tracks)"
        col.label(text=names)

    return picker


class SUB_PT_face_picker(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ultimate"
    bl_label = "Easy Facial Animation"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "POSE", "EDIT_ARMATURE"}

    def draw(self, context):
        draw_face_picker_layout(self.layout, context, show_grid=True)


class SUB_PT_face_picker_window(Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_label = "Easy Facial Animation"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _is_picker_screen(getattr(context, "screen", None))

    def draw(self, context):
        draw_popup_picker_layout(self.layout, context)


class SUB_PT_face_picker_anim_data(Panel):
    bl_label = "Easy Facial Animation"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "SUB_PT_sub_smush_anim_data_main"

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        return context.object.type == "ARMATURE"

    def draw(self, context):
        draw_face_picker_layout(self.layout, context, show_grid=False)
