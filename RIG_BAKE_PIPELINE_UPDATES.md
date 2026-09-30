# Animation rig, export, hair UV, normals and HB Master Shader updates

Tested on Blender 4.5 LTS and 5.0.

## Animation rig

### New IK engine (`source/extras/smash_ik.py`)
The Blender IK constraints on ArmL/KneeL are replaced by an analytic two-bone IK
(law-of-cosines driver + Damped/Locked Track on `BL_MCH_IK*` bones).

- **The pole always decides the bend.** The old solver started from the FK keys
  underneath, so while matching an existing animation it looked fine, but posing in
  IK dropped the elbow straight down even with ArmIK behind it.
- **Deterministic:** the same control pose gives the same limb in every action.
- **Twist rings** (`BL_IKTwist_<bone>`) for the upper and lower arm/leg.
- **Foot roll** (`BL_IKFootRoll_<side>`): rotate forward to roll onto the ball of
  the foot with the toes on the floor, back to lift the toes. **Toe control**
  (`BL_IKToe_<side>`).
- Control names (HandIK/ArmIK/FootIK/KneeIK) and the `sub_use_ik_arms/legs`
  switches are unchanged, so the switch, visibility and keying tools keep working.
- Matching IK to an FK animation is exact (0.001 over a 200-frame test animation).
- **Create Animation Rig** builds it. **Create IK Bones** uses it too (the old
  solver is still there under "Legacy IK Solver"). An existing rig gets an
  **Upgrade IK (pole-driven)** button in the Animation panel.
- **Snap IK → FK / FK → IK** buttons (current frame, keyed) to switch without a pop.
- Bake & Remove IK deletes every `BL_*IK*` bone and its drivers.

### Mouth controls (`source/extras/mouth_rig.py`)
When the armature has mouth bones (Jaw, Uplip*/Downlip*, as on the MHA fighters):
`BL_Mouth` (down opens the jaw, sideways shifts it, forward/back pushes it),
`BL_MouthCornerL/R` and `BL_UpLip`/`BL_DownLip`. They add on top of the keyed
mouth animation. "Add Mouth Controls" in Create Animation Rig; Bake and Remove Rig
bakes them.

### Thumb
The thumb pad folded the thumb past the palm and back over the hand, because three
joints at the finger maximum add up to about 250°. It now folds across the palm
toward the little finger, and the base joint moves least.

### Fixes
- `finger_sliders`: `PoseBone.select` does not exist on 4.5+ (rig creation crashed).
- "Clean keyframes after creation" let error build up when it removed several keys
  in a row, which flattened slow motion: a test character's head moved 0.026 units. Every
  removed key is now checked against the final segment, and the rig uses a 1e-5
  tolerance.

## Animation export: Bake Rig on Export (on by default)
You no longer have to Bake & Remove IK before exporting. On export, bones posed by
the rig (IK limbs, finger sliders, mouth controls) are written as they are
displayed. The rig controls (HandIK, FootIK, ...) are left out of the .nuanmb. The
scene is not changed. This covers single export, batch export and the PSA batch
retarget.

On a test character: the baked export re-imports exactly (0.0 error). The raw keys were up to
2.8 units off when animating in IK.

## Hair Bake UVs (Model Tools panel)
Smart Seams unwraps the UV map in place, which throws away the cel-shade UVs the
hair texture is mapped with. MHA hair stacks every card on one texture strip:
Aizawa's hair has 99.8% overlap.

1. **Make Bake UVs:** copies map1 to `BakeUV` and unwraps only the copy with Smart
   Seams, with no overlap. map1 stays the render UV map.
2. **Bake Textures** as usual. Cycles reads the hair texture through map1 and writes
   into the BakeUV layout.
3. **Use Bake UVs:** BakeUV becomes map1 for export. The old UVs are kept inside
   the mesh (not exported). **Restore Cel UVs** brings them back.

Results: Aizawa (Pl13) goes from 99.8% to 0% overlap, and Ch013 from 100% to 0%.
The transferred texture matches the source exactly.

## Smart Normals (Model Tools panel)
Replaces anime or flat-looking normals, choosing the mode by material name:
- **Hair:** combines the overall rounded form, local clump volume and a little of
  the mesh's own normal, welded so card splits don't show.
- **Soft** (face): a lighter version of Hair.
- **Weighted** (everything else): face-area weighted smooth normals with creases
  kept sharp.
- Eyes and mouth meshes are left alone.

The result is written as custom normals, which the model exporter exports.

## HB Master Shader v2
- New inputs:
  - **AO Map** (the game's AO texture).
  - **Toon Amount**.
  - **Shadow Color Map** and **Shadow Map Amount**.
  - **Deep Shadow** (tint and threshold) for a second, darker shadow band.
- New **Eye** and **Unlit** presets. Preset guessing handles brow/lash, eye,
  eyeshadow (skin makeup) and glow/emissive names.
- Converting a PSK-imported material splits its AO multiply back into Base Color +
  AO Map.
- With **AO to PRM** on, a linked AO Map goes into PRM.b instead of a ray-traced
  AO pass.

## Misc
- Classes registered both by their module and by `new_classes_to_register` no
  longer print "already registered" at startup.
