from . import ui
from . import version_check

def register():
    """Register updater components"""
    version_check.register_properties()

def unregister():
    """Unregister updater components"""
    version_check.unregister_properties()