"""Check Against Vanilla: flag PRM/NOR/matl values outside what vanilla fighters ship.

The reference ranges come from Sephiroth (edge), Pyra (eflame), Mythra (elight)
and Mario c00: every body texture set grouped by surface, PRM/NOR read as
p5 / median / p95 per channel over the texels the mesh covers. A material is
checked against the set for its surface (skin, hair, cloth, shiny, metal, eye).

The check reads the maps export reads (the matl Texture6 / Texture4 slots, the
side-loaded twin's when that is the one exported), else the newest bake of the
material, and samples them only where the material's mesh has UVs, so unused
texture space filled with defaults does not count.

A flag the user means (Dabi's CustomVector30.y 40-44 is a hard anime shadow, on
purpose) can be marked intended per material; it is then listed but greyed out.
"""
import json
import os

import bpy
import numpy as np
from bpy.types import Operator, Panel

from . import core
from . import smash_bridge

RESULT_KEY = "hb_vanilla_check"
INTENDED_KEY = "hb_vanilla_intended"
TEXT_NAME = "HB Vanilla Check"
SAMPLES = 200_000

# (low, high) of the median per channel, and the highest p95 vanilla reaches.
# rough = PRM.g, spec = PRM.a, metal = PRM.r (non-SSS only).
REFERENCE = {
    'SKIN':  {'rough': (0.59, 0.97), 'spec': (0.0, 0.16), 'spec_p95': 0.18,
              'cv30_x': (0.4, 0.7), 'cv30_y': (1.5, 4.0),
              'from': "Sephiroth skin_edge_001, Pyra/Mythra skin_flame, Mario skin_mario_001"},
    'HAIR':  {'rough': (0.5, 1.0), 'spec': (0.0, 0.21), 'spec_p95': 0.22,
              'from': "Sephiroth alp_edge, Pyra/Mythra alp_flame/alp_light, Mario alp_mario"},
    'CLOTH': {'rough': (0.37, 0.87), 'spec': (0.0, 0.16), 'spec_p95': 0.18, 'metal': 0.5,
              'from': "Sephiroth def_edge, Pyra/Mythra def_flame/def_light, Mario body"},
    'SHINY': {'rough': (0.25, 0.87), 'spec': (0.0, 0.21), 'spec_p95': 0.25, 'metal': 0.5,
              'from': "the glossier vanilla def_ sets (Mythra def_light_003, Mario body)"},
    'METAL': {'rough': (0.3, 0.85), 'spec': (0.0, 0.25), 'spec_p95': 0.3,
              'from': "vanilla metal parts (roughness 0.66-0.71 on Sephiroth/Mario)"},
    'EYE':   {'rough': (0.15, 0.35), 'spec': (0.1, 0.16), 'spec_p95': 0.17,
              'cv30_x': (0.4, 0.7), 'cv30_y': (1.5, 4.0),
              'from': "EyeL/EyeR on all four"},
}


# =============================================================================
# MAPS
# =============================================================================
def _slot_image(material, param):
    from .texturecraft import slot_image
    try:
        return slot_image(material, param)
    except Exception:
        return None


def _newest_bake(material, suffix):
    """The newest <stem><suffix>.png in the bake folder, or None."""
    props = getattr(bpy.context.scene, 'sub_bake_texs_properties', None)
    if props is None or not bpy.data.filepath:
        return None
    core.apply_settings(props)
    users = [o for o in bpy.context.view_layer.objects if o.type == 'MESH'
             and any(s.material == material for s in o.material_slots)]
    if not users:
        return None
    stem = core.make_stem(users[0], material)
    folder = core.BAKE_DIR
    if not os.path.isdir(folder):
        return None
    hits = [os.path.join(folder, n) for n in os.listdir(folder)
            if n.startswith(stem) and n.endswith(suffix + ".png")]
    return max(hits, key=os.path.getmtime) if hits else None


def map_pixels(material, param, suffix):
    """(H, W, 4) floats of a material's PRM or NOR and where they came from."""
    from .texturecraft import read_image, read_png
    image = _slot_image(material, param)
    if image is not None:
        px = read_image(image)
        if px is not None:
            return px, image.name
    path = _newest_bake(material, suffix)
    if path is not None:
        return read_png(path), os.path.basename(path)
    return None, None


