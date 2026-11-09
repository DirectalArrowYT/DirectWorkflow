# =============================================================================
# HB Master Shader - one toon node group for skin, hair, cloth, leather, metal
#
# Replaces the per-character SkinGroup / HairGroup / handsGroup family, which
# were the same 21-node graph copied and re-tuned by editing constants inside
# the group. Every one of those constants is a socket here, so one shared group
# covers every surface and a material only differs in its own instance values.
#
# The group is split into three parts, and the split is the whole point:
#
#   COL        Base Color -> tint/HSV -> ambient-occlusion tint. View and light
#              independent, so it is exactly what Bake Textures writes into
#              _col. Nothing directional ever goes here: a fighter turns round
#              and gets mirrored in game, and a baked terminator would flip to
#              the wrong side with it.
#   TOON       Cel shadow, rim and highlight on top of COL. Viewport only - the
#              game lights the model itself. The old groups got their cel
#              shadow from Shader to RGB, which Cycles does not evaluate, so it
#              never reached the bake anyway; now that is by design instead of
#              by accident, and "View = 1" shows exactly what will bake.
#   BAKE DATA  A Principled BSDF named BAKE_SHADER carrying COL, alpha, normal
#              and emission, so the baker's normal walk finds the right node.
#              The Smash PRM channels are plain sockets in PRM units, which the
#              baker reads straight off the instance (core.hb_master_instance).
#              It is mixed in at 2% even in toon view: Cycles bakes the NOR map
#              from the BSDF closures' normals, and an emission-only surface
#              has none, so without it every NOR bake would come out flat.
#
# Built from code, not appended from a library .blend, so every character file
# gets the identical group and "Update" can rebuild it in place: sockets are
# matched by name and kept, so every material keeps its own values.
# =============================================================================

import bpy
from bpy.types import Operator, Panel
from bpy.props import BoolProperty, EnumProperty
from mathutils import Vector

MASTER_NAME = "HB Master Shader"
MASTER_VERSION = 6
VERSION_KEY = "hb_master_version"

# Names the baker keys off. BAKE_SHADER is core.OVERRIDE_NODE_SHADER.
BAKE_SHADER_NAME = "BAKE_SHADER"
BAKE_COLOR_NAME = "HB_BakeColor"
# The PRM/NOR values after Surface Detail. The baker reads these instead of the
# instance sockets once any detail is on (core.hb_detail_active).
ROUGH_FINAL_NAME = "HB_Roughness_Final"
SPEC_FINAL_NAME = "HB_Specular_Final"
CAVITY_FINAL_NAME = "HB_Cavity_Final"
TRANSITION_FINAL_NAME = "HB_Transition_Final"
DETAIL_AMOUNTS = ("Roughness Variation", "Specular Variation", "Edge Wear", "Mesh Cavity")

# Cycles drops closures below 1e-5, and a near-black albedo times this is still
# well clear of that. Visually it is invisible next to the toon emission.
PBR_CARRIER_WEIGHT = 0.02

# Transition noise Fac (Detail 2) sits mostly in this range; stretched to
# 0.1-1.0 it lands near vanilla NOR.b's spread (5%-95% about 0.2-0.95).
TRANSITION_NOISE_RANGE = (0.3, 0.7)


# =============================================================================
# INTERFACE
# =============================================================================
class _S:
    """One group input socket."""
    def __init__(self, name, kind, default=None, lo=None, hi=None, factor=False,
                 desc="", hide=False, toggle=False):
        self.name, self.kind, self.default = name, kind, default
        self.lo, self.hi, self.factor = lo, hi, factor
        self.desc, self.hide, self.toggle = desc, hide, toggle


F, C, V, I, B = ('NodeSocketFloat', 'NodeSocketColor', 'NodeSocketVector',
                 'NodeSocketInt', 'NodeSocketBool')

# (panel name or None for the top level, closed by default, sockets)
INTERFACE = [
    (None, False, [
        _S("Base Color", C, (0.8, 0.8, 0.8, 1.0),
           desc="Albedo - wire the character's diffuse texture here"),
        _S("Alpha", F, 1.0, 0.0, 1.0, True, "Opacity. Baked into the _col alpha"),
        _S("Normal", V, (0.0, 0.0, 0.0), hide=True,
           desc="From a Normal Map or Bump node. Empty = smooth mesh normals. "
                "Baked into _nor"),
        _S("View", I, 0, 0, 4,
           desc="0 Toon (final look)   1 COL: exactly what bakes into _col   "
                "2 PRM: R metal/SSS, G rough, B AO   3 PBR preview of the bake data   "
                "4 Game: Smash's fighter shading of the bake data"),
    ]),
    ("Color", False, [
        _S("Tint", C, (1.0, 1.0, 1.0, 1.0), desc="Multiplies Base Color. Baked"),
        _S("Hue", F, 0.5, 0.0, 1.0, True, "0.5 = unchanged. Baked"),
        _S("Saturation", F, 1.0, 0.0, 2.0, desc="1 = unchanged. Baked"),
        _S("Brightness", F, 1.0, 0.0, 2.0, desc="1 = unchanged. Baked"),
    ]),
    ("Occlusion", False, [
        _S("AO Strength", F, 0.6, 0.0, 1.0, True,
           "How much of the AO tint lands in creases. Baked into _col"),
        _S("AO Distance", F, 0.5, 0.0, 1000.0,
           desc="Search distance in scene units - a few percent of the "
                "character's height. Convert Materials sets it from the mesh size"),
        _S("AO Contrast", F, 0.3, 0.0, 0.95, True,
           "Higher keeps the tint to the deepest creases only"),
        _S("AO Color", C, (0.5, 0.35, 0.35, 1.0),
           desc="Multiplies the albedo where occluded. A saturated dark tint "
                "reads as painted anime shading"),
        _S("AO Map", F, 1.0, 0.0, 1.0, True,
           "A painted / game occlusion texture (e.g. _AO). White = open, black = fully "
           "occluded. Adds to the computed AO, and bakes into _col the same way"),
        _S("AO to PRM", B, False,
           desc="Also bake a real AO pass into PRM.b. Off: AO lives in _col "
                "only and PRM.b stays white, so it is not applied twice"),
    ]),
    ("Toon Shading", False, [
        _S("Toon Amount", F, 1.0, 0.0, 1.0, True,
           "0 = unlit (flat COL, e.g. eyes, glowing parts), 1 = full cel shading. "
           "Viewport only - never baked"),
        _S("Shadow Tint", C, (0.72, 0.62, 0.72, 1.0),
           desc="Multiplies COL on the shadow side. Viewport only"),
        _S("Shadow Color Map", C, (1.0, 1.0, 1.0, 1.0),
           desc="A painted shadow-side texture (many anime games ship one, e.g. _S / "
                "shade textures). Used on the shadow side by Shadow Map Amount. "
                "Viewport only"),
        _S("Shadow Map Amount", F, 0.0, 0.0, 1.0, True,
           "0 = shadow side is COL x Shadow Tint, 1 = Shadow Color Map x Shadow Tint"),
        _S("Shadow Threshold", F, 0.5, 0.0, 1.0, True,
           "Where the terminator sits. With scene lights it is in light units, "
           "so brighter lights widen the lit side"),
        _S("Shadow Softness", F, 0.03, 0.0, 1.0, True, "Width of the shadow edge"),
        _S("Shadow Mask", F, 1.0, 0.0, 1.0, True,
           "0 forces shadow. Wire a painted mask or vertex color here. "
           "TextureCraft: Anisotropy angle"),
        _S("AO Shadowing", F, 0.25, 0.0, 1.0, True, "Lets occlusion push areas into shadow"),
        _S("Scene Lights", B, True,
           desc="On: shade from the scene's lights (EEVEE only - Shader to RGB). "
                "Off: shade from Light Direction, identical in EEVEE and Cycles"),
        _S("Light Direction", V, (0.35, -0.6, 0.7), -1.0, 1.0,
           desc="World-space direction towards the light. Also drives the rim "
                "side and the highlight in both modes"),
    ]),
    # A panel-toggle bool takes its panel's name (Blender renames it), so each
    # toggle panel is named after its toggle to keep every socket name unique -
    # the baker and the presets look sockets up by name.
    ("Deep Shadow", True, [
        _S("Deep Shadow", B, False, toggle=True,
           desc="A second, darker shadow tone (two-tone anime cel shading). Viewport only"),
        _S("Deep Shadow Tint", C, (0.55, 0.45, 0.6, 1.0),
           desc="Multiplies the shadow color in the deepest shadow"),
        _S("Deep Shadow Threshold", F, 0.25, 0.0, 1.0, True,
           "Where the deep shadow starts. Keep it below Shadow Threshold"),
    ]),
    ("Rim Light", True, [
        _S("Rim Light", B, True, toggle=True, desc="Viewport only"),
        _S("Rim Color", C, (1.0, 1.0, 1.0, 1.0),
           desc="Light colors brighten the silhouette, dark colors outline it"),
        _S("Rim Width", F, 0.25, 0.0, 1.0, True),
        _S("Rim Softness", F, 0.05, 0.0, 1.0, True),
        _S("Rim Strength", F, 0.25, 0.0, 1.0, True),
        _S("Rim Lit Side Only", F, 0.7, 0.0, 1.0, True,
           "1 = rim only where the surface is lit"),
        _S("Rim Mask", F, 1.0, 0.0, 1.0, True,
           "0 removes the rim here. TextureCraft: Sheen roughness"),
    ]),
    ("Toon Highlight", True, [
        _S("Toon Highlight", B, False, toggle=True,
           desc="Viewport only. Unrelated to the PRM Specular the game uses"),
        _S("Highlight Color", C, (1.0, 1.0, 1.0, 1.0)),
        _S("Highlight Size", F, 0.15, 0.0, 1.0, True),
        _S("Highlight Softness", F, 0.1, 0.0, 1.0, True),
        _S("Highlight Strength", F, 0.5, 0.0, 2.0),
        _S("Hair Ring", F, 0.0, 0.0, 1.0, True,
           "0 = round highlight, 1 = anisotropic ring around the head. "
           "TextureCraft: Anisotropy level"),
        _S("Highlight Mask", F, 1.0, 0.0, 1.0, True,
           "0 removes the highlight here. TextureCraft: Coat roughness"),
    ]),
    ("Emission", True, [
        _S("Emission Color", C, (0.0, 0.0, 0.0, 1.0), desc="Baked into _emi"),
        _S("Emission Strength", F, 1.0, 0.0, 100.0,
           desc="Above 1 is kept: the baker normalises the map and reports the "
                "CustomVector3 that restores it"),
    ]),
    ("Smash PRM", False, [
        _S("Metalness", F, 0.0, 0.0, 1.0, True,
           "PRM.r. Keep it 0 or 1 - in game anything between is a half-metal"),
        _S("Roughness", F, 0.6, 0.0, 1.0, True, "PRM.g"),
        _S("Specular", F, 0.16, 0.0, 1.0, True,
           "PRM.a. 0.16 is vanilla; 1 = reflectance 0.2"),
        _S("SSS Mask", F, 0.0, 0.0, 1.0, True,
           "PRM.r on a skin (subsurface) Smash shader instead of Metalness. "
           "Vanilla skin uses 1. TextureCraft: Scattering weight"),
        _S("PRM AO", F, 1.0, 0.0, 1.0, True,
           "A painted occlusion map for PRM.b alone: unlike AO Map it doesn't tint "
           "_col, so nothing is darkened twice. TextureCraft: Ambient occlusion"),
    ]),
    ("Smash NOR", False, [
        _S("Cavity", F, 1.0, 0.0, 1.0, True,
           "NOR.a. Dark crevices lose their specular. TextureCraft: Specular"),
        _S("Transition Blend", F, 1.0, 0.0, 1.0, True,
           "NOR.b. Where the material transition (ditto metal, gold) starts first. "
           "TextureCraft: Sheen color"),
        _S("Transition Noise", F, 0.0, 0.0, 1.0, True,
           "Blends Transition Blend toward vanilla-style large 3D noise, so the transition "
           "spreads across the model in patches the way it does on vanilla fighters. "
           "Baked into NOR.b. Add Transition Map turns it on"),
        _S("Transition Size", F, 3.0, 0.0, 1000.0,
           desc="Size of the transition patches in scene units. Add Transition Map sets it "
                "from the mesh size"),
        _S("Transition Seed", F, 0.0, -1000.0, 1000.0,
           desc="Picks a different transition pattern"),
    ]),
    ("Surface Detail", True, [
        _S("Roughness Variation", F, 0.0, 0.0, 1.0, True,
           "Noise added to Roughness, so the surface breaks up the way vanilla PRM.g does. "
           "Baked into PRM.g"),
        _S("Specular Variation", F, 0.0, 0.0, 1.0, True,
           "Noise on Specular. Baked into PRM.a"),
        _S("Edge Wear", F, 0.0, 0.0, 1.0, True,
           "Sharp outer edges come out smoother and shinier, like worn leather or metal. "
           "From the mesh shape (Cycles only, so it shows in the bake and PBR view)"),
        _S("Mesh Cavity", F, 0.0, 0.0, 1.0, True,
           "Darkens Cavity (NOR.a) in creases found from the mesh, so seams and folds "
           "lose their specular in game. Baked into NOR.a"),
        _S("Detail Size", F, 0.05, 0.0, 1000.0,
           desc="Size of the variation in scene units. Add Surface Detail sets it from "
                "the mesh size"),
        _S("Cavity Distance", F, 0.05, 0.0, 1000.0,
           desc="How far Mesh Cavity looks for nearby surfaces, in scene units. "
                "Add Surface Detail sets it from the mesh size"),
    ]),
    ("Game Preview", True, [
        _S("SSS Blend", F, 0.5, 0.0, 1.0, True,
           "CustomVector30.x: how far SSS-masked texels blend to SSS Color. "
           "Sync Game Preview copies it from the material data. View 4 only"),
        _S("SSS Falloff", F, 1.5, 0.0, 100.0,
           desc="CustomVector30.y: how hard the skin light/shadow edge is (vanilla 1.5-4). "
                "View 4 only"),
        _S("SSS Color", C, (0.25, 0.03, 0.0, 1.0),
           desc="CustomVector11: the colour skin blends to. View 4 only"),
        _S("Game Light", C, (1.0, 1.0, 1.0, 1.0),
           desc="Colour and strength of the fighter key light, from Light Direction. "
                "View 4 only"),
        _S("Game Ambient", C, (0.4, 0.4, 0.45, 1.0),
           desc="Ambient (diffuse and reflection) light. View 4 only"),
        _S("Transition Preview", F, 0.0, 0.0, 1.0, True,
           "The in-game transition factor: texels whose NOR.b is above 1 - this turn to "
           "metal, as they would under a Metal Box or a metal ditto. View 4 only"),
    ]),
]
OUTPUT_NAME = "Shader"


