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
  **Upgrade IK (pole-driven)** button in Smash Anim → Animation Rig.
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

### Rotate Animation (Smash Anim → Animation Rig)
Turns a whole animation around the vertical axis. For example, turn a ported idle
35 degrees so it reads like the 2D pose it came from.

- Trans / Rot / Hip keep their keys (the list is editable), so they get no rotation
  keys. The rotation goes onto the bones directly below them (Waist, LegC, ...) and
  onto the rig controls that hang off them (HandIK, FootIK, poles), so IK limbs turn
  with the body.
- The axis goes through the hips on every frame: the character turns in place,
  keeps its path, and its feet stay on the floor.
- Throw is left alone, since it's gameplay.
- Works for a whole action, the scene range, or the current frame.

## Animation export: Bake Rig on Export (on by default)
You no longer have to Bake & Remove IK before exporting. On export, bones posed by
the rig (IK limbs, finger sliders, mouth controls) are written as they are
displayed. The rig controls (HandIK, FootIK, ...) are left out of the .nuanmb. The
scene is not changed. This covers single export, batch export and the PSA batch
retarget.

On a test character: the baked export re-imports exactly (0.0 error). The raw keys were up to
2.8 units off when animating in IK.

## Hair Bake UVs (Smash Model → UVs & Normals)
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

## Smart Normals (Smash Model → UVs & Normals)
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

## Sidebar layout
The single "Ultimate" tab (about 25 panels) and the separate "IK Bones" tab are replaced by
five tabs. Which tab each panel sits in, and in what order, is set in one table:
`source/ui_tabs.py`.

- **Smash:** Model Importer, Animation Importer, Model Exporter, Animation Exporter,
  Raw Animations, Material Re-Importer, updater.
- **Smash Anim:**
  - Animation Rig (create, IK/FK, snap, fingers, Rotate Animation, bake and remove)
  - Poses (Idle Pose Library, User Poses)
  - Easy Facial Animation
  - Eyes
  - Hand Control Rig
  - Weapon Rig
  - Animation Utilities (root motion, reset, ground, Mirror Animation)
  - Retargeting
  - Legacy IK (old generators, Bulk IK)
- **Smash Model:**
  - Viewport & Materials
  - Mesh
  - UVs & Normals
  - Bones (Roll Copier, Bone Symmetry, Vanilla Roll Preset)
  - Bake Textures (with HB Master Shader)
  - VIS Mesh Bake
  - Attribute Renamer
  - Rig Combiner
  - Fighter Scale
- **Smash Swing:** Swing, Auto Swing Bones, Swing Bone Axis.
- **Smash Stage:** Stage Tools.

The old "Animation Tools", "Model Tools" and "Misc." panels are split into the sections
above. Their hand-made collapsible boxes are now real sub-panels, so Blender remembers
which ones are open.

Also fixed:
- Disabling the add-on raised an error part-way. Classes registered twice were
  unregistered twice, and Expy Kit stopped at the first panel the retargeting module
  had already removed. Disable and re-enable now work.
- The updater panel said "CrusherD2 / animation-workflow". It now shows the repo and
  branch it actually checks.

## Eye Converter (MHA) (Smash Model tab)
MHA eyes are an eye (sclera) mesh plus an extruded iris disc that a bone slides around.
Smash eyes are one flat surface: the eye white on `map1`, and the iris as a decal on
`uvSet` that the game scrolls through `CustomVector31` on the `EyeL`/`EyeR` materials.
Mario (c00) is the reference. Select the mesh with the eyes and run **Convert Eyes**. Per
eye, it:

- **Finds the iris:** an iris/pupil material, or else the loose part of the eye material
  weighted to `L_eye` / `R_eye`.
- **Renders the iris head-on** into a decal with a transparent surround, and writes
  `uvSet` so the decal lands exactly where the iris was at neutral `CustomVector31`.
- **Paints out the pupil.** MHA eye textures often have a pupil painted into the eye white,
  which would otherwise stay put while the real iris scrolls away.
- **Flattens the iris disc** onto the eye surface (MHA eye meshes have a gap under the iris)
  and gives it eye-white UVs, so the eye is one flat mesh.
- **Moves the eye-bone weights** to the bone above them (Head/face), and replaces the MHA
  vertex colours with `colorSet1` at Mario's neutral 0.502.