def uv_samples(material, count=SAMPLES, seed=0):
    """Random UV points spread over the faces that use `material`, by UV area."""
    tris = []
    for obj in bpy.context.view_layer.objects:
        if obj.type != 'MESH':
            continue
        indices = [i for i, s in enumerate(obj.material_slots) if s.material == material]
        if not indices:
            continue
        mesh = obj.data
        uv = mesh.uv_layers.active
        if uv is None:
            continue
        mesh.calc_loop_triangles()
        n = len(mesh.loop_triangles)
        if n == 0:
            continue
        loops = np.empty(n * 3, np.int32)
        mesh.loop_triangles.foreach_get("loops", loops)
        mat_idx = np.empty(n, np.int32)
        mesh.loop_triangles.foreach_get("material_index", mat_idx)
        uvs = np.empty(len(uv.data) * 2, np.float32)
        uv.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)[loops].reshape(n, 3, 2)
        tris.append(uvs[np.isin(mat_idx, indices)])
    if not tris:
        return None
    tris = np.concatenate(tris)
    e1, e2 = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    area = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    if area.sum() <= 0:
        return None
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tris), size=count, p=area / area.sum())
    a, b = rng.random(count), rng.random(count)
    flip = a + b > 1
    a[flip], b[flip] = 1 - a[flip], 1 - b[flip]
    t = tris[pick]
    return t[:, 0] + a[:, None] * (t[:, 1] - t[:, 0]) + b[:, None] * (t[:, 2] - t[:, 0])


def sample(px, uv):
    """Nearest texel of a top-down image at each UV (wrapped)."""
    h, w = px.shape[:2]
    x = np.floor((uv[:, 0] % 1.0) * w).astype(np.int64).clip(0, w - 1)
    y = np.floor((1.0 - uv[:, 1] % 1.0) * h).astype(np.int64).clip(0, h - 1)
    return px[y, x]


def stats(values):
    p5, med, p95 = np.percentile(values, (5, 50, 95))
    return float(p5), float(med), float(p95)


# =============================================================================
# CHECK
# =============================================================================
def surface_of(material):
    from .master_shader import guess_preset, surface_kind
    node = smash_bridge.master_node(material)
    ctx = core.MatlContext(material)
    if node is not None:
        kind = surface_kind(material, node)
    else:
        kind = guess_preset(material.name)
    # A subsurface shader makes an unnamed surface skin; hair and eyes on one
    # (Dabi's hair, every vanilla eye) keep their own ranges.
    if ctx.is_subsurface and kind in ('CLOTH', 'SHINY', 'METAL'):
        kind = 'SKIN'
    return kind, ctx


def intended(material):
    raw = material.get(INTENDED_KEY, "")
    return {k for k in str(raw).split(",") if k}


