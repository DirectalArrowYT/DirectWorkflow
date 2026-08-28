"""
Keep unused datablocks from being discarded on save.

Blender drops any datablock with zero users when the file is written. That is
normally housekeeping, but it is hostile to this workflow, where plenty of data
is deliberately not assigned to anything:

  - actions that are not the armature's current one - every animation in a
    moveset except the one being edited
  - side-loaded materials, which exist precisely to not be on a mesh
  - baked images that have been written to disk but not assigned to a slot yet
  - node groups kept around between materials

A fake user is Blender's own answer: a reference that exists only to stop the
collection. This marks everything at once, and can keep doing so on every save
so newly created data is protected without having to remember.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator
from bpy.props import BoolProperty


# Datablock collections worth protecting, as (bpy.data attribute, label).
# Meshes, objects and armatures are deliberately absent: those are only
# user-less when genuinely orphaned, and pinning them would keep every deleted
# mesh in the file forever.
PROTECTED_COLLECTIONS = (
    ('actions', 'Actions'),
    ('materials', 'Materials'),
    ('images', 'Images'),
    ('node_groups', 'Node Groups'),
    ('texts', 'Texts'),
    ('palettes', 'Palettes'),
)


def protect_all(collections=None):
    """Set a fake user on every datablock in the given collections.

    Returns {label: newly protected count}.
    """
    results = {}
    for attribute, label in PROTECTED_COLLECTIONS:
        if collections is not None and attribute not in collections:
            continue
        data = getattr(bpy.data, attribute, None)
        if data is None:
            continue
        count = 0
        for datablock in data:
            # Library data belongs to the file it was linked from; setting a
            # fake user on it is not ours to do and does not survive anyway.
            if getattr(datablock, 'library', None) is not None:
                continue
            if not datablock.use_fake_user:
                try:
                    datablock.use_fake_user = True
                    count += 1
                except AttributeError:
                    pass
        results[label] = count
    return results


@persistent
def _protect_on_save(_dummy):
    scene = bpy.context.scene if bpy.context else None
    if scene is None:
        return
    scene_props = getattr(scene, 'sub_scene_properties', None)
    if scene_props is None or not getattr(scene_props, 'auto_protect_datablocks', False):
        return
    results = protect_all()
    total = sum(results.values())
    if total:
        print(f'[sub] Protected {total} datablock(s) with a fake user before saving.')


def register_handler():
    if _protect_on_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_protect_on_save)


def unregister_handler():
    if _protect_on_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_protect_on_save)


class SUB_OP_protect_datablocks(Operator):
    """Give every action, material, image and node group a fake user"""
    bl_idname = 'sub.protect_datablocks'
    bl_label = 'Protect Unused Data'
    bl_description = (
        'Set a fake user on all actions, materials, images, node groups and '
        'texts so Blender stops discarding the ones that are not currently '
        'assigned to anything when the file is saved'
    )
    bl_options = {'REGISTER', 'UNDO'}

    actions: BoolProperty(name='Actions', default=True)
    materials: BoolProperty(name='Materials', default=True)
    images: BoolProperty(name='Images', default=True)
    node_groups: BoolProperty(name='Node Groups', default=True)
    texts: BoolProperty(name='Texts', default=True)
    palettes: BoolProperty(name='Palettes', default=False)

    def draw(self, context):
        layout = self.layout
        column = layout.column(align=True)
        for attribute, label in PROTECTED_COLLECTIONS:
            data = getattr(bpy.data, attribute, None)
            unprotected = sum(1 for d in (data or ())
                              if not d.use_fake_user and d.library is None)
            row = column.row()
            row.prop(self, attribute)
            row.label(text=f'{unprotected} unprotected')

        scene_props = getattr(context.scene, 'sub_scene_properties', None)
        if scene_props is not None:
            layout.separator()
            layout.prop(scene_props, 'auto_protect_datablocks')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def execute(self, context):
        wanted = {attribute for attribute, _ in PROTECTED_COLLECTIONS
                  if getattr(self, attribute, False)}
        results = protect_all(wanted)
        total = sum(results.values())
        detail = ', '.join(f'{count} {label.lower()}'
                           for label, count in results.items() if count)
        if total:
            self.report({'INFO'}, f'Protected {total} datablock(s): {detail}')
        else:
            self.report({'INFO'}, 'Everything was already protected.')
        return {'FINISHED'}
