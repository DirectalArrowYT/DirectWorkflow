# Retargeting Integration - Testing Guide

## Quick Verification Steps

### 1. Basic Installation Check
After enabling the plugin:
1. Open Blender
2. Create or open a scene with an Armature
3. Select the armature
4. Check console output for: `"Registering Retargeting module (expy_kit integration)..."`
5. Should see: `"Retargeting module registered successfully!"`

### 2. Panel Visibility Test

#### Test A: Ultimate Tab Panel
1. Select an armature object
2. Switch to **POSE mode** (Tab or mode selector)
3. Press **N** to show sidebar
4. Look for **"Ultimate"** tab
5. **Retargeting** panel should be visible
6. Panel should show:
   - "Expy Kit Retargeting Tools" header
   - Info about switching to Expy tab
   - Feature list
7. Switch to **OBJECT mode**
8. **Retargeting** panel should disappear ✓

#### Test B: Expy Tab Panels
1. Select an armature object
2. Switch to **POSE mode**
3. Look for **"Expy"** tab in sidebar
4. Should see multiple panels:
   - ✓ Expy Mapping
   - ✓ Bind To
   - ✓ Spine (closed by default)
   - ✓ Arms
   - ✓ Legs
   - ✓ Action Name Candidates (if action exists)
5. Switch to **OBJECT mode**
6. All Expy panels should disappear ✓

### 3. Context Menu Test
1. Select armature in **POSE mode**
2. Right-click in viewport
3. Should see new menu items:
   - ✓ **Binding** (submenu)
     - Bind to Active Armature
     - Enable/disable constraints
     - Select Constrained Controls
   - ✓ **Conversion** (submenu)
     - Rigify Game Friendly
     - Revert dots in Names
     - Convert Bone Names
     - Extract Metarig
   - ✓ **Animation** (submenu)
     - Action Range to Scene
     - Bake Constrained Actions
     - Rename Actions from .fbx data
     - Transfer Root Motion

### 4. Operators Test

#### Test: Bone Mapping Panel
1. In **POSE mode** on armature
2. Go to **Expy** tab → **Expy Mapping** panel
3. Try setting a preset from dropdown at top
4. Should populate bone name fields
5. Try using eyedropper icon next to a field:
   - Select a bone in viewport
   - Click eyedropper
   - Bone name should fill in ✓

#### Test: Mode Auto-Switch
1. Have armature selected in **OBJECT mode**
2. Try to use any retargeting operator from context menu
3. Should prompt or auto-switch to POSE mode ✓
   (Note: Some operators may show an error - this is expected if not in POSE mode)

### 5. Preset Installation Check
1. Open Blender Preferences
2. Go to File Paths → Scripts → User Scripts path
3. Navigate to: `scripts/presets/armature/retarget/`
4. Should see preset files:
   - ✓ Mixamo.py
   - ✓ Rigify_Deform.py
   - ✓ Smash.py
   - ✓ Unreal_Mannequin.py
   - ✓ And more...

### 6. Full Workflow Test

#### Simple Binding Test
1. Create two armatures (or duplicate one)
2. Name them "Source" and "Target"
3. Select "Source" → **POSE mode**
4. Expy tab → Expy Mapping → Set some bones
5. Select both armatures (Target active last)
6. Right-click → **Binding** → **Bind to Active Armature**
7. Dialog should appear with many options ✓
8. Click OK
9. Should create retarget bones on Target
10. Moving Source should now move Target ✓

## Expected Console Output

On plugin enable, you should see:
```
Loading Smash Ultimate Blender Tools...
  Registering Retargeting module (expy_kit integration)...
  Retargeting module registered successfully!
Loaded Smash Ultimate Blender Tools!
```

On plugin disable:
```
Unloading Smash Ultimate Blender Tools...
  Unregistering Retargeting module...
  Retargeting module unregistered successfully!
Unloaded Smash Ultimate Blender Tools!
```

## Common Issues & Solutions

### Issue: "No Expy tab visible"
**Solution:** Make sure you're in POSE mode on an armature object

### Issue: "Retargeting panel missing from Ultimate tab"
**Solution:** Enter POSE mode - the panel only appears in POSE mode

### Issue: "Import errors in console"
**Solution:** Check that `expy_kit/` folder exists at the same level as `source/`

### Issue: "Operators do nothing or show errors"
**Cause:** Many operators require specific conditions:
- Must be in POSE mode
- Must have specific bones mapped
- Some require 2 armatures selected
**Solution:** Read operator tooltips and check requirements

### Issue: "Presets dropdown is empty"
**Solution:** 
1. Check console for preset installation errors
2. Manually check: `[User Scripts]/presets/armature/retarget/`
3. Try restarting Blender

### Issue: "Context menus don't show up"
**Solution:** Ensure you're right-clicking with an armature selected in POSE mode

## Integration Checklist

Before considering integration complete, verify:

- [ ] Plugin loads without errors
- [ ] Console shows successful registration messages
- [ ] Ultimate tab → Retargeting panel visible in POSE mode
- [ ] Ultimate tab → Retargeting panel hidden in other modes
- [ ] Expy tab panels visible in POSE mode
- [ ] Expy tab panels hidden in other modes  
- [ ] Right-click context menus show Binding/Conversion/Animation
- [ ] Presets are installed and accessible
- [ ] At least one operator works (e.g., bone mapping)
- [ ] Plugin unloads cleanly without errors
- [ ] Can reload plugin without issues

## Success Criteria

✓ **Integration is successful if:**
1. All expy_kit features are accessible
2. Everything only shows in POSE mode
3. No modifications to expy_kit core functionality
4. Clean console output (no errors)
5. Plugin can be enabled/disabled multiple times
6. No conflicts with existing Smash Ultimate features

## Notes

- The integration is designed to be transparent - users just get the full expy_kit experience
- POSE mode restriction is the only behavioral change from standalone expy_kit
- All expy_kit documentation and tutorials still apply
- The Ultimate tab panel is just a reference/discovery aid - all real functionality is in Expy tab

