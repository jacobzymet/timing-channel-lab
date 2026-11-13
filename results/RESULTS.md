# Timing channel lab results

Historical protocol: fixed thresholds, no in-band calibration. The original
receiver was accidentally given payload environment variables (which its decoder
ignored). These runs do not establish payload isolation; current results are in
`calibrated/RESULTS.md`. Narrow-pulse errors depend strongly on threshold bias.

Generated: 2026-11-13T15:52:59Z

Work request bodies were identical (`POST /work`, 64-byte body). The client delayed
the whole body. The reported BER is normalized bit edit distance.

| Scenario | Run | Bits | Decoded | BER | Frame OK | Log Δ BER | Log read BER | 0 mean (ms) | 1 mean (ms) | bit/s |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| framed_clean | 20261113T153823Z-framed_clean-r0 | 56 | 56 | 0.000 | yes | 0.000 | 0.000 | 81.4 | 241.9 | 3.22 |
| raw_clean | 20261113T153823Z-raw_clean-r0 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 81.6 | 241.5 | 3.05 |
| raw_clean | 20261113T153823Z-raw_clean-r1 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 81.6 | 241.7 | 2.94 |
| raw_clean | 20261113T153823Z-raw_clean-r2 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 81.7 | 241.5 | 3.00 |
| raw_narrow | 20261113T153823Z-raw_narrow-r0 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 121.4 | 136.3 | 4.07 |
| raw_narrow | 20261113T153823Z-raw_narrow-r1 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 121.7 | 136.5 | 4.04 |
| raw_narrow | 20261113T153823Z-raw_narrow-r2 | 64 | 64 | 0.000 | yes | 0.000 | 0.000 | 121.6 | 136.7 | 4.06 |
| raw_noisy | 20261113T155052Z-raw_noisy-r0 | 64 | 71 | 0.141 | no | n/a | n/a | n/a | n/a | 3.01 |
| raw_noisy | 20261113T155052Z-raw_noisy-r1 | 64 | 75 | 0.172 | no | n/a | n/a | n/a | n/a | 2.94 |
| raw_noisy | 20261113T155052Z-raw_noisy-r2 | 64 | 74 | 0.172 | no | n/a | n/a | n/a | n/a | 2.99 |
| raw_tight | 20261113T154252Z-raw_tight-r0 | 64 | 64 | 0.047 | no | 0.125 | 0.000 | 121.3 | 125.3 | 4.15 |
| raw_tight | 20261113T154252Z-raw_tight-r1 | 64 | 64 | 0.016 | no | 0.062 | 0.000 | 121.2 | 125.2 | 4.15 |
| raw_tight | 20261113T154252Z-raw_tight-r2 | 64 | 64 | 0.094 | no | 0.141 | 0.000 | 121.3 | 125.5 | 4.15 |
| raw_tighter | 20261113T154557Z-raw_tighter-r0 | 64 | 64 | 0.469 | no | 0.547 | 0.000 | 121.4 | 123.5 | 4.17 |
| raw_tighter | 20261113T154557Z-raw_tighter-r1 | 64 | 64 | 0.344 | no | 0.469 | 0.000 | 121.4 | 123.4 | 4.16 |
| raw_tighter | 20261113T154557Z-raw_tighter-r2 | 64 | 64 | 0.391 | no | 0.516 | 0.016 | 121.4 | 123.6 | 4.16 |

## Per-scenario mean BER

- `framed_clean`: 0.000 (1 run(s))
- `raw_clean`: 0.000 (3 run(s))
- `raw_narrow`: 0.000 (3 run(s))
- `raw_noisy`: 0.161 (3 run(s))
- `raw_tight`: 0.052 (3 run(s))
- `raw_tighter`: 0.401 (3 run(s))
