"""Local helper service: wraps the engine in jobs for the Premiere panel. Binds 127.0.0.1 only."""


import os as _os

# BLAS (OpenBLAS in numpy's wheels) called from several threads at once crashed with an access
# violation on Windows (CI, shot analysis). One BLAS thread per caller is the standard fix; heavy
# work runs in our own worker threads/processes anyway. Must be set before numpy is imported.
for _k in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_k, "1")
__version__ = "0.5.0"
