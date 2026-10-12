# =============================================================================
# HB Hair Strands - painted-looking hair strands for the HB Master Shader
#
# Vanilla Smash hair (Sephiroth's alp_edge_*, Cloud, Marth...) is a flat albedo
# with soft light and dark streaks running root to tip, and a NOR map of clump
# ridges with fine lines on top. This group makes both procedurally so they
# bake: Color goes into the master's Base Color (-> _col) and Normal into its
# Normal (-> _nor).
#
# The streaks need a direction to run along, and that is the whole problem:
#
#   RADIAL (default)  Strands fan out from a Root point at the crown, as lines
#                     of constant compass angle around the object's Z axis. No
#                     UV layout needed, so it works on game rips whose hair is
#                     auto-unwrapped into randomly turned islands (All's
#                     Justice), and nothing shows at the UV seams.
#   UV                Strands run along V. For hair whose UVs are combed
#                     straight, the way vanilla Smash hair is laid out.
#
# Object coordinates follow the armature, so bake in the rest pose.
#
# Built from code like the master shader, and rebuilt in place the same way:
# sockets are matched by name, so every material keeps its own values.
# =============================================================================

import bpy
from bpy.types import Operator
from mathutils import Vector

from .master_shader import _S, F, C, V, _set_socket_props, _Graph, _inp, _out, master_nodes

HAIR_NAME = "HB Hair Strands"
HAIR_VERSION = 1
VERSION_KEY = "hb_hair_strands_version"

INTERFACE = [
    (None, False, [
        _S("Base Color", C, (0.5, 0.5, 0.5, 1.0),
           desc="The hair color under the strands - a flat color, a root-to-tip ramp or a texture"),
        _S("Strand Count", F, 400.0, 1.0, 4000.0,
           desc="Strands around the head (Radial), or across the 0-1 UV square (UV)"),
        _S("Seed", F, 0.0, -1000.0, 1000.0, desc="Another arrangement of the same strands"),
    ]),
    ("Strand Color", False, [
        _S("Dark Strands", F, 0.25, 0.0, 1.0, True, "How far the gaps between strands darken"),
        _S("Light Strands", F, 0.15, 0.0, 1.0, True, "How much of Light Strand Color the raised strands take"),
        _S("Light Strand Color", C, (0.8, 0.8, 0.8, 1.0),
           desc="Vanilla keeps this close to the hair color, a little lighter and greyer"),
        _S("Clump Shading", F, 0.2, 0.0, 1.0, True, "Darkens the valleys between clumps of strands"),
    ]),
    ("Strand Shape", False, [
        _S("Clump Size", F, 8.0, 1.0, 100.0, desc="Strands per clump"),
        _S("Length Variation", F, 0.15, 0.0, 10.0,
           desc="How soon a strand fades into the next along its length. 0 = every strand "
                "runs the whole way"),
        _S("Waviness", F, 0.3, 0.0, 2.0, desc="Bends the strands off a straight line"),
        _S("Wave Scale", F, 0.5, 0.0, 100.0, desc="Size of the bends: lower is longer, lazier waves"),
    ]),
    ("Strand Normal", False, [
        _S("Bump Strength", F, 0.25, 0.0, 1.0, True, "Baked into _nor through the master's Normal"),
        _S("Bump Distance", F, 0.03, 0.0, 1000.0,
           desc="Depth of the grooves in scene units - under a percent of the head's size"),
        _S("Clump Bump", F, 0.8, 0.0, 1.0, True,
           "Share of the height that comes from clumps rather than single strands"),
        _S("Normal", V, (0.0, 0.0, 0.0), hide=True,
           desc="An existing Normal Map or Bump to add the strands on top of"),
    ]),
    ("Strand Flow", False, [
        _S("Root", V, (0.0, 0.0, 0.0), -100000.0, 100000.0,
           desc="Object-space point at the crown that the strands fan out from. "
                "Add Hair Strands sets it to the top of the mesh"),
        _S("Crown Fade", F, 0.3, 0.0, 1000.0,
           desc="Strands fade out this close to the Root's vertical axis, where they "
                "would all pinch together"),
        _S("Use UV", F, 0.0, 0.0, 1.0, True,
           "0 = Radial from Root, 1 = along the UV map, for hair with combed UVs"),
        _S("UV Angle", F, 0.0, -360.0, 360.0,
           desc="Degrees. 0 = strands run along V, 90 = along U"),
    ]),
]
OUTPUTS = [
    ("Color", C, "Base Color with the strands painted in. Into the master's Base Color"),
    ("Normal", V, "Into the master's Normal"),
    ("Height", F, "The strand height field the Normal is bumped from"),
    ("Strands", F, "0 in the gaps, 1 on the strands"),
]


