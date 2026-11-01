"""Round trip between the Smash materials and TextureCraft.

Send: the selected meshes go out as one OBJ with a texture set per Smash texture set,
and each set starts on a "Smash Base" fill layer holding the current COL/PRM/NOR/EMI, split
into TextureCraft's channels. Get: the running app (or the saved project, when it is closed)
exports through SMASH_PRESET, the maps are packed back into `<stem>_col/_nor/_prm/_emi.png` in
the Bake Textures folder and assigned to the same texture slots Apply Baked Textures writes,
so Export Model and Compile Existing Bakes pick them up like any bake.

Everything lives next to the .blend, in //texturecraft. The project there holds the user's
paint work, so nothing here replaces it without being asked to.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile

import bpy
import numpy as np
from bpy.props import EnumProperty
from bpy.types import Operator, Panel

from . import core
from .operators import _find_texture_slot, _load_or_refresh_image, apply_target_materials

PROJECT_FOLDER = "texturecraft"
PROJECT_NAME = "project.tcraft"
MESH_NAME = "mesh.obj"
MANIFEST_NAME = "manifest.json"
EXPORT_TIMEOUT = 600
PADDING_TEXELS = 8
BASE_LAYER = "Smash Base"

SLOT_COL, SLOT_NOR, SLOT_EMI, SLOT_PRM = 'Texture0', 'Texture4', 'Texture5', 'Texture6'
EXTRA_CHANNELS = ('ambientOcclusion', 'specularLevel', 'opacity', 'emissive')


def _ch(channel, component=0):
    return {"Channel": {"channel": channel, "component": component}}


def _nrm(component):
    return {"Virtual": {"map": "NormalOpenGl", "component": component}}


def _map(suffix, channels):
    return {"file": f"$textureSet_{suffix}", "format": "Png", "bit_depth": 8, "channels": channels}


# The channels PRM packs (metal, rough, AO, specular), the layout pack_prm and pack_nor write.
# AO goes through AoMixed so an AO bake done in TextureCraft lands in PRM.b. NOR is green-up,
# like Blender's bakes. `cov` is not a texture: constants are written on covered texels and
# dilated with them, so it is the mask of what the mesh actually reaches.
SMASH_PRESET = {
    "name": "Smash Ultimate",
    "maps": [
        _map("col", [["R", _ch("BaseColor", 0)], ["G", _ch("BaseColor", 1)],
                     ["B", _ch("BaseColor", 2)], ["A", _ch("Opacity")]]),
        _map("prm", [["R", _ch("Metallic")], ["G", _ch("Roughness")],
                     ["B", {"Virtual": {"map": "AoMixed", "component": 0}}],
                     ["A", _ch("SpecularLevel")]]),
        _map("nor", [["R", _nrm(0)], ["G", _nrm(1)], ["B", _nrm(2)]]),
        _map("emi", [["R", _ch("Emissive", 0)], ["G", _ch("Emissive", 1)],
                     ["B", _ch("Emissive", 2)]]),
        _map("cov", [["R", {"Constant": 1.0}], ["G", {"Constant": 1.0}], ["B", _ch("Metallic")]]),
    ],
}


# =============================================================================
# PATHS AND THE TWO PROGRAMS
# =============================================================================

def project_dir():
    return bpy.path.abspath(f"//{PROJECT_FOLDER}")


def project_path():
    return os.path.join(project_dir(), PROJECT_NAME)


def has_project():
    return os.path.isfile(project_path())


def load_manifest():
    with open(os.path.join(project_dir(), MANIFEST_NAME), encoding="utf-8") as f:
        return json.load(f)


def save_manifest(sets):
    data = {
        "sets": [{"name": s["name"], "stem": s["stem"],
                  "groups": [[m.name for m in g] for g in s["groups"]]} for s in sets],
    }
    with open(os.path.join(project_dir(), MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def find_program(name):
    """A desktop-launched Blender often has no ~/.local/bin on its PATH."""
    found = shutil.which(name) or shutil.which(name, path=os.path.expanduser("~/.local/bin"))
    if not found:
        raise RuntimeError(f"{name} not found - is TextureCraft installed?")
    return found


def run_batch(script):
    folder = project_dir()
    path = os.path.join(folder, "batch.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(script, f)
    result = subprocess.run([find_program("texturecraft-cli"), "batch", path],
                            capture_output=True, text=True, timeout=EXPORT_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError("texturecraft-cli: " + (result.stderr or result.stdout).strip()[-400:])


class Control:
    """The app's control channel (docs/control-protocol.md): one JSON object per line."""

    def __init__(self, port):
        token_file = os.path.join(tempfile.gettempdir(), f"texturecraft-control-{port}.token")
        with open(token_file, encoding="utf-8") as f:
            token = f.read().strip()
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.sock.settimeout(EXPORT_TIMEOUT)
        self.stream = self.sock.makefile("rw", encoding="utf-8")
        self.next_id = 0
        self.call("auth", {"token": token})

    def call(self, method, params=None):
        self.next_id += 1
        self.stream.write(json.dumps({"id": self.next_id, "method": method,
                                      "params": params or {}}) + "\n")
        self.stream.flush()
        reply = self.stream.readline()
        if not reply:
            raise RuntimeError("TextureCraft closed the control connection")
        reply = json.loads(reply)
        if not reply.get("ok"):
            raise RuntimeError(f"TextureCraft: {reply.get('error')}")
        return reply.get("result")

    def run(self, command, params=None):
        return self.call("command.run", {"id": command, "params": params or {}})

    def open_project(self):
        return self.run("project.inspect").get("path")

    def close(self):
        self.sock.close()


