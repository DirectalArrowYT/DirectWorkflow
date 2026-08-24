# Expy Kit Retargeting Integration

## Overview
The Expy Kit retargeting plugin has been fully integrated into Smash Ultimate Blender Tools as a new "Retargeting" category. The integration is 1:1 with the original plugin, with the only modification being that all functionality is **only visible when in POSE mode**.

## Features Included
All Expy Kit features are available:
- **Bone Mapping & Presets** - Map bones between different rig types (Rigify, Mixamo, Unreal, etc.)
- **Armature Binding** - Constrain bones between armatures for animation transfer
- **Animation Retargeting** - Transfer animations between different character rigs
- **Rigify Game-Friendly Export** - Convert Rigify rigs to single-hierarchy for game engines
- **Root Motion Transfer** - Extract root motion from hip animation
- **Action Baking** - Bake constrained animations
- **Bone Name Conversion** - Convert bone naming conventions between formats

## How to Access

### Method 1: Ultimate Tab (Reference Panel)
1. Select an Armature object
2. Switch to **Pose Mode** (Tab key or mode selector)
3. Open the **Ultimate** tab in the sidebar (press N if sidebar is hidden)
4. Look for the **Retargeting** panel
5. This panel provides quick info and directs you to the full interface

### Method 2: Expy Tab (Full Interface)
1. Select an Armature object
2. Switch to **Pose Mode** (Tab key or mode selector)
3. Open the **Expy** tab in the sidebar
4. All retargeting panels are available here:
   - Expy Mapping (main panel)
   - Bind To
   - Spine, Arms, Arms IK, Legs, Legs IK
   - Fingers, Face, Root, Custom Bones
   - Action Name Candidates

## Important Notes

### POSE Mode Requirement
- **All retargeting panels only appear in POSE mode**
- If you can't see the panels, ensure you're in POSE mode on an armature object
- The plugin will automatically guide you to switch to POSE mode if needed

### Context Menus
Right-click in POSE mode to access additional retargeting operations:
- **Binding** menu - Bind to Active Armature, Enable/Disable Constraints, etc.
- **Conversion** menu - Rigify Game Friendly, Convert Bone Names, Extract Metarig, etc.
- **Animation** menu - Action Range to Scene, Bake Constrained Actions, Root Motion Transfer, etc.

### Presets
Bone mapping presets are automatically installed on first run:
- Rigify (Deform, Controls, Metarig)
- Mixamo
- Unreal Mannequin (4.x and 5.0)
- Daz Genesis 8
- Actor Core
- FighterZ, 2XKO, Sparking Zero
- Smash Ultimate
- And more...

## Workflow Example

### Basic Animation Retargeting
1. Import your source character with animation (e.g., Mixamo)
2. Import your target character (e.g., Smash Ultimate rig)
3. Select target character armature → **Enter POSE mode**
4. Go to **Expy** tab → **Expy Mapping** panel
5. Set up bone mappings (or use presets)
6. Select both armatures (target active)
7. Right-click → **Binding** → **Bind to Active Armature**
8. Configure retargeting options and bind
9. Right-click → **Animation** → **Bake Constrained Actions**
10. Done! Animations are now on your target rig

### Rigify to Game Engine
1. Generate Rigify rig with your character
2. **Enter POSE mode** on the Rigify armature
3. Right-click → **Conversion** → **Rigify Game Friendly**
4. Configure options (keep backup, rename, etc.)
5. Execute - your rig now has a single-root hierarchy suitable for game engines

## Technical Details

### File Structure
```
smash-ultimate-blender-animation-workflow/
├── source/
│   └── retargeting/
│       └── __init__.py         # Integration module
├── expy_kit/                   # Original expy_kit plugin
│   ├── operators.py
│   ├── properties.py
│   ├── ui.py
│   ├── preset_handler.py
│   └── rig_mapping/
│       └── presets/
```

### Integration Method
- The integration wraps expy_kit's panels with POSE mode restrictions
- All operators, properties, and functionality remain unchanged (1:1)
- Original expy_kit panels are replaced with POSE-mode-only versions
- A reference panel is added to the Ultimate tab for easy discovery

## Troubleshooting

**Q: I don't see the Retargeting panel**
- Ensure you're in POSE mode on an armature object

**Q: The Expy tab is empty**
- Switch to POSE mode - all panels require POSE mode

**Q: Presets aren't loading**
- Check Blender console for errors
- Presets are stored in your Blender user scripts folder

**Q: Operators don't work**
- Ensure you have the correct objects selected
- Check operator requirements (many require 2 armatures selected)
- Read tooltips and panel hints

## Support & Documentation

For detailed documentation on Expy Kit features:
- [Expy Kit GitHub](https://github.com/carlosedubarreto/ExPy-Kit)
- [Expy Kit Video Tutorial](https://www.youtube.com/watch?v=pFouaNVxcso)

For Smash Ultimate Blender tools:
- [Main GitHub](https://github.com/ssbucarlos/smash-ultimate-blender)
- [Wiki](https://github.com/ssbucarlos/smash-ultimate-blender/wiki)

## Credits

- **Expy Kit** by Carlos Eduardo Barreto
- Integration into Smash Ultimate Blender Tools
- Smash Ultimate Blender by Carlos Aguilar & SMG

