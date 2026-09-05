"""Temporary transparent viewport photo / GIF copied to the clipboard."""

from __future__ import annotations

import atexit
import ctypes
import os
import struct
import sys
import time

import bpy
import numpy as np
from bpy.props import EnumProperty
from bpy.types import Operator

_CAPTURE_DIR_NAME = "smash_ultimate_captures"
_temp_files = []
_atexit_registered = False

# Match SSBH Editor's clipboard GIF recorder (src/capture.rs).
GIF_FRAME_STEP = 2
GIF_FRAME_DELAY_MS = 33
GIF_FRAMES_PER_TICK = 2
GIF_ENCODER_SPEED = 10


def _capture_dir():
    root = getattr(bpy.app, "tempdir", "") or ""
    if not root:
        import tempfile
        root = tempfile.gettempdir()
    path = os.path.join(root, _CAPTURE_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _new_temp_path(ext):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"smash_{stamp}_{os.getpid()}_{len(_temp_files)}.{ext.lstrip('.')}"
    path = os.path.join(_capture_dir(), name)
    _temp_files.append(path)
    return path


def cleanup_temp_captures():
    for path in list(_temp_files):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    _temp_files.clear()
    folder = None
    try:
        folder = _capture_dir()
    except Exception:
        folder = None
    if folder and os.path.isdir(folder):
        try:
            os.rmdir(folder)
        except OSError:
            pass


def _same_rna(left, right):
    if left is None or right is None:
        return False
    if left is right:
        return True
    try:
        return left.as_pointer() == right.as_pointer()
    except Exception:
        return left == right


def _window_region(area):
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _window_for_area(context, area):
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return getattr(context, "window", None)
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for candidate in screen.areas:
            if _same_rna(candidate, area):
                return window
    return getattr(context, "window", None)


def _find_view3d(context):
    area = getattr(context, "area", None)
    if area is not None and getattr(area, "type", "") == "VIEW_3D":
        region = _window_region(area)
        if region is not None:
            return area, region
    screens = []
    screen = getattr(context, "screen", None)
    if screen is not None:
        screens.append(screen)
    wm = getattr(context, "window_manager", None)
    if wm is not None:
        for window in wm.windows:
            other = getattr(window, "screen", None)
            if other is None:
                continue
            if not any(_same_rna(other, existing) for existing in screens):
                screens.append(other)
    for screen in screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = _window_region(area)
            if region is not None:
                return area, region
    return None, None


def _view3d_shading(area):
    space = area.spaces.active if area is not None else None
    shading = getattr(space, "shading", None)
    return str(getattr(shading, "type", "") or "")


def _should_use_smash_capture(context, area=None):
    from . import smash_viewport

    scene = getattr(context, "scene", None)
    if not smash_viewport._viewport_enabled(scene):
        return False
    if area is None:
        area, _region = _find_view3d(context)
    shading = _view3d_shading(area)
    # Smash Viewport only draws in Rendered. Solid / Material stay Workbench / EEVEE.
    return shading in ("", "RENDERED")


def _capture_smash_rgba(context, restore=True, flush=True, wait=False):
    from . import smash_viewport
    return smash_viewport.capture_rgba_frame(
        context,
        transparent=True,
        restore_background=restore,
        flush=flush,
        wait=wait,
    )


def _free_gl_cache(cache):
    if not cache:
        return
    offscreen = cache.pop("offscreen", None)
    cache.clear()
    if offscreen is None:
        return
    try:
        offscreen.free()
    except Exception:
        pass


def _gl_offscreen(cache, width, height):
    import gpu

    size = (int(width), int(height))
    if cache is not None:
        current = cache.get("offscreen")
        if current is not None and cache.get("size") == size:
            return current
        _free_gl_cache(cache)
    offscreen = gpu.types.GPUOffScreen(size[0], size[1])
    if cache is not None:
        cache["offscreen"] = offscreen
        cache["size"] = size
    return offscreen


def _draw_view3d(offscreen, scene, view_layer, space, region, view_matrix, projection):
    args = (scene, view_layer, space, region, view_matrix, projection)
    for kwargs in (
        {"do_color_management": True, "draw_background": False},
        {"draw_background": False},
        {"do_color_management": True},
        {},
    ):
        try:
            offscreen.draw_view3d(*args, **kwargs)
            return
        except TypeError:
            continue
    raise TypeError("GPUOffScreen.draw_view3d is not available")


def _rgba_from_framebuffer(width, height):
    import gpu

    framebuffer = gpu.state.active_framebuffer_get()
    buffer = framebuffer.read_color(0, 0, width, height, 4, 0, "UBYTE")
    try:
        pixels = np.frombuffer(buffer, dtype=np.uint8, count=width * height * 4).copy()
    except (TypeError, ValueError, BufferError):
        pixels = np.array(buffer, dtype=np.uint8).reshape(-1)[: width * height * 4].copy()
    pixels = pixels.reshape((height, width, 4))
    pixels = np.ascontiguousarray(pixels[::-1])
    alpha = pixels[:, :, 3]
    pixels[:, :, 3] = np.where(alpha < 10, 0, 255)
    return pixels.tobytes()


def _capture_gpu_view3d(context, area, region, cache=None):
    import gpu

    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None
    width, height = int(region.width), int(region.height)
    if width < 8 or height < 8:
        return None
    overlay = getattr(space, "overlay", None)
    old_overlays = overlay.show_overlays if overlay is not None else None
    owned = cache is None
    offscreen = _gl_offscreen(cache, width, height)
    try:
        if overlay is not None:
            overlay.show_overlays = False
        view_matrix = rv3d.view_matrix.copy()
        projection = rv3d.window_matrix.copy()
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
            _draw_view3d(
                offscreen,
                context.scene,
                context.view_layer,
                space,
                region,
                view_matrix,
                projection,
            )
            rgba = _rgba_from_framebuffer(width, height)
        return width, height, rgba
    finally:
        if overlay is not None and old_overlays is not None:
            overlay.show_overlays = old_overlays
        if owned:
            try:
                offscreen.free()
            except Exception:
                pass


def _capture_opengl_ops(context, area, region):
    window = _window_for_area(context, area) or getattr(context, "window", None)
    if window is None:
        return None
    space = area.spaces.active
    overlay = getattr(space, "overlay", None)
    scene = context.scene
    render = scene.render
    image_settings = render.image_settings
    path = os.path.join(getattr(bpy.app, "tempdir", "") or _capture_dir(), "smash_gl_capture.png")
    old = {
        "filepath": render.filepath,
        "file_format": image_settings.file_format,
        "color_mode": image_settings.color_mode,
        "film_transparent": bool(getattr(render, "film_transparent", False)),
        "overlays": overlay.show_overlays if overlay is not None else None,
    }
    loaded = None
    try:
        if overlay is not None:
            overlay.show_overlays = False
        render.film_transparent = True
        image_settings.file_format = "PNG"
        image_settings.color_mode = "RGBA"
        render.filepath = path
        override = {"window": window, "area": area, "region": region, "scene": scene}
        try:
            with context.temp_override(**override):
                bpy.ops.render.opengl(write_still=True, view_context=True)
        except TypeError:
            with context.temp_override(**override):
                bpy.ops.render.opengl(write_still=True)
        if not os.path.isfile(path):
            return None
        loaded = bpy.data.images.load(path)
        width, height = int(loaded.size[0]), int(loaded.size[1])
        pixels = np.empty(width * height * 4, dtype=np.float32)
        loaded.pixels.foreach_get(pixels)
        pixels = (np.clip(pixels, 0.0, 1.0) * 255.0).astype(np.uint8).reshape((height, width, 4))
        pixels = np.ascontiguousarray(pixels[::-1])
        alpha = pixels[:, :, 3]
        pixels[:, :, 3] = np.where(alpha < 10, 0, 255)
        return width, height, pixels.tobytes()
    finally:
        if loaded is not None:
            bpy.data.images.remove(loaded)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        render.filepath = old["filepath"]
        image_settings.file_format = old["file_format"]
        image_settings.color_mode = old["color_mode"]
        render.film_transparent = old["film_transparent"]
        if overlay is not None and old["overlays"] is not None:
            overlay.show_overlays = old["overlays"]


def _capture_opengl_rgba(context, cache=None):
    area, region = _find_view3d(context)
    if area is None or region is None:
        return None
    window = _window_for_area(context, area) or getattr(context, "window", None)
    override = {}
    if window is not None:
        override["window"] = window
    override["area"] = area
    override["region"] = region
    override["scene"] = context.scene
    try:
        with context.temp_override(**override):
            captured = _capture_gpu_view3d(context, area, region, cache=cache)
        if captured is not None:
            return captured
    except Exception:
        captured = None
    try:
        return _capture_opengl_ops(context, area, region)
    except Exception:
        return None


def capture_rgba(context, restore=True, flush=True, wait=False, gl_cache=None):
    area, _region = _find_view3d(context)
    if _should_use_smash_capture(context, area):
        result = _capture_smash_rgba(context, restore=restore, flush=flush, wait=wait)
        if result is not None:
            return result
    return _capture_opengl_rgba(context, cache=gl_cache)


def save_png(path, width, height, rgba):
    arr = np.frombuffer(rgba, dtype=np.uint8, count=width * height * 4).reshape((height, width, 4))
    arr = np.ascontiguousarray(arr[::-1])
    image = bpy.data.images.new("SmashCaptureTmp", width, height, alpha=True)
    try:
        floats = (arr.astype(np.float32) / 255.0).ravel()
        image.pixels.foreach_set(floats)
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    return path


def _open_native_gif_encoder(path, delay_ms=GIF_FRAME_DELAY_MS, speed=GIF_ENCODER_SPEED):
    from . import smash_viewport

    lib = smash_viewport.load_native_library()
    if lib is None or not hasattr(lib, "ssbh_preview_gif_begin"):
        return None
    rc = lib.ssbh_preview_gif_begin(
        path.encode("utf-8"),
        int(delay_ms),
        int(speed),
    )
    if rc != 0:
        raise RuntimeError(smash_viewport._native_error() or "Could not start the GIF encoder")
    return lib


class NativeGifEncoder:
    """Incremental GIF writer using the same `image` crate encoder as SSBH Editor."""

    def __init__(self, path, delay_ms=GIF_FRAME_DELAY_MS, speed=GIF_ENCODER_SPEED):
        self.path = path
        self._lib = _open_native_gif_encoder(path, delay_ms=delay_ms, speed=speed)
        if self._lib is None:
            raise RuntimeError("native GIF encoder unavailable")

    def add_frame(self, width, height, rgba):
        from . import smash_viewport

        needed = int(width) * int(height) * 4
        payload = bytes(rgba[:needed])
        buf = (ctypes.c_uint8 * needed).from_buffer_copy(payload)
        rc = self._lib.ssbh_preview_gif_add_frame(
            ctypes.addressof(buf),
            needed,
            int(width),
            int(height),
        )
        if rc != 0:
            raise RuntimeError(smash_viewport._native_error() or "Could not encode GIF frame")

    def finish(self):
        from . import smash_viewport

        rc = self._lib.ssbh_preview_gif_finish()
        if rc != 0:
            raise RuntimeError(smash_viewport._native_error() or "Could not finish GIF")

    def cancel(self):
        try:
            self._lib.ssbh_preview_gif_cancel()
        except Exception:
            pass


class PillowGifEncoder:
    def __init__(self, path, delay_ms=GIF_FRAME_DELAY_MS):
        from PIL import Image

        self._Image = Image
        self.path = path
        self.delay_ms = max(1, int(delay_ms))
        self._frames = []

    def add_frame(self, width, height, rgba):
        image = self._Image.frombytes("RGBA", (int(width), int(height)), bytes(rgba))
        self._frames.append(image)

    def finish(self):
        if not self._frames:
            raise RuntimeError("No GIF frames")
        converted = [self._to_palette(frame) for frame in self._frames]
        converted[0].save(
            self.path,
            save_all=True,
            append_images=converted[1:],
            loop=0,
            duration=self.delay_ms,
            disposal=2,
            transparency=0,
        )

    def cancel(self):
        self._frames.clear()

    def _to_palette(self, image):
        alpha = image.getchannel("A")
        method = getattr(self._Image, "FASTOCTREE", None)
        if method is None:
            quantize = getattr(self._Image, "Quantize", None)
            method = getattr(quantize, "FASTOCTREE", 2) if quantize is not None else 2
        quantized = image.convert("RGB").quantize(colors=255, method=method)
        mask = alpha.point(lambda value: 255 if value < 128 else 0)
        clear = self._Image.new("P", image.size, 0)
        quantized.paste(clear, mask=mask)
        quantized.info["transparency"] = 0
        return quantized


def _new_gif_encoder(path):
    try:
        return NativeGifEncoder(path)
    except RuntimeError as exc:
        if "unavailable" not in str(exc):
            raise
    try:
        return PillowGifEncoder(path)
    except ImportError:
        raise RuntimeError(
            "GIF encoding needs the Smash Viewport plugin rebuilt, or Pillow installed "
            "for Blender's Python"
        ) from None


def _clipboard_file_path(path):
    try:
        canonical = os.path.realpath(path)
    except OSError:
        canonical = os.path.abspath(path)
    # Windows canonicalize() prefixes paths with \\?\, which CF_HDROP cannot use.
    if canonical.startswith("\\\\?\\UNC\\"):
        canonical = "\\\\" + canonical[8:]
    elif canonical.startswith("\\\\?\\"):
        canonical = canonical[4:]
    return canonical


def _win_global_data(data):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    GMEM_MOVEABLE = 0x0002
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("GlobalAlloc failed")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalLock failed")
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def _win_hdrop_bytes(path):
    # DROPFILES is 20 bytes on Windows. ctypes may pad POINT, so pack it by hand.
    encoded = _clipboard_file_path(path).encode("utf-16-le") + b"\x00\x00\x00\x00"
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    return header + encoded


def _rgba_to_dib(width, height, rgba):
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        int(width),
        int(height),
        1,
        32,
        0,
        int(width) * int(height) * 4,
        0,
        0,
        0,
        0,
    )
    arr = np.frombuffer(rgba, dtype=np.uint8, count=width * height * 4).reshape((height, width, 4))
    bgra = np.ascontiguousarray(arr[::-1, :, [2, 1, 0, 3]])
    return header + bgra.tobytes()


