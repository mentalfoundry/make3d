"""Detect body and finger joints on a front render of a character.

Usage: ~/3d/venv/bin/python -I ~/3d/tools/detect_joints.py FRONT.png OUT.json [DEBUG.png]
Coordinates in OUT.json are normalised to the image (x right, y down, 0..1).
"""
import json, os, sys
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision
from PIL import Image, ImageDraw

MODELS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
POSE_NAMES = ["nose", "l_eye_in", "l_eye", "l_eye_out", "r_eye_in", "r_eye", "r_eye_out",
              "l_ear", "r_ear", "mouth_l", "mouth_r", "l_shoulder", "r_shoulder", "l_elbow",
              "r_elbow", "l_wrist", "r_wrist", "l_pinky", "r_pinky", "l_index", "r_index",
              "l_thumb", "r_thumb", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle",
              "l_heel", "r_heel", "l_foot", "r_foot"]
HAND_NAMES = ["wrist", "thumb1", "thumb2", "thumb3", "thumb4", "index1", "index2", "index3", "index4",
              "middle1", "middle2", "middle3", "middle4", "ring1", "ring2", "ring3", "ring4",
              "pinky1", "pinky2", "pinky3", "pinky4"]

src, out = sys.argv[1], sys.argv[2]
debug = sys.argv[3] if len(sys.argv) > 3 else None
img = Image.open(src).convert("RGB")
W, H = img.size

pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=f"{MODELS}/pose_landmarker_heavy.task"),
    min_pose_detection_confidence=0.3))
res = pose.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(img)))
if not res.pose_landmarks:
    sys.exit("no body found")
body = {n: [p.x, p.y, p.visibility] for n, p in zip(POSE_NAMES, res.pose_landmarks[0])}

# Hands: crop around each wrist and upscale, which detects far more reliably than the full image
hand_det = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=f"{MODELS}/hand_landmarker.task"),
    num_hands=1, min_hand_detection_confidence=0.2))
hands = {}
for side in ("l", "r"):
    wx, wy = body[f"{side}_wrist"][:2]
    ix, iy = body[f"{side}_index"][:2]
    r = max(abs(ix - wx) * W, abs(iy - wy) * H, 0.02 * H) * 2.2
    cx, cy = (wx + ix) / 2 * W, (wy + iy) / 2 * H
    box = [int(cx - r), int(cy - r), int(cx + r), int(cy + r)]
    crop = img.crop(box).resize((512, 512), Image.LANCZOS)
    hr = hand_det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(crop)))
    if not hr.hand_landmarks:
        print(f"{side} hand: not detected")
        continue
    sx, sy = (box[2] - box[0]) / 512, (box[3] - box[1]) / 512
    hands[side] = {n: [(box[0] + p.x * 512 * sx) / W, (box[1] + p.y * 512 * sy) / H]
                   for n, p in zip(HAND_NAMES, hr.hand_landmarks[0])}
    print(f"{side} hand: detected ({hr.handedness[0][0].category_name}, "
          f"score {hr.handedness[0][0].score:.2f})")

json.dump({"size": [W, H], "body": body, "hands": hands}, open(out, "w"), indent=1)

if debug:
    d = ImageDraw.Draw(img)
    for n, (x, y, v) in body.items():
        d.ellipse([x * W - 5, y * H - 5, x * W + 5, y * H + 5], fill=(255, 0, 0))
    for side, pts in hands.items():
        for n, (x, y) in pts.items():
            d.ellipse([x * W - 3, y * H - 3, x * W + 3, y * H + 3], fill=(0, 255, 0))
    img.save(debug)
print("saved", out)
