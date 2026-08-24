# Load the native ssbh_data_py extension for this OS and CPython ABI.
# Builds are extracted from https://github.com/ScanMountGoat/ssbh_data_py/releases
# Shipped ABIs: cp310 (Blender 4.0–4.1), cp311 (Blender 4.2–4.5), cp313 (Blender 5.x).
from .._native_loader import load_native

load_native('ssbh_data_py')
