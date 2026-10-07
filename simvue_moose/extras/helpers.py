"""Helper functions for MOOSE connector."""

from typing import Literal

import numpy
from pyvista import BoundsTuple, MultiBlock

AXES_IDX = {"x": 0, "y": 1, "z": 2}


def check_within_bounds(
    value: float, bounds: BoundsTuple, axis: Literal["x", "y", "z"]
) -> bool:
    """Check a provided value is between bounds of given axis.

    Parameters
    ----------
    value : float
        Value to check
    bounds : BoundsTuple
        Bounds of mesh
    axis : Literal["x", "y", "z"]
        Axis to check along

    Returns
    -------
    bool
        Whether value is between bounds

    """
    if axis not in AXES_IDX:
        return False
    ax_min = bounds[AXES_IDX[axis] * 2]
    ax_max = bounds[(AXES_IDX[axis] * 2) + 1]
    return value >= ax_min and value <= ax_max


def get_varying_axes_ticks(
    mesh: MultiBlock, fixed_axis: Literal["x", "y", "z"]
) -> tuple[list[str], numpy.ndarray, numpy.ndarray]:
    """Get labels and ticks of axes along vaying dimensions.

    Parameters
    ----------
    mesh : MultiBlock
        Mesh to find axes for
    fixed_axis : Literal["x", "y", "z"]
        The axis which is fixed for the slice

    Returns
    -------
    tuple[list[str], numpy.ndarray, numpy.ndarray]
        List of axes labels, first axis ticks, second axis ticks

    """
    varying_axes = AXES_IDX.copy()
    varying_axes.pop(fixed_axis)
    varying_axes_idx = list(varying_axes.values())
    ax1 = mesh.points[:, varying_axes_idx[0]]
    ax2 = mesh.points[:, varying_axes_idx[1]]
    aspect_ratio = abs(ax2.max() - ax2.min()) / abs(ax1.max() - ax1.min())
    ax1_ticks = numpy.linspace(
        ax1.min(), ax1.max(), 100
    )  # TODO more logical sizes here?
    ax2_ticks = numpy.linspace(ax2.min(), ax2.max(), int(100 * aspect_ratio))
    return list(varying_axes.keys()), ax1_ticks, ax2_ticks
