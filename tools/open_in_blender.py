"""Open a .glb in the Blender GUI, ready to inspect.

Usage: ~/apps/blender/blender --python ~/3d/tools/open_in_blender.py -- MODEL.glb
"""
import sys
import bpy

src = sys.argv[sys.argv.index("--") + 1]

# Clear the default cube, camera and light
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)

bpy.ops.import_scene.gltf(filepath=src)


def setup_view():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.shading.type = "MATERIAL"  # show textures and metal
            space.overlay.show_floor = True
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.object.select_all(action="SELECT")
                bpy.ops.view3d.view_axis(type="FRONT")
                bpy.ops.view3d.view_selected()
                bpy.ops.object.select_all(action="DESELECT")
    return None  # run once


bpy.app.timers.register(setup_view, first_interval=0.5)
