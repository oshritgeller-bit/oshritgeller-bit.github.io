"""Embedded napari viewer: canvas plus docked layer controls and layer list."""

from __future__ import annotations

import os

os.environ.setdefault("QT_API", "pyside6")

import numpy as np
from napari.components import ViewerModel
from napari.qt import QtViewer, get_stylesheet
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDockWidget, QMainWindow

from stack_analysis import UNIT_NAMES, Calibration, StackData, nice_length


CHANNEL_COLORMAPS = {"nuclei": "cyan", "membranes": "magenta", "blobs": "gray"}
LABELS_NAME = "Objects"
CAMERA_ANGLES_3D = (0, -40, 140)


class NapariPanel(QMainWindow):
    """A QMainWindow used as a plain widget so napari's docks can live inside it."""

    zChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.viewer = ViewerModel(title="Stack Analysis Studio")
        self.qt_viewer = QtViewer(self.viewer)
        self.setCentralWidget(self.qt_viewer)
        self.calibration: Calibration | None = None
        self.scale_bar_visible = True

        for title, widget, area in (
            ("Layer controls", self.qt_viewer.controls, Qt.DockWidgetArea.RightDockWidgetArea),
            ("Layers", self.qt_viewer.layers, Qt.DockWidgetArea.RightDockWidgetArea),
        ):
            dock = QDockWidget(title, self)
            dock.setObjectName(title.replace(" ", ""))
            dock.setWidget(widget)
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )
            self.addDockWidget(area, dock)

        self.viewer.dims.events.current_step.connect(self._on_current_step)

    # ------------------------------------------------------------------ layers

    def show_stack(self, stack: StackData, calibration: Calibration) -> None:
        self.viewer.layers.clear()
        self.calibration = calibration
        for name, data in stack.channels.items():
            low, high = np.percentile(data, (0.5, 99.8))
            self.viewer.add_image(
                data,
                name=name,
                colormap=CHANNEL_COLORMAPS.get(name, "gray"),
                blending="additive",
                contrast_limits=(float(low), float(max(high, low + 1))),
                scale=calibration.scale,
                units=(UNIT_NAMES[calibration.unit],) * 3,
            )
        self.viewer.dims.axis_labels = ("z", "y", "x")
        self._update_scale_bar()
        self.viewer.reset_view()
        self.go_to_slice(stack.shape[0] // 2)

    def set_calibration(self, calibration: Calibration) -> None:
        self.calibration = calibration
        self.clear_labels()
        for layer in self.viewer.layers:
            layer.scale = calibration.scale
            layer.units = (UNIT_NAMES[calibration.unit],) * 3
        self._update_scale_bar()
        self.reset_view()

    def set_labels(self, labels: np.ndarray) -> None:
        self.clear_labels()
        if self.calibration is None:
            return
        self.viewer.add_labels(
            labels,
            name=LABELS_NAME,
            opacity=0.55,
            scale=self.calibration.scale,
            units=(UNIT_NAMES[self.calibration.unit],) * 3,
        )

    def clear_labels(self) -> None:
        if LABELS_NAME in self.viewer.layers:
            self.viewer.layers.remove(LABELS_NAME)

    def clear(self) -> None:
        self.viewer.layers.clear()
        self.calibration = None
        self.viewer.dims.ndisplay = 2
        self._update_scale_bar()

    # ----------------------------------------------------------------- display

    def _update_scale_bar(self) -> None:
        scale_bar = self.viewer.canvas.overlays.scale_bar
        has_data = self.calibration is not None and len(self.viewer.layers) > 0
        scale_bar.visible = self.scale_bar_visible and has_data
        scale_bar.box = True
        scale_bar.colored = False
        scale_bar.ticks = True
        scale_bar.position = "bottom_right"
        if has_data:
            layer = self.viewer.layers[0]
            extent_y = layer.data.shape[-2] * self.calibration.y
            extent_x = layer.data.shape[-1] * self.calibration.x
            scale_bar.length = nice_length(min(extent_y, extent_x))

    def set_scale_bar_visible(self, visible: bool) -> None:
        self.scale_bar_visible = bool(visible)
        self._update_scale_bar()

    def set_ndisplay(self, ndisplay: int) -> None:
        if ndisplay not in (2, 3):
            raise ValueError("Display must be 2D or 3D.")
        self.viewer.dims.ndisplay = ndisplay
        self.reset_view()

    def reset_view(self) -> None:
        # reset_view() fits the data; set the 3D angle afterwards and never touch
        # camera.zoom, otherwise the volume ends up tiny.
        self.viewer.reset_view()
        if self.viewer.dims.ndisplay == 3:
            self.viewer.scene.camera.angles = CAMERA_ANGLES_3D

    def go_to_slice(self, index: int) -> int:
        if not len(self.viewer.layers):
            raise ValueError("Load a stack first.")
        depth = int(self.viewer.dims.nsteps[0])
        index = max(0, min(depth - 1, int(index)))
        self.viewer.dims.set_current_step(0, index)
        return index

    @property
    def current_slice(self) -> int:
        return int(self.viewer.dims.current_step[0]) if len(self.viewer.layers) else 0

    def _on_current_step(self, event=None) -> None:
        if len(self.viewer.layers):
            self.zChanged.emit(self.current_slice)

    def save_screenshot(self, path: str) -> None:
        self.qt_viewer.screenshot(path, flash=False)

    def set_theme(self, theme: str) -> None:
        theme = "light" if theme == "light" else "dark"
        self.viewer.theme = theme
        self.setStyleSheet(get_stylesheet(theme))