def _set_socket_props(item, spec):
    item.description = spec.desc
    if spec.default is not None:
        try:
            item.default_value = spec.default
        except Exception:
            pass
    for attr, value in (("min_value", spec.lo), ("max_value", spec.hi)):
        if value is not None and hasattr(item, attr):
            setattr(item, attr, value)
    if spec.factor:
        try:
            item.subtype = 'FACTOR'
        except Exception:
            pass
    item.hide_value = spec.hide
    if spec.toggle and hasattr(item, "is_panel_toggle"):
        item.is_panel_toggle = True


def _ensure_interface(ng):
    """Make the interface match INTERFACE, keeping every socket that exists.

    A material stores its instance values against socket identifiers, so a
    socket that is removed and re-added comes back at its default in every
    material. Reusing matching sockets is what lets Update rebuild the group
    without resetting anyone's settings. Returns {name: default} for the inputs
    that are new, which _default_new_sockets() then sets on existing nodes.
    """
    iface = ng.interface
    sockets = {(it.in_out, it.name): it for it in iface.items_tree if it.item_type == 'SOCKET'}
    panels = {it.name: it for it in iface.items_tree if it.item_type == 'PANEL'}
    keep = set()
    new = {}

    def socket(name, in_out, kind, parent=None):
        item = sockets.get((in_out, name))
        if item is not None and item.socket_type != kind:
            iface.remove(item)
            item = None
        if item is None:
            item = iface.new_socket(name=name, in_out=in_out, socket_type=kind, parent=parent)
        keep.add(item.identifier)
        return item

    out = socket(OUTPUT_NAME, 'OUTPUT', 'NodeSocketShader')
    root = out.parent
    order = [(out, root)]

    for panel_name, closed, specs in INTERFACE:
        parent = root
        if panel_name is not None:
            parent = panels.get(panel_name) or iface.new_panel(panel_name)
            parent.default_closed = closed
            keep.add(("PANEL", panel_name))
        for spec in specs:
            if ('INPUT', spec.name) not in sockets and spec.default is not None:
                new[spec.name] = spec.default
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

    # Put everything in INTERFACE order, per parent.
    position = {}
    for item, parent in order:
        key = parent.as_pointer() if parent is not None else 0
        index = position.get(key, 0)
        try:
            iface.move_to_parent(item, parent, index)
        except Exception:
            pass
        position[key] = index + 1
    return new


def _default_new_sockets(ng, defaults):
    """Give nodes that already exist the interface default on new sockets.

    Blender starts a new socket at 0 on them, whatever the interface says, and
    a mask at 0 switches its effect off and bakes black into NOR and PRM.
    """
    ng.interface_update(bpy.context)
    trees = [m.node_tree for m in bpy.data.materials if m.node_tree]
    for tree in trees + list(bpy.data.node_groups):
        for node in tree.nodes:
            if node.type != 'GROUP' or node.node_tree != ng:
                continue
            for name, value in defaults.items():
                socket = node.inputs.get(name)
                if socket is not None and not socket.is_linked:
                    socket.default_value = value


# =============================================================================
# GRAPH
# =============================================================================
def _inp(node, key):
    """An enabled input by identifier, then by name. Mix/Map Range carry
    several same-named sockets for different data types, only one enabled."""
    for s in node.inputs:
        if s.identifier == key and s.enabled:
            return s
    for s in node.inputs:
        if s.name == key and s.enabled:
            return s
    raise KeyError(f"{node.bl_idname} has no enabled input {key!r}")


def _out(node, key):
    for s in node.outputs:
        if s.identifier == key and s.enabled:
            return s
    for s in node.outputs:
        if s.name == key and s.enabled:
            return s
    raise KeyError(f"{node.bl_idname} has no enabled output {key!r}")


