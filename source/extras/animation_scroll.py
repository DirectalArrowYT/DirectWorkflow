import bpy
from bpy.types import Operator
from bpy.props import BoolProperty

from ..anim.fcurve_compat import get_fcurves

# Global variable to track the last checked object
_last_checked_object = None

# Global variable to track the last action to detect changes
_last_action = None

def reset_unanimated_bones_to_rest(armature, action):
    """
    Reset bones that are not animated in the given action to their rest positions.
    
    Args:
        armature: The armature object
        action: The action to check for animated bones
    """
    try:
        # Store original mode to restore later
        original_mode = bpy.context.mode
        
        # Switch to pose mode if not already
        if original_mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        
        if not action or not get_fcurves(action):
            # If no action or no fcurves, reset all bones to rest (except _RET bones)
            for bone in armature.pose.bones:
                # Skip bones with "_RET" suffix - don't reset them to rest pose
                if bone.name.endswith("_RET"):
                    continue
                    
                bone.location = (0, 0, 0)
                bone.rotation_euler = (0, 0, 0)
                bone.rotation_quaternion = (1, 0, 0, 0)
                bone.rotation_axis_angle = (0, 0, 1, 0)
                bone.scale = (1, 1, 1)
            
            # Update the view layer to apply the transform resets
            bpy.context.view_layer.update()
            return
        
        # Get all bone names that are animated in this action
        animated_bones = set()
        for fcurve in get_fcurves(action):
            if fcurve.data_path.startswith('pose.bones["'):
                # Extract bone name from data path like 'pose.bones["BoneName"].location'
                try:
                    bone_name = fcurve.data_path.split('"')[1]
                    animated_bones.add(bone_name)
                except (IndexError, ValueError):
                    continue
        
        # Reset bones that are not animated to their rest positions
        for bone in armature.pose.bones:
            if bone.name not in animated_bones:
                # Skip bones with "_RET" suffix - don't reset them to rest pose
                if bone.name.endswith("_RET"):
                    continue
                    
                # Reset location to rest position
                bone.location = (0, 0, 0)
                
                # Reset rotation based on rotation mode
                if bone.rotation_mode == 'QUATERNION':
                    bone.rotation_quaternion = (1, 0, 0, 0)  # Identity quaternion
                elif bone.rotation_mode == 'AXIS_ANGLE':
                    bone.rotation_axis_angle = (0, 0, 1, 0)  # No rotation
                else:
                    bone.rotation_euler = (0, 0, 0)  # No rotation
                
                # Reset scale to rest position
                bone.scale = (1, 1, 1)
        
        # Update the view layer to apply the transform resets
        bpy.context.view_layer.update()
        
    except Exception as e:
        # Silently handle any errors to avoid breaking the animation scroll
        print(f"Error in reset_unanimated_bones_to_rest: {e}")

def handle_action_change(context):
    """
    Handle action changes from the UI dropdown menu.
    This function is called by the timer to check if the action has changed.
    """
    global _last_action
    
    try:
        # Check if we have an active object with animation data
        if (context.active_object and 
            context.active_object.type == 'ARMATURE' and 
            context.active_object.animation_data):
            
            current_action = context.active_object.animation_data.action
            
            # If the action has changed, reset unanimated bones
            if current_action != _last_action:
                _last_action = current_action
                
                if current_action:
                    # Reset unanimated bones to rest positions
                    reset_unanimated_bones_to_rest(context.active_object, current_action)
                else:
                    # If no action is selected, reset all bones to rest
                    reset_unanimated_bones_to_rest(context.active_object, None)
                    
    except Exception as e:
        # Silently handle any errors to avoid breaking the system
        print(f"Error in handle_action_change: {e}")