def check_material(material):
    """List of {'id', 'level', 'text'} for one material."""
    kind, ctx = surface_of(material)
    ref = REFERENCE.get(kind)
    if ref is None:
        return kind, [{'id': 'skip', 'level': 'INFO',
                       'text': f"{kind.title()} - not compared (no vanilla PBR reference)"}]
    flags = []

    def flag(fid, level, text):
        flags.append({'id': fid, 'level': level, 'text': text})

    # matl data: subsurface strength / hardness.
    if ctx.is_subsurface:
        ref = dict(ref, cv30_x=REFERENCE['SKIN']['cv30_x'], cv30_y=REFERENCE['SKIN']['cv30_y'])
    if ctx.is_subsurface:
        data = smash_bridge.matl_data(ctx.data_source)
        cv30 = data.vectors.get('CustomVector30') if data is not None else None
        if cv30 is not None:
            x, y = float(cv30.value[0]), float(cv30.value[1])
            lo, hi = ref['cv30_x']
            if not lo - 0.05 <= x <= hi + 0.05:
                flag('cv30_x', 'WARN', f"CustomVector30.x (SSS blend) {x:.2f} - vanilla {lo}-{hi}")
            lo, hi = ref['cv30_y']
            if y > hi * 2.5:
                flag('cv30_y', 'WARN',
                     f"CustomVector30.y (SSS falloff) {y:.1f} - vanilla {lo}-{hi}; this high gives a "
                     f"hard cel-shaded terminator. Mark intended if that is the look")
            elif not lo - 0.1 <= y <= hi + 0.1:
                flag('cv30_y', 'WARN', f"CustomVector30.y (SSS falloff) {y:.2f} - vanilla {lo}-{hi}")

    uv = uv_samples(material)
    prm, prm_src = map_pixels(material, 'Texture6', '_prm')
    if prm is None:
        flag('no_prm', 'INFO', "No PRM yet (no Texture6 image, no bake) - bake first")
    elif uv is None:
        flag('no_uv', 'INFO', "No UVs on the meshes using it - maps not checked")
    else:
        s = sample(prm, uv)
        metal = s[:, 0] if not ctx.is_subsurface else np.zeros(len(s))
        rough, ao, spec = stats(s[:, 1]), stats(s[:, 2]), stats(s[:, 3])
        lo, hi = ref['rough']
        if rough[1] < lo - 0.03:
            flag('rough_low', 'WARN',
                 f"Roughness median {rough[1]:.2f} - vanilla {kind.lower()} {lo:.2f}-{hi:.2f}; "
                 f"reads wet/glossy in game")
        elif rough[1] > hi + 0.03:
            flag('rough_high', 'WARN',
                 f"Roughness median {rough[1]:.2f} - vanilla {kind.lower()} {lo:.2f}-{hi:.2f}; "
                 f"reads chalky with no highlight")
        if kind in ('SKIN', 'CLOTH', 'SHINY', 'METAL') and rough[2] - rough[0] < 0.02:
            flag('rough_flat', 'INFO',
                 f"Roughness is one flat value ({rough[1]:.2f}); vanilla body sets vary by up "
                 f"to 0.64 across the set. Add Surface Detail breaks it up")
        slo, shi = ref['spec']
        if spec[1] > shi + 0.03:
            flag('spec_high', 'WARN',
                 f"Specular median {spec[1]:.2f} (F0 {spec[1] * 0.2:.3f}) - vanilla "
                 f"{kind.lower()} {slo:.2f}-{shi:.2f}")
        elif spec[2] > ref['spec_p95'] + 0.08:
            flag('spec_peaks', 'WARN',
                 f"Specular p95 {spec[2]:.2f} - vanilla {kind.lower()} tops out near "
                 f"{ref['spec_p95']:.2f}")
        elif kind == 'EYE' and spec[1] < slo - 0.03:
            flag('spec_low', 'WARN', f"Specular median {spec[1]:.2f} - vanilla eyes {slo}-{shi}")
        if 'metal' in ref:
            m = stats(metal)
            if m[1] >= ref['metal']:
                flag('metal', 'WARN',
                     f"PRM.r (metalness) median {m[1]:.2f} on a {kind.lower()} surface - "
                     f"vanilla keeps cloth non-metal; metal parts go in their own material")
        elif kind == 'METAL' and not ctx.is_subsurface:
            m = stats(metal)
            if m[1] < 0.5:
                flag('not_metal', 'INFO', f"Named like metal but PRM.r median is {m[1]:.2f}")
        if kind != 'EYE' and ao[0] > 0.99:
            flag('ao_flat', 'INFO',
                 "PRM.b (AO) is 1.0 everywhere - vanilla p5 is 0.0-0.34 on body sets, so seams "
                 "and undersides stay lit in game")

    nor, _nor_src = map_pixels(material, 'Texture4', '_nor')
    if nor is not None and uv is not None:
        nor_s = sample(nor, uv)
        cav, trans = stats(nor_s[:, 3]), stats(nor_s[:, 2])
        if kind not in ('EYE', 'UNLIT') and trans[2] - trans[0] < 0.05:
            flag('transition_flat', 'INFO',
                 f"NOR.b (transition) is one flat value ({trans[1]:.2f}) - vanilla spreads it "
                 f"0.15-0.98, so a Metal Box or metal ditto sweeps across in patches instead of "
                 f"switching all at once. Add Transition Map makes one")
        if kind == 'SKIN' and cav[0] > 0.995:
            flag('cavity_flat', 'INFO',
                 "NOR.a (cavity) is 1.0 everywhere - Mario's and Sephiroth's skin darken "
                 "creases to 0.36-0.79. Add Surface Detail's Mesh Cavity bakes one from the mesh")
        if cav[1] < 0.5 and kind == 'CLOTH':
            flag('cavity_dark', 'WARN',
                 f"NOR.a (cavity) median {cav[1]:.2f} - vanilla cloth stays 0.95-1.0; the whole "
                 f"set loses specular")

    if prm is not None:
        flags.insert(0, {'id': 'source', 'level': 'SOURCE', 'text': f"PRM: {prm_src}"})
    return kind, flags