def connect(port):
    """The running app, or None when it isn't up on this port."""
    try:
        return Control(port)
    except (OSError, ValueError, RuntimeError):
        return None


def same_file(a, b):
    try:
        return bool(a and b) and os.path.samefile(a, b)
    except OSError:
        return False


def launch(port):
    subprocess.Popen([find_program("texturecraft"), "--control", str(port), project_path()],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# =============================================================================
# TEXTURE SETS
# =============================================================================

def slot_image(material, param):
    slot = _find_texture_slot(material, param)
    return slot.image if slot is not None else None


def collect_sets():
    """One TextureCraft texture set per texture the game sees.

    Materials are merged the way Bake Textures bundles them, and then again when they read
    the same COL/NOR/PRM: Smash models share one atlas across materials all the time, and two
    sets would mean painting that one texture twice and the second export undoing the first.
    """
    groups, _ = core.collect_targets()
    merged = {}
    for key, group in groups.items():
        base = group["materials"][0]
        col = slot_image(base, SLOT_COL)
        signature = (col, slot_image(base, SLOT_NOR), slot_image(base, SLOT_PRM)) if col else key
        entry = merged.setdefault(signature, {
            "stem": core.make_stem(group["objects"][0], base), "groups": [], "objects": []})
        entry["groups"].append(group["materials"])
        entry["objects"].extend(o for o in group["objects"] if o not in entry["objects"])

    sets = list(merged.values())
    taken = set()
    for entry in sets:
        # TextureCraft rewrites material names it doesn't like, so it gets ones it won't touch.
        name = "".join(c if c.isalnum() or c in "_-" else "_" for c in entry["stem"])
        while name in taken:
            name += "_"
        taken.add(name)
        entry["name"] = name
    return sets


def uv_name(mesh):
    return "map1" if "map1" in mesh.uv_layers else mesh.uv_layers.active.name


def export_mesh(sets, path):
    """Write the meshes with one OBJ material per texture set, on map1, rest pose."""
    material_for = {}
    temp_materials = []
    for entry in sets:
        temp = bpy.data.materials.new(entry["name"])
        temp_materials.append(temp)
        for materials in entry["groups"]:
            for material in materials:
                material_for[material.name] = temp
    unused = bpy.data.materials.new("_unused")
    temp_materials.append(unused)

    collection = bpy.data.collections.new("__texturecraft_export")
    bpy.context.scene.collection.children.link(collection)
    temp_objects = []
    temp_meshes = []
    try:
        seen = []
        for entry in sets:
            seen.extend(o for o in entry["objects"] if o not in seen)
        for obj in seen:
            if not obj.data.uv_layers:
                raise RuntimeError(f"{obj.name} has no UV map")
            mesh = obj.data.copy()
            temp_meshes.append(mesh)
            for i, slot in enumerate(obj.material_slots):
                name = slot.material.name if slot.material else ""
                mesh.materials[i] = material_for.get(name, unused)
            uv = mesh.uv_layers[uv_name(obj.data)]
            mesh.uv_layers.active = uv
            uv.active_render = True
            temp = bpy.data.objects.new(obj.name, mesh)
            temp.matrix_world = obj.matrix_world
            temp_objects.append(temp)
            collection.objects.link(temp)

        bpy.context.view_layer.update()
        bpy.ops.wm.obj_export(
            filepath=path, collection=collection.name, export_selected_objects=False,
            export_animation=False, export_uv=True, export_normals=True, export_colors=False,
            export_materials=True, export_triangulated_mesh=True, apply_modifiers=False,
            forward_axis='NEGATIVE_Z', up_axis='Y')
    finally:
        for obj in temp_objects:
            bpy.data.objects.remove(obj)
        for mesh in temp_meshes:
            bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)
        for material in temp_materials:
            bpy.data.materials.remove(material)


