"""
Smash effects in Blender: empties that play an effect (vanilla or a mod's .eff), baked
frame by frame by EffectForge and laid over the camera view and the render.
"""

import bpy

from . import forge
from . import properties
from . import layering
from . import operators
from . import ui


def register():
    properties.register()
    for cls in operators.classes + ui.classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(operators.classes + ui.classes):
        bpy.utils.unregister_class(cls)
    properties.unregister()
