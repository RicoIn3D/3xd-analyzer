# AnySplat inference (sideprojekt_3d/anysplat/anysplat.sh). CUDA 12.8 + PyTorch 2.7 so Blackwell (sm_120) works too.
# TORCH_CUDA_ARCH_LIST is overridden at run time with the GPU's own architecture.
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel
ENV DEBIAN_FRONTEND=noninteractive TORCH_CUDA_ARCH_LIST="12.0" PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libgl1 libglib2.0-0 ninja-build && rm -rf /var/lib/apt/lists/*
RUN pip install "numpy<2" einops jaxtyping beartype hydra-core lightning dacite colorama \
    scikit-image matplotlib "moviepy==1.0.3" imageio imageio-ffmpeg timm plyfile tabulate \
    opencv-python-headless Pillow huggingface_hub safetensors e3nn lpips scikit-video colorspacious wandb
RUN pip install --no-deps xformers==0.0.30
RUN pip install torch_scatter -f https://data.pyg.org/whl/torch-2.7.0+cu128.html
RUN pip install --no-build-isolation gsplat==1.5.3
WORKDIR /anysplat
