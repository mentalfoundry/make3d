"""Render a turnaround preview (front, 3/4, side, back) of a .glb.

Usage: ~/apps/blender/blender -b --factory-startup --python-exit-code 1 \
         --python ~/3d/tools/preview.py -- MODEL.glb OUT.png [SIZE]
"""
import sys, math
import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]
size = int(argv[2]) if len(argv) > 2 else 512

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
# Skip helper shapes the glTF importer adds for displaying bones
meshes = [o for o in bpy.context.scene.objects
          if o.type == "MESH" and o.visible_get() and not any(
              c.type == "ARMATURE" and o.name in {p.custom_shape.name for p in c.pose.bones if p.custom_shape}
              for c in bpy.context.scene.objects)]
for o in bpy.context.scene.objects:
    if o.type == "MESH" and o not in meshes:
        o.hide_render = True

# Bounding box of everything imported
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, dims = (lo + hi) / 2, hi - lo
tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
print(f"PREVIEW_STATS tris={tris} dims=({dims.x:.3f}, {dims.y:.3f}, {dims.z:.3f})")

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = size
scene.render.resolution_y = size
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"

world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.35, 0.35, 0.37, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
scene.world = world

for rot, energy in (((50, 0, 30), 3.0), ((60, 0, 210), 1.5)):
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = energy
    sun.rotation_euler = [math.radians(a) for a in rot]
    scene.collection.objects.link(sun)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = max(dims.x, dims.y, dims.z) * 1.15
scene.collection.objects.link(cam)
scene.camera = cam

# glTF is Y-up; Blender imports it Z-up with the model facing -Y
views = [("front", 0), ("three-quarter", 45), ("side", 90), ("back", 180)]
dist = max(dims) * 4
frames = []
for name, ang in views:
    a = math.radians(ang)
    cam.location = center + Vector((math.sin(a) * dist, -math.cos(a) * dist, 0))
    cam.rotation_euler = (Vector(center) - cam.location).to_track_quat("-Z", "Y").to_euler()
    path = f"{out[:-4]}-{name}.png"
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    frames.append(path)

# Stitch the four views side by side
imgs = [bpy.data.images.load(p) for p in frames]
w, h = imgs[0].size
sheet = bpy.data.images.new("sheet", w * len(imgs), h)
px = [0.0] * (w * len(imgs) * h * 4)
for i, im in enumerate(imgs):
    data = list(im.pixels)
    for y in range(h):
        row = data[y * w * 4:(y + 1) * w * 4]
        start = (y * w * len(imgs) + i * w) * 4
        px[start:start + w * 4] = row
sheet.pixels = px
sheet.filepath_raw = out
sheet.file_format = "PNG"
sheet.save()
print("PREVIEW_SAVED", out)
