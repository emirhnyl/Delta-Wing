"""Delta-Wing: parametrik kanat tasarımı, hızlı aerodinamik analiz ve OpenFOAM 3B CFD."""

import numpy as _np

if not hasattr(_np, "trapezoid"):  # numpy < 2.0 (macOS yerleşik Python 3.9 ortamları)
    _np.trapezoid = _np.trapz

__version__ = "2.0.0"