def target_materials(context):
    seen, out = set(), []
    objects = [o for o in context.selected_objects if o.type == 'MESH']
    if not objects:
        objects = [o for o in context.view_layer.objects if o.type == 'MESH']
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in seen or not mat.use_nodes:
                continue
            if mat.name.endswith(smash_bridge.SIDE_LOAD_SUFFIX):
                continue
            seen.add(mat.name)
            out.append(mat)
    return sorted(out, key=lambda m: m.name)


def run_check(context):
    results = []
    for mat in target_materials(context):
        kind, flags = check_material(mat)
        results.append({'material': mat.name, 'kind': kind, 'flags': flags})
    context.scene[RESULT_KEY] = json.dumps(results)
    write_text(results)
    return results


def write_text(results):
    text = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
    lines = ["HB Vanilla Check - vs Sephiroth, Pyra, Mythra, Mario c00", ""]
    for entry in results:
        mat = bpy.data.materials.get(entry['material'])
        ok = intended(mat) if mat else set()
        lines.append(f"{entry['material']}  [{entry['kind'].title()}]")
        ref = REFERENCE.get(entry['kind'])
        if ref:
            lines.append(f"    reference: {ref['from']}")
        real = [f for f in entry['flags'] if f['level'] in ('WARN', 'INFO')]
        if not real:
            lines.append("    within vanilla ranges")
        for f in entry['flags']:
            tag = 'intended' if f['id'] in ok else f['level'].lower()
            lines.append(f"    {tag:8} {f['text']}")
        lines.append("")
    text.from_string("\n".join(lines))


def load_results(scene):
    try:
        return json.loads(scene.get(RESULT_KEY, "[]"))
    except (TypeError, ValueError):
        return []


# =============================================================================
# OPERATORS / UI
# =============================================================================
class SUB_OP_hb_vanilla_check(Operator):
    """Compare the selected meshes' materials (all meshes if none are selected) with vanilla fighters"""
    bl_idname = 'sub.hb_vanilla_check'
    bl_label = 'Check Against Vanilla'
    bl_description = (
        'Compare each material\'s PRM, NOR and subsurface values with the same kind of surface '
        'on Sephiroth, Pyra, Mythra and Mario. Results show below and in the '
        f'"{TEXT_NAME}" text')

    def execute(self, context):
        results = run_check(context)
        warn = sum(1 for e in results for f in e['flags']
                   if f['level'] == 'WARN' and f['id'] not in
                   intended(bpy.data.materials.get(e['material'])))
        self.report({'WARNING' if warn else 'INFO'},
                    f"Vanilla check: {len(results)} material(s), {warn} warning(s)")
        return {'FINISHED'}


class SUB_OP_hb_vanilla_intended(Operator):
    """Mark this flag as intended for the material (or clear the mark)"""
    bl_idname = 'sub.hb_vanilla_intended'
    bl_label = 'Mark Intended'
    bl_options = {'REGISTER', 'UNDO'}

    material: bpy.props.StringProperty()
    flag: bpy.props.StringProperty()

    def execute(self, context):
        mat = bpy.data.materials.get(self.material)
        if mat is None:
            return {'CANCELLED'}
        marks = intended(mat)
        marks.symmetric_difference_update({self.flag})
        mat[INTENDED_KEY] = ",".join(sorted(marks))
        write_text(load_results(context.scene))
        return {'FINISHED'}


