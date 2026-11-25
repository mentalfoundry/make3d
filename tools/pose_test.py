"""Check a rig: render the rest pose with the skeleton drawn on top, plus a test pose.

Usage: ~/apps/blender/blender -b --python-exit-code 1 RIGGED.blend \
         --python ~/3d/tools/pose_test.py -- OUT_PREFIX
"""
import math, sys
import bpy
from mathutils import Vector, Quaternion

out = sys.argv[sys.argv.index("--") + 1]
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == "ARMATURE")
body = next(o for o in scene.objects if o.type == "MESH")

scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 900
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.35, 0.35, 0.37, 1)
scene.world = world
for rot, energy in (((50, 0, 30), 3.0), ((60, 0, 210), 1.5)):
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = energy
    sun.rotation_euler = [math.radians(x) for x in rot]
    scene.collection.objects.link(sun)

pts = [body.matrix_world @ Vector(c) for c in body.bound_box]
lo = Vector([min(p[i] for p in pts) for i in range(3)])
hi = Vector([max(p[i] for p in pts) for i in range(3)])
dims = hi - lo
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = max(dims.x, dims.z) * 1.15
scene.collection.objects.link(cam)
scene.camera = cam
center = (lo + hi) / 2


def shoot(path, angle):
    a = math.radians(angle)
    cam.location = center + Vector((math.sin(a) * 10, -math.cos(a) * 10, 0))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


# Rest pose, front view, plus 2D bone positions for drawing the skeleton
shoot(f"{out}-rest.png", 0)
S = cam.data.ortho_scale
with open(f"{out}-bones.txt", "w") as f:
    for b in rig.data.bones:
        h, t = rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local
        uv = lambda p: ((p.x - center.x) / S + 0.5, 0.5 - (p.z - center.z) / S)
        f.write(f"{b.name} {uv(h)[0]} {uv(h)[1]} {uv(t)[0]} {uv(t)[1]}\n")


def rot(bone, axis, deg):
    pb = rig.pose.bones.get(bone)
    if pb:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion(axis, math.radians(deg)) @ pb.rotation_quaternion


X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
for side, s in (("Left", 1), ("Right", -1)):
    rot(f"{side}Arm", Z, -25 * s)        # lower the arms
    rot(f"{side}ForeArm", X, 70)         # bend elbows
    for fng in ("Index", "Middle", "Ring", "Pinky"):
        for i in (1, 2, 3):
            rot(f"{side}Hand{fng}{i}", X, 55)  # make a fist
    for i in (1, 2, 3):
        rot(f"{side}HandThumb{i}", X, 30)
rot("LeftUpLeg", X, 45)
rot("LeftLeg", X, -70)
rot("Spine1", Y, 15)
rot("Head", Y, 25)
bpy.context.view_layer.update()
shoot(f"{out}-posed-front.png", 0)
shoot(f"{out}-posed-side.png", 90)
print("POSE_TEST_DONE")
