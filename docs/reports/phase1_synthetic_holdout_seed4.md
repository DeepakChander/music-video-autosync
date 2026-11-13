# Phase 1 gate - synthetic holdout (seed4, 200 ProRes takes, 25 sequence)

- **engine_version**: 0.1.0
- **sequence fps**: 25/1
- **analysis wall time (s)**: 14.54
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
| ambiguous_truth_clips | 39 |
| ambiguous_flagged_or_correct_pct | 100.0 |
| ambiguous_flagged_AMBIGUOUS_pct | 84.62 |
| near_ambiguous_clips | 21 |
| near_ambiguous_wrong_HIGH_MEDIUM | [] |
| rate_search_correct_pct | 100.0 |
| abs_error_ms_p50 | 11.995 |
| abs_error_ms_p95 | 19.2 |
| abs_error_ms_max | 19.923 |
| status_counts | {'UNSYNCED': 34, 'HIGH': 82, 'LOW': 49, 'MEDIUM': 35} |

## Per case

| case | counts |
|---|---|
| avoffset | {'HIGH': 4, 'within_1f': 6, 'LOW': 2} |
| chorus | {'LOW': 16, 'within_1f': 5} |
| clean | {'HIGH': 10, 'within_1f': 19, 'LOW': 5, 'MEDIUM': 5} |
| corrupt | {'UNSYNCED': 2} |
| drift | {'MEDIUM': 3, 'within_1f': 12, 'HIGH': 8, 'LOW': 1} |
| long | {'HIGH': 4, 'within_1f': 4} |
| lossy | {'HIGH': 14, 'within_1f': 22, 'MEDIUM': 4, 'LOW': 6} |
| noaudio | {'UNSYNCED': 6} |
| preroll | {'HIGH': 10, 'within_1f': 19, 'LOW': 3, 'MEDIUM': 7} |
| room | {'LOW': 9, 'within_1f': 42, 'HIGH': 23, 'MEDIUM': 12} |
| short | {'UNSYNCED': 8, 'LOW': 3, 'within_1f': 3, 'MEDIUM': 1} |
| silent | {'UNSYNCED': 4} |
| slowmo | {'MEDIUM': 3, 'within_1f': 14, 'HIGH': 9, 'LOW': 4} |
| unrelated | {'UNSYNCED': 14} |

## Drift (reported, never corrected)

| clip | truth ppm | measured |
|---|---|---|
| T008_drift | -100.0 | {'ppm': -87.3, 'max_error_ms': 8.618, 'windows': 5, 'residual_ms': 0.142, 'reliable': True} |
| T022_drift | -100.0 | {'ppm': -118.5, 'max_error_ms': 2.548, 'windows': 5, 'residual_ms': 0.093, 'reliable': True} |
| T042_drift | -100.0 | {'ppm': None, 'max_error_ms': 18.448, 'windows': 5, 'residual_ms': 4.854, 'reliable': False} |
| T046_drift | -100.0 | {'ppm': -86.1, 'max_error_ms': 5.341, 'windows': 5, 'residual_ms': 0.178, 'reliable': True} |
| T048_drift | 100.0 | {'ppm': None, 'max_error_ms': 16.571, 'windows': 5, 'residual_ms': 6.431, 'reliable': False} |
| T115_drift | 100.0 | {'ppm': 104.6, 'max_error_ms': 6.742, 'windows': 5, 'residual_ms': 0.036, 'reliable': True} |
| T122_drift | -100.0 | {'ppm': None, 'max_error_ms': 11.299, 'windows': 5, 'residual_ms': 4.232, 'reliable': False} |
| T141_drift | 100.0 | {'ppm': 105.4, 'max_error_ms': 2.831, 'windows': 5, 'residual_ms': 0.21, 'reliable': True} |
| T154_drift | 100.0 | {'ppm': 65.8, 'max_error_ms': 7.66, 'windows': 5, 'residual_ms': 0.868, 'reliable': True} |
| T161_drift | 100.0 | {'ppm': None, 'max_error_ms': 6.081, 'windows': 5, 'residual_ms': 1.576, 'reliable': False} |
| T180_drift | 100.0 | {'ppm': 96.2, 'max_error_ms': 4.12, 'windows': 5, 'residual_ms': 0.177, 'reliable': True} |
| T189_drift | 100.0 | {'ppm': 113.2, 'max_error_ms': 1.984, 'windows': 5, 'residual_ms': 0.141, 'reliable': True} |
