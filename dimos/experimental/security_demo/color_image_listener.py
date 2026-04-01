"""Minimal module that subscribes to color_image and prints a heartbeat."""

from __future__ import annotations

import sys
from typing import Any

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.msgs.sensor_msgs.Image import Image


class ColorImageListener(Module[ModuleConfig]):
    color_image: In[Image]

    _count: int = 0

    @rpc
    def start(self) -> None:
        super().start()
        self.color_image.subscribe(self._on_color_image)

    def _on_color_image(self, image: Image) -> None:
        self._count += 1
        if self._count <= 5 or self._count % 100 == 0:
            print(
                f"[ColorImageListener] frame #{self._count} "
                f"{image.width}x{image.height}",
                file=sys.stderr,
                flush=True,
            )


color_image_listener = ColorImageListener.blueprint