# =============================================================================
# PIXELS
# =============================================================================

def read_image(image):
    """(H, W, 4) top-down floats as stored in the file, or None for an image with no pixels."""
    if image is None or image.size[0] == 0 or image.size[1] == 0:
        return None
    width, height = image.size
    px = np.empty(width * height * 4, dtype=np.float32)
    try:
        image.pixels.foreach_get(px)
    except (RuntimeError, ValueError):
        return None
    px = px.reshape(height, width, 4)[::-1].copy()
    if image.is_float and image.colorspace_settings.name != "Non-Color":
        from ..extras.smash_vp_hb import _linear_to_srgb
        px[..., :3] = _linear_to_srgb(np.clip(px[..., :3], 0.0, 1.0))
    return px


def read_png(path):
    image = bpy.data.images.load(path, check_existing=False)
    try:
        core.set_colorspace(image, "Non-Color")
        return read_image(image)
    finally:
        bpy.data.images.remove(image)


def write_png(path, px):
    from ..extras.smash_vp_hb import _encode_png
    _encode_png(path, (np.clip(px, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))


def resized(px, width, height):
    from ..extras.smash_vp_hb import _resize
    return _resize(px, width, height)


def gray(values):
    return np.dstack([values, values, values, np.ones_like(values)])


def write_seeds(entry, folder):
    """Split a set's textures into one PNG per channel. Returns {channel: path}, and the size."""
    base = entry["groups"][0][0]
    prefix = os.path.join(folder, entry["name"])
    seeds = {}

    def seed(channel, px):
        path = f"{prefix}_{channel}.png"
        write_png(path, px)
        seeds[channel] = path

    col = read_image(slot_image(base, SLOT_COL) or shader_image(base, "Base Color"))
    if col is not None:
        seed("baseColor", np.dstack([col[..., :3], np.ones(col.shape[:2], np.float32)]))
        seed("opacity", gray(col[..., 3]))

    prm = read_image(slot_image(base, SLOT_PRM))
    if prm is not None:
        for channel, index in (("metallic", 0), ("roughness", 1),
                               ("ambientOcclusion", 2), ("specularLevel", 3)):
            seed(channel, gray(prm[..., index]))

    nor = read_image(slot_image(base, SLOT_NOR))
    if nor is not None:
        # NOR.b is the material transition blend and NOR.a the cavity map, not Z.
        x, y = nor[..., 0] * 2.0 - 1.0, nor[..., 1] * 2.0 - 1.0
        z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
        seed("normal", np.dstack([nor[..., 0], nor[..., 1], z * 0.5 + 0.5,
                                  np.ones_like(z)]))

    emi = read_image(slot_image(base, SLOT_EMI))
    if emi is not None:
        seed("emissive", np.dstack([emi[..., :3], np.ones(emi.shape[:2], np.float32)]))

    sizes = [max(px.shape[:2]) for px in (col, prm, nor) if px is not None]
    return seeds, max(sizes) if sizes else 0


def power_of_two(size):
    return int(np.clip(2 ** round(np.log2(max(size, 1))), 128, 8192))


def seed_steps(index, seeds, resolution):
    """The steps that give texture set `index` its channels and its Smash Base layer."""
    steps = [{"id": "textureSet.select", "params": {"ts": index}},
             {"id": "textureSet.resolution", "params": {"ts": index, "resolution": resolution}}]
    for channel in EXTRA_CHANNELS:
        steps.append({"id": "textureSet.channel.add", "params": {"ts": index, "channel": channel}})
    steps.append({"id": "layer.add.fill", "params": {"name": BASE_LAYER}})

    metal, rough, ao, spec = core.PRM_DEFAULT
    uniform = {"metallic": metal, "roughness": rough, "ambientOcclusion": ao,
               "specularLevel": spec, "opacity": 1.0, "emissive": [0.0, 0.0, 0.0]}
    for channel, path in seeds.items():
        steps.append({"id": "resource.import", "params": {"path": path}})
        steps.append({"id": "layer.fill.set",
                      "params": {"channel": channel, "image": os.path.basename(path)}})
    for channel, value in uniform.items():
        if channel not in seeds:
            steps.append({"id": "layer.fill.set", "params": {"channel": channel, "value": value}})
    steps.append({"id": "layer.move", "params": {"delta": -1}})
    return steps


def build_project(sets, resolution):
    """A new project: the mesh, then each set seeded from the current textures."""
    folder = project_dir()
    seed_dir = os.path.join(folder, "seed")
    shutil.rmtree(seed_dir, ignore_errors=True)
    os.makedirs(seed_dir)
    mesh_path = os.path.join(folder, MESH_NAME)
    export_mesh(sets, mesh_path)

    steps = [{"id": "file.new", "params": {
        "mesh": mesh_path, "resolution": 2048, "uvTiles": False,
        "name": os.path.splitext(os.path.basename(bpy.data.filepath))[0]}}]
    for index, entry in enumerate(sets):
        seeds, size = write_seeds(entry, seed_dir)
        steps.extend(seed_steps(index, seeds, resolution or power_of_two(size or 2048)))
    run_batch({"steps": steps, "save": project_path()})


def update_project_mesh(sets, control):
    """Swap the project's mesh, keeping what was painted on it."""
    mesh_path = os.path.join(project_dir(), MESH_NAME)
    export_mesh(sets, mesh_path)
    step = {"path": mesh_path, "preserveStrokes": True}
    if control is not None:
        control.run("file.reimportMesh", step)
        control.run("file.save")
    else:
        run_batch({"open": project_path(),
                   "steps": [{"id": "file.reimportMesh", "params": step}],
                   "save": project_path()})


def export_textures(port, report):
    """Export every set to //texturecraft/export, from the app when it has this project open."""
    folder = os.path.join(project_dir(), "export")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    params = {"dir": folder, "preset": SMASH_PRESET, "ts": "all",
              "padding": "dilation", "dilation": PADDING_TEXELS}

    control = connect(port)
    if control is not None:
        try:
            if same_file(control.open_project(), project_path()):
                control.run("file.save")
                control.run("file.exportTextures", params)
                return folder
            report({'WARNING'}, "TextureCraft has another project open - "
                                "reading the saved project instead")
        finally:
            control.close()

    run_batch({"open": project_path(),
               "steps": [{"id": "file.exportTextures", "params": params}]})
    return folder


# =============================================================================
# BACK INTO THE MATERIALS
# =============================================================================

def merged_map(tc, covered, current, default):
    """TextureCraft where the mesh reaches, the texture as it was everywhere else.

    A texture is usually bigger than what was sent: a hidden expression mesh, or one that
    wasn't selected, reads the same atlas, and the export has nothing for those texels.
    """
    height, width = tc.shape[:2]
    if current is None:
        below = np.empty_like(tc)
        below[...] = default
    else:
        below = resized(current, width, height)
    return np.where(covered[..., None], tc, below)


def pack_set(entry, folder, out_dir):
    """Write this set's Smash maps to out_dir. Returns {suffix: path}, and any notes."""
    base = entry["groups"][0][0]
    source = os.path.join(folder, entry["name"])
    if not os.path.isfile(f"{source}_cov.png"):
        return {}, [f"{entry['name']} is not in the TextureCraft project - use Update Mesh"]
    covered = read_png(f"{source}_cov.png")[..., 0] > 0.5
    height, width = covered.shape
    out, notes = {}, []

    def write(suffix, px):
        path = os.path.join(out_dir, f"{entry['stem']}{suffix}.png")
        write_png(path, px)
        out[suffix] = path

    write("_col", merged_map(read_png(f"{source}_col.png"), covered,
                             read_image(slot_image(base, SLOT_COL)), core.COL_DEFAULT))
    write("_prm", merged_map(read_png(f"{source}_prm.png"), covered,
                             read_image(slot_image(base, SLOT_PRM)), core.PRM_DEFAULT))

    current_nor = read_image(slot_image(base, SLOT_NOR))
    nor = merged_map(read_png(f"{source}_nor.png"), covered, current_nor, core.NOR_DEFAULT)
    # Only RG is a normal. The texture's B and A aren't anything TextureCraft has.
    if current_nor is not None:
        nor[..., 2:] = resized(current_nor, width, height)[..., 2:]
    else:
        nor[..., 2] = core.NOR_BLUE_CONST
        nor[..., 3] = core.NOR_ALPHA_CONST
    write("_nor", nor)

    emi = read_png(f"{source}_emi.png")
    if _find_texture_slot(base, SLOT_EMI) is not None:
        write("_emi", merged_map(emi, covered, read_image(slot_image(base, SLOT_EMI)),
                                 core.EMI_DEFAULT))
    elif emi[..., :3].max() > 0.0:
        notes.append(f"{base.name}: emissive was painted, but the material has no "
                     f"{SLOT_EMI} slot - apply the Emissive preset to use it")
    return out, notes


def upstream_image_nodes(socket_, depth=0, found=None):
    """Every Image Texture feeding an input, through whatever sits in between."""
    found = [] if found is None else found
    if depth > 6:
        return found
    for link in socket_.links:
        node = link.from_node
        if node.type == 'TEX_IMAGE':
            if node not in found:
                found.append(node)
        else:
            for upstream in node.inputs:
                if upstream.is_linked:
                    upstream_image_nodes(upstream, depth + 1, found)
    return found


def main_shader(material):
    """The HB Master or Principled node this material renders through, or None."""
    from ..extras.smash_vp_hb import master_node
    node = master_node(material)
    if node is not None or not material.use_nodes:
        return node
    output = core.get_output_node(material)
    surface = output.inputs.get("Surface") if output else None
    if surface is not None and surface.is_linked:
        node = surface.links[0].from_node
        if node.type == 'BSDF_PRINCIPLED':
            return node
    return None


def shader_image(material, input_name):
    shader = main_shader(material)
    sock = shader.inputs.get(input_name) if shader else None
    nodes = upstream_image_nodes(sock) if sock else []
    return nodes[0].image if len(nodes) == 1 else None


def _named_node(tree, idname, name, location):
    node = tree.nodes.get(name)
    if node is None:
        node = tree.nodes.new(idname)
        node.name = name
        node.location = location
    return node


def show_on_shader(material, images, notes):
    """Put the maps on the shader too, for materials rendering through HB Master or Principled.

    The Smash slots are what export reads; the shader is what the viewport shows and what the
    next bake reads, so a material whose shader kept the old images would look unchanged and
    bake the paint away. Images already wired in are swapped, unwired inputs get wired.
    """
    shader = main_shader(material)
    if shader is None:
        return
    tree = material.node_tree
    x, y = shader.location[0] - 900, shader.location[1]

    def image_node(suffix, offset):
        node = _named_node(tree, 'ShaderNodeTexImage', f"TC{suffix}", (x, y + offset))
        node.image = images[suffix]
        uv = _named_node(tree, 'ShaderNodeUVMap', f"TC{suffix}_uv", (x - 250, y + offset))
        uv.uv_map = "map1"
        if not node.inputs["Vector"].is_linked:
            tree.links.new(uv.outputs["UV"], node.inputs["Vector"])
        return node

    def place(input_name, suffix, offset, wire):
        sock = shader.inputs.get(input_name)
        if sock is None or suffix not in images:
            return
        wired = upstream_image_nodes(sock)
        if len(wired) == 1:
            wired[0].image = images[suffix]
        elif sock.is_linked:
            notes.append(f"{material.name}: {input_name} goes through several textures - "
                         f"left as it is")
        else:
            wire(image_node(suffix, offset), sock)

    def separate():
        node = _named_node(tree, 'ShaderNodeSeparateColor', "TC_prm_split", (x + 300, y - 300))
        if not node.inputs[0].is_linked:
            tree.links.new(image_node("_prm", -300).outputs["Color"], node.inputs[0])
        return node

    def normal_wire(node, sock):
        normal_map = _named_node(tree, 'ShaderNodeNormalMap', "TC_nor_map", (x + 300, y - 600))
        normal_map.uv_map = "map1"
        tree.links.new(node.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], sock)

    place("Base Color", "_col", 0, lambda n, s: tree.links.new(n.outputs["Color"], s))
    for input_name, channel in (("Metalness", "Red"), ("Metallic", "Red"), ("Roughness", "Green")):
        place(input_name, "_prm", -300,
              lambda n, s, c=channel: tree.links.new(separate().outputs[c], s))
    place("Specular", "_prm", -300, lambda n, s: tree.links.new(n.outputs["Alpha"], s))
    place("Normal", "_nor", -600, normal_wire)