def _ensure_interface(ng):
    """Same keep-what-exists rule as the master shader's, with several outputs."""
    iface = ng.interface
    sockets = {(it.in_out, it.name): it for it in iface.items_tree if it.item_type == 'SOCKET'}
    panels = {it.name: it for it in iface.items_tree if it.item_type == 'PANEL'}
    keep = set()

    def socket(name, in_out, kind, parent=None):
        item = sockets.get((in_out, name))
        if item is not None and item.socket_type != kind:
            iface.remove(item)
            item = None
        if item is None:
            item = iface.new_socket(name=name, in_out=in_out, socket_type=kind, parent=parent)
        keep.add(item.identifier)
        return item

    order = []
    root = None
    for name, kind, desc in OUTPUTS:
        item = socket(name, 'OUTPUT', kind)
        item.description = desc
        root = item.parent
        order.append((item, root))

    for panel_name, closed, specs in INTERFACE:
        parent = root
        if panel_name is not None:
            parent = panels.get(panel_name) or iface.new_panel(panel_name)
            parent.default_closed = closed
            keep.add(("PANEL", panel_name))
        for spec in specs:
            item = socket(spec.name, 'INPUT', spec.kind, parent if panel_name else None)
            _set_socket_props(item, spec)
            order.append((item, parent))
        if panel_name is not None:
            order.append((parent, root))

    for it in list(iface.items_tree):
        if it.item_type == 'SOCKET' and it.identifier not in keep:
            iface.remove(it)
        elif it.item_type == 'PANEL' and ("PANEL", it.name) not in keep:
            iface.remove(it)

    position = {}
    for item, parent in order:
        key = parent.as_pointer() if parent is not None else 0
        index = position.get(key, 0)
        try:
            iface.move_to_parent(item, parent, index)
        except Exception:
            pass
        position[key] = index + 1


