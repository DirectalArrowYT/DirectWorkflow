from bpy.types import Panel

class SUB_PT_update_plugin(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Updater'

    # Always visible - this used to poll on UPDATE_AVAILABLE, which meant the
    # ENTIRE panel (including the only "Check for Updates" button) vanished
    # the moment the addon was already up to date. There was then no way to
    # see updater status or trigger a check from the UI at all until another
    # commit happened to land. draw() below shows whichever state actually
    # applies instead.

    def draw(self, context):
        from ...__init__ import bl_info
        from .version_check import (DISABLE_UPDATE_CHECK, LATEST_COMMIT_SHA, LATEST_COMMIT_MESSAGE,
                                     LATEST_COMMIT_DATE, CURRENT_COMMIT_SHA, CURRENT_COMMIT_MESSAGE,
                                     UPDATE_AVAILABLE, UPDATE_STATUS, UPDATE_DOWNLOAD_PROGRESS)

        layout = self.layout
        layout.use_property_split = False

        current_version = bl_info['version']
        layout.row().label(text=f"Plugin version: v{current_version[0]}.{current_version[1]}.{current_version[2]}")

        if DISABLE_UPDATE_CHECK:
            layout.row().label(text="Updater is disabled (source/updater/version_check.py)", icon='INFO')
            return

        if UPDATE_STATUS == "checking":
            layout.row().label(text="Checking for updates...", icon='INFO')
            return

        if UPDATE_STATUS == "downloading":
            layout.row().label(text="Downloading update...", icon='IMPORT')
            row = layout.row()
            row.scale_y = 0.5
            row.progress(factor=UPDATE_DOWNLOAD_PROGRESS, text=f"Progress: {UPDATE_DOWNLOAD_PROGRESS:.1%}")
            return

        if UPDATE_STATUS == "installing":
            layout.row().label(text="Installing update...", icon='FILE_REFRESH')
            layout.row().label(text="Please wait, Blender will restart automatically!")
            layout.row().label(text="Do not close Blender manually!", icon='ERROR')
            return

        if not UPDATE_AVAILABLE:
            layout.row().label(text="Up to date" if CURRENT_COMMIT_SHA else "Not checked yet", icon='CHECKMARK' if CURRENT_COMMIT_SHA else 'INFO')
            layout.row().operator("sub.check_for_updates", text="Check for Updates", icon='FILE_REFRESH')
            return

        # Commit information
        layout.row().label(text="A new update is available!")
        layout.separator()

        # Current commit
        if CURRENT_COMMIT_MESSAGE:
            # Truncate long commit messages
            current_msg = CURRENT_COMMIT_MESSAGE[:50] + "..." if len(CURRENT_COMMIT_MESSAGE) > 50 else CURRENT_COMMIT_MESSAGE
            layout.row().label(text=f"Current: {current_msg}")
        elif CURRENT_COMMIT_SHA:
            layout.row().label(text=f"Current: {CURRENT_COMMIT_SHA[:8]}")
        else:
            layout.row().label(text="Current: Unknown")

        # Latest commit
        if LATEST_COMMIT_MESSAGE:
            # Truncate long commit messages
            latest_msg = LATEST_COMMIT_MESSAGE[:50] + "..." if len(LATEST_COMMIT_MESSAGE) > 50 else LATEST_COMMIT_MESSAGE
            layout.row().label(text=f"Latest: {latest_msg}")
        elif LATEST_COMMIT_SHA:
            layout.row().label(text=f"Latest: {LATEST_COMMIT_SHA[:8]}")

        if LATEST_COMMIT_DATE:
            layout.row().label(text=f"Date: {LATEST_COMMIT_DATE[:10]}")  # Show just the date part

        layout.separator()

        layout.row().operator("sub.check_for_updates", text="Refresh Update Check")
        col = layout.column()
        col.scale_y = 1.5
        col.operator("sub.download_update", text="Download & Install Update", icon='IMPORT')

class SUB_PT_updater_settings(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Plugin Updater'
    bl_parent_id = "SUB_PT_update_plugin"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        from .version_check import UPDATE_STATUS

        layout = self.layout
        layout.use_property_split = False
        
        # Manual controls
        layout.row().label(text="Manual Controls:")
        layout.row().operator("sub.check_for_updates", text="Check for Updates", icon='FILE_REFRESH')
        
        if UPDATE_STATUS not in ["downloading", "installing"]:
            layout.row().operator("sub.download_update", text="Force Download & Install", icon='IMPORT')
        
        # Information
        layout.separator()
        layout.row().label(text="Repository: private server (Hype Bros Studios)")
        layout.row().label(text="Branch: main")
        layout.row().label(text="Updates monitor commits on this branch")