def _copy_windows_clipboard(path, png_bytes=None, rgba=None, size=None, as_file=False):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

    CF_DIB = 8
    CF_HDROP = 15
    opened = False
    for _ in range(12):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.05)
    if not opened:
        raise OSError("Could not open the clipboard")
    handles = []
    try:
        user32.EmptyClipboard()
        if as_file and path:
            handle = _win_global_data(_win_hdrop_bytes(path))
            handles.append(handle)
            if not user32.SetClipboardData(CF_HDROP, handle):
                raise OSError("SetClipboardData CF_HDROP failed")
            handles.remove(handle)
        if png_bytes and not as_file:
            png_format = user32.RegisterClipboardFormatW("PNG")
            handle = _win_global_data(png_bytes)
            handles.append(handle)
            if png_format and user32.SetClipboardData(png_format, handle):
                handles.remove(handle)
            if rgba is not None and size is not None:
                dib = _rgba_to_dib(size[0], size[1], rgba)
                handle = _win_global_data(dib)
                handles.append(handle)
                if user32.SetClipboardData(CF_DIB, handle):
                    handles.remove(handle)
            if path:
                handle = _win_global_data(_win_hdrop_bytes(path))
                handles.append(handle)
                if user32.SetClipboardData(CF_HDROP, handle):
                    handles.remove(handle)
        if not as_file and not png_bytes:
            raise OSError("Nothing to copy")
    finally:
        for handle in handles:
            try:
                kernel32.GlobalFree(handle)
            except Exception:
                pass
        user32.CloseClipboard()