def _build_nodes(ng):
    ng.nodes.clear()
    g = _Graph(ng)

    f_flow = g.frame("FLOW  -  the coordinate the strands run along", (0.20, 0.26, 0.34))
    f_strand = g.frame("STRANDS  -  stretched noise", (0.22, 0.22, 0.32))
    f_col = g.frame("COLOR  -  into the master's Base Color", (0.20, 0.30, 0.20))
    f_nor = g.frame("NORMAL  -  into the master's Normal", (0.35, 0.25, 0.15))

    gi_flow = g.add("NodeGroupInput", -2600, 0, frame=f_flow)
    gi_strand = g.add("NodeGroupInput", -700, -500, frame=f_strand)
    gi_col = g.add("NodeGroupInput", 500, 500, frame=f_col)
    gi_nor = g.add("NodeGroupInput", 500, -600, frame=f_nor)
    gout = g.add("NodeGroupOutput", 1900, 0)
    tc = g.add("ShaderNodeTexCoord", -2600, 400, frame=f_flow)

    idents = {it.name: it.identifier for it in ng.interface.items_tree
              if it.item_type == 'SOCKET' and it.in_out == 'INPUT'}

    def gs(node, name):
        return next(s for s in node.outputs if s.identifier == idents[name])

    def noise(x, y, vector, scale, detail, frame, label):
        node = g.add("ShaderNodeTexNoise", x, y, label, frame=frame, noise_dimensions='3D')
        g.link(vector, node.inputs["Vector"])
        if isinstance(scale, bpy.types.NodeSocket):
            g.link(scale, node.inputs["Scale"])
        else:
            node.inputs["Scale"].default_value = scale
        node.inputs["Detail"].default_value = detail
        node.inputs["Roughness"].default_value = 0.55
        return node.outputs[0]

    def combine(x, y, vx, vy, vz, frame, label=""):
        node = g.add("ShaderNodeCombineXYZ", x, y, label, frame=frame)
        for sock, value in zip(node.inputs, (vx, vy, vz)):
            if isinstance(value, bpy.types.NodeSocket):
                g.link(value, sock)
            else:
                sock.default_value = value
        return node.outputs[0]

    def rotate_z(x, y, vector, angle, frame, label):
        node = g.add("ShaderNodeVectorRotate", x, y, label, frame=frame, rotation_type='Z_AXIS')
        g.link(vector, node.inputs["Vector"])
        g.link(angle, node.inputs["Angle"])
        return node.outputs[0]

    def separate(x, y, vector, frame):
        node = g.add("ShaderNodeSeparateXYZ", x, y, frame=frame)
        g.link(vector, node.inputs[0])
        return node.outputs

    seed = g.math('MULTIPLY', -2300, -520, gs(gi_flow, "Seed"), 13.7, frame=f_flow, label="Seed")

    # --- FLOW: radial -----------------------------------------------------------
    # A strand is a line of constant compass angle around the Root's Z axis. The
    # angle is fed to the noise as a point on a circle, so there is no seam where
    # atan2 would wrap. Waviness turns each point a little around the axis first.
    q = g.vmath('SUBTRACT', -2300, 300, tc.outputs["Object"], gs(gi_flow, "Root"), frame=f_flow,
                label="From Root")
    wave = noise(-2300, 80, tc.outputs["Object"], gs(gi_flow, "Wave Scale"), 1.0, f_flow, "Waves")
    wave = g.math('SUBTRACT', -2100, 80, wave, 0.5, frame=f_flow)
    wave = g.math('MULTIPLY', -1940, 80, wave, gs(gi_flow, "Waviness"), frame=f_flow)
    qw = rotate_z(-1750, 300, q, wave, f_flow, "Bend")
    qx, qy, _qz = separate(-1570, 300, qw, f_flow)
    flat = combine(-1400, 300, qx, qy, 0.0, f_flow, "Drop Z")
    radius = g.vmath('LENGTH', -1220, 180, flat, frame=f_flow, out="Value", label="Distance from axis")
    compass = g.vmath('NORMALIZE', -1220, 330, flat, frame=f_flow, label="Compass direction")
    ring = g.math('DIVIDE', -1400, 520, gs(gi_flow, "Strand Count"), 6.2832, frame=f_flow,
                  label="Noise cells around")
    compass = g.vmath('SCALE', -1040, 330, compass, frame=f_flow)
    g.link(ring, compass.node.inputs["Scale"])
    cx, cy, _cz = separate(-880, 330, compass, f_flow)
    along = g.vmath('LENGTH', -1220, 40, q, frame=f_flow, out="Value", label="Distance from Root")
    along = g.math('MULTIPLY_ADD', -1040, 40, along, gs(gi_flow, "Length Variation"), seed, frame=f_flow)
    radial = combine(-700, 300, cx, cy, along, f_flow, "Radial coordinate")

    # --- FLOW: UV -------------------------------------------------------------------
    angle = g.math('RADIANS', -2300, -300, gs(gi_flow, "UV Angle"), frame=f_flow)
    uv = rotate_z(-1750, -300, tc.outputs["UV"], angle, f_flow, "UV Angle")
    u, v, _w = separate(-1570, -300, uv, f_flow)
    across = g.math('MULTIPLY', -1220, -260, u, gs(gi_flow, "Strand Count"), frame=f_flow)
    # V covers a whole island in 0-1 where Radial counts scene units, hence the 10.
    lv = g.math('MULTIPLY', -1400, -420, gs(gi_flow, "Length Variation"), 10.0, frame=f_flow)
    along_uv = g.math('MULTIPLY', -1220, -420, v, lv, frame=f_flow)
    uv_coord = combine(-700, -300, across, along_uv, seed, f_flow, "UV coordinate")

    coord_node = g.add("ShaderNodeMix", -480, 100, "Radial / UV", frame=f_flow, data_type='VECTOR')
    g.link(gs(gi_flow, "Use UV"), _inp(coord_node, "Factor_Float"))
    g.link(radial, _inp(coord_node, "A_Vector"))
    g.link(uv_coord, _inp(coord_node, "B_Vector"))
    coord = _out(coord_node, "Result_Vector")

    crown = g.smoothstep(-880, 60, radius, 0.0, gs(gi_flow, "Crown Fade"), frame=f_flow, label="Crown Fade")
    fade = g.mix('FLOAT', 'MIX', -480, -120, gs(gi_flow, "Use UV"), crown, 1.0, frame=f_flow,
                 label="Fade (off in UV)")

    # --- STRANDS ----------------------------------------------------------------------
    n = noise(-200, 200, coord, 1.0, 3.0, f_strand, "Strands")
    clump_scale = g.math('DIVIDE', -480, -500, 1.0, gs(gi_strand, "Clump Size"), frame=f_strand)
    clump_coord = g.vmath('ADD', -480, -320, coord, (0.0, 0.0, 31.4), frame=f_strand)
    clump = noise(-200, -300, clump_coord, clump_scale, 1.0, f_strand, "Clumps")
    # Normalised fBm sits in roughly 0.25-0.75, so the bands are cut around 0.5.
    dark = g.smoothstep(40, 420, n, 0.50, 0.32, frame=f_strand, label="Gaps")
    light = g.smoothstep(40, 180, n, 0.54, 0.70, frame=f_strand, label="Raised strands")
    strands = g.smoothstep(40, -60, n, 0.32, 0.68, frame=f_strand, label="Strand height")
    clumps = g.smoothstep(40, -300, clump, 0.32, 0.68, frame=f_strand, label="Clump height")
    valley = g.math('SUBTRACT', 240, -300, 1.0, clumps, frame=f_strand, label="Clump valleys")

    # --- COLOR ------------------------------------------------------------------------------
    black = (0.0, 0.0, 0.0, 1.0)
    k = g.math('MULTIPLY', 500, 250, dark, fade, frame=f_col)
    k = g.math('MULTIPLY', 680, 250, k, gs(gi_col, "Dark Strands"), frame=f_col)
    col = g.mix('RGBA', 'MIX', 880, 450, k, gs(gi_col, "Base Color"), black, frame=f_col,
                label="Dark strands")
    k = g.math('MULTIPLY', 500, 80, valley, fade, frame=f_col)
    k = g.math('MULTIPLY', 680, 80, k, gs(gi_col, "Clump Shading"), frame=f_col)
    col = g.mix('RGBA', 'MIX', 1080, 450, k, col, black, frame=f_col, label="Clump shading")
    k = g.math('MULTIPLY', 500, -90, light, fade, frame=f_col)
    k = g.math('MULTIPLY', 680, -90, k, gs(gi_col, "Light Strands"), frame=f_col)
    col = g.mix('RGBA', 'MIX', 1280, 450, k, col, gs(gi_col, "Light Strand Color"), frame=f_col,
                label="Light strands")

    # --- NORMAL -----------------------------------------------------------------------------
    height = g.mix('FLOAT', 'MIX', 780, -500, gs(gi_nor, "Clump Bump"), strands, clumps, frame=f_nor,
                   label="Strands / clumps")
    height = g.math('MULTIPLY', 960, -500, height, fade, frame=f_nor, label="Height")
    bump = g.add("ShaderNodeBump", 1200, -600, frame=f_nor)
    g.link(gs(gi_nor, "Bump Strength"), _inp(bump, "Strength"))
    g.link(gs(gi_nor, "Bump Distance"), _inp(bump, "Distance"))
    g.link(height, _inp(bump, "Height"))
    g.link(gs(gi_nor, "Normal"), _inp(bump, "Normal"))
    shown = g.math('MULTIPLY', 960, -320, strands, fade, frame=f_nor, label="Strands")

    g.link(col, gout.inputs["Color"])
    g.link(bump.outputs["Normal"], gout.inputs["Normal"])
    g.link(height, gout.inputs["Height"])
    g.link(shown, gout.inputs["Strands"])

    for node, loc in g.placed:
        if hasattr(node, "location_absolute"):
            node.location_absolute = loc
        else:
            node.location = loc


