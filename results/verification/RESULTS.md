# Timing channel lab results

Generated: 2026-11-13T16:35:25Z

Work request bodies are constant within each run; the sender delays the whole body. Error rate is normalized Levenshtein distance (including insertions/deletions). Data rate includes the framed length field; calibration and sync consume time.

| Scenario | Run | Pairs | Bits | Decoded | Edit rate | Exact | Log Δ rate | Log read rate | Threshold (ms) | 0 mean (ms) | 1 mean (ms) | bit/s |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| framed_blind_long | 20261113T163431Z-b5388b-framed_blind_long-r0 | 4 | 144 | 144 | 0.000 | yes | 0.000 | 0.000 | 161.331 | 81.3 | 241.3 | 3.14 |

## Per-scenario mean normalized edit distance

- `framed_blind_long`: 0.000 (1 run(s))