class _Graph:
    def __init__(self, nt):
        self.nt = nt
        self.placed = []

    def add(self, idname, x, y, label="", name=None, frame=None, **props):
        node = self.nt.nodes.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        if label:
            node.label = label
        if name:
            node.name = name
        node.location = (x, y)
        if frame is not None:
            node.parent = frame
        self.placed.append((node, (x, y)))
        return node

    def frame(self, label, color):
        node = self.nt.nodes.new("NodeFrame")
        node.label = label
        node.label_size = 28
        node.use_custom_color = True
        node.color = color
        node.shrink = True
        return node

    def link(self, from_socket, to_socket):
        self.nt.links.new(from_socket, to_socket)

    def set(self, socket, value):
        socket.default_value = value

    # --- small builders ------------------------------------------------------
    def math(self, op, x, y, a=None, b=None, c=None, frame=None, label="", clamp=False):
        node = self.add("ShaderNodeMath", x, y, label, frame=frame, operation=op, use_clamp=clamp)
        for index, value in enumerate((a, b, c)):
            if value is None:
                continue
            if isinstance(value, bpy.types.NodeSocket):
                self.link(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return node.outputs[0]

    def vmath(self, op, x, y, a=None, b=None, frame=None, label="", out="Vector"):
        node = self.add("ShaderNodeVectorMath", x, y, label, frame=frame, operation=op)
        for index, value in enumerate((a, b)):
            if value is None:
                continue
            if isinstance(value, bpy.types.NodeSocket):
                self.link(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return _out(node, out)

    def mix(self, kind, blend, x, y, fac, a, b, frame=None, label="", name=None):
        node = self.add("ShaderNodeMix", x, y, label, name=name, frame=frame,
                        data_type=kind, blend_type=blend, clamp_result=False)
        suffix = "_Color" if kind == 'RGBA' else "_Float"
        for key, value in (("Factor_Float", fac), ("A" + suffix, a), ("B" + suffix, b)):
            sock = _inp(node, key)
            if isinstance(value, bpy.types.NodeSocket):
                self.link(value, sock)
            else:
                sock.default_value = value
        return _out(node, "Result" + suffix)

    def smoothstep(self, x, y, value, lo, hi, frame=None, label=""):
        node = self.add("ShaderNodeMapRange", x, y, label, frame=frame,
                        data_type='FLOAT', interpolation_type='SMOOTHSTEP', clamp=True)
        for key, v in (("Value", value), ("From Min", lo), ("From Max", hi)):
            sock = _inp(node, key)
            if isinstance(v, bpy.types.NodeSocket):
                self.link(v, sock)
            else:
                sock.default_value = v
        _inp(node, "To Min").default_value = 0.0
        _inp(node, "To Max").default_value = 1.0
        return _out(node, "Result")

    def band(self, x, y, value, centre, width, frame=None, label=""):
        """smoothstep(centre - width/2, centre + width/2, value).

        The hi edge gets a hair of extra width so a softness of 0 is a clean
        step rather than a zero-width range, which Map Range turns into 0."""
        lo = self.math('MULTIPLY_ADD', x - 360, y + 60, width, -0.5, centre, frame=frame)
        hi = self.math('MULTIPLY_ADD', x - 360, y - 100, width, 0.5, centre, frame=frame)
        hi = self.math('ADD', x - 180, y - 100, hi, 0.0005, frame=frame)
        return self.smoothstep(x, y, value, lo, hi, frame=frame, label=label)


def _build_nodes(ng):
    nt = ng
    nt.nodes.clear()
    g = _Graph(nt)

    f_col = g.frame("COL  -  baked into _col", (0.20, 0.30, 0.20))
    f_toon = g.frame("TOON  -  viewport only, never baked", (0.22, 0.22, 0.32))
    f_bake = g.frame("BAKE DATA  -  read by Bake Textures", (0.35, 0.25, 0.15))
    f_out = g.frame("OUTPUT", (0.25, 0.25, 0.25))

    gi_col = g.add("NodeGroupInput", -2300, 300, frame=f_col)
    gi_toon = g.add("NodeGroupInput", -2300, -900, frame=f_toon)
    gi_bake = g.add("NodeGroupInput", -600, 1300, frame=f_bake)
    gi_out = g.add("NodeGroupInput", 1500, -300, frame=f_out)
    gout = g.add("NodeGroupOutput", 2500, 100, frame=f_out)
    geo = g.add("ShaderNodeNewGeometry", -2300, -1600, frame=f_toon)

    # By identifier, so a socket's display name never matters here.
    idents = {it.name: it.identifier for it in ng.interface.items_tree
              if it.item_type == 'SOCKET' and it.in_out == 'INPUT'}

    def group_socket(node, name):
        ident = idents[name]
        return next(s for s in node.outputs if s.identifier == ident)

    def gc(name):
        return group_socket(gi_col, name)

    def gt(name):
        return group_socket(gi_toon, name)

    # --- COL ------------------------------------------------------------------
    c0 = g.mix('RGBA', 'MULTIPLY', -2000, 450, 1.0, gc("Base Color"), gc("Tint"),
               frame=f_col, label="Tint")
    hsv = g.add("ShaderNodeHueSaturation", -1780, 450, "Hue / Saturation / Brightness", frame=f_col)
    g.link(c0, hsv.inputs["Color"])
    g.link(gc("Hue"), hsv.inputs["Hue"])
    g.link(gc("Saturation"), hsv.inputs["Saturation"])
    g.link(gc("Brightness"), hsv.inputs["Value"])
    c1 = hsv.outputs["Color"]

    ao = g.add("ShaderNodeAmbientOcclusion", -2000, 100, "Ambient Occlusion", frame=f_col,
               samples=16, only_local=False, inside=False)
    g.link(gc("AO Distance"), ao.inputs["Distance"])
    g.link(gc("Normal"), ao.inputs["Normal"])
    open_ = g.add("ShaderNodeMapRange", -1780, 100, "AO -> openness", frame=f_col,
                  data_type='FLOAT', interpolation_type='LINEAR', clamp=True)
    g.link(ao.outputs["AO"], _inp(open_, "Value"))
    g.link(gc("AO Contrast"), _inp(open_, "From Min"))
    _inp(open_, "From Max").default_value = 1.0
    openness = _out(open_, "Result")
    # A painted AO map multiplies the computed openness, so both darken the same way.
    openness = g.math('MULTIPLY', -1780, -60, openness, gc("AO Map"), frame=f_col, label="x AO Map")
    occl = g.math('SUBTRACT', -1560, 100, 1.0, openness, frame=f_col)
    occl = g.math('MULTIPLY', -1380, 100, occl, gc("AO Strength"), frame=f_col, clamp=True,
                  label="AO amount")
    ao_tint = g.mix('RGBA', 'MULTIPLY', -1380, 400, 1.0, c1, gc("AO Color"), frame=f_col,
                    label="AO Color")
    col = g.mix('RGBA', 'MIX', -1150, 350, occl, c1, ao_tint, frame=f_col,
                label="COL (bakes to _col)", name=BAKE_COLOR_NAME)

    # --- TOON: shared vectors ----------------------------------------------------
    # A zero-strength Bump turns the optional Normal socket into a real vector.
    # Only a shader's own Normal input falls back to the mesh normal when left
    # empty; math nodes would read (0, 0, 0) and every dot product below would
    # collapse to zero on any material without a normal map.
    nrm = g.add("ShaderNodeBump", -2000, -1300, "Shading Normal", frame=f_toon)
    _inp(nrm, "Strength").default_value = 0.0
    g.link(gt("Normal"), _inp(nrm, "Normal"))
    N = nrm.outputs["Normal"]
    L = g.vmath('NORMALIZE', -2000, -1650, gt("Light Direction"), frame=f_toon, label="L")
    Vv = geo.outputs["Incoming"]

    # --- TOON: diffuse term --------------------------------------------------------
    dif = g.add("ShaderNodeBsdfDiffuse", -1700, -500, "Scene light (EEVEE)", frame=f_toon)
    dif.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    g.link(gt("Normal"), dif.inputs["Normal"])
    s2r = g.add("ShaderNodeShaderToRGB", -1500, -500, frame=f_toon)
    g.link(dif.outputs["BSDF"], s2r.inputs["Shader"])
    bw = g.add("ShaderNodeRGBToBW", -1320, -500, frame=f_toon)
    g.link(s2r.outputs["Color"], bw.inputs["Color"])

    ndl = g.vmath('DOT_PRODUCT', -1700, -800, N, L, frame=f_toon, out="Value", label="N.L")
    half = g.math('MULTIPLY_ADD', -1500, -800, ndl, 0.5, 0.5, frame=f_toon, label="Half Lambert")
    diffuse = g.mix('FLOAT', 'MIX', -1120, -650, gt("Scene Lights"), half, bw.outputs["Val"],
                    frame=f_toon, label="Light source")
    ao_shadow = g.mix('FLOAT', 'MIX', -1120, -900, gt("AO Shadowing"), 1.0, openness,
                      frame=f_toon, label="AO Shadowing")
    diffuse = g.math('MULTIPLY', -920, -650, diffuse, gt("Shadow Mask"), frame=f_toon)
    diffuse = g.math('MULTIPLY', -760, -650, diffuse, ao_shadow, frame=f_toon)
    lit = g.band(-380, -650, diffuse, gt("Shadow Threshold"), gt("Shadow Softness"),
                 frame=f_toon, label="Lit")

    shadow_base = g.mix('RGBA', 'MIX', -560, -300, gt("Shadow Map Amount"), col, gt("Shadow Color Map"),
                        frame=f_toon, label="Shadow base")
    shadow_col = g.mix('RGBA', 'MULTIPLY', -380, -300, 1.0, shadow_base, gt("Shadow Tint"),
                       frame=f_toon, label="Shadow color")
    toon = g.mix('RGBA', 'MIX', -120, -350, lit, shadow_col, col, frame=f_toon, label="Cel")

    # Deep shadow: a second, darker tone below its own threshold.
    deep_lit = g.band(-380, -150, diffuse, gt("Deep Shadow Threshold"), gt("Shadow Softness"),
                      frame=f_toon, label="Deep lit")
    deep_amount = g.math('SUBTRACT', -200, -150, 1.0, deep_lit, frame=f_toon)
    deep_amount = g.math('MULTIPLY', -60, -150, deep_amount, gt("Deep Shadow"), frame=f_toon)
    deep_col = g.mix('RGBA', 'MULTIPLY', -60, -250, 1.0, shadow_col, gt("Deep Shadow Tint"),
                     frame=f_toon, label="Deep shadow color")
    toon = g.mix('RGBA', 'MIX', 80, -350, deep_amount, toon, deep_col, frame=f_toon,
                 label="+ Deep shadow")

    # --- TOON: highlight -----------------------------------------------------------
    H = g.vmath('ADD', -1700, -1150, L, Vv, frame=f_toon)
    H = g.vmath('NORMALIZE', -1520, -1150, H, frame=f_toon, label="H")
    ndh = g.vmath('DOT_PRODUCT', -1320, -1100, N, H, frame=f_toon, out="Value")
    ndh = g.math('MAXIMUM', -1140, -1100, ndh, 0.0, frame=f_toon, label="Blinn")
    tangent = g.add("ShaderNodeTangent", -1700, -1400, "Radial Z", frame=f_toon,
                    direction_type='RADIAL', axis='Z')
    tdh = g.vmath('DOT_PRODUCT', -1320, -1350, tangent.outputs["Tangent"], H,
                  frame=f_toon, out="Value")
    tdh2 = g.math('MULTIPLY', -1140, -1350, tdh, tdh, frame=f_toon)
    kk = g.math('SUBTRACT', -980, -1350, 1.0, tdh2, frame=f_toon)
    kk = g.math('MAXIMUM', -820, -1350, kk, 0.0, frame=f_toon)
    kk = g.math('SQRT', -660, -1350, kk, frame=f_toon, label="Kajiya-Kay")
    term = g.mix('FLOAT', 'MIX', -500, -1150, gt("Hair Ring"), ndh, kk,
                 frame=f_toon, label="Highlight shape")
    centre = g.math('MULTIPLY_ADD', -700, -1600, gt("Highlight Size"), -0.25, 1.0, frame=f_toon)
    width = g.math('MULTIPLY', -700, -1760, gt("Highlight Softness"), 0.1, frame=f_toon)
    spec = g.band(-140, -1150, term, centre, width, frame=f_toon, label="Highlight")
    spec = g.math('MULTIPLY', 40, -1150, spec, gt("Highlight Strength"), frame=f_toon)
    spec = g.math('MULTIPLY', 200, -1150, spec, gt("Toon Highlight"), frame=f_toon)
    spec = g.math('MULTIPLY', 360, -1150, spec, lit, frame=f_toon, label="Only where lit")
    spec = g.math('MULTIPLY', 360, -1000, spec, gt("Highlight Mask"), frame=f_toon)
    toon = g.mix('RGBA', 'ADD', 560, -400, spec, toon, gt("Highlight Color"), frame=f_toon,
                 label="+ Highlight")

    # --- TOON: rim -------------------------------------------------------------------
    ndv = g.vmath('DOT_PRODUCT', -1320, -1850, N, Vv, frame=f_toon, out="Value")
    ndv = g.math('MAXIMUM', -1140, -1850, ndv, 0.0, frame=f_toon)
    facing = g.math('SUBTRACT', -980, -1850, 1.0, ndv, frame=f_toon, label="Facing")
    rim_centre = g.math('SUBTRACT', -700, -2000, 1.0, gt("Rim Width"), frame=f_toon)
    rim = g.band(-140, -1850, facing, rim_centre, gt("Rim Softness"), frame=f_toon, label="Rim")
    rim_side = g.mix('FLOAT', 'MIX', 40, -2050, gt("Rim Lit Side Only"), 1.0, lit, frame=f_toon)
    rim = g.math('MULTIPLY', 200, -1850, rim, rim_side, frame=f_toon)
    rim = g.math('MULTIPLY', 360, -1850, rim, gt("Rim Strength"), frame=f_toon)
    rim = g.math('MULTIPLY', 520, -1850, rim, gt("Rim Light"), frame=f_toon)
    rim = g.math('MULTIPLY', 520, -1700, rim, gt("Rim Mask"), frame=f_toon)
    toon = g.mix('RGBA', 'MIX', 760, -450, rim, toon, gt("Rim Color"), frame=f_toon,
                 label="+ Rim")

    # Toon Amount 0 = unlit: the flat COL, with no shadow, rim or highlight.
    toon = g.mix('RGBA', 'MIX', 860, -300, gt("Toon Amount"), col, toon, frame=f_toon,
                 label="Toon Amount")

    emission = g.vmath('SCALE', 760, -750, gt("Emission Color"), frame=f_toon)
    g.link(gt("Emission Strength"), emission.node.inputs["Scale"])
    toon = g.mix('RGBA', 'ADD', 960, -450, 1.0, toon, emission, frame=f_toon, label="+ Emission")

    # --- BAKE DATA -------------------------------------------------------------------
    def gb(name):
        return gi_bake.outputs[name]

    # --- SURFACE DETAIL: per-pixel variation, all of it baked -----------------------
    f_det = g.frame("SURFACE DETAIL  -  baked into PRM.g / PRM.a / NOR.a", (0.30, 0.22, 0.12))
    gd = g.add("NodeGroupInput", -2300, 2100, frame=f_det)

    def gdet(name):
        return group_socket(gd, name)

    pos = g.add("ShaderNodeNewGeometry", -2300, 1700, frame=f_det)
    inv = g.math('DIVIDE', -2100, 1900, 1.0, gdet("Detail Size"), frame=f_det, label="1 / size")
    coord = g.vmath('SCALE', -1900, 1800, pos.outputs["Position"], frame=f_det)
    g.link(inv, coord.node.inputs["Scale"])
    noise = g.add("ShaderNodeTexNoise", -1700, 1800, "Variation noise", frame=f_det,
                  noise_dimensions='3D')
    g.link(coord, noise.inputs["Vector"])
    _inp(noise, "Scale").default_value = 1.0
    _inp(noise, "Detail").default_value = 6.0
    _inp(noise, "Roughness").default_value = 0.6
    split = g.add("ShaderNodeSeparateColor", -1500, 1800, frame=f_det)
    g.link(noise.outputs["Color"], split.inputs[0])

    def centred(channel, x, y):
        # Noise sits around 0.5 with a spread of about +-0.25; this makes it about +-0.5.
        return g.math('MULTIPLY_ADD', x, y, split.outputs[channel], 2.0, -1.0, frame=f_det,
                      label=f"{channel} -> -1..1")

    # Sharp convex edges: pointiness above the flat 0.5.
    edge = g.add("ShaderNodeMapRange", -1700, 1500, "Edge (pointiness)", frame=f_det,
                 data_type='FLOAT', interpolation_type='SMOOTHSTEP', clamp=True)
    g.link(pos.outputs["Pointiness"], _inp(edge, "Value"))
    _inp(edge, "From Min").default_value = 0.52
    _inp(edge, "From Max").default_value = 0.62
    wear = g.math('MULTIPLY', -1500, 1500, _out(edge, "Result"), gdet("Edge Wear"),
                  frame=f_det, label="Edge Wear")

    rough = g.math('MULTIPLY_ADD', -1300, 1900, centred("Red", -1300, 2000),
                   gdet("Roughness Variation"), gb("Roughness"), frame=f_det)
    rough = g.math('MULTIPLY', -1100, 1900, rough,
                   g.math('MULTIPLY_ADD', -1300, 1400, wear, -0.6, 1.0, frame=f_det),
                   frame=f_det, clamp=True, label="Roughness (final)")
    rough.node.name = ROUGH_FINAL_NAME

    spec_var = g.math('MULTIPLY_ADD', -1300, 1700, centred("Green", -1300, 1800),
                      gdet("Specular Variation"), 1.0, frame=f_det)
    spec = g.math('MULTIPLY', -1100, 1700, gb("Specular"), spec_var, frame=f_det)
    spec = g.math('MULTIPLY', -950, 1700, spec,
                  g.math('MULTIPLY_ADD', -1100, 1400, wear, 0.8, 1.0, frame=f_det),
                  frame=f_det, clamp=True, label="Specular (final)")
    spec.node.name = SPEC_FINAL_NAME

    crease = g.add("ShaderNodeAmbientOcclusion", -1700, 1200, "Creases", frame=f_det,
                   samples=8, only_local=True, inside=False)
    g.link(gdet("Cavity Distance"), crease.inputs["Distance"])
    g.link(gb("Normal"), crease.inputs["Normal"])
    crease_open = g.add("ShaderNodeMapRange", -1500, 1200, "Crease -> cavity", frame=f_det,
                        data_type='FLOAT', interpolation_type='SMOOTHSTEP', clamp=True)
    g.link(crease.outputs["AO"], _inp(crease_open, "Value"))
    _inp(crease_open, "From Min").default_value = 0.35
    _inp(crease_open, "From Max").default_value = 0.95
    mesh_cav = g.mix('FLOAT', 'MIX', -1300, 1200, gdet("Mesh Cavity"), 1.0,
                     _out(crease_open, "Result"), frame=f_det)
    cavity = g.math('MULTIPLY', -1100, 1200, gb("Cavity"), mesh_cav, frame=f_det, clamp=True,
                    label="Cavity (final)")
    cavity.node.name = CAVITY_FINAL_NAME

    # Transition: vanilla NOR.b is large soft 3D noise in model space spread over
    # roughly 0.15-0.98 (median ~0.55), so the switch sweeps across in patches.
    t_inv = g.math('DIVIDE', -2100, 900, 1.0, gdet("Transition Size"), frame=f_det,
                   label="1 / transition size")
    t_coord = g.vmath('SCALE', -1900, 900, pos.outputs["Position"], frame=f_det)
    g.link(t_inv, t_coord.node.inputs["Scale"])
    t_noise = g.add("ShaderNodeTexNoise", -1700, 900, "Transition noise", frame=f_det,
                    noise_dimensions='4D')
    g.link(t_coord, t_noise.inputs["Vector"])
    g.link(gdet("Transition Seed"), _inp(t_noise, "W"))
    _inp(t_noise, "Scale").default_value = 1.0
    _inp(t_noise, "Detail").default_value = 2.0
    _inp(t_noise, "Roughness").default_value = 0.5
    t_spread = g.add("ShaderNodeMapRange", -1500, 900, "Noise -> vanilla spread", frame=f_det,
                     data_type='FLOAT', clamp=True)
    g.link(t_noise.outputs["Fac"], _inp(t_spread, "Value"))
    _inp(t_spread, "From Min").default_value = TRANSITION_NOISE_RANGE[0]
    _inp(t_spread, "From Max").default_value = TRANSITION_NOISE_RANGE[1]
    _inp(t_spread, "To Min").default_value = 0.1
    _inp(t_spread, "To Max").default_value = 1.0
    trans = g.mix('FLOAT', 'MIX', -1300, 900, gdet("Transition Noise"), gb("Transition Blend"),
                  _out(t_spread, "Result"), frame=f_det, label="Transition (final)")
    trans.node.name = TRANSITION_FINAL_NAME

    bsdf = g.add("ShaderNodeBsdfPrincipled", -150, 1300, "Smash bake data (PBR)",
                 name=BAKE_SHADER_NAME, frame=f_bake)
    g.link(col, bsdf.inputs["Base Color"])
    g.link(gb("Metalness"), bsdf.inputs["Metallic"])
    g.link(rough, bsdf.inputs["Roughness"])
    # PRM.a = 0.4 x Blender's Specular IOR Level (see core.PRM_SPECULAR_SCALE),
    # so this keeps the PBR preview reflecting what the game will.
    # Cavity takes the specular away in game, so the PBR view does the same.
    spec_level = g.math('MULTIPLY', -500, 900, spec, cavity, frame=f_bake, label="x Cavity")
    spec_level = g.math('MULTIPLY', -350, 900, spec_level, 2.5, frame=f_bake,
                        label="PRM.a -> Specular IOR Level")
    g.link(spec_level, bsdf.inputs["Specular IOR Level"])
    g.link(gb("Alpha"), bsdf.inputs["Alpha"])
    g.link(gb("Normal"), bsdf.inputs["Normal"])
    g.link(gb("Emission Color"), bsdf.inputs["Emission Color"])
    g.link(gb("Emission Strength"), bsdf.inputs["Emission Strength"])

    prm_r = g.math('MAXIMUM', -350, 650, gb("Metalness"), gb("SSS Mask"), frame=f_bake)
    prm_b = g.mix('FLOAT', 'MIX', -350, 480, gb("AO to PRM"), 1.0, openness, frame=f_bake,
                  label="PRM.b")
    prm_b = g.math('MULTIPLY', -200, 480, prm_b, gb("PRM AO"), frame=f_bake)
    prm = g.add("ShaderNodeCombineColor", -150, 600, "PRM view", frame=f_bake)
    g.link(prm_r, prm.inputs[0])
    g.link(rough, prm.inputs[1])
    g.link(prm_b, prm.inputs[2])

    # --- GAME -------------------------------------------------------------------------
    # Smash's SFX_PBS fighter shading of the same data that bakes: one key light
    # from Light Direction plus ambient, fake SSS from CustomVector30/11, GGX
    # specular with F0 = PRM.a x 0.2, cavity and AO taking specular away.
    # Unlit (Emission) so it reads the same in EEVEE and Cycles.
    f_game = g.frame("GAME  -  View 4, viewport only", (0.15, 0.25, 0.35))
    gg_in = g.add("NodeGroupInput", 900, 2000, frame=f_game)

    def gg(name):
        return group_socket(gg_in, name)

    sss_blend = g.math('MULTIPLY', 1100, 2100, gg("SSS Mask"), gg("SSS Blend"), frame=f_game,
                       label="sssBlend = PRM.r x CV30.x")
    albedo = g.mix('RGBA', 'MIX', 1300, 2100, sss_blend, col, gg("SSS Color"), frame=f_game,
                   label="Albedo -> CV11")
    lambert = g.math('MAXIMUM', 1100, 1900, ndl, 0.0, frame=f_game, label="N.L")
    skin = g.math('MULTIPLY', 1100, 1750, ndl, gg("SSS Falloff"), frame=f_game)
    skin = g.math('MULTIPLY_ADD', 1250, 1750, skin, 0.5, 0.5, frame=f_game, clamp=True,
                  label="Skin wrap (CV30.y)")
    direct = g.mix('FLOAT', 'MIX', 1400, 1850, sss_blend, lambert, skin, frame=f_game,
                   label="Direct shading")
    metal = gg("Metalness")
    dielectric = g.math('SUBTRACT', 1100, 1600, 1.0, metal, frame=f_game)
    light = g.vmath('SCALE', 1550, 1850, gg("Game Light"), frame=f_game, label="Key light")
    g.link(direct, light.node.inputs["Scale"])
    ambient = g.vmath('SCALE', 1550, 1700, gg("Game Ambient"), frame=f_game, label="Ambient x AO")
    g.link(prm_b, ambient.node.inputs["Scale"])
    irradiance = g.vmath('ADD', 1700, 1800, light, ambient, frame=f_game)
    diffuse_c = g.vmath('SCALE', 1550, 2100, albedo, frame=f_game, label="Diffuse albedo")
    g.link(dielectric, diffuse_c.node.inputs["Scale"])
    game_diffuse = g.vmath('MULTIPLY', 1850, 2000, diffuse_c, irradiance, frame=f_game,
                           label="Diffuse")

    # GGX: alpha = roughness^2.
    a = g.math('MULTIPLY', 1100, 1400, rough, rough, frame=f_game, label="alpha")
    a2 = g.math('MULTIPLY', 1250, 1400, a, a, frame=f_game)
    a2 = g.math('MAXIMUM', 1400, 1400, a2, 0.0004, frame=f_game)
    ndh_g = g.vmath('DOT_PRODUCT', 1100, 1250, N, H, frame=f_game, out="Value")
    ndh_g = g.math('MAXIMUM', 1250, 1250, ndh_g, 0.0, frame=f_game, label="N.H")
    d = g.math('MULTIPLY', 1400, 1250, ndh_g, ndh_g, frame=f_game)
    a2m1 = g.math('SUBTRACT', 1400, 1100, a2, 1.0, frame=f_game)
    d = g.math('MULTIPLY_ADD', 1550, 1250, d, a2m1, 1.0, frame=f_game)
    d = g.math('MULTIPLY', 1700, 1250, d, d, frame=f_game)
    d = g.math('MULTIPLY', 1850, 1250, d, 3.14159265, frame=f_game)
    ggx = g.math('DIVIDE', 2000, 1300, a2, d, frame=f_game, label="GGX D")
    ndv_g = g.math('MAXIMUM', 1700, 1100, ndv, 0.1, frame=f_game)
    vis = g.math('DIVIDE', 1850, 1100, lambert, ndv_g, frame=f_game)
    vis = g.math('MULTIPLY', 2000, 1100, vis, 0.25, frame=f_game, label="N.L / 4 N.V")
    spec_d = g.math('MULTIPLY', 2150, 1250, ggx, vis, frame=f_game)
    spec_d = g.math('MULTIPLY', 2300, 1250, spec_d, cavity, frame=f_game, label="x Cavity")
    f0_d = g.math('MULTIPLY', 1100, 1000, spec, 0.2, frame=f_game, label="F0 = PRM.a x 0.2")
    f0 = g.mix('RGBA', 'MIX', 1300, 1000, metal, f0_d, albedo, frame=f_game,
               label="F0 (metal -> albedo)")
    spec_light = g.vmath('SCALE', 2450, 1250, gg("Game Light"), frame=f_game)
    g.link(spec_d, spec_light.node.inputs["Scale"])
    # Ambient reflection: the cube map, dimmer and blurrier as roughness rises.
    glossy = g.math('MULTIPLY_ADD', 1700, 950, rough, -0.7, 1.0, frame=f_game,
                    label="Env sharpness")
    env_amt = g.math('MULTIPLY', 1850, 950, glossy, prm_b, frame=f_game)
    env_amt = g.math('MULTIPLY', 2000, 950, env_amt, cavity, frame=f_game, label="x AO x Cavity")
    env = g.vmath('SCALE', 2150, 950, gg("Game Ambient"), frame=f_game)
    g.link(env_amt, env.node.inputs["Scale"])
    spec_in = g.vmath('ADD', 2600, 1150, spec_light, env, frame=f_game)
    game_spec = g.vmath('MULTIPLY', 2750, 1100, spec_in, f0, frame=f_game, label="Specular")
    game = g.vmath('ADD', 2900, 1700, game_diffuse, game_spec, frame=f_game)
    game_emit = g.vmath('SCALE', 2900, 1500, gg("Emission Color"), frame=f_game)
    g.link(gg("Emission Strength"), game_emit.node.inputs["Scale"])
    game = g.vmath('ADD', 3050, 1650, game, game_emit, frame=f_game, label="Game")

    # Transition preview: NOR.b above 1 - factor switches to the transition
    # material. Shown as a plain polished metal, which is what most look like.
    t_amt = gg("Transition Preview")
    t_thresh = g.math('SUBTRACT', 2600, 2300, 1.0, t_amt, frame=f_game)
    t_on = g.math('GREATER_THAN', 2750, 2300, trans, t_thresh, frame=f_game)
    t_any = g.math('GREATER_THAN', 2750, 2450, t_amt, 0.001, frame=f_game)
    t_on = g.math('MULTIPLY', 2900, 2300, t_on, t_any, frame=f_game, label="Transitioned")
    m_light = g.math('MULTIPLY_ADD', 2600, 2150, lambert, 0.5, 0.25, frame=f_game)
    m_key = g.vmath('SCALE', 2750, 2150, gg("Game Light"), frame=f_game)
    g.link(m_light, m_key.node.inputs["Scale"])
    m_amb = g.vmath('SCALE', 2750, 2000, gg("Game Ambient"), frame=f_game)
    _inp(m_amb.node, "Scale").default_value = 0.8
    m_irr = g.vmath('ADD', 2900, 2100, m_key, m_amb, frame=f_game)
    m_col = g.vmath('MULTIPLY', 3050, 2100, m_irr, (0.42, 0.44, 0.48), frame=f_game)
    m_hi = g.math('POWER', 3050, 1950, ndh_g, 40.0, frame=f_game, label="Metal highlight")
    m_hi_c = g.vmath('SCALE', 3200, 1950, gg("Game Light"), frame=f_game)
    g.link(m_hi, m_hi_c.node.inputs["Scale"])
    metal_look = g.vmath('ADD', 3200, 2100, m_col, m_hi_c, frame=f_game,
                         label="Transition metal (approx.)")
    game = g.mix('RGBA', 'MIX', 3350, 1800, t_on, game, metal_look, frame=f_game,
                 label="Game + transition")

    # --- OUTPUT ----------------------------------------------------------------------
    view = gi_out.outputs["View"]
    is_col = g.math('COMPARE', 1700, -200, view, 1.0, 0.5, frame=f_out, label="View = COL")
    is_prm = g.math('COMPARE', 1700, -360, view, 2.0, 0.5, frame=f_out, label="View = PRM")
    is_pbr = g.math('COMPARE', 1700, -520, view, 3.0, 0.5, frame=f_out, label="View = PBR")
    shown = g.mix('RGBA', 'MIX', 1900, 100, is_col, toon, col, frame=f_out)
    shown = g.mix('RGBA', 'MIX', 2050, 100, is_prm, shown, prm.outputs["Color"], frame=f_out)
    is_game = g.math('COMPARE', 1700, -680, view, 4.0, 0.5, frame=f_out, label="View = Game")
    shown = g.mix('RGBA', 'MIX', 2100, 0, is_game, shown, game, frame=f_out)

    emit = g.add("ShaderNodeEmission", 2200, 150, frame=f_out)
    g.link(shown, emit.inputs["Color"])
    transparent = g.add("ShaderNodeBsdfTransparent", 2200, 300, frame=f_out)
    toon_sh = g.add("ShaderNodeMixShader", 2350, 200, "Alpha", frame=f_out)
    g.link(gi_out.outputs["Alpha"], toon_sh.inputs[0])
    g.link(transparent.outputs[0], toon_sh.inputs[1])
    g.link(emit.outputs[0], toon_sh.inputs[2])

    carrier = g.math('MAXIMUM', 2200, -400, is_pbr, PBR_CARRIER_WEIGHT, frame=f_out,
                     label="PBR weight (NOR carrier)")
    final = g.add("ShaderNodeMixShader", 2500, 250, frame=f_out)
    g.link(carrier, final.inputs[0])
    g.link(toon_sh.outputs[0], final.inputs[1])
    g.link(bsdf.outputs["BSDF"], final.inputs[2])
    g.link(final.outputs[0], gout.inputs[OUTPUT_NAME])

    # Frames re-fit to their children on draw; place children in canvas space.
    for node, loc in g.placed:
        if hasattr(node, "location_absolute"):
            node.location_absolute = loc
        else:
            node.location = loc


def build_master_group():
    """Create the HB Master Shader, or rebuild it in place. Returns the group."""
    ng = bpy.data.node_groups.get(MASTER_NAME)
    if ng is None:
        ng = bpy.data.node_groups.new(MASTER_NAME, 'ShaderNodeTree')
    new = _ensure_interface(ng)
    _build_nodes(ng)
    _default_new_sockets(ng, new)
    ng[VERSION_KEY] = MASTER_VERSION
    ng.description = ("Toon master for skin, hair, cloth and metal. COL/PRM/NOR/EMI "
                      "bake through Bake Textures; toon shading is viewport only")
    return ng


def is_master_group(tree):
    return tree is not None and tree.name.startswith(MASTER_NAME)


def master_nodes(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    return [n for n in material.node_tree.nodes
            if n.type == 'GROUP' and is_master_group(n.node_tree)]


# =============================================================================
# PRESETS
# =============================================================================
# Only the look. AO Distance is scale-dependent and set by conversion from the
# mesh size instead, so applying a preset never breaks it.
PRESETS = {
    'SKIN': {
        "Toon Amount": 1.0, "Shadow Map Amount": 0.0,
        "AO Strength": 0.7, "AO Contrast": 0.35, "AO Color": (0.78, 0.36, 0.30, 1.0),
        "Shadow Tint": (0.96, 0.68, 0.64, 1.0), "Shadow Softness": 0.04,
        "AO Shadowing": 0.3,
        "Rim Light": True, "Rim Color": (1.0, 0.92, 0.86, 1.0), "Rim Strength": 0.2,
        "Rim Width": 0.25, "Rim Lit Side Only": 0.8,
        "Toon Highlight": False,
        "Metalness": 0.0, "Roughness": 0.7, "Specular": 0.1, "SSS Mask": 1.0,
    },
    'HAIR': {
        "Toon Amount": 1.0, "Shadow Map Amount": 0.0,
        "AO Strength": 0.6, "AO Contrast": 0.3, "AO Color": (0.45, 0.38, 0.5, 1.0),
        "Shadow Tint": (0.62, 0.6, 0.78, 1.0), "Shadow Softness": 0.03,
        "AO Shadowing": 0.3,
        "Rim Light": True, "Rim Color": (1.0, 1.0, 1.0, 1.0), "Rim Strength": 0.25,
        "Rim Width": 0.22, "Rim Lit Side Only": 0.6,
        "Toon Highlight": True, "Highlight Color": (1.0, 1.0, 1.0, 1.0), "Highlight Size": 0.12,
        "Highlight Softness": 0.15, "Highlight Strength": 0.35, "Hair Ring": 1.0,
        "Metalness": 0.0, "Roughness": 0.45, "Specular": 0.1, "SSS Mask": 0.0,
    },
    'CLOTH': {
        "Toon Amount": 1.0, "Shadow Map Amount": 0.0,
        "AO Strength": 0.6, "AO Contrast": 0.3, "AO Color": (0.45, 0.42, 0.55, 1.0),
        "Shadow Tint": (0.68, 0.66, 0.8, 1.0), "Shadow Softness": 0.03,
        "AO Shadowing": 0.25,
        "Rim Light": True, "Rim Color": (1.0, 1.0, 1.0, 1.0), "Rim Strength": 0.18,
        "Rim Width": 0.25, "Rim Lit Side Only": 0.7,
        "Toon Highlight": False,
        "Metalness": 0.0, "Roughness": 0.85, "Specular": 0.12, "SSS Mask": 0.0,
    },
    'SHINY': {
        "Toon Amount": 1.0, "Shadow Map Amount": 0.0,
        "AO Strength": 0.6, "AO Contrast": 0.3, "AO Color": (0.45, 0.42, 0.55, 1.0),
        "Shadow Tint": (0.6, 0.58, 0.72, 1.0), "Shadow Softness": 0.02,
        "AO Shadowing": 0.25,
        "Rim Light": True, "Rim Color": (1.0, 1.0, 1.0, 1.0), "Rim Strength": 0.3,
        "Rim Width": 0.2, "Rim Lit Side Only": 0.6,
        "Toon Highlight": True, "Highlight Color": (1.0, 1.0, 1.0, 1.0), "Highlight Size": 0.1,
        "Highlight Softness": 0.08, "Highlight Strength": 0.8, "Hair Ring": 0.0,
        "Metalness": 0.0, "Roughness": 0.3, "Specular": 0.4, "SSS Mask": 0.0,
    },
    'METAL': {
        "Toon Amount": 1.0, "Shadow Map Amount": 0.0,
        "AO Strength": 0.5, "AO Contrast": 0.3, "AO Color": (0.4, 0.4, 0.45, 1.0),
        "Shadow Tint": (0.5, 0.5, 0.56, 1.0), "Shadow Softness": 0.02,
        "AO Shadowing": 0.2,
        "Rim Light": True, "Rim Color": (1.0, 1.0, 1.0, 1.0), "Rim Strength": 0.35,
        "Rim Width": 0.2, "Rim Lit Side Only": 0.5,
        "Toon Highlight": True, "Highlight Color": (1.0, 1.0, 1.0, 1.0), "Highlight Size": 0.18,
        "Highlight Softness": 0.05, "Highlight Strength": 1.0, "Hair Ring": 0.0,
        "Metalness": 1.0, "Roughness": 0.3, "Specular": 0.5, "SSS Mask": 0.0,
    },
}

PRESETS['EYE'] = {
    "Toon Amount": 0.0, "Shadow Map Amount": 0.0, "Deep Shadow": False,
    "AO Strength": 0.0,
    "Rim Light": False, "Toon Highlight": False,
    "Metalness": 0.0, "Roughness": 0.4, "Specular": 0.3, "SSS Mask": 0.0,
}
PRESETS['UNLIT'] = {
    "Toon Amount": 0.0, "Shadow Map Amount": 0.0, "Deep Shadow": False,
    "AO Strength": 0.0,
    "Rim Light": False, "Toon Highlight": False,
    "Metalness": 0.0, "Roughness": 0.8, "Specular": 0.16, "SSS Mask": 0.0,
}

PRESET_ITEMS = (
    ('SKIN', "Skin", "Warm shadow, SSS mask 1 for Smash skin shaders"),
    ('HAIR', "Hair", "Cool shadow, anisotropic ring highlight"),
    ('CLOTH', "Cloth", "Matte fabric"),
    ('SHINY', "Shiny", "Leather, plastic, rubber - glossy but not metal"),
    ('METAL', "Metal", "PRM metalness 1"),
    ('EYE', "Eye", "Unlit (no cel shadow, rim or AO), like anime eyes"),
    ('UNLIT', "Unlit", "Flat color for glowing parts, effects and decals"),
)


def guess_preset(*names):
    """Pick a preset from material / object / old group names."""
    text = " ".join(n for n in names if n).lower()
    for keys, preset in (
        (("brow", "lash"), 'HAIR'),
        # Makeup painted on the face (MHA eyeshadow / eyeliner meshes), not the eye itself.
        (("eyeshadow", "eye_shadow", "eyeline"), 'SKIN'),
        (("eye", "pupil", "iris"), 'EYE'),
        (("glow", "emissive", "emit", "effect"), 'UNLIT'),
        (("shiny", "leather", "latex", "rubber", "boot", "belt", "glossy"), 'SHINY'),
        (("metal", "zip", "buckle", "chain", "armor", "armour", "ring", "pin"), 'METAL'),
        (("hair", "alp", "brow", "lash"), 'HAIR'),
        (("skin", "face", "neck", "hand", "arm", "leg", "body_skin", "flesh"), 'SKIN'),
    ):
        if any(k in text for k in keys):
            return preset
    return 'CLOTH'


# Surface Detail amounts per surface. Vanilla fighters vary PRM.g/PRM.a and
# darken NOR.a in seams; these start each kind somewhere believable.
DETAIL_PRESETS = {
    # Noise sits mostly in 0.35-0.65, so a variation of 0.25 moves roughness
    # about +-0.06; vanilla sets spread 0.1-0.6 across a set.
    'SKIN':  {"Roughness Variation": 0.25, "Specular Variation": 0.5,  "Edge Wear": 0.0,  "Mesh Cavity": 0.5},
    'HAIR':  {"Roughness Variation": 0.3,  "Specular Variation": 0.6,  "Edge Wear": 0.0,  "Mesh Cavity": 0.6},
    'CLOTH': {"Roughness Variation": 0.4,  "Specular Variation": 0.5,  "Edge Wear": 0.15, "Mesh Cavity": 0.8},
    'SHINY': {"Roughness Variation": 0.35, "Specular Variation": 0.4,  "Edge Wear": 0.4,  "Mesh Cavity": 0.7},
    'METAL': {"Roughness Variation": 0.4,  "Specular Variation": 0.3,  "Edge Wear": 0.5,  "Mesh Cavity": 0.7},
    'EYE':   {"Roughness Variation": 0.0,  "Specular Variation": 0.0,  "Edge Wear": 0.0,  "Mesh Cavity": 0.0},
    'UNLIT': {"Roughness Variation": 0.0,  "Specular Variation": 0.0,  "Edge Wear": 0.0,  "Mesh Cavity": 0.0},
}
# Fractions of the character's height.
TRANSITION_SIZE_OF_HEIGHT = 0.15
DETAIL_SIZE_OF_HEIGHT = 0.01
CAVITY_DISTANCE_OF_HEIGHT = 0.012


def surface_kind(material, node):
    """Which DETAIL_PRESETS entry fits a material.

    Hair, eyes and glow go by name (Smash hair often runs on the skin shader);
    otherwise the instance's values decide, then the name.
    """
    def value(name):
        sock = node.inputs.get(name)
        return sock.default_value if sock is not None and not sock.is_linked else 0.0
    named = guess_preset(material.name)
    if named in ('HAIR', 'EYE', 'UNLIT'):
        return named
    if value("SSS Mask") > 0.5:
        return 'SKIN'
    if value("Metalness") >= 0.5:
        return 'METAL'
    return named


def apply_surface_detail(material, node, amount=1.0, height=None):
    """Turn Surface Detail on for one instance. Returns the kind used."""
    kind = surface_kind(material, node)
    for name, value in DETAIL_PRESETS[kind].items():
        sock = node.inputs.get(name)
        if sock is not None and not sock.is_linked:
            sock.default_value = min(1.0, value * amount)
    if height:
        for name, fraction in (("Detail Size", DETAIL_SIZE_OF_HEIGHT),
                               ("Cavity Distance", CAVITY_DISTANCE_OF_HEIGHT)):
            sock = node.inputs.get(name)
            if sock is not None and not sock.is_linked:
                sock.default_value = round(height * fraction, 5)
    return kind


def apply_transition_map(material, node, amount=1.0, height=None, seed=0.0):
    """Turn the transition noise on for one instance. Eyes get vanilla's flat 0."""
    kind = surface_kind(material, node)
    values = {"Transition Noise": min(1.0, amount), "Transition Seed": seed}
    if kind == 'EYE':
        values = {"Transition Noise": 0.0, "Transition Blend": 0.0}
    elif height:
        values["Transition Size"] = round(height * TRANSITION_SIZE_OF_HEIGHT, 4)
    for name, value in values.items():
        sock = node.inputs.get(name)
        if sock is not None and not sock.is_linked:
            sock.default_value = value
    return kind


def apply_preset(node, preset):
    """Write a preset's values onto one group instance. Linked sockets are left alone."""
    changed = 0
    for name, value in PRESETS[preset].items():
        sock = node.inputs.get(name)
        if sock is None or sock.is_linked:
            continue
        sock.default_value = value
        changed += 1
    return changed


# =============================================================================
# CONVERSION
# =============================================================================
# The SkinGroup family: Texture / Shade / AO Col / Metallic / IOR / Roughness /
# Alpha (+ Normal), one Shader output named Result. Matched by signature rather
# than name, so SkinGroup.001, handsGroup and any other re-tuned copy convert.
OLD_TOON_SIGNATURE = {"Texture", "Shade", "AO Col", "Metallic", "Roughness"}


def _input_names(tree):
    return {it.name for it in tree.interface.items_tree
            if it.item_type == 'SOCKET' and it.in_out == 'INPUT'}


def is_old_toon_group(node):
    return (node.type == 'GROUP' and node.node_tree is not None
            and not is_master_group(node.node_tree)
            and OLD_TOON_SIGNATURE <= _input_names(node.node_tree))


def _is_overlay_bump_group(node):
    """The 'bump' helper: overlays Geometry.Normal and a Bump node's output as
    *colors*. The result is not a unit vector, so it shaded as noise and would
    bake a noise NOR map. Replaced with a real Bump node on the same height."""
    return (node.type == 'GROUP' and node.node_tree is not None
            and {"Height", "B", "Strength"} <= _input_names(node.node_tree)
            and any(n.type == 'BUMP' for n in node.node_tree.nodes))


def _new_master_node(nt, near):
    node = nt.nodes.new("ShaderNodeGroup")
    node.node_tree = bpy.data.node_groups[MASTER_NAME]
    node.name = MASTER_NAME
    node.label = MASTER_NAME
    node.location = near.location
    node.width = 240
    return node


def _move_input(nt, src_socket, dst_socket):
    """Carry a link, or the constant, from an old socket onto a new one."""
    if src_socket is None or dst_socket is None:
        return
    if src_socket.is_linked:
        nt.links.new(src_socket.links[0].from_socket, dst_socket)
    elif hasattr(src_socket, "default_value") and hasattr(dst_socket, "default_value"):
        try:
            dst_socket.default_value = src_socket.default_value
        except (TypeError, ValueError):
            pass


def _fix_normal_link(nt, master_normal):
    """Swap an overlay-bump group feeding Normal for a real Bump node."""
    if not master_normal.is_linked:
        return None
    src = master_normal.links[0].from_node
    if not _is_overlay_bump_group(src):
        return None
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = src.location
    bump.label = "Bump (was overlay group)"
    strength = src.inputs.get("Strength")
    bump.inputs["Strength"].default_value = (strength.default_value if strength is not None
                                             and not strength.is_linked else 0.2)
    bump.inputs["Distance"].default_value = 0.05
    height = src.inputs.get("Height")
    if height is not None and height.is_linked:
        nt.links.new(height.links[0].from_socket, bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], master_normal)
    nt.nodes.remove(src)
    return bump


def convert_material(material, preset=None, ao_distance=None, include_principled=False):
    """Move one material onto the HB Master Shader. Returns a one-line report,
    or None when there was nothing to convert."""
    if material is None or not material.use_nodes or material.node_tree is None:
        return None
    nt = material.node_tree
    if master_nodes(material):
        return None

    old = next((n for n in nt.nodes if is_old_toon_group(n)), None)
    principled = None
    if old is None and include_principled:
        principled = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'
                           and n.outputs["BSDF"].is_linked), None)
    source = old or principled
    if source is None:
        return None
    # Smash import/export materials drive the real in-game shader - never touch.
    if any(n.type == 'GROUP' and n.node_tree is not None
           and n.node_tree.name.startswith("Smash Ultimate Master Shader")
           for n in nt.nodes):
        return None

    # Material name only. The old group names say nothing about the surface:
    # Dabi's Clothes used "SkinGroup.001" and ClothesShiny "HairGroup", and
    # reading those made cloth convert as skin (SSS Mask 1 -> baked as skin).
    chosen = preset or guess_preset(material.name)
    node = _new_master_node(nt, source)
    apply_preset(node, chosen)
    if ao_distance is not None:
        node.inputs["AO Distance"].default_value = ao_distance

    if old is not None:
        _move_input(nt, old.inputs.get("Texture"), node.inputs["Base Color"])
        _move_input(nt, old.inputs.get("Alpha"), node.inputs["Alpha"])
        _move_input(nt, old.inputs.get("Normal"), node.inputs["Normal"])
        rough = old.inputs.get("Roughness")
        if rough is not None and not rough.is_linked:
            node.inputs["Roughness"].default_value = rough.default_value
        metal = old.inputs.get("Metallic")
        # The old Metallic was a look knob for the Blender render (0.06, 0.24,
        # 0.47...), which in game is a half-metal. Only a deliberate full metal
        # survives the move.
        if metal is not None and not metal.is_linked and metal.default_value >= 0.9:
            node.inputs["Metalness"].default_value = 1.0
        outputs = old.outputs
        was = f"'{old.node_tree.name}'"
    else:
        base = principled.inputs.get("Base Color")
        ao_mix = base.links[0].from_node if base is not None and base.is_linked else None
        if (ao_mix is not None and ao_mix.bl_idname == 'ShaderNodeMix'
                and ao_mix.name.startswith("PSK Ambient Occlusion")):
            # The PSK/PSA importer multiplies the game's AO texture into Base Color. Split it
            # back up: the texture goes to Base Color and the AO texture to AO Map, so the
            # occlusion is tinted like the rest of the AO and never applied twice.
            _move_input(nt, ao_mix.inputs[6], node.inputs["Base Color"])
            _move_input(nt, ao_mix.inputs[7], node.inputs["AO Map"])
            nt.nodes.remove(ao_mix)
        else:
            _move_input(nt, base, node.inputs["Base Color"])
        _move_input(nt, principled.inputs.get("Alpha"), node.inputs["Alpha"])
        _move_input(nt, principled.inputs.get("Normal"), node.inputs["Normal"])
        _move_input(nt, principled.inputs.get("Roughness"), node.inputs["Roughness"])
        _move_input(nt, principled.inputs.get("Emission Color"), node.inputs["Emission Color"])
        _move_input(nt, principled.inputs.get("Emission Strength"), node.inputs["Emission Strength"])
        metal = principled.inputs.get("Metallic")
        if metal is not None and not metal.is_linked:
            node.inputs["Metalness"].default_value = 1.0 if metal.default_value >= 0.5 else 0.0
        outputs = principled.outputs
        was = "Principled BSDF"

    for out in outputs:
        for link in list(out.links):
            nt.links.new(node.outputs[OUTPUT_NAME], link.to_socket)
    nt.nodes.remove(source)
    bump = _fix_normal_link(nt, node.inputs["Normal"])
    return (f"{material.name}: {was} -> {MASTER_NAME} ({chosen.title()})"
            + ("; overlay bump group replaced with a Bump node" if bump else ""))


