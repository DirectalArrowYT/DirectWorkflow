# Smash Viewport native plugin

Blender's Python GPU overlay cannot match SSBH Editor. This crate wraps
`ssbh_wgpu` (the same renderer SSBH Editor uses), renders offscreen, and the
addon blits that image into a 3D View set to **Rendered**.

Solid / Material shading stay Workbench / EEVEE.

CI ships plugins for all three platforms so Smash Viewport works after an
addon update:

- Windows: `native/bin/ssbh_blender_preview.dll` (DX12)
- Linux: `native/bin/libssbh_blender_preview.so` (Vulkan)
- macOS: `native/bin/libssbh_blender_preview.dylib` (Metal, Intel + Apple Silicon)

Linux and macOS use CPU blit into the viewport (no DX/GL interop). Rebuild
and replace the matching file when the crate changes.

## Build

1. Put `ssbh_wgpu` at `native/vendor/ssbh_wgpu`.
   - Copy the SSBH Editor `vendor/ssbh_wgpu` folder, or
   - Clone [ScanMountGoat/ssbh_editor](https://github.com/ScanMountGoat/ssbh_editor)
     at the commit in `native/ssbh_editor.rev` and copy `vendor/ssbh_wgpu`.
2. Nightly or recent stable Rust (wgpu 29).
3. From `native/ssbh_blender_preview/`:

```
python patch_vendor.py
cargo build --release
```

Copy the result over the shipped plugin:

- Windows: `target/release/ssbh_blender_preview.dll` → `native/bin/ssbh_blender_preview.dll`
- Linux: `target/release/libssbh_blender_preview.so` → `native/bin/libssbh_blender_preview.so`
- macOS: `target/release/libssbh_blender_preview.dylib` → `native/bin/libssbh_blender_preview.dylib`

The addon searches `native/bin/` then `target/release/` then `target/debug/`.

Do not commit `vendor/`, `target/`, or `*.reload.dll`. Do not put a
machine-specific path in `Cargo.toml`.

The patch step adds per-mesh transforms and opaque coverage. Use a local vendor copy, not a junction to another checkout. CI applies the same patch.

## Regression checks

Run `Blender --background --factory-startup --python-exit-code 1 --python native/ssbh_blender_preview/test_viewport.py` from the repository root. Optional arguments after `--` are the Vegito model folder, transformation `.nuanmb`, and built plugin path. The optional GPU checks verify opaque animated hair, mesh translation/reset, material reset/reapply, transparent background, and report the median material-update time across five 120-frame samples.

Material animation now uploads only changed material entries. Mesh transform sync shares armature inverse matrices and uploads only changed meshes. These optimizations preserve rendering quality; the material microbenchmark is not an end-to-end viewport FPS measurement.