# Timer function to check if we should auto-start/stop animation scroll
def auto_start_animation_scroll_timer():
    """
    Timer function that ensures animation scroll is always active when appropriate conditions are met.
    Automatically starts when you select an armature with multiple animations and stops when you don't.
    Also handles action changes from the UI dropdown menu.
    """
    global _last_checked_object
    
    try:
        context = bpy.context
        current_object = context.active_object
        
        # Handle action changes from UI dropdown
        handle_action_change(context)
        
        # Only check if the active object has changed
        if current_object != _last_checked_object:
            _last_checked_object = current_object
            
            # Check if we have the right conditions
            if (current_object and 
                current_object.type == 'ARMATURE' and 
                current_object.animation_data):
                
                # Get available actions (filter out SAP and _old)
                actions = [action for action in bpy.data.actions 
                          if not ("SAP" in action.name or "_old" in action.name)]
                
                # Only auto-start if we have multiple animations and modal isn't already running
                if len(actions) >= 2 and not SUB_OP_animation_scroll_modal._running:
                    # Start the modal operator
                    bpy.ops.sub.animation_scroll_modal('INVOKE_DEFAULT')
                    
            # Stop the modal if conditions are no longer met
            elif SUB_OP_animation_scroll_modal._running:
                # Set flag for modal to stop itself
                SUB_OP_animation_scroll_modal._should_auto_stop = True
                
    except Exception as e:
        # Silently handle any errors to avoid breaking Blender
        pass
    
    # Return interval for next check (1 second)
    return 1.0

# Handler to automatically start animation scroll when conditions are met
@bpy.app.handlers.persistent
def auto_start_animation_scroll_handler(scene):
    """
    Handler that ensures animation scroll is always active when appropriate.
    """
    # We'll use the timer instead of this handler for better performance
    pass

class SUB_OP_animation_scroll_modal(Operator):
    """Modal operator that automatically enables animation scrolling when working with armatures"""
    bl_idname = "sub.animation_scroll_modal"
    bl_label = "Animation Scroll Modal"
    bl_description = "Automatically scroll through animations using the mouse wheel when hovering over animation areas"
    bl_options = {'REGISTER'}

    _running = False
    _handler = None
    _should_auto_stop = False

    @classmethod
    def poll(cls, context):
        # This is called by the auto-start timer to check if conditions are appropriate
        has_object = context.active_object is not None
        is_armature = has_object and context.active_object.type == 'ARMATURE'
        has_anim_data = is_armature and context.active_object.animation_data is not None
        
        return (context.active_object and 
                context.active_object.type == 'ARMATURE' and
                context.active_object.animation_data)

    def modal(self, context, event):
        # Check if we should auto-stop
        if SUB_OP_animation_scroll_modal._should_auto_stop:
            SUB_OP_animation_scroll_modal._should_auto_stop = False
            return self.cancel(context)
        
        # Handle wheel events
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            # Check if mouse is over animation areas (Action Editor, Dopesheet, etc.)
            if self.is_mouse_over_animation_area(context, event):
                if event.value == 'PRESS':
                    self.cycle_animation(context, event.type == 'WHEELUPMOUSE')
                    return {'RUNNING_MODAL'}
        
        return {'PASS_THROUGH'}

    def is_mouse_over_animation_area(self, context, event):
        """Check if mouse is over the action name area in the dopesheet editor"""
        mouse_x = event.mouse_x
        mouse_y = event.mouse_y
        
        # Get the window and its screen
        window = context.window
        screen = window.screen
        
        for area in screen.areas:
            # Check if mouse is within this area
            if (area.x <= mouse_x <= area.x + area.width and 
                area.y <= mouse_y <= area.y + area.height):
                
                # Only work in the Dopesheet Editor (where the action name text box is)
                if area.type == 'DOPESHEET_EDITOR':
                    for space in area.spaces:
                        if hasattr(space, 'mode'):
                            # Only in Action Editor mode (where action names are shown)
                            if space.mode in {'ACTION', 'SHAPEKEY'}:
                                return True
        
        return False

    def cycle_animation(self, context, scroll_up):
        """Cycle through available animations"""
        armature = context.active_object
        
        if not armature or not armature.animation_data:
            return
        
        # Get all available actions, filtering out SAP and _old actions
        actions = [action for action in bpy.data.actions 
                  if not ("SAP" in action.name or "_old" in action.name)]
        
        if len(actions) < 2:
            self.report({'INFO'}, f"Need at least 2 actions to cycle (found {len(actions)})")
            return  # Need at least 2 actions to cycle
        
        current_action = armature.animation_data.action
        current_index = -1
        
        # Find current action index
        if current_action:
            try:
                current_index = actions.index(current_action)
            except ValueError:
                current_index = -1
        
        # Calculate next index
        if scroll_up:
            next_index = (current_index - 1) % len(actions)
        else:
            next_index = (current_index + 1) % len(actions)
        
        # Set the new action
        new_action = actions[next_index]
        armature.animation_data.action = new_action
        
        # Update global action tracking
        global _last_action
        _last_action = new_action
        
        # Reset unanimated bones to their rest positions
        reset_unanimated_bones_to_rest(armature, new_action)
        
        # Update the timeline to show the full animation range
        if new_action and get_fcurves(new_action):
            # Find the first and last keyframed frames in the action
            first_frame = float('inf')
            last_frame = float('-inf')
            has_keyframes = False
            
            for fcurve in get_fcurves(new_action):
                if fcurve.keyframe_points:
                    for keyframe in fcurve.keyframe_points:
                        frame_num = int(keyframe.co[0])
                        first_frame = min(first_frame, frame_num)
                        last_frame = max(last_frame, frame_num)
                        has_keyframes = True
            
            if has_keyframes:
                # Set the timeline to show the complete animation range
                context.scene.frame_start = first_frame
                context.scene.frame_end = last_frame
                # Keep the current frame position - don't jump to start
        
        # Show notification with frame range info
        if new_action and get_fcurves(new_action):
            frame_range = f" (frames {context.scene.frame_start}-{context.scene.frame_end})"
        else:
            frame_range = ""
        self.report({'INFO'}, f"Switched to animation: {new_action.name}{frame_range}")

    def execute(self, context):
        # Start the modal operator (only called by auto-start timer)
        context.window_manager.modal_handler_add(self)
        SUB_OP_animation_scroll_modal._running = True
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        SUB_OP_animation_scroll_modal._running = False
        return {'CANCELLED'}