def copy_to_clipboard(path, png_bytes=None, rgba=None, size=None, as_file=False):
    if sys.platform == "win32":
        _copy_windows_clipboard(path, png_bytes=png_bytes, rgba=rgba, size=size, as_file=as_file)
        return True
    try:
        bpy.context.window_manager.clipboard = path
    except Exception:
        return False
    return True


class SUB_OP_gif_or_photo(Operator):
    bl_idname = "sub.gif_or_photo"
    bl_label = "Gif or Photo"
    bl_description = (
        "Copy a transparent viewport photo or animation GIF to the clipboard. "
        "The temp file is deleted when Blender closes"
    )
    bl_options = {"REGISTER"}

    mode: EnumProperty(
        name="Capture",
        items=(
            ("PHOTO", "Photo", "Screenshot of the viewport without background"),
            ("GIF", "GIF", "Record the loaded animation without background"),
        ),
        default="PHOTO",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mode", expand=True)
        layout.label(text="Copied to clipboard.")
        layout.label(text="Temp file is deleted when Blender closes.")

    def execute(self, context):
        if self.mode == "GIF":
            return self._begin_gif(context)
        return self._take_photo(context)

    def _take_photo(self, context):
        captured = capture_rgba(context, restore=True, flush=True, wait=True)
        if captured is None:
            self.report({"ERROR"}, "Could not capture the viewport.")
            return {"CANCELLED"}
        width, height, rgba = captured
        path = _new_temp_path("png")
        save_png(path, width, height, rgba)
        with open(path, "rb") as handle:
            png_bytes = handle.read()
        try:
            copy_to_clipboard(path, png_bytes=png_bytes, rgba=rgba, size=(width, height), as_file=False)
        except Exception as exc:
            self.report({"WARNING"}, f"Saved temp photo, but clipboard copy failed: {exc}")
            return {"FINISHED"}
        self.report({"INFO"}, "Photo copied to clipboard.")
        return {"FINISHED"}

    def _gif_status(self, context, text):
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set(text)
            except Exception:
                pass

    def _gif_progress(self, context, completed, total):
        total = max(1, int(total))
        completed = max(0, int(completed))
        try:
            context.window_manager.progress_update(100.0 * completed / total)
        except Exception:
            pass
        self._gif_status(context, f"Recording GIF: frame {completed} of {total}")
        area, _region = _find_view3d(context)
        if area is not None:
            area.tag_redraw()

    def _begin_gif(self, context):
        scene = context.scene
        start = int(scene.frame_start)
        end = int(scene.frame_end)
        if end < start:
            self.report({"ERROR"}, "Scene end frame is before the start frame.")
            return {"CANCELLED"}
        path = _new_temp_path("gif")
        try:
            encoder = _new_gif_encoder(path)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self._gif_path = path
        self._gif_encoder = encoder
        self._gif_start = start
        self._gif_end = end
        self._gif_step = GIF_FRAME_STEP
        self._gif_next = float(start)
        self._gif_original = int(scene.frame_current)
        self._gif_completed = 0
        self._gif_total = int((end - start) / GIF_FRAME_STEP) + 1
        self._gif_gl_cache = {}
        self._timer = None

        wm = context.window_manager
        try:
            wm.progress_begin(0, 100)
        except Exception:
            pass
        self._gif_status(context, f"Recording GIF: frame 0 of {self._gif_total}")
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _stop_gif_timer(self, context):
        timer = getattr(self, "_timer", None)
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except Exception:
                pass
            self._timer = None
        try:
            context.window_manager.progress_end()
        except Exception:
            pass
        self._gif_status(context, None)

    def _restore_after_gif(self, context):
        from . import smash_viewport

        scene = getattr(context, "scene", None)
        original = getattr(self, "_gif_original", None)
        if scene is not None and original is not None:
            scene.frame_set(int(original))
        smash_viewport.restore_opaque_background(context)
        area, _region = _find_view3d(context)
        if area is not None:
            area.tag_redraw()

    def _release_gif_gl(self):
        _free_gl_cache(getattr(self, "_gif_gl_cache", None))
        self._gif_gl_cache = None

    def _cancel_gif(self, context, message=None):
        encoder = getattr(self, "_gif_encoder", None)
        self._gif_encoder = None
        if encoder is not None:
            try:
                encoder.cancel()
            except Exception:
                pass
        self._stop_gif_timer(context)
        self._restore_after_gif(context)
        self._release_gif_gl()
        path = getattr(self, "_gif_path", None)
        if path:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
            try:
                _temp_files.remove(path)
            except ValueError:
                pass
        if message:
            self.report({"WARNING"}, message)
        else:
            self.report({"INFO"}, "GIF recording cancelled.")
        return {"CANCELLED"}

    def _finish_gif(self, context):
        encoder = getattr(self, "_gif_encoder", None)
        self._gif_encoder = None
        completed = int(getattr(self, "_gif_completed", 0))
        try:
            if encoder is not None:
                encoder.finish()
        except Exception as exc:
            self._stop_gif_timer(context)
            self._restore_after_gif(context)
            self._release_gif_gl()
            self.report({"ERROR"}, f"Could not finish GIF: {exc}")
            return {"CANCELLED"}
        self._stop_gif_timer(context)
        self._restore_after_gif(context)
        self._release_gif_gl()
        path = getattr(self, "_gif_path", None)
        if not path or not os.path.isfile(path):
            self.report({"ERROR"}, "GIF file was not written.")
            return {"CANCELLED"}
        try:
            copy_to_clipboard(path, as_file=True)
        except Exception as exc:
            self.report({"WARNING"}, f"Saved temp GIF, but clipboard copy failed: {exc}")
            return {"FINISHED"}
        self.report({"INFO"}, f"GIF copied to clipboard ({completed} frames).")
        return {"FINISHED"}

    def _tick_gif(self, context):
        scene = context.scene
        encoder = getattr(self, "_gif_encoder", None)
        if encoder is None:
            return self._cancel_gif(context, "GIF encoder is missing.")
        end = float(self._gif_end)
        for _ in range(GIF_FRAMES_PER_TICK):
            if self._gif_next > end:
                break
            frame = int(round(self._gif_next))
            scene.frame_set(frame)
            context.view_layer.update()
            self._gif_progress(context, self._gif_completed + 1, self._gif_total)
            captured = capture_rgba(
                context,
                restore=False,
                flush=False,
                wait=True,
                gl_cache=getattr(self, "_gif_gl_cache", None),
            )
            if captured is None:
                return self._cancel_gif(context, "Could not capture the viewport.")
            width, height, rgba = captured
            try:
                encoder.add_frame(width, height, rgba)
            except Exception as exc:
                return self._cancel_gif(context, f"Could not encode GIF frame: {exc}")
            self._gif_completed += 1
            self._gif_next += self._gif_step
        if self._gif_next > end:
            return self._finish_gif(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            return self._cancel_gif(context)
        if event.type == "TIMER":
            return self._tick_gif(context)
        return {"RUNNING_MODAL"}


def register():
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(cleanup_temp_captures)
        _atexit_registered = True


def unregister():
    global _atexit_registered
    cleanup_temp_captures()
    if _atexit_registered:
        try:
            atexit.unregister(cleanup_temp_captures)
        except Exception:
            pass
        _atexit_registered = False
