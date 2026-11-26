"""Make a TRELLIS .glb game-ready: real-world scale, origin at the feet,
reduced triangle count, fresh UVs, and baked textures (colour, metal/roughness,
normal map). Exports .glb, .fbx and a .blend you can open and tweak.

Usage:
  ~/apps/blender/blender -b --factory-startup --python-exit-code 1 \
      --python ~/3d/tools/gameready.py -- MODEL.glb OUT_DIR \
      [--height 1.8] [--tris 30000] [--tex 2048]
"""
import argparse, math, os, sys, time
import bpy
import bmesh
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("out_dir")
ap.add_argument("--height", type=float, default=1.8, help="final height in metres (0 = keep)")
ap.add_argument("--tris", type=int, default=30000, help="target triangle count")
ap.add_argument("--tex", type=int, default=2048, help="texture size in pixels")
ap.add_argument("--voxel", type=float, default=0.004,
                help="remesh voxel size in metres for a 1.8 m figure (0 = skip remesh)")
ap.add_argument("--name", default=None)
a = ap.parse_args(argv)

t0 = time.time()
name = a.name or os.path.splitext(os.path.basename(a.src))[0]
os.makedirs(a.out_dir, exist_ok=True)


def log(msg):
    print(f"[gameready {time.time() - t0:6.1f}s] {msg}", flush=True)


def tri_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def select_only(*objs, active=None):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or objs[0]


# ---------------------------------------------------------------- import
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a.src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
select_only(*meshes)
if len(meshes) > 1:
    bpy.ops.object.join()
high = bpy.context.view_layer.objects.active
# Drop any empty parents from the importer, keeping the world transform
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
high.name = f"{name}_high"
log(f"imported {tri_count(high):,} triangles")

# ---------------------------------------------------------- scale + origin
pts = [high.matrix_world @ Vector(c) for c in high.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
scale = a.height / (hi.z - lo.z) if a.height > 0 else 1.0
feet = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
high.data.transform(np.array([[scale, 0, 0, -feet.x * scale],
                              [0, scale, 0, -feet.y * scale],
                              [0, 0, scale, -feet.z * scale],
                              [0, 0, 0, 1]]).tolist())
high.data.update()
dims = (hi - lo) * scale
log(f"scaled to {dims.x:.2f} x {dims.y:.2f} x {dims.z:.2f} m (origin at feet, facing -Y)")

# ------------------------------------------------------------- low poly
low = high.copy()
low.data = high.data.copy()
low.name = name
bpy.context.scene.collection.objects.link(low)

# Weld the UV-seam splits so the mesh decimates as one surface
bm = bmesh.new()
bm.from_mesh(low.data)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5 * a.height)
bm.to_mesh(low.data)
bm.free()
low.data.materials.clear()
while low.data.uv_layers:
    low.data.uv_layers.remove(low.data.uv_layers[0])
if low.data.has_custom_normals:
    select_only(low)
    bpy.ops.mesh.customdata_custom_splitnormals_clear()

