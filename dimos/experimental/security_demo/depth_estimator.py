# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Depth estimation for the security demo.

MPS is excluded from device selection because ``.to("mps")`` can hang
indefinitely in forked worker processes, blocking the GIL and starving
all sibling threads (including the shared-memory fanout thread that
delivers ``color_image`` callbacks).
"""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage
import torch
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_MODEL_NAME = "depth-anything/Depth-Anything-V2-Small-hf"
_MAX_WIDTH = 640


def _get_device() -> str:
    """Return the best available torch device: CUDA > CPU."""
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class DepthEstimatorSync:
    """Synchronous depth estimator. No threads — call :meth:`estimate_depth` directly."""

    def __init__(self, processor: AutoImageProcessor, model: AutoModelForDepthEstimation, device: str) -> None:
        self._processor = processor
        self._model = model
        self._device = device

    def estimate_depth(self, image: Image) -> Image:
        rgb = image.to_rgb()
        pil_image = PILImage.fromarray(rgb.data)
        if pil_image.width > _MAX_WIDTH:
            scale = _MAX_WIDTH / pil_image.width
            new_h = int(pil_image.height * scale)
            pil_image = pil_image.resize((_MAX_WIDTH, new_h), PILImage.Resampling.BILINEAR)

        inputs = self._processor(images=pil_image, return_tensors="pt").to(self._device)
        with torch.no_grad():
            outputs = self._model(**inputs)

        depth = torch.nn.functional.interpolate(
            outputs.predicted_depth.unsqueeze(1),
            size=(image.height, image.width),
            mode="bicubic",
            align_corners=False,
        ).squeeze()

        depth_np = depth.cpu().numpy().astype(np.float32)
        return Image.from_numpy(depth_np, format=ImageFormat.DEPTH, frame_id=image.frame_id, ts=image.ts)


def load_depth_model(device: str | None = None) -> DepthEstimator:
    """Load the depth model and return a ready-to-use estimator."""
    device = device or _get_device()
    logger.info("Loading depth model", model=_MODEL_NAME, device=device)
    processor = AutoImageProcessor.from_pretrained(_MODEL_NAME)
    model = AutoModelForDepthEstimation.from_pretrained(_MODEL_NAME).to(device)
    logger.info("Depth model loaded")
    return DepthEstimatorSync(processor, model, device)
