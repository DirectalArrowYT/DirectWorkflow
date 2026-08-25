"""
Shared material-bundling rule.

Materials that are the same surface with a different finish - cloth vs
metal, e.g. Body vs BodyShiny - don't need their own texture set; BodyShiny
keeps its own material parameters (its own metalness etc. in the matl entry)
but shares Body's texture files.

This one rule drives three different places that all need to agree on the
same grouping, or a bundled bake's file names stop lining up with what
model export looks for:
  - source/bake_texs/core.py     bakes one texture set per group
  - create_matl_from_blender_materials.py   points a bundled material's
    texture params at the base material's images instead of its own
  - export_nutexb.py             skips exporting a bundled material's own
    (now-superseded) images, so the output folder doesn't accumulate files
    nothing references
"""

# Any material whose name is another material's name plus one of these
# suffixes bundles into it automatically - e.g. BodyShiny bundles into Body.
MATERIAL_MERGE_SUFFIXES = ["Shiny"]

# Explicit bundles for anything the suffix rule doesn't catch.
#   "Body": ["Body", "BodyShiny", "BodyTrim"]
MATERIAL_GROUPS = {}


def group_key_for(mat_name, all_names):
    """Which texture set / matl group a material belongs to.

    Explicit MATERIAL_GROUPS wins; otherwise a name ending in one of
    MATERIAL_MERGE_SUFFIXES folds into the material it is derived from, but
    only when that base material actually exists.
    """
    for key, members in MATERIAL_GROUPS.items():
        if mat_name in members:
            return key
    for suffix in MATERIAL_MERGE_SUFFIXES:
        if suffix and mat_name.endswith(suffix):
            base = mat_name[:-len(suffix)]
            if base in all_names:
                return base
    return mat_name


def is_bundled(mat_name, all_names):
    """True when mat_name folds into a *different* material's group."""
    return group_key_for(mat_name, all_names) != mat_name


def bundled_material_map(materials):
    """Which materials collapse into which, for a set of Blender materials.

    Returns {bundled material -> base material}, e.g. {BodyShiny: Body}. Only
    includes a pair when the base material is actually present.

    This is what makes a bundle a real merge rather than just shared texture
    files. The baker already writes every member of a bundle into one texture
    set, each filling its own UV islands - so Body's PRM already carries
    BodyShiny's metalness and roughness in the texels BodyShiny covers. Once
    that is true the second material has nothing left to say: it points at the
    same textures, and the per-texel PRM values are what actually drive the
    shading. Exporting it anyway costs an extra material and an extra draw call
    to render a surface the first material already describes.
    """
    import bpy

    by_name = {material.name: material for material in materials if material is not None}
    all_names = set(by_name)

    mapping = {}
    for name, material in by_name.items():
        key = group_key_for(name, all_names)
        if key == name:
            continue
        base = by_name.get(key) or bpy.data.materials.get(key)
        if base is not None:
            mapping[material] = base
    return mapping


# --- Side-loaded materials ---------------------------------------------------
# A "side-loaded" material is a twin of a real, mesh-assigned material that
# Material Re-Importer creates when its Side-Load option is on: it carries
# freshly re-imported smash material data (shader, textures, params) but is
# never assigned to any mesh, so the live material - and whatever you've set
# up on it - is left completely alone. Export's "Prefer Side-Loaded Materials"
# toggle reads from the twin instead of the live material when one exists.
#
# The naming convention is deliberately explicit rather than relying on
# Blender's own ".001" collision suffixing, which is not a signal anyone
# controls - two completely unrelated materials can end up ".001" apart for
# reasons that have nothing to do with side-loading.
SIDE_LOAD_SUFFIX = " (Side-Loaded)"


def side_loaded_name(base_name):
    """The material name a side-loaded twin of base_name would have."""
    return f"{base_name}{SIDE_LOAD_SUFFIX}"