- **Makes the materials:** its own object per eye with `EyeL`/`EyeR` (Mario's shader and
  settings) plus the `D`/`G`/`L` special-state variants, built the way the model importer
  builds them.

Textures are written to `//eye_textures` next to the .blend:
- `eye_<name>_w_col`: the eye white.
- `eye_<name>_bl_col` / `eye_<name>_br_col`: the left and right iris decals.

**Scroll Scale: Match Vanilla** (the default) makes the eye span as much of `uvSet` as
Mario's (0.29–0.84 on Aizawa, against Mario's 0.30–0.85). The same `CustomVector31` offset
then moves the iris as far across the eye as on a vanilla fighter.

### Baked expression eyes
Models with baked VIS expressions have one eye mesh per expression. Select all of them
(any order) and run **Convert Eyes** once. They share one set of `EyeL`/`EyeR` materials
and textures (read from the first mesh), each keeps its visibility, and meshes without an
iris (for example an eyeshadow strip with the eye material) are skipped. Painting out the
pupil also clears the dark rim and black gaps around it, filling from the surrounding eye
white. **Setup CustomVector31** no longer fails on empty material slots.

## Crash when closing Blender
Blender disables add-ons while it frees the open file. The eye preview shutdown read the
scene's armatures at that point, which crashed Blender on exit. Unregistering no longer
touches scene data. The retargeting "Auto Detect Rigs" operator is now unregistered too,
so re-enabling the add-on no longer warns.

## HB Master Shader in Smash Viewport
Smash Viewport renders models that have no exported `.numshb` yet (a model you are still
building in Blender) with the game's own shaders. Before, these meshes got a white default
material, and any material built on the HB Master Shader showed up plain white. Now every
HB Master material shows what Bake Textures + export would ship:

- **_col:** Base Color × Tint → Hue/Saturation/Brightness → AO tint (the group's COL stage).
- **_prm:** Metalness (or the SSS Mask on a subsurface shader), Roughness, AO (with AO to
  PRM), Specular. Same rules as the baker.
- **_nor** from the Normal input, **_emi** when the shader reads Texture5.
- The Smash shader export will use: the material's own (or its side-loaded twin's), otherwise
  the standard fighter shader.

The toon part of the group (cel shadow, rim, highlight) never reaches the game, so it is not
shown. The game lights the model itself.

The settings are in the Smash Viewport panel (sidebar and Render properties):
- **HB Master Shader Preview** (on by default) and **Size** (512 / 1024 / 2048).
- **Quick** (automatic): inputs wired straight to an image are read from the image. The group's
  ray-traced AO is left out until you bake.
- **Bake HB Preview:** a small Cycles bake of every wired input and the group's AO. Procedural
  inputs (color ramps, mixes) and the AO then show exactly. **X** drops the bake.
- The sliders stay live either way. An edit rebuilds only that material's textures once you
  stop dragging (about 0.1 s for a 120-mesh character).
- **Metallic skin warning:** a skin material (SSS Mask) with no Smash shader ships on the
  standard shader, where the game reads the SSS Mask as metalness. The panel lists these; give
  them a side-loaded twin with the Skin (Subsurface) preset.

Also in the viewport's model builder:
- Each material slot gets its own Smash material. Before, a mesh with several materials was
  drawn with the first one everywhere.
- Vertices are split along UV and normal seams, the way export does it. Texture seams and
  custom normals (Smart Normals) now look as they will in game.
- Real tangents, so normal maps render correctly.

## Align UVs Upright (Hair): lay out by height
Standing each island upright was not enough. The re-pack placed islands wherever they fit,
so a tip could land above the crown. Smash hair reads the UV as one top-to-bottom map, like
Mario's (c00) hair. The new default layout, **By Height (Mario)**:
- places every island at the V that matches its height on the model: the top of the hair at
  the top of the tile, the tips and the nape at the bottom;
- runs the islands around the head across U, with the front in the middle and no overlaps;
- with **Fill Tile** (on), stretches each island's V range to exactly its height range. A
  root-to-tip gradient or the anisotropic highlight then lines up. Card hair has far more
  surface around the head than it is tall, so detail across the strands is squeezed.

On a test character's hair (≈500 islands), V now follows height with a 0.995 correlation, with
0% overlap. The old tight packing is still available as **Pack**.
