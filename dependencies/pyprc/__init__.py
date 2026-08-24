# Load the native pyprc extension for this OS and CPython ABI.
# Builds are extracted from https://github.com/BenHall-7/pyprc/releases
# Version-specific folders are tried first; cp37-abi3 wheels are a fallback
# (they work on Python 3.7+ including Blender 4.x / 5.x).
from .._native_loader import load_native

load_native('pyprc')