def apply_set(entry, paths, notes):
    """Assign a set's maps to every material the textures belong to. Returns how many landed."""
    images = {}
    for suffix, colorspace in (("_col", "sRGB"), ("_nor", "Non-Color"),
                               ("_prm", "Non-Color"), ("_emi", "sRGB")):
        if suffix in paths:
            image = _load_or_refresh_image(paths[suffix], f"{entry['stem']}{suffix}", colorspace)
            if colorspace == "Non-Color":
                image.alpha_mode = 'CHANNEL_PACKED'
            images[suffix] = image

    applied = 0
    for materials in entry["groups"]:
        for material in apply_target_materials(materials[0], 'BOTH'):
            for suffix, param in (("_col", SLOT_COL), ("_nor", SLOT_NOR),
                                  ("_prm", SLOT_PRM), ("_emi", SLOT_EMI)):
                slot = _find_texture_slot(material, param)
                if slot is None or suffix not in images:
                    continue
                slot.image = images[suffix]
                applied += 1
            show_on_shader(material, images, notes)
    return applied


def sets_from_manifest():
    sets, missing = [], []
    for record in load_manifest()["sets"]:
        groups = []
        for names in record["groups"]:
            materials = [bpy.data.materials.get(n) for n in names]
            missing.extend(n for n, m in zip(names, materials) if m is None)
            materials = [m for m in materials if m is not None]
            if materials:
                groups.append(materials)
        if groups:
            sets.append({"name": record["name"], "stem": record["stem"], "groups": groups})
    return sets, missing


