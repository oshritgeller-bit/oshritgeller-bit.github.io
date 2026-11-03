"""Smoke test for napari_panel.py. Needs OpenGL.

Linux without a display:  xvfb-run -a python test_napari_panel.py
(software OpenGL: LIBGL_ALWAYS_SOFTWARE=1). macOS/Windows: python test_napari_panel.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from napari.qt import get_qapp  # noqa: E402

from napari_panel import LABELS_NAME, NapariPanel  # noqa: E402
from stack_analysis import Calibration, segment_3d, synthetic_stack  # noqa: E402


def main() -> int:
    app = get_qapp()
    panel = NapariPanel()
    panel.resize(900, 700)
    panel.show()
    app.processEvents()

    stack = synthetic_stack()
    panel.show_stack(stack, stack.calibration)
    layer = panel.viewer.layers["blobs"]
    assert tuple(layer.scale) == stack.calibration.scale, layer.scale
    assert all(str(u) == "micrometer" for u in layer.units), layer.units
    scale_bar = panel.viewer.canvas.overlays.scale_bar
    assert scale_bar.visible and scale_bar.length == 5.0, (scale_bar.visible, scale_bar.length)

    seen = []
    panel.zChanged.connect(seen.append)
    assert panel.go_to_slice(10) == 10 and panel.current_slice == 10
    assert panel.go_to_slice(999) == stack.shape[0] - 1  # clamped
    assert seen[:2] == [10, stack.shape[0] - 1], seen

    labels, info = segment_3d(stack.channels["blobs"], stack.calibration)
    panel.set_labels(labels)
    objects = panel.viewer.layers[LABELS_NAME]
    assert objects.opacity == 0.55 and tuple(objects.scale) == stack.calibration.scale

    panel.set_calibration(Calibration(1.0, 0.5, 0.5, "nm"))
    assert LABELS_NAME not in panel.viewer.layers, "labels should be cleared on recalibration"
    assert str(layer.units[0]) == "nanometer" and tuple(layer.scale) == (1.0, 0.5, 0.5)
    assert scale_bar.length == 10.0, scale_bar.length

    panel.set_scale_bar_visible(False)
    assert not scale_bar.visible
    panel.set_scale_bar_visible(True)

    panel.set_ndisplay(3)
    app.processEvents()
    assert panel.viewer.dims.ndisplay == 3
    assert tuple(round(a) for a in panel.viewer.scene.camera.angles) == (0, -40, 140), panel.viewer.scene.camera.angles

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "shot.png"
        panel.save_screenshot(str(path))
        assert path.stat().st_size > 1000
    panel.set_ndisplay(2)
    panel.set_theme("light")
    panel.clear()
    assert len(panel.viewer.layers) == 0 and not scale_bar.visible
    panel.close()
    print(f"napari panel OK ({info['objectCount']} objects shown as labels).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