class SUB_PT_hb_vanilla_check(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash'
    bl_label = 'Vanilla Check'
    bl_parent_id = "SUB_PT_bake_texs"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.operator('sub.hb_vanilla_check', icon='VIEWZOOM')
        row = layout.row(align=True)
        row.operator_menu_enum('sub.hb_vanilla_match', 'character',
                               text='Match Fighter', icon='COMMUNITY')
        row.operator_menu_enum('sub.hb_vanilla_preset', 'preset',
                               text='Active Material', icon='PRESET')
        results = load_results(context.scene)
        if not results:
            layout.label(text='Not run yet', icon='INFO')
            return
        for entry in results:
            mat = bpy.data.materials.get(entry['material'])
            ok = intended(mat) if mat else set()
            flags = [f for f in entry['flags'] if f['level'] in ('WARN', 'INFO')]
            warn = [f for f in flags if f['level'] == 'WARN' and f['id'] not in ok]
            box = layout.box()
            head = box.row()
            head.label(text=f"{entry['material']}  ({entry['kind'].title()})",
                       icon='ERROR' if warn else 'CHECKMARK')
            for f in flags:
                row = box.row(align=True)
                done = f['id'] in ok
                sub = row.row()
                sub.active = not done
                sub.label(text=f['text'],
                          icon='CHECKBOX_HLT' if done else
                          ('ERROR' if f['level'] == 'WARN' else 'INFO'))
                if f['level'] == 'WARN' and mat is not None:
                    op = row.operator('sub.hb_vanilla_intended', text='',
                                      icon='LOOP_BACK' if done else 'CHECKMARK',
                                      emboss=False)
                    op.material = mat.name
                    op.flag = f['id']


# =============================================================================
# VANILLA PRESETS
# =============================================================================
# PRM values copied from one vanilla texture set each (median per channel).
# Variation and Mesh Cavity come from that set's p5-p95 spread, capped so
# noise does not stand in for painted detail. CustomVector30/11 are the
# material's SSS blend/falloff and colour, copied only when asked.
VANILLA_PRESETS = {
    'SEPH_SKIN':  dict(label="Sephiroth skin", kind='SKIN', src="edge skin_edge_001",
                       rough=0.867, spec=0.098, metal=0.0, sss=0.89, rough_var=0.5, spec_var=0.07,
                       cavity=0.21, cv30=(0.4, 1.5, 1.0, 1.0), cv11=(0.133, 0.067, 0.05, 1.0)),
    'PYRA_SKIN':  dict(label="Pyra / Mythra skin", kind='SKIN', src="eflame skin_flame_001",
                       rough=0.78, spec=0.098, metal=0.0, sss=1.0, rough_var=0.05, spec_var=0.0,
                       cavity=0.0, cv30=(0.5, 4.0, 1.0, 1.0), cv11=(0.37, 0.174, 0.112, 1.0)),
    'MARIO_SKIN': dict(label="Mario skin", kind='SKIN', src="mario skin_mario_001",
                       rough=0.718, spec=0.086, metal=0.0, sss=1.0, rough_var=0.12, spec_var=0.0,
                       cavity=0.64, cv30=(0.5, 1.5, 1.0, 1.0), cv11=(0.25, 0.033, 0.0, 1.0)),
    'SEPH_COAT':  dict(label="Sephiroth coat", kind='CLOTH', src="edge def_edge_001",
                       rough=0.663, spec=0.047, metal=0.0, sss=0.0, rough_var=0.5, spec_var=0.6,
                       cavity=0.16),
    'SEPH_METAL': dict(label="Sephiroth metal trim", kind='METAL', src="edge def_edge_002 (metal texels)",
                       rough=0.6, spec=0.16, metal=0.718, sss=0.0, rough_var=0.5, spec_var=0.0,
                       cavity=0.0),
    'PYRA_OUTFIT': dict(label="Pyra outfit", kind='CLOTH', src="eflame def_flame_001",
                        rough=0.435, spec=0.161, metal=0.0, sss=0.0, rough_var=0.5, spec_var=0.17,
                        cavity=0.0),
    'MYTHRA_OUTFIT': dict(label="Mythra outfit", kind='CLOTH', src="elight def_light_003",
                          rough=0.643, spec=0.157, metal=0.0, sss=0.0, rough_var=0.46, spec_var=0.08,
                          cavity=0.0),
    'MARIO_CLOTH': dict(label="Mario overalls & cap", kind='CLOTH', src="mario body",
                        rough=0.835, spec=0.161, metal=0.0, sss=0.0, rough_var=0.5, spec_var=0.6,
                        cavity=0.06),
    'SEPH_HAIR':  dict(label="Sephiroth hair", kind='HAIR', src="edge alp_edge_000",
                       rough=0.878, spec=0.059, metal=0.0, sss=0.0, rough_var=0.27, spec_var=0.11,
                       cavity=0.0),
    'PYRA_HAIR':  dict(label="Pyra / Mythra hair", kind='HAIR', src="eflame alp_flame_001",
                       rough=0.643, spec=0.161, metal=0.0, sss=0.0, rough_var=0.5, spec_var=0.12,
                       cavity=0.0),
    'MARIO_HAIR': dict(label="Mario hair & mustache", kind='HAIR', src="mario alp_mario_001",
                       rough=0.698, spec=0.063, metal=0.0, sss=0.0, rough_var=0.05, spec_var=0.11,
                       cavity=0.0),
    'SEPH_EYE':   dict(label="Sephiroth eye", kind='EYE', src="edge EyeL",
                       rough=0.337, spec=0.153, metal=0.0, sss=0.0, rough_var=0.0, spec_var=0.0,
                       cavity=0.0),
    'MARIO_EYE':  dict(label="Mario eye", kind='EYE', src="mario EyeL",
                       rough=0.165, spec=0.129, metal=0.0, sss=0.0, rough_var=0.0, spec_var=0.0,
                       cavity=0.0),
}

VANILLA_PRESET_ITEMS = tuple(
    (key, p['label'], f"PRM values from {p['src']}") for key, p in VANILLA_PRESETS.items())

# Which preset each surface takes when matching a whole character.
CHARACTERS = {
    'SEPHIROTH': {'SKIN': 'SEPH_SKIN', 'HAIR': 'SEPH_HAIR', 'CLOTH': 'SEPH_COAT',
                  'SHINY': 'SEPH_COAT', 'METAL': 'SEPH_METAL', 'EYE': 'SEPH_EYE'},
    'PYRA':      {'SKIN': 'PYRA_SKIN', 'HAIR': 'PYRA_HAIR', 'CLOTH': 'PYRA_OUTFIT',
                  'SHINY': 'PYRA_OUTFIT', 'METAL': 'SEPH_METAL', 'EYE': 'SEPH_EYE'},
    'MYTHRA':    {'SKIN': 'PYRA_SKIN', 'HAIR': 'PYRA_HAIR', 'CLOTH': 'MYTHRA_OUTFIT',
                  'SHINY': 'MYTHRA_OUTFIT', 'METAL': 'SEPH_METAL', 'EYE': 'SEPH_EYE'},
    'MARIO':     {'SKIN': 'MARIO_SKIN', 'HAIR': 'MARIO_HAIR', 'CLOTH': 'MARIO_CLOTH',
                  'SHINY': 'MARIO_CLOTH', 'METAL': 'SEPH_METAL', 'EYE': 'MARIO_EYE'},
}
CHARACTER_ITEMS = (
    ('SEPHIROTH', "Sephiroth", "Matte skin, low-spec coat and hair"),
    ('PYRA', "Pyra", "Softer skin falloff, glossier outfit and hair"),
    ('MYTHRA', "Mythra", "Pyra's skin and hair with Mythra's outfit"),
    ('MARIO', "Mario", "Rough cloth, crease cavity on the skin"),
)


def _set(node, name, value):
    sock = node.inputs.get(name)
    if sock is None or sock.is_linked:
        return False
    sock.default_value = value
    return True


def apply_vanilla_preset(material, key, detail=True, copy_sss=False):
    """Copy one vanilla set's PRM values onto a material's HB Master nodes.

    Only the physical values change; the toon look (shadow tint, rim, ...)
    stays as tuned. Returns a list of what changed, for the report.
    """
    from .master_shader import master_nodes
    preset = VANILLA_PRESETS[key]
    done = []
    for node in master_nodes(material):
        for name, value in (("Roughness", preset['rough']), ("Specular", preset['spec']),
                            ("Metalness", preset['metal']), ("SSS Mask", preset['sss'])):
            if _set(node, name, value):
                done.append(name)
        if detail:
            for name, value in (("Roughness Variation", preset['rough_var']),
                                ("Specular Variation", preset['spec_var']),
                                ("Mesh Cavity", preset['cavity'])):
                if _set(node, name, value):
                    done.append(name)
    if copy_sss and preset['kind'] == 'SKIN':
        ctx = core.MatlContext(material)
        data = smash_bridge.matl_data(ctx.data_source) if ctx.is_subsurface else None
        if data is not None:
            keep_y = 'cv30_y' in intended(material)
            for param in ('CustomVector30', 'CustomVector11'):
                entry = data.vectors.get(param)
                if entry is None:
                    continue
                value = list(preset['cv30' if param == 'CustomVector30' else 'cv11'])
                if param == 'CustomVector30' and keep_y:
                    value[1] = float(entry.value[1])
                entry.value = value
                done.append(param + (" (kept .y, marked intended)" if param == 'CustomVector30'
                                     and keep_y else ""))
    return sorted(set(done))


class SUB_OP_hb_vanilla_preset(Operator):
    """Copy a vanilla material's PRM values onto the active material's HB Master Shader"""
    bl_idname = 'sub.hb_vanilla_preset'
    bl_label = 'Apply Vanilla Material'
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(name='Vanilla Material', items=VANILLA_PRESET_ITEMS)
    detail: bpy.props.BoolProperty(
        name='Surface Detail', default=True,
        description='Also set roughness/specular variation and mesh cavity from the set\'s spread')
    copy_sss: bpy.props.BoolProperty(
        name='Copy SSS (CustomVector30/11)', default=False,
        description='Skin presets: also copy the subsurface blend, falloff and colour into the '
                    'material data. A CustomVector30.y marked intended is kept')

    @classmethod
    def poll(cls, context):
        from .master_shader import master_nodes
        obj = context.active_object
        return obj is not None and bool(master_nodes(obj.active_material))

    def execute(self, context):
        mat = context.active_object.active_material
        done = apply_vanilla_preset(mat, self.preset, self.detail, self.copy_sss)
        label = VANILLA_PRESETS[self.preset]['label']
        self.report({'INFO'}, f"{mat.name}: {label} ({', '.join(done) or 'nothing to set'})")
        return {'FINISHED'}


class SUB_OP_hb_vanilla_match(Operator):
    """Give every HB Master material on the selected meshes the PRM values of one vanilla fighter, surface by surface"""
    bl_idname = 'sub.hb_vanilla_match'
    bl_label = 'Match Vanilla Fighter'
    bl_options = {'REGISTER', 'UNDO'}

    character: bpy.props.EnumProperty(name='Fighter', items=CHARACTER_ITEMS)
    detail: bpy.props.BoolProperty(name='Surface Detail', default=True)
    copy_sss: bpy.props.BoolProperty(
        name='Copy SSS (CustomVector30/11)', default=False,
        description='Also copy skin subsurface values into the material data. A '
                    'CustomVector30.y marked intended is kept')

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        from .master_shader import master_nodes
        table = CHARACTERS[self.character]
        lines, seen = [], set()
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or mat.name in seen or not master_nodes(mat):
                    continue
                seen.add(mat.name)
                kind, _ctx = surface_of(mat)
                key = table.get(kind)
                if key is None:
                    lines.append(f"{mat.name}: {kind.title()} - left alone")
                    continue
                apply_vanilla_preset(mat, key, self.detail, self.copy_sss)
                lines.append(f"{mat.name}: {VANILLA_PRESETS[key]['label']}")
        for line in lines:
            print("  " + line)
        if not lines:
            self.report({'WARNING'}, "No HB Master materials on the selected meshes")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Matched {len(lines)} material(s) to {self.character.title()}")
        return {'FINISHED'}
