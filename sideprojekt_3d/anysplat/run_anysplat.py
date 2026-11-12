"""Run AnySplat on a folder of photos; write gaussians.ply, cameras.json and a fly-through video.

Runs inside the anysplat:cu128 image (see anysplat.sh), with AnySplat's code mounted at /anysplat.
cameras.json: per photo the camera-to-world extrinsic (OpenCV: x right, y down, z forward) and the normalised
intrinsic, in AnySplat's own frame. sideprojekt_3d/splat.py places the splat on the 3D model from these.
"""
import json
import os
import sys
from pathlib import Path

import torch

sys.path.insert(0, "/anysplat")
from src.misc.image_io import save_interpolated_video  # noqa: E402
from src.model.model.anysplat import AnySplat  # noqa: E402
from src.model.ply_export import export_ply  # noqa: E402
from src.utils.image import process_image  # noqa: E402

image_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
names = sorted(f for f in os.listdir(image_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
print(f"{len(names)} images")

model = AnySplat.from_pretrained("lhjiang/anysplat").to("cuda").eval()
for p in model.parameters():
    p.requires_grad = False

images = torch.stack([process_image(str(image_dir / n)) for n in names]).unsqueeze(0).to("cuda")
b, v, _, h, w = images.shape
with torch.no_grad():
    gaussians, pose = model.inference((images + 1) * 0.5)
print(f"{gaussians.means.shape[1]} gaussians, peak GPU {torch.cuda.max_memory_allocated() / 2**30:.1f} GB")

export_ply(gaussians.means[0], gaussians.scales[0], gaussians.rotations[0], gaussians.harmonics[0],
           gaussians.opacities[0], out_dir / "gaussians.ply")
cams = [{"image": n, "extrinsic": pose["extrinsic"][0, i].tolist(), "intrinsic": pose["intrinsic"][0, i].tolist()}
        for i, n in enumerate(names)]
(out_dir / "cameras.json").write_text(json.dumps({"image_size": [w, h], "cameras": cams}, indent=1))
save_interpolated_video(pose["extrinsic"], pose["intrinsic"], b, h, w, gaussians, str(out_dir), model.decoder)
print("done", out_dir)
