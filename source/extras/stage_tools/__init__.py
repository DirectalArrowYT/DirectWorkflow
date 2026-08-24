from . import light_nuanmb
from . import panel
from . import shpcanim


classes = (
    *light_nuanmb.classes,
    *shpcanim.classes,
    panel.SUB_PT_stage_tools,
)


def register():
    import bpy
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    if shpcanim.shpc_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(shpcanim.shpc_frame_change)


def unregister():
    import bpy
    if shpcanim.shpc_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(shpcanim.shpc_frame_change)
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
