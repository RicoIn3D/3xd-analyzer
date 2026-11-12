#!/usr/bin/env bash
# Optional step: make a facade splat from your own photos with AnySplat (needs an NVIDIA GPU and Docker with
# the NVIDIA runtime). Photos in fotos/bfe-<nr>/facade/, output in fotos/bfe-<nr>/anysplat/.
# The next ./run.sh --bfe <nr> places the splat on the 3D model. Without the splat the pipeline skips this step.
# Example: sideprojekt_3d/anysplat/anysplat.sh 4208619
set -euo pipefail
cd "$(dirname "$0")/../.."
bfe="${1:?brug: anysplat.sh <bfe> [fotomappe, standard facade]}"
photos="fotos/bfe-$bfe/${2:-facade}"
out="fotos/bfe-$bfe/anysplat"
[ -d "$photos" ] || { echo "Mangler fotos i $photos" >&2; exit 1; }

# AnySplat's code, pinned (MIT; the VGGT weights it builds on are CC BY-NC 4.0, see sideprojekt_3d/README.md)
repo=cache/anysplat/AnySplat
commit=5f5e208a7dd57d52e43ea0d553a95eab526e8775
if [ ! -d "$repo" ]; then
  git clone -q https://github.com/InternRobotics/AnySplat.git "$repo"
fi
git -C "$repo" -c advice.detachedHead=false checkout -q "$commit"

docker build -q -t anysplat:cu128 sideprojekt_3d/anysplat >/dev/null
mkdir -p "$out" "$HOME/.cache/huggingface" "$HOME/.cache/anysplat-torch-ext"
# Weights (about 7.5 GB) and gsplat's compiled CUDA kernels are cached in the Linux home, outside the repo.
# gsplat compiles on first use (a few minutes) for this GPU's architecture.
arch=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)
docker run --rm --gpus all \
  -e TORCH_CUDA_ARCH_LIST="$arch" -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/$repo:/anysplat:ro" -v "$PWD/sideprojekt_3d/anysplat/run_anysplat.py:/run_anysplat.py:ro" \
  -v "$PWD/$photos:/in:ro" -v "$PWD/$out:/out" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" -v "$HOME/.cache/anysplat-torch-ext:/root/.cache/torch_extensions" \
  anysplat:cu128 python /run_anysplat.py /in /out
echo "Splat: $out/gaussians.ply. Kør nu ./run.sh --bfe $bfe for at lægge den på 3D-modellen."