# --- HEADER BUTTON DRAW FUNCTION ---
def draw_animation_scroll_button(self, context):
    # Only show in Action Editor mode
    area = context.area
    if area and hasattr(area.spaces.active, 'mode') and area.spaces.active.mode == 'ACTION':
        is_running = SUB_OP_animation_scroll_modal._running
        icon = 'PLAY' if not is_running else 'PAUSE'
        label = 'Start Animation Scroll' if not is_running else 'Stop Animation Scroll'
        self.layout.operator('sub.toggle_animation_scroll_modal', text=label, icon=icon, depress=is_running)
        try:
            from .face_picker import armature_has_face_picker_menu
            if armature_has_face_picker_menu(context):
                self.layout.operator(
                    'sub.face_picker_popup',
                    text='Easy Facial Animation',
                    icon='IMAGE_DATA',
                )
        except Exception:
            pass

# --- TOGGLE OPERATOR ---
class SUB_OP_toggle_animation_scroll_modal(Operator):
    bl_idname = 'sub.toggle_animation_scroll_modal'
    bl_label = 'Toggle Animation Scroll Modal'
    bl_description = 'Start or stop the animation scroll modal operator'

    def execute(self, context):
        if SUB_OP_animation_scroll_modal._running:
            # Set the flag so the modal will stop itself
            SUB_OP_animation_scroll_modal._should_auto_stop = True
            self.report({'INFO'}, 'Animation scroll modal stopping...')
        else:
            bpy.ops.sub.animation_scroll_modal('INVOKE_DEFAULT')
            self.report({'INFO'}, 'Animation scroll modal started.')
        return {'FINISHED'}

def register():
    bpy.utils.register_class(SUB_OP_animation_scroll_modal)
    bpy.utils.register_class(SUB_OP_toggle_animation_scroll_modal)
    # Add button to Action Editor/Dope Sheet header
    bpy.types.DOPESHEET_HT_header.append(draw_animation_scroll_button)

def unregister():
    # Remove button from header
    bpy.types.DOPESHEET_HT_header.remove(draw_animation_scroll_button)
    bpy.utils.unregister_class(SUB_OP_toggle_animation_scroll_modal)
    bpy.utils.unregister_class(SUB_OP_animation_scroll_modal)
    # Reset class variables
    SUB_OP_animation_scroll_modal._running = False
    SUB_OP_animation_scroll_modal._should_auto_stop = False 