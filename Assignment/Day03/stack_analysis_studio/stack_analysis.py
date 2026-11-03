"""Qt-free analysis of calibrated 3D image stacks (z, y, x).

Every public function returns plain Python values (no numpy scalars, no NaN)
so results can be passed straight to ``json.dumps``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import numpy as np
from scipy import ndimage as ndi
from skimage import filters, measure, segmentation
from skimage.feature import peak_local_max


UNITS = ("µm", "nm", "mm")
# napari / pint unit names for the display units of an image layer.
UNIT_NAMES = {"µm": "micrometer", "nm": "nanometer", "mm": "millimeter"}
THRESHOLD_METHODS = ("otsu", "li", "manual")


def _num(value: object, digits: int = 4) -> float | None:
    """Round to a JSON-safe float; NaN and infinities become ``None``."""
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


@dataclass(frozen=True)
class Calibration:
    """Physical voxel size along z, y and x."""

    z: float
    y: float
    x: float
    unit: str = "µm"

    def __post_init__(self) -> None:
        for axis, value in (("z", self.z), ("y", self.y), ("x", self.x)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Voxel size {axis} must be a number.")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Voxel size {axis} must be a positive, finite number.")
        if self.unit not in UNITS:
            raise ValueError(f"Unit must be one of: {', '.join(UNITS)}.")

    @property
    def scale(self) -> tuple[float, float, float]:
        return (float(self.z), float(self.y), float(self.x))

    @property
    def voxel_volume(self) -> float:
        return float(self.z * self.y * self.x)

    @property
    def anisotropy(self) -> float:
        """How much coarser z sampling is than the finest lateral sampling."""
        return float(self.z / min(self.y, self.x))

    def to_dict(self) -> Dict[str, object]:
        return {
            "z": _num(self.z, 6),
            "y": _num(self.y, 6),
            "x": _num(self.x, 6),
            "unit": self.unit,
            "voxelVolume": _num(self.voxel_volume, 6),
            "anisotropy": _num(self.anisotropy, 3),
        }


@dataclass
class StackData:
    name: str
    channels: Dict[str, np.ndarray]
    calibration: Calibration
    source: str
    default_channel: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(n) for n in next(iter(self.channels.values())).shape)


# --------------------------------------------------------------------------
# Sample data
# --------------------------------------------------------------------------

def load_cells3d() -> StackData:
    """scikit-image ``cells3d``: 60×2×256×256, membranes + nuclei (download on first use)."""
    from skimage import data

    try:
        import lzma  # noqa: F401  (pooch needs it; scikit-image then misreports "pooch missing")
    except ImportError:
        raise RuntimeError(
            "This Python was built without lzma (xz) support, so the cells3d sample "
            "cannot be downloaded. Install xz and rebuild Python (e.g. "
            "'brew install xz' then 'pyenv install --force 3.12'), or use the "
            "synthetic sample."
        ) from None
    try:
        volume = data.cells3d()
    except Exception as exc:  # pooch raises several network/IO error types
        raise RuntimeError(
            "Could not download the cells3d sample (internet is needed on first "
            "use). Load the synthetic sample instead, which works offline. "
            f"Details: {exc}"
        ) from exc
    return StackData(
        name="cells3d (fluorescence cells)",
        channels={
            "nuclei": np.ascontiguousarray(volume[:, 1]),
            "membranes": np.ascontiguousarray(volume[:, 0]),
        },
        calibration=Calibration(0.29, 0.26, 0.26, "µm"),
        source="scikit-image data.cells3d (Allen Institute for Cell Science)",
        default_channel="nuclei",
    )


def synthetic_stack(seed: int = 7) -> StackData:
    """Offline sample: blurry blobs that dim with depth, plus noise."""
    rng = np.random.default_rng(seed)
    shape = (48, 160, 160)
    cal = Calibration(0.5, 0.25, 0.25, "µm")
    zz, yy, xx = np.meshgrid(
        *(np.arange(n) * s for n, s in zip(shape, cal.scale)), indexing="ij"
    )
    signal = np.zeros(shape, dtype=np.float32)
    extent = [n * s for n, s in zip(shape, cal.scale)]
    for _ in range(22):
        radius = rng.uniform(1.8, 3.2)
        center = [rng.uniform(radius, e - radius) for e in extent]
        distance = np.sqrt(
            (zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2
        )
        # Soft-edged ball: 1 inside, falling to 0 over ~0.3 µm at the edge.
        ball = 1.0 / (1.0 + np.exp((distance - radius) / 0.3))
        signal += rng.uniform(0.6, 1.0) * ball.astype(np.float32)
    # Depth attenuation: deeper slices receive and return less light.
    signal *= np.exp(-zz / 40.0).astype(np.float32)
    signal = ndi.gaussian_filter(signal, sigma=(1.0, 1.2, 1.2))
    image = 120.0 + 1800.0 * signal + rng.normal(0.0, 25.0, shape)
    return StackData(
        name="Synthetic blobs (offline)",
        channels={"blobs": np.clip(image, 0, 65535).astype(np.uint16)},
        calibration=cal,
        source=f"Generated in stack_analysis.synthetic_stack(seed={seed})",
        default_channel="blobs",
    )


SAMPLES = {
    "cells3d": load_cells3d,
    "synthetic": synthetic_stack,
}


# --------------------------------------------------------------------------
# Calibration helpers
# --------------------------------------------------------------------------

def nice_length(extent: float, fraction: float = 0.2) -> float:
    """Largest 1/2/5 × 10^n length not longer than ``fraction`` of ``extent``."""
    target = float(extent) * fraction
    if not math.isfinite(target) or target <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(target))
    for step in (5, 2, 1):
        if step * power <= target:
            return float(step * power)
    return float(power)


def calibration_summary(stack: StackData, cal: Calibration) -> Dict[str, object]:
    shape = stack.shape
    extent = [n * s for n, s in zip(shape, cal.scale)]
    return {
        **cal.to_dict(),
        "shape": list(shape),
        "extent": [_num(e, 3) for e in extent],
        "stackVolume": _num(extent[0] * extent[1] * extent[2], 3),
        "scaleBarLength": nice_length(min(extent[1], extent[2])),
    }


# --------------------------------------------------------------------------
# Z-axis profile
# --------------------------------------------------------------------------

def _threshold(values: np.ndarray, method: str) -> float:
    low, high = float(values.min()), float(values.max())
    if low == high:
        return high  # constant image: nothing is foreground
    if method == "li":
        return float(filters.threshold_li(values))
    return float(filters.threshold_otsu(values))


def z_profile(volume: np.ndarray, cal: Calibration) -> Dict[str, object]:
    """Per-slice intensity and quality metrics along z."""
    data = np.asarray(volume, dtype=np.float32)
    threshold = _threshold(data, "otsu")
    slices = []
    for index, plane in enumerate(data):
        foreground = plane > threshold
        fg_values = plane[foreground]
        bg_values = plane[~foreground]
        snr = None
        if fg_values.size and bg_values.size > 1:
            noise = float(bg_values.std())
            if noise > 0:
                snr = (float(fg_values.mean()) - float(bg_values.mean())) / noise
        slices.append(
            {
                "index": index,
                "z": _num(index * cal.z, 4),
                "mean": _num(plane.mean(), 3),
                "max": _num(plane.max(), 3),
                "std": _num(plane.std(), 3),
                # Variance of the Laplacian: high for sharp edges (and for noise).
                "focus": _num(ndi.laplace(plane).var(), 3),
                "foregroundPercent": _num(foreground.mean() * 100, 3),
                "snr": _num(snr, 3),
            }
        )

    means = np.array([s["mean"] for s in slices], dtype=float)
    focus = np.array([s["focus"] for s in slices], dtype=float)
    brightest = int(np.argmax(means))
    baseline = float(means.min())
    half_index = None
    if means[brightest] > baseline:
        half_level = baseline + 0.5 * (means[brightest] - baseline)
        below = np.nonzero(means[brightest:] < half_level)[0]
        if below.size:
            half_index = brightest + int(below[0])
    return {
        "threshold": _num(threshold, 3),
        "slices": slices,
        "brightestSlice": brightest,
        "bestFocusSlice": int(np.argmax(focus)),
        "halfIntensitySlice": half_index,
        "halfIntensityDepth": (
            _num((half_index - brightest) * cal.z, 4) if half_index is not None else None
        ),
    }


# --------------------------------------------------------------------------
# 3D segmentation
# --------------------------------------------------------------------------

def _ellipsoid_footprint(radius: float, scale: Sequence[float]) -> np.ndarray:
    """Boolean footprint of a sphere with a physical radius on an anisotropic grid."""
    half = [max(0, int(math.floor(radius / s))) for s in scale]
    grids = np.meshgrid(
        *(np.arange(-h, h + 1) * s for h, s in zip(half, scale)), indexing="ij"
    )
    return sum(g ** 2 for g in grids) <= radius ** 2 + 1e-9


def _remove_small(labels: np.ndarray, min_voxels: int) -> tuple[np.ndarray, int]:
    counts = np.bincount(labels.ravel())
    small = np.nonzero(counts < min_voxels)[0]
    small = small[small != 0]
    if small.size:
        labels = labels.copy()
        labels[np.isin(labels, small)] = 0
    return labels, int(small.size)


def segment_3d(
    volume: np.ndarray,
    cal: Calibration,
    method: str = "otsu",
    manual_threshold: float | None = None,
    sigma: float = 0.5,
    min_volume: float = 5.0,
    split_touching: bool = True,
    min_distance: float = 2.0,
) -> tuple[np.ndarray, Dict[str, object]]:
    """Smooth → threshold → 26-connected labels → size filter → optional watershed.

    ``sigma``, ``min_distance`` (physical length) and ``min_volume`` (physical
    volume) are in calibrated units so results do not depend on voxel size.
    """
    if method not in THRESHOLD_METHODS:
        raise ValueError(f"Threshold method must be one of: {', '.join(THRESHOLD_METHODS)}.")
    for label, value in (("Sigma", sigma), ("Minimum volume", min_volume), ("Minimum distance", min_distance)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} must be zero or a positive number.")

    data = np.asarray(volume, dtype=np.float32)
    sigma_voxels = tuple(sigma / s for s in cal.scale)
    smoothed = ndi.gaussian_filter(data, sigma=sigma_voxels) if sigma > 0 else data

    if method == "manual":
        if manual_threshold is None or not math.isfinite(manual_threshold):
            raise ValueError("Enter a numeric manual threshold.")
        threshold = float(manual_threshold)
    else:
        threshold = _threshold(smoothed, method)
    mask = smoothed > threshold

    labels, _count = ndi.label(mask, structure=np.ones((3, 3, 3), dtype=bool))
    min_voxels = max(1, int(math.ceil(min_volume / cal.voxel_volume)))
    labels, removed = _remove_small(labels, min_voxels)
    before_split = int(len(np.unique(labels)) - 1)

    if split_touching and before_split:
        mask = labels > 0
        # Distance in physical units, so anisotropic voxels are handled correctly.
        distance = ndi.distance_transform_edt(mask, sampling=cal.scale)
        peaks = peak_local_max(
            distance,
            footprint=_ellipsoid_footprint(max(min_distance, min(cal.scale)), cal.scale),
            labels=labels,
            exclude_border=False,
        )
        peak_mask = np.zeros(mask.shape, dtype=bool)
        peak_mask[tuple(peaks.T)] = True
        # Flat-topped maxima give several adjacent peaks; merge them into one marker.
        markers, _ = ndi.label(peak_mask, structure=np.ones((3, 3, 3), dtype=bool))
        labels = segmentation.watershed(-distance, markers, mask=mask)
        labels, removed_fragments = _remove_small(labels, min_voxels)
        removed += removed_fragments

    labels, _, _ = segmentation.relabel_sequential(labels)
    labels = labels.astype(np.int32)
    object_count = int(labels.max())
    info = {
        "method": method,
        "threshold": _num(threshold, 3),
        "sigma": _num(sigma, 4),
        "sigmaVoxels": [_num(s, 3) for s in sigma_voxels],
        "minVolume": _num(min_volume, 4),
        "minVoxels": min_voxels,
        "splitTouching": bool(split_touching),
        "minDistance": _num(min_distance, 4),
        "objectCount": object_count,
        "objectsBeforeSplit": before_split,
        "removedSmall": removed,
        "foregroundPercent": _num((labels > 0).mean() * 100, 3),
    }
    return labels, info


# --------------------------------------------------------------------------
# Object statistics
# --------------------------------------------------------------------------

def _surface_area(mask: np.ndarray, cal: Calibration) -> float:
    """Marching-cubes area of a blurred mask; raw binary meshes overestimate by ~10 %."""
    sigma_voxels = [0.75 * min(cal.scale) / s for s in cal.scale]
    pad = [int(math.ceil(3 * s)) + 1 for s in sigma_voxels]
    padded = np.pad(mask.astype(np.float32), [(p, p) for p in pad])
    blurred = ndi.gaussian_filter(padded, sigma=sigma_voxels)
    for candidate in (blurred, np.pad(mask.astype(np.float32), 1)):
        try:
            verts, faces, _n, _v = measure.marching_cubes(candidate, level=0.5, spacing=cal.scale)
            return float(measure.mesh_surface_area(verts, faces))
        except (ValueError, RuntimeError):
            continue  # e.g. a single voxel blurs below 0.5: fall back to the raw mask
    return 0.0


def object_stats(
    labels: np.ndarray, volume: np.ndarray, cal: Calibration
) -> List[Dict[str, object]]:
    objects = []
    raw = np.asarray(volume, dtype=np.float32)
    for index, bounds in enumerate(ndi.find_objects(labels), start=1):
        if bounds is None:
            continue
        mask = labels[bounds] == index
        voxels = int(mask.sum())
        if not voxels:
            continue
        object_volume = voxels * cal.voxel_volume
        area = _surface_area(mask, cal)
        diameter = (6.0 * object_volume / math.pi) ** (1.0 / 3.0)
        # Sphericity = area of a sphere with the same volume / measured area.
        sphericity = (math.pi ** (1 / 3) * (6 * object_volume) ** (2 / 3) / area) if area > 0 else None
        if sphericity is not None:
            sphericity = min(1.0, sphericity)  # mesh discretisation can push it just above 1
        coords = np.argwhere(mask)
        offset = np.array([s.start for s in bounds])
        centroid = (coords.mean(axis=0) + offset) * np.array(cal.scale)
        z_start, z_end = int(bounds[0].start), int(bounds[0].stop - 1)
        objects.append(
            {
                "label": index,
                "voxels": voxels,
                "volume": _num(object_volume, 4),
                "surfaceArea": _num(area, 4),
                "equivalentDiameter": _num(diameter, 4),
                "sphericity": _num(sphericity, 4),
                "zStart": z_start,
                "zEnd": z_end,
                "zExtent": _num((z_end - z_start + 1) * cal.z, 4),
                "centroidSlice": int(round(coords[:, 0].mean() + offset[0])),
                "centroidZ": _num(centroid[0], 4),
                "centroidY": _num(centroid[1], 4),
                "centroidX": _num(centroid[2], 4),
                "meanIntensity": _num(raw[bounds][mask].mean(), 3),
            }
        )
    return objects


def objects_per_slice(labels: np.ndarray) -> List[int]:
    # np.unique includes the background label 0, which count_nonzero ignores.
    return [int(np.count_nonzero(np.unique(plane))) for plane in labels]


def summarize_objects(objects: Sequence[Dict[str, object]]) -> Dict[str, object]:
    if not objects:
        return {
            "count": 0,
            "totalVolume": 0.0,
            "meanVolume": None,
            "medianVolume": None,
            "minVolume": None,
            "maxVolume": None,
            "meanDiameter": None,
            "meanSphericity": None,
        }
    volumes = np.array([o["volume"] for o in objects], dtype=float)
    diameters = np.array([o["equivalentDiameter"] for o in objects], dtype=float)
    sphericity = [o["sphericity"] for o in objects if o["sphericity"] is not None]
    return {
        "count": len(objects),
        "totalVolume": _num(volumes.sum(), 4),
        "meanVolume": _num(volumes.mean(), 4),
        "medianVolume": _num(np.median(volumes), 4),
        "minVolume": _num(volumes.min(), 4),
        "maxVolume": _num(volumes.max(), 4),
        "meanDiameter": _num(diameters.mean(), 4),
        "meanSphericity": _num(np.mean(sphericity), 4) if sphericity else None,
    }