def character_height(objects):
    """World-space height of the given meshes together, for AO Distance."""
    zs = []
    for obj in objects:
        if obj.type != 'MESH':
            continue
        for corner in obj.bound_box:
            zs.append((obj.matrix_world @ Vector(corner)).z)
    return (max(zs) - min(zs)) if zs else None


# =============================================================================
# OPERATORS / UI
# =============================================================================
class SUB_OP_hb_master_shader_update(Operator):
    """Add the HB Master Shader to this file, or rebuild it to the latest version"""
    bl_idname = 'sub.hb_master_shader_update'
    bl_label = 'Add / Update Master Shader'
    bl_description = (
        'Create the HB Master Shader node group in this file, or rebuild it in place '
        'to the version in the addon. Every material keeps its own settings'
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        existed = MASTER_NAME in bpy.data.node_groups
        ng = build_master_group()
        users = sum(1 for m in bpy.data.materials if master_nodes(m))
        self.report({'INFO'}, f"{'Rebuilt' if existed else 'Added'} {ng.name} "
                              f"v{MASTER_VERSION} - {users} material(s) use it")
        return {'FINISHED'}


class SUB_OP_hb_master_shader_convert(Operator):
    """Move the selected meshes' materials onto the HB Master Shader"""
    bl_idname = 'sub.hb_master_shader_convert'
    bl_label = 'Convert Selected Materials'
    bl_description = (
        'Replace SkinGroup / HairGroup style toon groups on the selected meshes with '
        'the HB Master Shader, keeping textures and normals wired and picking a '
        'preset from each material name'
    )
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        name='Preset',
        items=(('AUTO', "Auto (from name)", "Skin / Hair / Cloth / Shiny / Metal from the material name"),)
        + PRESET_ITEMS,
        default='AUTO',
    )
    include_principled: BoolProperty(
        name='Also Plain Principled',
        default=False,
        description='Also convert materials that are just a Principled BSDF. Off by '
                    'default so reference meshes and eyes are left alone',
    )

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        build_master_group()
        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        height = character_height(meshes)
        ao_distance = round(height * 0.03, 4) if height else None
        lines, seen = [], set()
        for obj in meshes:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or mat.name in seen:
                    continue
                seen.add(mat.name)
                line = convert_material(mat, None if self.preset == 'AUTO' else self.preset,
                                        ao_distance, self.include_principled)
                if line:
                    lines.append(line)
                    print("  " + line)
        if not lines:
            self.report({'WARNING'}, "Nothing to convert on the selected meshes")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Converted {len(lines)} material(s)"
                              + (f", AO Distance {ao_distance:g}" if ao_distance else "")
                              + " - see the console for details")
        return {'FINISHED'}


