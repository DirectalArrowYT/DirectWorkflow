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