# AI meshes have tangled (non-manifold) edges that tear when decimated, so
# rebuild a clean watertight surface first
if a.voxel > 0:
    mod = low.modifiers.new("remesh", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = a.voxel * a.height / 1.8 if a.height > 0 else a.voxel
    mod.adaptivity = 0.0
    select_only(low)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    log(f"remeshed to {tri_count(low):,} triangles (voxel {mod.voxel_size * 1000:.1f} mm)")

current = tri_count(low)
if current > a.tris:
    mod = low.modifiers.new("decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = a.tris / current
    mod.use_collapse_triangulate = True
    select_only(low)
    bpy.ops.object.modifier_apply(modifier=mod.name)
select_only(low)
bpy.ops.object.shade_smooth()
log(f"decimated to {tri_count(low):,} triangles")

# ------------------------------------------------------------------- UVs
# xatlas (same unwrapper TRELLIS uses) guarantees no overlapping UVs. Blender's
# Smart UV Project can stack layered parts (a tunic over a leg) on the same texels.
import subprocess, tempfile
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.quads_convert_to_tris()
bpy.ops.object.mode_set(mode="OBJECT")
low.data.uv_layers.new(name="UVMap")
with tempfile.TemporaryDirectory() as tmp:
    np.savez(f"{tmp}/mesh.npz",
             verts=np.array([v.co[:] for v in low.data.vertices], dtype=np.float32),
             faces=np.array([p.vertices[:] for p in low.data.polygons], dtype=np.int64))
    r = subprocess.run([os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "venv", "bin", "python"), "-I",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "xatlas_unwrap.py"),
                        f"{tmp}/mesh.npz", f"{tmp}/uv.npy", str(a.tex)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"xatlas failed: {r.stderr[-1500:]}")
    corner_uv = np.load(f"{tmp}/uv.npy")
uv_data = low.data.uv_layers["UVMap"].data
flat = np.zeros((len(uv_data), 2), dtype=np.float32)
for p, uvs in zip(low.data.polygons, corner_uv):
    flat[list(p.loop_indices)] = uvs
uv_data.foreach_set("uv", flat.ravel())
log(f"unwrapped UVs ({r.stdout.strip()})")

# ---------------------------------------------------------------- baking
scene = bpy.context.scene
scene.render.engine = "CYCLES"
device = "CPU"
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("HIP", "VULKAN", "ONEAPI"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            device = f"GPU ({backend}: {gpus[0].name})"
            break
except Exception as e:
    log(f"GPU setup failed, using CPU: {e}")
log(f"baking on {device}")

scene.cycles.samples = 4
bake = scene.render.bake
bake.use_selected_to_active = True
bake.use_cage = False
bake.cage_extrusion = 0.001 * a.height
bake.max_ray_distance = 0.006 * a.height
bake.margin = 16

high_mat = high.data.materials[0]
hn = high_mat.node_tree.nodes
hl = high_mat.node_tree.links
bsdf = next(n for n in hn if n.type == "BSDF_PRINCIPLED")
out = next(n for n in hn if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
surface_src = out.inputs["Surface"].links[0].from_socket

low_mat = bpy.data.materials.new(name)
low_mat.use_nodes = True
low.data.materials.append(low_mat)
ln = low_mat.node_tree.nodes
ll = low_mat.node_tree.links


def new_image(suffix, data):
    img = bpy.data.images.new(f"{name}_{suffix}", a.tex, a.tex, alpha=False)
    img.colorspace_settings.is_data = data
    return img


def bake_target(img):
    node = ln.new("ShaderNodeTexImage")
    node.image = img
    for n in ln:
        n.select = False
    node.select = True
    ln.active = node
    return node


def bake_channel(socket_name, img):
    """Bake one Principled input of the high-poly material via an emission pass."""
    inp = bsdf.inputs[socket_name]
    emit = hn.new("ShaderNodeEmission")
    if inp.links:
        hl.new(inp.links[0].from_socket, emit.inputs["Color"])
    else:
        v = inp.default_value
        emit.inputs["Color"].default_value = tuple(v) if hasattr(v, "__len__") else (v, v, v, 1)
    hl.new(emit.outputs["Emission"], out.inputs["Surface"])
    node = bake_target(img)
    select_only(high, low, active=low)
    bpy.ops.object.bake(type="EMIT")
    hl.new(surface_src, out.inputs["Surface"])
    hn.remove(emit)
    return node


color = new_image("basecolor", data=False)
rough = new_image("rough_tmp", data=True)
metal = new_image("metal_tmp", data=True)
normal = new_image("normal", data=True)

def source_image(sock):
    """The image texture feeding a shader input (following any nodes in between)."""
    stack = [l.from_node for l in sock.links]
    while stack:
        n = stack.pop()
        if n.type == "TEX_IMAGE" and n.image:
            return n.image
        for i in n.inputs:
            stack += [l.from_node for l in i.links]
    return None


def mesh_arrays(me):
    me.calc_loop_triangles()
    v = np.array([x.co[:] for x in me.vertices], dtype=np.float64)
    tri = np.array([t.vertices[:] for t in me.loop_triangles], dtype=np.int64)
    loops = np.array([t.loops[:] for t in me.loop_triangles], dtype=np.int64)
    uv = np.array([d.uv[:] for d in me.uv_layers.active.data], dtype=np.float64)
    return v, tri, loops, uv


def closest_point_transfer(sources):
    """For every texel of the low-poly, copy the texture colour of the closest point
    on the original surface. Unlike ray-cast baking this cannot pick up layers that
    sit behind thin clothing. Returns {name: (T*T, 4) pixels}, plus a coverage mask."""
    from mathutils.bvhtree import BVHTree
    T = a.tex
    hv, htri, hloops, huv = mesh_arrays(high.data)
    lv, ltri, lloops, luv = mesh_arrays(low.data)
    bvh = BVHTree.FromPolygons(hv.tolist(), htri.tolist())

    # Rasterise low-poly UV triangles: texel centres -> 3D positions
    texel_idx, positions = [], []
    for k in range(len(ltri)):
        p = luv[lloops[k]] * T - 0.5
        x0, y0 = np.floor(p.min(0)).astype(int)
        x1, y1 = np.ceil(p.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, T - 1), min(y1, T - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        gx, gy = gx.ravel(), gy.ravel()
        (ax, ay), (bx, by), (cx_, cy_) = p
        det = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
        if abs(det) < 1e-12:
            continue
        w0 = ((by - cy_) * (gx - cx_) + (cx_ - bx) * (gy - cy_)) / det
        w1 = ((cy_ - ay) * (gx - cx_) + (ax - cx_) * (gy - cy_)) / det
        w2 = 1 - w0 - w1
        inside = (w0 >= -0.02) & (w1 >= -0.02) & (w2 >= -0.02)
        if not inside.any():
            continue
        W = np.stack([w0, w1, w2], 1)[inside]
        texel_idx.append(gy[inside] * T + gx[inside])
        positions.append(W @ lv[ltri[k]])
    texel_idx = np.concatenate(texel_idx)
    positions = np.concatenate(positions)

    # Closest point on the original mesh, and its UV there
    hit_face = np.empty(len(positions), dtype=np.int64)
    hit_loc = np.empty((len(positions), 3))
    for i, pt in enumerate(positions.tolist()):
        loc, _, idx, _ = bvh.find_nearest(pt)
        hit_face[i] = idx
        hit_loc[i] = loc
    A, B, C = (hv[htri[hit_face, j]] for j in range(3))
    v0, v1, v2 = B - A, C - A, hit_loc - A
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
    den = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-20, 1e-20, d00 * d11 - d01 * d01)
    bv = (d11 * d20 - d01 * d21) / den
    bw = (d00 * d21 - d01 * d20) / den
    bu = 1 - bv - bw
    uv = (bu[:, None] * huv[hloops[hit_face, 0]] + bv[:, None] * huv[hloops[hit_face, 1]]
          + bw[:, None] * huv[hloops[hit_face, 2]])

    out = {}
    for key, img in sources.items():
        w, h = img.size
        src = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
        x = np.clip(uv[:, 0] * w - 0.5, 0, w - 1)
        y = np.clip(uv[:, 1] * h - 0.5, 0, h - 1)
        xi, yi = np.floor(x).astype(int), np.floor(y).astype(int)
        xj, yj = np.minimum(xi + 1, w - 1), np.minimum(yi + 1, h - 1)
        fx, fy = (x - xi)[:, None], (y - yi)[:, None]
        col = (src[yi, xi] * (1 - fx) * (1 - fy) + src[yi, xj] * fx * (1 - fy)
               + src[yj, xi] * (1 - fx) * fy + src[yj, xj] * fx * fy)
        buf = np.zeros((T * T, 4), dtype=np.float32)
        buf[texel_idx] = col
        out[key] = buf
    mask = np.zeros(T * T, dtype=bool)
    mask[texel_idx] = True
    return out, mask


def dilate(buf, mask, steps=16):
    """Grow texture islands outward so seams don't show dark edges."""
    T = a.tex
    img = buf.reshape(T, T, 4).copy()
    m = mask.reshape(T, T).copy()
    for _ in range(steps):
        acc = np.zeros_like(img)
        cnt = np.zeros((T, T), dtype=np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sm = np.roll(m, (dy, dx), (0, 1))
            acc += np.roll(img, (dy, dx), (0, 1)) * sm[..., None]
            cnt += sm
        grow = (~m) & (cnt > 0)
        img[grow] = acc[grow] / cnt[grow][:, None]
        m |= grow
    img[..., 3] = 1.0
    return img.reshape(-1, 4)


sources = {"color": source_image(bsdf.inputs["Base Color"])}
mr_src = source_image(bsdf.inputs["Roughness"]) or source_image(bsdf.inputs["Metallic"])
if mr_src:
    sources["mr"] = mr_src
if sources["color"] is not None:
    bufs, mask = closest_point_transfer(sources)
    color.pixels[:] = dilate(bufs["color"], mask).ravel()
    color_node = bake_target(color)
    if "mr" in bufs:
        mrb = dilate(bufs["mr"], mask)
        rough.pixels[:] = np.repeat(mrb[:, 1:2], 4, 1).ravel()
        metal.pixels[:] = np.repeat(mrb[:, 2:3], 4, 1).ravel()
        rough_node, metal_node = bake_target(rough), bake_target(metal)
    else:
        rough_node = bake_channel("Roughness", rough)
        metal_node = bake_channel("Metallic", metal)
    log("transferred colour and metal/roughness (closest point)")
else:
    color_node = bake_channel("Base Color", color)
    rough_node = bake_channel("Roughness", rough)
    metal_node = bake_channel("Metallic", metal)
    log("baked colour and metal/roughness")
normal_node = bake_target(normal)
select_only(high, low, active=low)
bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT")
log("baked normal map")

# Pack roughness (G) and metallic (B) into one glTF-style texture
px = lambda img: np.array(img.pixels[:], dtype=np.float32).reshape(-1, 4)
mr = new_image("metal_rough", data=True)
packed = np.ones((a.tex * a.tex, 4), dtype=np.float32)
packed[:, 1] = px(rough)[:, 0]
packed[:, 2] = px(metal)[:, 0]
mr.pixels[:] = packed.ravel()
for n in (rough_node, metal_node):
    ln.remove(n)
bpy.data.images.remove(rough)
bpy.data.images.remove(metal)

# Save textures next to the exports
tex_dir = os.path.join(a.out_dir, "textures")
os.makedirs(tex_dir, exist_ok=True)
for img in (color, mr, normal):
    img.filepath_raw = os.path.join(tex_dir, f"{img.name}.png")
    img.file_format = "PNG"
    img.save()

# ------------------------------------------------- final low-poly material
lbsdf = next(n for n in ln if n.type == "BSDF_PRINCIPLED")
ll.new(color_node.outputs["Color"], lbsdf.inputs["Base Color"])
mr_node = ln.new("ShaderNodeTexImage")
mr_node.image = mr
sep = ln.new("ShaderNodeSeparateColor")
ll.new(mr_node.outputs["Color"], sep.inputs["Color"])
ll.new(sep.outputs["Green"], lbsdf.inputs["Roughness"])
ll.new(sep.outputs["Blue"], lbsdf.inputs["Metallic"])
nmap = ln.new("ShaderNodeNormalMap")
ll.new(normal_node.outputs["Color"], nmap.inputs["Color"])
ll.new(nmap.outputs["Normal"], lbsdf.inputs["Normal"])
for i, n in enumerate((color_node, mr_node, normal_node)):
    n.location = (-700, 300 - i * 300)
sep.location, nmap.location = (-400, 0), (-400, -300)

# ---------------------------------------------------------------- export
high.hide_set(True)
high.hide_render = True
select_only(low)
glb = os.path.join(a.out_dir, f"{name}.glb")
fbx = os.path.join(a.out_dir, f"{name}.fbx")
blend = os.path.join(a.out_dir, f"{name}.blend")
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True)
bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, path_mode="COPY",
                         embed_textures=True, apply_scale_options="FBX_SCALE_ALL")
bpy.context.preferences.filepaths.save_version = 0
for img in bpy.data.images:   # self-contained: make3d copies this .blend up a folder for props
    if img.source == "FILE" and img.filepath and not img.packed_file:
        img.pack()
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
log(f"exported {glb}, {fbx}, {blend}")
print(f"GAMEREADY_DONE tris={tri_count(low)} height={dims.z:.2f}")
