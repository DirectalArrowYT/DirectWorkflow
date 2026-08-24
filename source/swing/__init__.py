from . import operators
from . import sub_swing_data
from . import ui    
from . import preset_handler

# Install presets on import
preset_handler.install_presets()

def register():
    preset_handler.register()

def unregister():
    preset_handler.unregister()    