"""Auto-rig a humanoid in A/T-pose: body joints from pose detection on a front
render, finger joints from the mesh shape, skin weights from Blender's bone heat.
Bone names follow the Mixamo convention (Hips, Spine, LeftArm, LeftHandIndex1...),
which Unity, Unreal and Godot humanoid retargeting recognise.

Usage:
  ~/apps/blender/blender -b --factory-startup --python-exit-code 1 \
      --python ~/3d/tools/rig.py -- MODEL.glb OUT_DIR [--name NAME] [--no-fingers]
"""
import argparse, json, math, os, subprocess, sys, time
from collections import deque
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

TOOLS = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(os.path.dirname(TOOLS), "venv", "bin", "python")

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("out_dir")
ap.add_argument("--name")
ap.add_argument("--no-fingers", action="store_true")
a = ap.parse_args(argv)
name = a.name or os.path.splitext(os.path.basename(a.src))[0]
os.makedirs(a.out_dir, exist_ok=True)
t0 = time.time()


def log(msg):
    print(f"[rig {time.time() - t0:6.1f}s] {msg}", flush=True)


def select_only(*objs, active=None):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or objs[0]


# ------------------------------------------------------------------ import
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a.src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
select_only(*meshes)
if len(meshes) > 1:
    bpy.ops.object.join()
body = bpy.context.view_layer.objects.active
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
for o in list(bpy.context.scene.objects):
    if o is not body:
        bpy.data.objects.remove(o, do_unlink=True)
body.name = name

# glTF splits vertices along UV seams; weld them so the surface is connected
# (bone heat and finger detection both need that). UVs are kept per face corner.
import bmesh
bm = bmesh.new()
bm.from_mesh(body.data)
before = len(bm.verts)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
bm.to_mesh(body.data)
bm.free()
if body.data.has_custom_normals:
    select_only(body)
    bpy.ops.mesh.customdata_custom_splitnormals_clear()
select_only(body)
bpy.ops.object.shade_smooth()

V =np.array([v.co[:] for v in body.data.vertices])
lo, hi = V.min(0), V.max(0)
height = hi[2] - lo[2]
bvh = BVHTree.FromPolygons([v.co.copy() for v in body.data.vertices],
                           [p.vertices[:] for p in body.data.polygons])
log(f"loaded {len(V):,} vertices (welded from {before:,}), height {height:.2f}")

# ------------------------------------------------------- front render
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 1400
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
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
S = max(hi[0] - lo[0], height) * 1.1
cam.data.ortho_scale = S
cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
cam.location = (cx, lo[1] - 5 * height, cz)
cam.rotation_euler = (math.radians(90), 0, 0)  # look along +Y at the character's front
scene.collection.objects.link(cam)
scene.camera = cam
front_png = os.path.join(a.out_dir, f"{name}-front.png")
scene.render.filepath = front_png
bpy.ops.render.render(write_still=True)
log("rendered front view")

joints_json = os.path.join(a.out_dir, f"{name}-joints.json")
r = subprocess.run([VENV_PY, "-I", os.path.join(TOOLS, "detect_joints.py"), front_png, joints_json,
                    os.path.join(a.out_dir, f"{name}-joints.png")],
                   capture_output=True, text=True)
if r.returncode != 0:
    sys.exit(f"joint detection failed: {r.stderr[-1500:]}")
J = json.load(open(joints_json))["body"]
log("detected body joints")


def depth_center(x, z):
    """Middle of the first solid part hit by a ray through (x, z) from the front."""
    o = Vector((x, lo[1] - 1, z))
    hit, nrm, _, _ = bvh.ray_cast(o, Vector((0, 1, 0)))
    if hit is None:
        near = bvh.find_nearest(Vector((x, (lo[1] + hi[1]) / 2, z)))[0]
        return near.y if near else (lo[1] + hi[1]) / 2
    exit_, _, _, _ = bvh.ray_cast(hit + Vector((0, 1e-4, 0)), Vector((0, 1, 0)))
    return (hit.y + exit_.y) / 2 if exit_ is not None else hit.y


def P(*names, frac=None):
    """World position of a detected joint (or the average of several)."""
    us = [J[n][0] for n in names]
    vs = [J[n][1] for n in names]
    u, v = sum(us) / len(us), sum(vs) / len(vs)
    x = cx + (u - 0.5) * S
    z = cz + (0.5 - v) * S
    return Vector((x, depth_center(x, z), z))