class SUB_OP_hb_master_shader_preset(Operator):
    """Apply a look preset to the HB Master Shader in the active material"""
    bl_idname = 'sub.hb_master_shader_preset'
    bl_label = 'Apply Master Shader Preset'
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name='Preset', items=PRESET_ITEMS)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and bool(master_nodes(obj.active_material))

    def execute(self, context):
        mat = context.active_object.active_material
        for node in master_nodes(mat):
            apply_preset(node, self.preset)
        self.report({'INFO'}, f"{mat.name}: {self.preset.title()} preset applied")
        return {'FINISHED'}


class SUB_OP_hb_surface_detail(Operator):
    """Turn on Surface Detail for the selected meshes' HB Master materials"""
    bl_idname = 'sub.hb_surface_detail'
    bl_label = 'Add Surface Detail'
    bl_description = (
        'Give the selected meshes\' HB Master materials roughness and specular variation, '
        'edge wear and mesh cavity suited to each surface (skin, hair, cloth, shiny, metal), '
        'sized from the meshes. Baked into PRM and NOR'
    )
    bl_options = {'REGISTER', 'UNDO'}

    amount: bpy.props.FloatProperty(
        name='Amount', default=1.0, min=0.0, max=2.0,
        description='Scales every amount; 0 turns Surface Detail off')

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        if MASTER_NAME not in bpy.data.node_groups:
            build_master_group()
        ng = bpy.data.node_groups[MASTER_NAME]
        if ng.get(VERSION_KEY, 0) < MASTER_VERSION:
            build_master_group()
        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        height = character_height(meshes)
        kinds, seen = {}, set()
        for obj in meshes:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or mat.name in seen:
                    continue
                seen.add(mat.name)
                for node in master_nodes(mat):
                    kind = apply_surface_detail(mat, node, self.amount, height)
                    kinds.setdefault(kind, []).append(mat.name)
        if not kinds:
            self.report({'WARNING'}, "No HB Master materials on the selected meshes")
            return {'CANCELLED'}
        for kind, names in sorted(kinds.items()):
            print(f"  Surface Detail {kind.title()}: {', '.join(names)}")
        summary = ", ".join(f"{len(n)} {k.lower()}" for k, n in sorted(kinds.items()))
        self.report({'INFO'}, f"Surface Detail on {sum(map(len, kinds.values()))} "
                              f"material(s): {summary}")
        return {'FINISHED'}


