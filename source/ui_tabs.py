"""
Where every sidebar panel of the add-on lives: which tab, and in what order.

The panels are defined all over the add-on, and Blender orders sidebar tabs (and the
panels inside them) by registration order, which follows module import order rather
than any plan. `organize()` runs at the end of the add-on's register(): it re-registers
the add-on's sidebar panels in the order below, with the tab set here, so the layout is
decided in this one table.
"""

import bpy

TAB_IO = "Smash"
TAB_ANIM = "Smash Anim"
TAB_MODEL = "Smash Model"
TAB_SWING = "Smash Swing"
TAB_STAGE = "Smash Stage"

TABS = (TAB_IO, TAB_ANIM, TAB_MODEL, TAB_SWING, TAB_STAGE)

# Top-level panels, in order, per tab. Sub-panels follow their parent.
LAYOUT = {
    TAB_IO: (
        "SUB_PT_import_model",
        "SUB_PT_import_anim",
        "SUB_PT_export_model",
        "SUB_PT_export_anim",
        "SUB_PT_raw_animations",
        "SUB_PT_reimport_materials",
        "SUB_PT_update_plugin",
    ),
    TAB_ANIM: (
        "SUB_PT_anim_rig",
        "SUB_PT_anim_poses",
        "SUB_PT_face_picker",
        "SUB_PT_anim_eyes",
        "SUB_PT_rig_helper",
        "SUB_PT_weapon_rig",
        "SUB_PT_anim_utilities",
        "SUB_PT_retargeting_main",
        "SUB_PT_anim_legacy_ik",
    ),
    TAB_MODEL: (
        "SUB_PT_model_viewport",
        "SUB_PT_model_mesh",
        "SUB_PT_model_uvs",
        "SUB_PT_model_eyes",
        "SUB_PT_model_bones",
        "SUB_PT_bake_texs",
        "SUB_PT_vis_mesh_bake",
        "SUB_PT_attribute_renamer",
        "SUB_PT_rig_combiner",
        "SUB_PT_ultimate_exo_skel",
        "SUB_PT_fighter_scale",
    ),
    TAB_SWING: (
        "SUB_PT_swing_io",
        "SUB_PT_auto_swing_bones",
        "SUB_PT_swing_axis",
    ),
    TAB_STAGE: (
        "SUB_PT_stage_tools",
    ),
}

_PLACE = {name: (tab_index, order)
          for tab_index, tab in enumerate(TABS)
          for order, name in enumerate(LAYOUT[tab])}
_TAB_OF = {name: tab for tab in TABS for name in LAYOUT[tab]}


def _our_sidebar_panels(package):
    panels = []
    for cls in bpy.types.Panel.__subclasses__():
        if not getattr(cls, 'is_registered', False):
            continue
        if not cls.__module__.startswith(package):
            continue
        if getattr(cls, 'bl_space_type', None) != 'VIEW_3D' or getattr(cls, 'bl_region_type', None) != 'UI':
            continue
        panels.append(cls)
    return panels


def _panel_id(cls):
    return getattr(cls, 'bl_idname', '') or cls.__name__


def organize(package):
    """Put the add-on's sidebar panels in their tabs, in order. Panels missing from LAYOUT
    stay in the first tab, after the listed ones, so nothing disappears."""
    panels = _our_sidebar_panels(package)
    by_id = {_panel_id(cls): cls for cls in panels}
    children = {}
    roots = []
    for cls in panels:
        parent = getattr(cls, 'bl_parent_id', '')
        if parent and parent in by_id:
            children.setdefault(parent, []).append(cls)
        else:
            roots.append(cls)

    unplaced = [c.__name__ for c in roots if c.__name__ not in _PLACE]
    if unplaced:
        print(f"Smash Ultimate Blender Tools: sidebar panels without a tab in ui_tabs.py: {unplaced}")
    roots.sort(key=lambda c: _PLACE.get(c.__name__, (0, 1000)))

    ordered = []

    def walk(cls, tab):
        ordered.append((cls, tab))
        for child in children.get(_panel_id(cls), []):
            walk(child, tab)

    for cls in roots:
        walk(cls, _TAB_OF.get(cls.__name__, TAB_IO))

    for cls, _tab in reversed(ordered):
        bpy.utils.unregister_class(cls)
    for index, (cls, tab) in enumerate(ordered):
        cls.bl_category = tab
        cls.bl_order = index
        bpy.utils.register_class(cls)