# ------------------------------------------------------------ body joints
hips = P("l_hip", "r_hip")
neck = P("l_shoulder", "r_shoulder")
neck.z += 0.02 * height
face = P("mouth_l", "mouth_r", "nose")
head = neck.lerp(face, 0.6)
head.y = depth_center(head.x, head.z)
top_hit = bvh.ray_cast(Vector((head.x, head.y, hi[2] + 1)), Vector((0, 0, -1)))[0]
head_top = Vector((head.x, head.y, top_hit.z if top_hit else hi[2]))
pts = {"Hips": hips, "Neck": neck, "Head": head, "HeadTop_End": head_top}
for i, f in enumerate((0.3, 0.55, 0.8)):
    pts[f"Spine{'' if i == 0 else i}"] = hips.lerp(neck, f)
for side, s in (("Left", "l"), ("Right", "r")):
    sh = P(f"{s}_shoulder")
    pts[f"{side}Shoulder"] = neck.lerp(sh, 0.18)
    pts[f"{side}Arm"] = sh
    pts[f"{side}ForeArm"] = P(f"{s}_elbow")
    pts[f"{side}Hand"] = P(f"{s}_wrist")
    pts[f"{side}UpLeg"] = P(f"{s}_hip")
    pts[f"{side}Leg"] = P(f"{s}_knee")
    ankle = P(f"{s}_ankle")
    pts[f"{side}Foot"] = ankle
    # Toes: frontmost point of the foot below the ankle
    foot = V[(V[:, 2] < ankle.z) & (np.abs(V[:, 0] - ankle.x) < 0.08 * height)]
    tip = foot[foot[:, 1].argmin()] if len(foot) else np.array([ankle.x, ankle.y - 0.1 * height, lo[2]])
    toe_end = Vector((tip[0], tip[1], lo[2] + 0.02 * height))
    pts[f"{side}ToeBase"] = Vector((ankle.x, ankle.y, toe_end.z)).lerp(toe_end, 0.6)
    pts[f"{side}Toe_End"] = toe_end

# ------------------------------------------------------------------ fingers
adj = [[] for _ in range(len(V))]
for e in body.data.edges:
    i, j = e.vertices
    adj[i].append(j)
    adj[j].append(i)


def components(idx_set):
    seen, comps = set(), []
    for s in idx_set:
        if s in seen:
            continue
        comp, q = [], deque([s])
        seen.add(s)
        while q:
            v = q.popleft()
            comp.append(v)
            for n in adj[v]:
                if n in idx_set and n not in seen:
                    seen.add(n)
                    q.append(n)
        comps.append(comp)
    return comps


def find_fingers(wrist, elbow):
    d = np.array((wrist - elbow).normalized())
    w = np.array(wrist)
    rel = V - w
    t = rel @ d
    perp = np.linalg.norm(rel - np.outer(t, d), axis=1)
    fl = (wrist - elbow).length
    mask = (t > -0.05 * fl) & (t < 1.0 * fl) & (perp < 0.6 * fl)
    cand = set(np.nonzero(mask)[0].tolist())
    seed = min(cand, key=lambda i: np.linalg.norm(V[i] - w))
    hand = next(c for c in components(cand) if seed in c)
    hand = np.array(hand)
    ht = t[hand]
    tip_t = ht.max()
    # Sweep a cutting plane from the fingertips down towards the wrist. Each
    # separate piece above the cut is a finger; when two pieces join, the one
    # with the lower tip "dies" there. Real fingers live longest (persistence).
    born, death = {}, {}
    for tc in np.linspace(0.98 * tip_t, 0.05 * tip_t, 90):
        for comp in components(set(hand[ht > tc].tolist())):
            inside = [k for k in born if k in comp]
            if not inside:
                born[max(comp, key=lambda i: t[i])] = tc
                continue
            inside.sort(key=lambda i: -t[i])
            for k in inside[1:]:
                death.setdefault(k, tc)
    eldest = max(born, key=lambda i: t[i])
    persist = {k: t[k] - death[k] for k in death}
    others = sorted(persist, key=lambda k: -persist[k])[:4]
    if os.environ.get("RIG_DEBUG"):
        log(f"hand verts {len(hand)}, tip {tip_t:.3f}, persistence "
            f"{[round(persist[k] / tip_t, 2) for k in sorted(persist, key=lambda k: -persist[k])[:7]]}")
    if len(others) < 4 or persist[others[-1]] < 0.07 * tip_t:
        return None
    tips = [eldest] + others
    merge_t = [None] + [death[k] for k in others]
    thumb = 1 + int(np.argmin(merge_t[1:]))
    merge_t[0] = float(np.median([m for i, m in enumerate(merge_t[1:], 1) if i != thumb]))
    fingers = []
    for k, tip in enumerate(tips):
        comp = next(c for c in components(set(hand[ht > merge_t[k]].tolist())) if tip in c)
        cv = V[comp]
        base = cv[t[comp] < merge_t[k] + 0.08 * (t[tip] - merge_t[k])].mean(0)
        tipv = V[tip]
        axis = tipv - base
        L = np.linalg.norm(axis)
        axis /= L
        s = (cv - base) @ axis
        chain = [Vector(base)]
        for f in (0.42, 0.72):
            sel = np.abs(s - f * L) < 0.07 * L
            chain.append(Vector(cv[sel].mean(0) if sel.any() else base + axis * f * L))
        chain.append(Vector(tipv))
        fingers.append(chain)
    order = sorted((k for k in range(5) if k != thumb),
                   key=lambda k: (fingers[k][0] - fingers[thumb][0]).length)
    names = ["Index", "Middle", "Ring", "Pinky"]
    out = {"Thumb": fingers[thumb]}
    out.update({names[i]: fingers[k] for i, k in enumerate(order)})
    return out