# =============================================================================
# OPERATORS AND PANEL
# =============================================================================

class SUB_OP_texturecraft_send(Operator):
    """Open the selected Smash meshes in TextureCraft"""
    bl_idname = 'sub.texturecraft_send'
    bl_label = 'Send To TextureCraft'
    bl_description = (
        'Open the selected meshes in TextureCraft, with one texture set per Smash texture '
        'and the current COL/PRM/NOR/EMI as a base layer to paint over. '
        'Uses the naming settings from Bake Textures'
    )
    bl_options = {'REGISTER'}

    mode: EnumProperty(
        name='Project',
        description='What to do with the TextureCraft project this .blend already has',
        items=(
            ('OPEN', 'Keep Painting',
             'Open the project as it is. Nothing is sent'),
            ('UPDATE', 'Update Mesh',
             'Send the meshes again and keep the painted work on them. '
             'Use after editing the mesh or its UVs'),
            ('NEW', 'Start Over',
             'Rebuild the project from the current textures. '
             'The old one is kept as project.tcraft.bak'),
        ),
        default='OPEN',
    )

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.selected_objects

    def invoke(self, context, event):
        if has_project():
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.label(text='This .blend already has a TextureCraft project.')
        self.layout.prop(self, 'mode', expand=True)

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)
        os.makedirs(project_dir(), exist_ok=True)
        port = props.tc_port
        existed = has_project()
        control = connect(port)
        try:
            opened = control.open_project() if control is not None else None
            if opened and not same_file(opened, project_path()):
                raise RuntimeError("TextureCraft has another project open - close it first")
            if not existed or self.mode != 'OPEN':
                sets = collect_sets()
                if existed and self.mode == 'UPDATE':
                    update_project_mesh(sets, control)
                else:
                    if existed:
                        shutil.copy2(project_path(), project_path() + ".bak")
                    build_project(sets, int(props.tc_resolution))
                    if control is not None:
                        control.run("file.open", {"path": project_path()})
                save_manifest(sets)
                message = f"Sent {len(sets)} texture set(s) to TextureCraft"
            else:
                message = "Opened the TextureCraft project"
            if control is None:
                launch(port)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            if control is not None:
                control.close()
        self.report({'INFO'}, message)
        return {'FINISHED'}


