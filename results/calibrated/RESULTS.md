# Timing channel lab results

Generated: 2026-11-13T16:34:20Z

Work request bodies are constant within each run; the sender delays the whole body. Error rate is normalized Levenshtein distance (including insertions/deletions). Data rate includes the framed length field; calibration and sync consume time.

| Scenario | Run | Pairs | Bits | Decoded | Edit rate | Exact | Log Δ rate | Log read rate | Threshold (ms) | 0 mean (ms) | 1 mean (ms) | bit/s |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| framed_clean | 20261113T162350Z-98492c-framed_clean-r0 | 4 | 56 | 56 | 0.000 | yes | 0.000 | 0.000 | 161.228 | 81.3 | 241.2 | 2.82 |
| raw_clean | 20261113T162542Z-86f576-raw_clean-r0 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 161.188 | 81.3 | 241.4 | 2.73 |
| raw_clean | 20261113T162542Z-86f576-raw_clean-r1 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 161.353 | 81.3 | 241.3 | 2.64 |
| raw_clean | 20261113T162542Z-86f576-raw_clean-r2 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 161.250 | 81.2 | 241.2 | 2.69 |
| raw_narrow | 20261113T162542Z-86f576-raw_narrow-r0 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 128.390 | 121.2 | 136.2 | 3.65 |
| raw_narrow | 20261113T162542Z-86f576-raw_narrow-r1 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 128.462 | 121.1 | 136.1 | 3.63 |
| raw_narrow | 20261113T162542Z-86f576-raw_narrow-r2 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 128.514 | 121.2 | 136.2 | 3.64 |
| raw_noisy | 20261113T162542Z-86f576-raw_noisy-r0 | 4 | 64 | 74 | 0.172 | no | n/a | n/a | 161.375 | n/a | n/a | 2.71 |
| raw_noisy | 20261113T162542Z-86f576-raw_noisy-r1 | 4 | 64 | 72 | 0.156 | no | n/a | n/a | 161.399 | n/a | n/a | 2.64 |
| raw_noisy | 20261113T162542Z-86f576-raw_noisy-r2 | 4 | 64 | 69 | 0.109 | no | n/a | n/a | 132.623 | n/a | n/a | 2.69 |
| raw_tight | 20261113T162542Z-86f576-raw_tight-r0 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 123.174 | 121.3 | 125.1 | 3.73 |
| raw_tight | 20261113T162542Z-86f576-raw_tight-r1 | 4 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 123.106 | 121.4 | 125.2 | 3.72 |
| raw_tight | 20261113T162542Z-86f576-raw_tight-r2 | 4 | 64 | 64 | 0.016 | no | 0.000 | 0.000 | 123.004 | 121.3 | 125.1 | 3.72 |
| raw_tighter | 20261113T162542Z-86f576-raw_tighter-r0 | 4 | 64 | 64 | 0.016 | no | 0.031 | 0.000 | 122.094 | 121.2 | 123.1 | 3.74 |
| raw_tighter | 20261113T162542Z-86f576-raw_tighter-r1 | 4 | 64 | 64 | 0.016 | no | 0.000 | 0.000 | 122.132 | 121.3 | 123.2 | 3.74 |
| raw_tighter | 20261113T162542Z-86f576-raw_tighter-r2 | 4 | 64 | 64 | 0.016 | no | 0.016 | 0.000 | 122.225 | 121.2 | 123.3 | 3.74 |

## Per-scenario mean normalized edit distance

- `framed_clean`: 0.000 (1 run(s))
- `raw_clean`: 0.000 (3 run(s))
- `raw_narrow`: 0.000 (3 run(s))
- `raw_noisy`: 0.146 (3 run(s))
- `raw_tight`: 0.005 (3 run(s))
- `raw_tighter`: 0.016 (3 run(s))
