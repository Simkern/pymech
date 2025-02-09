import re
from functools import partial
from pathlib import Path

import numpy as np
import xarray as xr

from .neksuite import readnek

__all__ = (
    "open_dataset",
    "open_mfdataset",
)


nek_ext_pattern = re.compile(
    r"""
   .*          # one or more characters
   \.          # character "."
   f           # character "f"
   (\d{5}|ld)  # 5 digits or the characters "ld"
""",
    re.VERBOSE,
)


def can_open_nek_dataset(path):
    """A regular expression check of the file extension.

    .. hint::

        - Would not match: .f90 .f .fort .f0000
        - Would match: .fld .f00001 .f12345

    """
    return nek_ext_pattern.match(str(path))


def open_dataset(path, **kwargs):
    """Helper function for opening a file as an :class:`xarray.Dataset`.

    Parameters
    ----------
    path : str
            Path to a field file (only Nek files are supported at the moment.)

    kwargs : dict
            Keyword arguments passed on to the compatible open function.

    """
    if can_open_nek_dataset(path):
        _open = _open_nek_dataset
    else:
        raise NotImplementedError(f"Filetype: {Path(path).suffix} is not supported.")

    return _open(path, **kwargs)


open_mfdataset = partial(
    xr.open_mfdataset, combine="nested", concat_dim="time", engine="pymech"
)
open_mfdataset.__doc__ = """Helper function for opening multiple files as an
:class:`xarray.Dataset`. See :func:`xarray.open_mfdataset` for documentation on
parameters."""


def _open_nek_dataset(path, drop_variables=None):
    """Interface for converting Nek field files into xarray_ datasets."""
    field = readnek(path)
    if isinstance(field, int):
        raise OSError(f"Failed to load {path}")

    elements = field.elem
    elem_stores = [_NekDataStore(elem) for elem in elements]
    elem_dsets = [
        xr.Dataset.load_store(store).set_coords(store.axes) for store in elem_stores
    ]

    # See: https://github.com/MITgcm/xmitgcm/pull/200
    ds = xr.combine_by_coords(elem_dsets, combine_attrs="drop")
    ds.coords.update({"time": field.time})

    if drop_variables:
        ds = ds.drop_vars(drop_variables)

    return ds


class PymechXarrayBackend(xr.backends.BackendEntrypoint):
    def guess_can_open(self, filename_or_obj):
        return can_open_nek_dataset(filename_or_obj)

    def open_dataset(
        self,
        filename_or_obj,
        *,
        drop_variables=None,
        # other backend specific keyword arguments
        # `chunks` and `cache` DO NOT go here, they are handled by xarray
    ):
        return _open_nek_dataset(filename_or_obj, drop_variables)

    open_dataset_parameters = ("filename_or_obj", "drop_variables")


class _NekDataStore(xr.backends.common.AbstractDataStore):
    """Xarray store for a Nek field element.

    Parameters
    ----------
    elem: :class:`pymech.core.Elem`
        A Nek5000 element.

    """

    def __init__(self, elem):
        self.elem = elem
        self.axes = ("z", "y", "x")

    def meshgrid_to_dim(self, mesh):
        """Reverse of np.meshgrid. This method extracts one-dimensional
        coordinates from a cubical array format for every direction

        """
        dim = np.unique(np.round(mesh, 8))
        return dim

    def get_dimensions(self):
        return self.axes

    def get_attrs(self):
        elem = self.elem
        attrs = {
            "boundary_conditions": elem.bcs,
            "curvature": elem.curv,
            "curvature_type": elem.ccurv,
        }
        return attrs

    def get_variables(self):
        """Generate an xarray dataset from a single element."""
        ax = self.axes
        elem = self.elem

        data_vars = {
            ax[2]: self.meshgrid_to_dim(elem.pos[0]),  # x
            ax[1]: self.meshgrid_to_dim(elem.pos[1]),  # y
            ax[0]: self.meshgrid_to_dim(elem.pos[2]),  # z
            "xmesh": (ax, elem.pos[0]),
            "ymesh": (ax, elem.pos[1]),
            "zmesh": (ax, elem.pos[2]),
            "ux": (ax, elem.vel[0]),
            "uy": (ax, elem.vel[1]),
            "uz": (ax, elem.vel[2]),
        }
        if elem.pres.size:
            data_vars["pressure"] = ax, elem.pres[0]

        if elem.temp.size:
            data_vars["temperature"] = ax, elem.temp[0]

        if elem.scal.size:
            data_vars.update(
                {
                    "s{:02d}".format(iscalar + 1): (ax, elem.scal[iscalar])
                    for iscalar in range(elem.scal.shape[0])
                }
            )

        return data_vars