class SUB_OP_texturecraft_get(Operator):
    """Bring the TextureCraft textures back onto the Smash materials"""
    bl_idname = 'sub.texturecraft_get'
    bl_label = 'Get From TextureCraft'
    bl_description = (
        'Save the TextureCraft project, pack its textures into COL/NOR/PRM/EMI in the Bake '
        'Textures folder and assign them to the materials and their side-loaded copies. '
        'Overwrites PNGs of the same name there. Works with TextureCraft closed too, '
        'from the last save'
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and has_project()

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)
        try:
            sets, missing = sets_from_manifest()
            if not sets:
                raise RuntimeError("None of the sent materials are in this .blend")
            folder = export_textures(props.tc_port, self.report)
            core.ensure_dir(core.BAKE_DIR)
            applied = 0
            notes = [f"material {n} is gone - skipped" for n in missing]
            for entry in sets:
                paths, set_notes = pack_set(entry, folder, core.BAKE_DIR)
                notes.extend(set_notes)
                applied += apply_set(entry, paths, notes)
        except (RuntimeError, OSError, ValueError, KeyError,
                subprocess.SubprocessError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        message = f"Applied {applied} texture(s) from {len(sets)} set(s)"
        if notes:
            message += ". " + "; ".join(dict.fromkeys(notes))
        self.report({'WARNING'} if notes else {'INFO'}, message)
        return {'FINISHED'}


class SUB_PT_texturecraft(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash'
    bl_label = 'TextureCraft'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        if not context.blend_data.filepath:
            layout.label(text='Save the .blend first.', icon='ERROR')
            return
        props = context.scene.sub_bake_texs_properties

        col = layout.column()
        col.scale_y = 1.4
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator('sub.texturecraft_send', icon='EXPORT')
        col.operator('sub.texturecraft_get', icon='IMPORT')

        layout.prop(props, 'tc_resolution')
        layout.prop(props, 'tc_port')
        layout.label(text='Compile with Bake Textures when done', icon='INFO')
