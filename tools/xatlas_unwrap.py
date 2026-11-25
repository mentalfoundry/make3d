"""Unwrap a triangle mesh with xatlas (no overlapping UVs).

Usage: ~/3d/venv/bin/python -I xatlas_unwrap.py IN.npz OUT.npy TEXSIZE
IN.npz holds 'verts' (N,3) float and 'faces' (M,3) int.
OUT.npy gets per-face-corner UVs, shape (M,3,2), in the same face order.
"""
import sys
import numpy as np
import xatlas

src, dst, tex = sys.argv[1], sys.argv[2], int(sys.argv[3])
d = np.load(src)
atlas = xatlas.Atlas()
atlas.add_mesh(d["verts"].astype(np.float32), d["faces"].astype(np.uint32))
co = xatlas.ChartOptions()
co.max_iterations = 2
po = xatlas.PackOptions()
po.resolution = tex
po.padding = 4
po.bilinear = True
po.bruteForce = False
atlas.generate(co, po)
vmapping, indices, uvs = atlas[0]
np.save(dst, uvs[indices].astype(np.float32))
print(f"xatlas: {atlas.chart_count} charts, utilization {atlas.utilization:.2f}")
