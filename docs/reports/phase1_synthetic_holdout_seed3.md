# Phase 1 gate - synthetic holdout (seed3, 200 ProRes takes, 24000/1001 sequence)

- **engine_version**: 0.1.0
- **sequence fps**: 24000/1001
- **analysis wall time (s)**: 15.09
- **gate**: synthetic: PASS

| Metric | Value |
|---|---|
| clips_in_truth | 200 |
| clips_in_result | 200 |
| unique_expected_to_sync | 106 |
| position_within_1_frame_pct | 100.0 |
| placed_HIGH_MEDIUM_within_1_frame_pct | 99.06 |
| wrong_HIGH_or_MEDIUM | [] |
| unrelated_or_no_audio_placed | [] |
| expected_reason_correct_pct | 100.0 |
| ambiguous_truth_clips | 36 |
| ambiguous_flagged_or_correct_pct | 100.0 |
| ambiguous_flagged_AMBIGUOUS_pct | 80.56 |
| near_ambiguous_clips | 27 |
| near_ambiguous_wrong_HIGH_MEDIUM | [] |
| rate_search_correct_pct | 100.0 |
| abs_error_ms_p50 | 11.028 |
| abs_error_ms_p95 | 19.586 |
| abs_error_ms_max | 20.625 |
| status_counts | {'MEDIUM': 41, 'LOW': 48, 'HIGH': 80, 'UNSYNCED': 31} |

## Per case

| case | counts |
|---|---|
| avoffset | {'HIGH': 4, 'within_1f': 6, 'LOW': 2} |
| chorus | {'LOW': 16, 'within_1f': 5} |
| clean | {'MEDIUM': 3, 'within_1f': 16, 'HIGH': 11, 'LOW': 6} |
| corrupt | {'UNSYNCED': 2} |
| drift | {'MEDIUM': 6, 'within_1f': 12, 'HIGH': 3, 'LOW': 3} |
| long | {'HIGH': 4, 'within_1f': 4} |
| lossy | {'HIGH': 12, 'within_1f': 23, 'MEDIUM': 4, 'LOW': 8} |
| noaudio | {'UNSYNCED': 6} |
| preroll | {'HIGH': 12, 'within_1f': 20, 'MEDIUM': 6, 'LOW': 2} |
| room | {'MEDIUM': 12, 'within_1f': 42, 'LOW': 7, 'HIGH': 25} |
| short | {'MEDIUM': 4, 'within_1f': 6, 'LOW': 3, 'UNSYNCED': 5} |
| silent | {'UNSYNCED': 4} |
| slowmo | {'HIGH': 9, 'within_1f': 16, 'MEDIUM': 6, 'LOW': 1} |
| unrelated | {'UNSYNCED': 14} |

## Drift (reported, never corrected)

| clip | truth ppm | measured |
|---|---|---|
| T001_drift | 100.0 | {'ppm': 104.1, 'max_error_ms': 1.678, 'windows': 5, 'residual_ms': 0.056, 'reliable': True} |
| T023_drift | -100.0 | {'ppm': None, 'max_error_ms': 8.439, 'windows': 4, 'residual_ms': 4.864, 'reliable': False} |
| T052_drift | 100.0 | {'ppm': None, 'max_error_ms': 11.047, 'windows': 5, 'residual_ms': 3.807, 'reliable': False} |
| T075_drift | 100.0 | {'ppm': 98.0, 'max_error_ms': 1.701, 'windows': 5, 'residual_ms': 0.094, 'reliable': True} |
| T098_drift | -100.0 | {'ppm': -114.2, 'max_error_ms': 1.694, 'windows': 5, 'residual_ms': 0.134, 'reliable': True} |
| T127_drift | -100.0 | {'ppm': -86.6, 'max_error_ms': 1.389, 'windows': 5, 'residual_ms': 0.136, 'reliable': True} |
| T132_drift | 100.0 | {'ppm': 99.0, 'max_error_ms': 2.228, 'windows': 5, 'residual_ms': 0.213, 'reliable': True} |
| T133_drift | 100.0 | {'ppm': 97.6, 'max_error_ms': 2.563, 'windows': 5, 'residual_ms': 0.181, 'reliable': True} |
| T172_drift | 100.0 | {'ppm': 99.2, 'max_error_ms': 1.694, 'windows': 5, 'residual_ms': 0.211, 'reliable': True} |
| T173_drift | -100.0 | {'ppm': -98.0, 'max_error_ms': 2.56, 'windows': 5, 'residual_ms': 0.268, 'reliable': True} |
| T194_drift | -100.0 | {'ppm': -87.4, 'max_error_ms': 4.332, 'windows': 5, 'residual_ms': 0.267, 'reliable': True} |
| T200_drift | 100.0 | {'ppm': 92.8, 'max_error_ms': 3.469, 'windows': 5, 'residual_ms': 0.053, 'reliable': True} |