def sync_game_preview(material):
    """Copy the material's CustomVector30.x/.y and CustomVector11 into its Game Preview
    inputs. Returns True when the material had SSS data to copy."""
    from . import core, smash_bridge
    ctx = core.MatlContext(material)
    data = smash_bridge.matl_data(ctx.data_source) if ctx.is_subsurface else None
    v30 = data.vectors.get('CustomVector30') if data is not None else None
    v11 = data.vectors.get('CustomVector11') if data is not None else None
    for node in master_nodes(material):
        if v30 is not None:
            for name, value in (("SSS Blend", float(v30.value[0])),
                                ("SSS Falloff", float(v30.value[1]))):
                sock = node.inputs.get(name)
                if sock is not None and not sock.is_linked:
                    sock.default_value = value
        if v11 is not None:
            sock = node.inputs.get("SSS Color")
            if sock is not None and not sock.is_linked:
                sock.default_value = tuple(float(c) for c in v11.value[:3]) + (1.0,)
        if v30 is None:
            # Not a skin shader in game: no subsurface at all.
            sock = node.inputs.get("SSS Blend")
            if sock is not None and not sock.is_linked:
                sock.default_value = 0.0
    return v30 is not None


class SUB_OP_hb_game_preview(Operator):
    """Show every HB Master material as Smash shades it (View 4), with its SSS values from the material data"""
    bl_idname = 'sub.hb_game_preview'
    bl_label = 'Game Preview'
    bl_options = {'REGISTER', 'UNDO'}

    view: bpy.props.EnumProperty(
        name='View',
        items=(('4', "Game", "Smash's fighter shading of the bake data"),
               ('0', "Toon", "Back to the toon look"),
               ('3', "PBR", "Blender PBR preview of the bake data")),
        default='4')
    transition: bpy.props.FloatProperty(
        name='Transition', default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        description='Preview the in-game transition (Metal Box, metal ditto) at this factor')

    def execute(self, context):
        if MASTER_NAME in bpy.data.node_groups:
            ng = bpy.data.node_groups[MASTER_NAME]
            if ng.get(VERSION_KEY, 0) < MASTER_VERSION:
                build_master_group()
        count = skin = 0
        for mat in bpy.data.materials:
            nodes = master_nodes(mat)
            if not nodes:
                continue
            if self.view == '4' and sync_game_preview(mat):
                skin += 1
            for node in nodes:
                sock = node.inputs.get("View")
                if sock is not None and not sock.is_linked:
                    sock.default_value = int(self.view)
                sock = node.inputs.get("Transition Preview")
                if sock is not None and not sock.is_linked:
                    sock.default_value = self.transition if self.view == '4' else 0.0
            count += 1
        if not count:
            self.report({'WARNING'}, "No HB Master materials in this file")
            return {'CANCELLED'}
        extra = f", SSS from material data on {skin}" if self.view == '4' else ""
        self.report({'INFO'}, f"View {self.view} on {count} material(s){extra}")
        return {'FINISHED'}