finger_pts = {}
if not a.no_fingers:
    for side in ("Left", "Right"):
        try:
            f = find_fingers(pts[f"{side}Hand"], pts[f"{side}ForeArm"])
        except Exception as e:
            f = None
            log(f"{side} fingers: error {e}")
        if f:
            finger_pts[side] = f
            log(f"{side} hand: 5 fingers found")
        else:
            log(f"{side} hand: could not separate 5 fingers; hand gets a single bone")

# ---------------------------------------------------------------- skeleton
arm_data = bpy.data.armatures.new(f"{name}_rig")
rig = bpy.data.objects.new(f"{name}_rig", arm_data)
scene.collection.objects.link(rig)
select_only(rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones


def bone(bname, head, tail, parent=None, connect=False):
    b = eb.new(bname)
    b.head, b.tail = head, tail
    if (b.tail - b.head).length < 1e-4:
        b.tail = b.head + Vector((0, 0, 0.01 * height))
    if parent:
        b.parent = eb[parent]
        b.use_connect = connect
    return b


bone("Hips", pts["Hips"], pts["Spine"])
bone("Spine", pts["Spine"], pts["Spine1"], "Hips", True)
bone("Spine1", pts["Spine1"], pts["Spine2"], "Spine", True)
bone("Spine2", pts["Spine2"], pts["Neck"], "Spine1", True)
bone("Neck", pts["Neck"], pts["Head"], "Spine2", True)
bone("Head", pts["Head"], pts["HeadTop_End"], "Neck", True)
for side in ("Left", "Right"):
    bone(f"{side}Shoulder", pts[f"{side}Shoulder"], pts[f"{side}Arm"], "Spine2")
    bone(f"{side}Arm", pts[f"{side}Arm"], pts[f"{side}ForeArm"], f"{side}Shoulder", True)
    bone(f"{side}ForeArm", pts[f"{side}ForeArm"], pts[f"{side}Hand"], f"{side}Arm", True)
    fp = finger_pts.get(side)
    hand_tail = fp["Middle"][0] if fp else pts[f"{side}Hand"] + (pts[f"{side}Hand"] - pts[f"{side}ForeArm"]) * 0.35
    bone(f"{side}Hand", pts[f"{side}Hand"], hand_tail, f"{side}ForeArm", True)
    if fp:
        for fname, chain in fp.items():
            parent = f"{side}Hand"
            for i in range(3):
                bname = f"{side}Hand{fname}{i + 1}"
                bone(bname, chain[i], chain[i + 1], parent, i > 0)
                parent = bname
    bone(f"{side}UpLeg", pts[f"{side}UpLeg"], pts[f"{side}Leg"], "Hips")
    bone(f"{side}Leg", pts[f"{side}Leg"], pts[f"{side}Foot"], f"{side}UpLeg", True)
    bone(f"{side}Foot", pts[f"{side}Foot"], pts[f"{side}ToeBase"], f"{side}Leg", True)
    bone(f"{side}ToeBase", pts[f"{side}ToeBase"], pts[f"{side}Toe_End"], f"{side}Foot", True)
# Consistent rolls: limbs and fingers face forward (-Y)
bpy.ops.armature.select_all(action="SELECT")
bpy.ops.armature.calculate_roll(type="GLOBAL_NEG_Y")
bpy.ops.object.mode_set(mode="OBJECT")
log(f"built skeleton with {len(arm_data.bones)} bones")

# ------------------------------------------------------------ skin weights
select_only(body, rig, active=rig)
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
groups = {g.index for g in body.vertex_groups}
unweighted = sum(1 for v in body.data.vertices
                 if not any(g.group in groups and g.weight > 1e-3 for g in v.groups))
def count_weighted(obj):
    return sum(1 for v in obj.data.vertices if any(g.weight > 1e-3 for g in v.groups))


def weights_via_proxy():
    """Bone heat on a rebuilt watertight copy, then copy the weights across."""
    proxy = body.copy()
    proxy.data = body.data.copy()
    proxy.name = "weight_proxy"
    scene.collection.objects.link(proxy)
    proxy.parent = None
    proxy.modifiers.clear()
    proxy.vertex_groups.clear()
    select_only(proxy)
    m = proxy.modifiers.new("remesh", "REMESH")
    # Not decimated afterwards: decimation itself can break bone heat
    m.mode, m.voxel_size, m.adaptivity = "VOXEL", 0.008 * height / 1.8, 0.0
    bpy.ops.object.modifier_apply(modifier=m.name)
    select_only(proxy, rig, active=rig)
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    ok = count_weighted(proxy) > 0.9 * len(proxy.data.vertices)
    if ok:
        select_only(body)
        m = body.modifiers.new("transfer", "DATA_TRANSFER")
        m.object = proxy
        m.use_vert_data = True
        m.data_types_verts = {"VGROUP_WEIGHTS"}
        m.vert_mapping = "POLYINTERP_NEAREST"
        m.layers_vgroup_select_src = "ALL"
        m.layers_vgroup_select_dst = "NAME"
        bpy.ops.object.modifier_move_to_index(modifier=m.name, index=0)
        bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(proxy, do_unlink=True)
    return ok


def weights_nearest_bone():
    """Last resort: each vertex follows its nearest bones, with a soft falloff."""
    from mathutils.geometry import intersect_point_line
    deform = [b for b in rig.data.bones if not b.name.endswith("_End")]
    for vg in list(body.vertex_groups):
        body.vertex_groups.remove(vg)
    groups = {b.name: body.vertex_groups.new(name=b.name) for b in deform}
    for v in body.data.vertices:
        dists = []
        for b in deform:
            p, f = intersect_point_line(v.co, b.head_local, b.tail_local)
            p = b.head_local if f < 0 else b.tail_local if f > 1 else p
            dists.append(((v.co - p).length, b.name))
        dists.sort()
        near = [(d, n) for d, n in dists[:3] if d < dists[0][0] * 1.5 + 0.01 * height]
        w = [1 / (d + 1e-4) ** 2 for d, _ in near]
        for (d, n), wi in zip(near, w):
            groups[n].add([v.index], wi / sum(w), "REPLACE")


if unweighted > 0.02 * len(body.data.vertices):
    log(f"bone heat left {unweighted:,} vertices unweighted; retrying on a rebuilt copy")
    if weights_via_proxy():
        log("weights transferred from the rebuilt copy")
    else:
        log("rebuilt copy failed too; using nearest-bone weights (rougher)")
        weights_nearest_bone()
    weighted = [v.index for v in body.data.vertices
                if any(g.weight > 1e-3 for g in v.groups)]
    if not weighted:
        sys.exit("could not compute any skin weights")
    from mathutils.kdtree import KDTree
    kd = KDTree(len(weighted))
    for i in weighted:
        kd.insert(body.data.vertices[i].co, i)
    kd.balance()
    for v in body.data.vertices:
        if not any(g.weight > 1e-3 for g in v.groups):
            _, src, _ = kd.find(v.co)
            for g in body.data.vertices[src].groups:
                body.vertex_groups[g.group].add([v.index], g.weight, "REPLACE")
log(f"skinned ({unweighted:,} of {len(body.data.vertices):,} vertices needed help)")

# ------------------------------------------------------------------ export
select_only(body, rig, active=rig)
glb = os.path.join(a.out_dir, f"{name}.glb")
fbx = os.path.join(a.out_dir, f"{name}.fbx")
blend = os.path.join(a.out_dir, f"{name}.blend")
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True)
bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, path_mode="COPY", embed_textures=True,
                         add_leaf_bones=False, apply_scale_options="FBX_SCALE_ALL")
for o in list(scene.objects):
    if o.type in {"LIGHT", "CAMERA"}:
        bpy.data.objects.remove(o, do_unlink=True)
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
log(f"exported {glb}, {fbx}, {blend}")
print(f"RIG_DONE bones={len(arm_data.bones)} fingers={','.join(finger_pts) or 'none'}")
