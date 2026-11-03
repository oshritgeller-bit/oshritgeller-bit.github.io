"""Checks for stack_analysis.py. Run with ``python test_stack_analysis.py`` (or pytest)."""

from __future__ import annotations

import json
import math

import numpy as np

from stack_analysis import (
    Calibration,
    nice_length,
    object_stats,
    objects_per_slice,
    segment_3d,
    summarize_objects,
    synthetic_stack,
    z_profile,
)


def ball(shape, center, radius, scale):
    grids = np.meshgrid(*(np.arange(n) * s for n, s in zip(shape, scale)), indexing="ij")
    return sum((g - c) ** 2 for g, c in zip(grids, center)) <= radius ** 2


def test_calibration_validation():
    for bad in [(0, 1, 1), (-1, 1, 1), (math.nan, 1, 1), (math.inf, 1, 1)]:
        try:
            Calibration(*bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid calibration {bad}")
    try:
        Calibration(1, 1, 1, "inch")
    except ValueError:
        pass
    else:
        raise AssertionError("accepted invalid unit")
    cal = Calibration(0.5, 0.25, 0.25)
    assert cal.scale == (0.5, 0.25, 0.25)
    assert math.isclose(cal.voxel_volume, 0.03125)
    assert math.isclose(cal.anisotropy, 2.0)


def test_nice_length():
    assert nice_length(66.56) == 10.0   # 20 % of 66.56 = 13.3 → 10
    assert nice_length(40.0) == 5.0     # 8 → 5
    assert nice_length(12.0) == 2.0     # 2.4 → 2
    assert nice_length(0.4) == 0.05     # 0.08 → 0.05
    assert nice_length(0) == 1.0


def test_sphere_volume_and_area():
    for scale in [(1.0, 1.0, 1.0), (0.5, 0.25, 0.25)]:
        cal = Calibration(*scale)
        # ≥ 10 voxels per axis: smaller digital balls differ from 4/3·π·r³ by > 1 %.
        radius = 10.0
        shape = tuple(int(2 * radius / s) + 8 for s in scale)
        center = [n * s / 2 for n, s in zip(shape, scale)]
        labels = ball(shape, center, radius, scale).astype(np.int32)
        stats = object_stats(labels, labels.astype(np.float32), cal)
        assert len(stats) == 1
        true_volume = 4 / 3 * math.pi * radius ** 3
        true_area = 4 * math.pi * radius ** 2
        volume_error = abs(stats[0]["volume"] - true_volume) / true_volume
        area_error = abs(stats[0]["surfaceArea"] - true_area) / true_area
        print(f"  scale {scale}: volume error {volume_error:.2%}, area error {area_error:.2%}, "
              f"sphericity {stats[0]['sphericity']}")
        assert volume_error < 0.01, volume_error
        assert area_error < 0.05, area_error


def test_watershed_splits_touching_spheres():
    cal = Calibration(1.0, 1.0, 1.0)
    shape = (40, 40, 60)
    image = (ball(shape, (20, 20, 21), 8, cal.scale) | ball(shape, (20, 20, 36), 8, cal.scale))
    image = image.astype(np.float32) * 1000 + 10
    _, merged = segment_3d(image, cal, sigma=0, split_touching=False, min_volume=1)
    labels, split = segment_3d(image, cal, sigma=0, split_touching=True, min_distance=4, min_volume=1)
    assert merged["objectCount"] == 1, merged
    assert split["objectCount"] == 2, split
    stats = object_stats(labels, image, cal)
    assert len(stats) == 2


def test_empty_and_single_voxel():
    cal = Calibration(0.5, 0.2, 0.2)
    empty = np.zeros((6, 12, 12), dtype=np.uint16)
    labels, info = segment_3d(empty, cal)
    assert info["objectCount"] == 0 and labels.max() == 0
    assert object_stats(labels, empty, cal) == []
    assert summarize_objects([])["count"] == 0
    assert objects_per_slice(labels) == [0] * 6
    profile = z_profile(empty, cal)
    assert profile["halfIntensitySlice"] is None
    json.dumps(profile, allow_nan=False)

    single = np.zeros((6, 12, 12), dtype=np.int32)
    single[3, 6, 6] = 1
    stats = object_stats(single, single.astype(np.float32), cal)
    assert len(stats) == 1 and stats[0]["voxels"] == 1 and stats[0]["surfaceArea"] > 0
    labels, info = segment_3d(single * 1000, cal, method="manual", manual_threshold=10,
                              sigma=0, min_volume=0)
    assert info["objectCount"] == 1


def test_bad_parameters():
    cal = Calibration(1, 1, 1)
    image = np.zeros((4, 4, 4))
    for kwargs in [dict(method="foo"), dict(method="manual"), dict(sigma=-1), dict(min_volume=math.nan)]:
        try:
            segment_3d(image, cal, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted {kwargs}")


def test_synthetic_pipeline_is_json_safe():
    stack = synthetic_stack()
    volume = stack.channels[stack.default_channel]
    cal = stack.calibration
    profile = z_profile(volume, cal)
    labels, info = segment_3d(volume, cal)
    objects = object_stats(labels, volume, cal)
    summary = summarize_objects(objects)
    per_slice = objects_per_slice(labels)
    print(f"  synthetic: {info['objectCount']} objects, threshold {info['threshold']}, "
          f"half-intensity depth {profile['halfIntensityDepth']}")
    assert 5 <= info["objectCount"] <= 40
    assert len(profile["slices"]) == volume.shape[0] == len(per_slice)
    # Depth attenuation: the deepest slices are dimmer than the brightest one.
    assert profile["slices"][-1]["mean"] < profile["slices"][profile["brightestSlice"]]["mean"]
    json.dumps({"profile": profile, "info": info, "objects": objects, "summary": summary},
               allow_nan=False)
    for value in (info, summary, *objects):
        for item in value.values():
            assert not isinstance(item, np.generic), type(item)


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        print(name)
        fn()
    print(f"All {len(tests)} tests passed.")