def build_hair_group():
    """Create HB Hair Strands, or rebuild it in place. Returns the group."""
    ng = bpy.data.node_groups.get(HAIR_NAME)
    if ng is None:
        ng = bpy.data.node_groups.new(HAIR_NAME, 'ShaderNodeTree')
    _ensure_interface(ng)
    _build_nodes(ng)
    ng[VERSION_KEY] = HAIR_VERSION
    ng.description = ("Smash-style painted hair strands. Color -> HB Master Shader Base Color, "
                      "Normal -> its Normal; both bake through Bake Textures")
    return ng


def hair_nodes(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    return [n for n in material.node_tree.nodes
            if n.type == 'GROUP' and n.node_tree is not None
            and n.node_tree.name.startswith(HAIR_NAME)]


def crown_of(obj):
    """(top centre, largest side) of a mesh in its own space - where the Root goes.

    From the mesh data, not obj.bound_box: that one follows the armature, and a
    posed character would put the Root wherever the head happens to be."""
    xs, ys, zs = zip(*(v.co for v in obj.data.vertices))
    size = max(max(a) - min(a) for a in (xs, ys, zs))
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(zs)), size


def add_to_material(material, obj=None):
    """Put HB Hair Strands in front of the material's HB Master Shader.

    Whatever fed the master's Base Color and Normal now feeds the strands, and
    the strands feed the master. Returns the node, or None with no master.
    """
    masters = master_nodes(material)
    if not masters:
        return None
    build_hair_group()
    existing = hair_nodes(material)
    if existing:
        return existing[0]
    nt = material.node_tree
    master = masters[0]
    node = nt.nodes.new("ShaderNodeGroup")
    node.node_tree = bpy.data.node_groups[HAIR_NAME]
    node.name = node.label = HAIR_NAME
    node.width = 220
    node.location = (master.location.x - 280, master.location.y)
    for name, out in (("Base Color", "Color"), ("Normal", "Normal")):
        target = master.inputs[name]
        if target.is_linked:
            nt.links.new(target.links[0].from_socket, node.inputs[name])
        elif name == "Base Color":
            node.inputs[name].default_value = target.default_value
        nt.links.new(node.outputs[out], target)
    if obj is not None and obj.type == 'MESH' and len(obj.data.vertices):
        root, size = crown_of(obj)
        node.inputs["Root"].default_value = root
        # Head-sized defaults, scaled to whatever units the character is in.
        size = size or 1.0
        node.inputs["Crown Fade"].default_value = round(size * 0.15, 4)
        node.inputs["Bump Distance"].default_value = round(size * 0.008, 4)
        node.inputs["Length Variation"].default_value = round(0.6 / size, 4)
        node.inputs["Wave Scale"].default_value = round(2.0 / size, 4)
    return node


class SUB_OP_hb_hair_strands_add(Operator):
    """Add Smash-style hair strands in front of the active material's HB Master Shader"""
    bl_idname = 'sub.hb_hair_strands_add'
    bl_label = 'Add / Update Hair Strands'
    bl_description = (
        'Add the HB Hair Strands group to the active material, between its textures and the '
        'HB Master Shader, with the Root set to the top of the active mesh. Run it again to '
        'rebuild the group to the version in the addon - every material keeps its settings'
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and bool(master_nodes(obj.active_material))

    def execute(self, context):
        obj = context.active_object
        mat = obj.active_material
        had = bool(hair_nodes(mat))
        build_hair_group()
        add_to_material(mat, obj)
        self.report({'INFO'}, f"{mat.name}: {HAIR_NAME} v{HAIR_VERSION} "
                              f"{'rebuilt' if had else 'added'}")
        return {'FINISHED'}