class SUB_OP_hb_transition_map(Operator):
    """Give the selected meshes' HB Master materials a vanilla-style transition map (NOR.b)"""
    bl_idname = 'sub.hb_transition_map'
    bl_label = 'Add Transition Map'
    bl_description = (
        'Fill NOR.b of the selected meshes\' HB Master materials with large 3D noise like '
        'vanilla fighters, so Metal Box, gold and metal-ditto transitions sweep across in '
        'patches instead of all at once. Eyes get 0 as in vanilla. Preview it with '
        'Game Preview and the node\'s Transition Preview input'
    )
    bl_options = {'REGISTER', 'UNDO'}

    amount: bpy.props.FloatProperty(
        name='Amount', default=1.0, min=0.0, max=1.0,
        description='How far NOR.b goes from Transition Blend to the noise; 0 turns it off')
    seed: bpy.props.FloatProperty(
        name='Seed', default=0.0, description='Picks a different pattern')

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        ng = bpy.data.node_groups.get(MASTER_NAME)
        if ng is None or ng.get(VERSION_KEY, 0) < MASTER_VERSION:
            build_master_group()
        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        height = character_height(meshes)
        done, eyes, seen = [], [], set()
        for obj in meshes:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or mat.name in seen:
                    continue
                seen.add(mat.name)
                for node in master_nodes(mat):
                    kind = apply_transition_map(mat, node, self.amount, height, self.seed)
                    (eyes if kind == 'EYE' else done).append(mat.name)
        if not done and not eyes:
            self.report({'WARNING'}, "No HB Master materials on the selected meshes")
            return {'CANCELLED'}
        extra = f", eyes set to 0: {', '.join(eyes)}" if eyes else ""
        self.report({'INFO'}, f"Transition map on {len(done)} material(s){extra}")
        return {'FINISHED'}


