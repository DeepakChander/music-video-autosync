"""Music Video AutoSync sync engine. Pure Python: no Premiere code, no network sockets."""

__version__ = "0.1.0"

# Bump whenever the analysis algorithm changes in a way that changes results. It is part of every
# cache key, so a cached clip result is never reused across algorithm changes.
# 2: candidates are rescored on sample-aligned master features (locate.rescore).
# 3: an AMBIGUOUS clip reports the earliest of its near-tied positions.
# 4: two-pass layout (HIGH/MEDIUM by the spec rule, LOW into gaps).
# 5: the earliest-position rule applies only to float-noise ties (tie_ratio), not all near ties.
ALGORITHM_REVISION = 5