class SUB_PT_hb_master_shader(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash'
    bl_label = 'Master Shader'
    bl_parent_id = "SUB_PT_bake_texs"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        ng = bpy.data.node_groups.get(MASTER_NAME)
        if ng is None:
            layout.label(text='Not in this file yet', icon='INFO')
        else:
            version = ng.get(VERSION_KEY, 0)
            if version < MASTER_VERSION:
                layout.label(text=f'v{version} in file - update available', icon='ERROR')
            else:
                layout.label(text=f'{MASTER_NAME} v{version}', icon='CHECKMARK')
        layout.operator('sub.hb_master_shader_update', icon='NODETREE')
        row = layout.row()
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator('sub.hb_master_shader_convert', icon='MATERIAL')
        layout.operator_menu_enum('sub.hb_master_shader_preset', 'preset',
                                  text='Apply Preset to Active Material', icon='PRESET')
        layout.operator('sub.hb_surface_detail', icon='TEXTURE')
        layout.operator('sub.hb_transition_map', icon='MOD_WAVE')
        row = layout.row(align=True)
        row.operator('sub.hb_game_preview', text='Game Preview', icon='SHADING_RENDERED').view = '4'
        row.operator('sub.hb_game_preview', text='Toon', icon='SHADING_SOLID').view = '0'

        layout.operator('sub.hb_hair_strands_add', icon='STRANDS')
