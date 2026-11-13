# Timing channel lab

A Docker lab for a **discrete timing covert channel** between separate containers.

## What the containers represent

The basic experiment uses three containers:

| Container | What it represents |
|---|---|
| `sender` | One app or agent's tool. It knows a message and tries to send it. |
| `receiver` | A separate app or agent's tool. It does not know the message and tries to recover it. |
| `resource` | A shared HTTP service that both programs are allowed to use. |

**All three run Python programs; no AI models run inside them.** The lab tests
the communication mechanism, not model behaviour or agent coordination. An
optional fourth container, `noise`, adds unrelated traffic to the shared service.

The sender occupies the service briefly for **0** and longer for **1**. The
receiver measures how long its own requests wait to recover those bits. The
sender does not send the message directly to the receiver.

Separate apps sharing infrastructure is realistic: they can share databases,
proxies, or queues. The deliberate simplification here is **one HTTP worker**, so
one request blocks others. Services with multiple workers or request buffering
may not expose this same timing channel.

Every sender `POST /work` has the same method, path, headers and body (64 bytes
by default). The sender sends the headers, waits, then sends the **whole body**.
A single-threaded resource blocks while reading it. The receiver actively probes
`GET /ping` and recovers symbols from the resulting queueing latency.

## Isolation and lifecycle

- Only the sender receives `CHANNEL_MESSAGE` and `CHANNEL_BITS`. Receiver startup
  fails if either is present, even if empty. The runner also checks Docker's actual
  receiver environment and mounts, saving `isolation.json` for each run.
- The receiver has a private output volume. No other role mounts it, the Docker
  socket, or host source files. The image contains only Python source, not the
  experiment specification or result files. Traffic stays on an internal bridge
  without published ports.
- The receiver marks readiness in its private `/tmp`. Compose starts the sender
  and optional noise only after that health check passes. The marker is never
  shared with another container.
- Capture lasts until the runner observes sender completion, stops noise, and
  gracefully stops the receiver. A deadline scales with the transmission length;
  it does not truncate capture to a fixed scenario duration. Unique Compose project
  names prevent one run's cleanup from deleting another run's resources.
- Each invocation builds a uniquely tagged image and checks the running image's
  source hashes against the saved host snapshot. Parallel invocations cannot
  silently replace each other's code through a shared image tag. Build context
  excludes results, experiment files and bytecode caches.
- Startup failures retain available logs and partial traces. Cleanup errors are
  attached to the original failure. Failed runs are excluded from reports;
  subset runs preserve earlier successful results in the aggregate.
- The receiver decodes bits **inside its container**, using only its trace and
  public timing parameters. The host independently recomputes that decode, then
  reads sender ground truth for scoring. Ground truth never sets the threshold.

Start/stop and readiness are public experiment controls. This setup demonstrates
that the supplied receiver recovers data through timing; it does not measure
whether an adversarial implementation could exploit other host or network flaws.

## Protocol and calibration

Four roles use Python's standard library:

| Role | Behaviour |
|---|---|
| `resource` | One HTTP worker; `/work` blocks on its fixed-size body, `/ping` returns 204. A socket timeout bounds abandoned requests. |
| `sender` | One identical `/work` request per symbol, followed by a fixed gap. |
| `receiver` | Sequential `/ping` probes every 1-2 ms when idle, recording latency and failures. |
| `noise` | Optional independent, seeded random `/work` contention; records completed noise events. |

New frames contain:

```
sync | 0,1 repeated four times | data | sync
```

Framed data is a 16-bit big-endian byte length followed by UTF-8 payload bits.
Raw mode carries the requested bit block directly. Calibration and sync are not
counted as data bits. `None` represents sync; data symbols are `0` and `1`.

| Baseline parameter | Value |
|---|---:|
| Zero pulse | 80 ms |
| One pulse | 240 ms |
| Sync pulse | 600 ms |
| Gap | 150 ms |
| Event threshold | 50 ms |
| Sync threshold | 400 ms |

The bit threshold is the midpoint of the median observed zero and one calibration
RTTs. This accounts for systematic network/scheduling overhead without consulting
the payload. Truncated or reversed calibration classes produce an explicit error.
Noise can still corrupt calibration, insert events, or obscure sync pulses.

`CHANNEL_CALIBRATION_PAIRS=0` selects the original fixed-threshold protocol. Old
captures without this field retain their original interpretation. New environment
configuration defaults to four pairs; direct `ChannelConfig()` defaults to zero
for compatibility with old captures and synthetic callers.

## Run

Prerequisites: Docker Desktop with its Linux engine running, and Python 3.11+.

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python scripts/run_experiment.py
```

Run a subset or choose an output directory:

```powershell
python scripts/run_experiment.py --only raw_clean,raw_noisy
python scripts/run_experiment.py --results results/my-run
```

The default output is `results/calibrated/`:

```
RESULTS.md               aggregate table
aggregate.json           machine-readable analyses
<timestamp>-<scenario>-r<N>/
  config.json            scenario, defaults, seed repeat, Compose project
  environment.json       Docker/Python versions and image identity
  isolation.json         actual receiver environment/mount checks
  sender.json            intended symbols and measured pulse schedule
  receiver.json          raw latency samples and independent receiver decode
  resource.log           structured work records with body hashes and read times
  sender.log, receiver.log, noise.log (when enabled)
  analysis.json
  summary.md
```

Reanalyse or plot without starting Docker:

```powershell
python -m timingchannel.analysis results/calibrated/<run-directory>
python scripts/rebuild_results.py --reanalyze
python scripts/plot_trace.py results/calibrated/<run-directory> --output trace.png
```

Plotting requires `python -m pip install -e ".[plot]"`.
`--reanalyze` also rebuilds missing analyses from complete sender/receiver traces.
Source snapshots are saved as `source-<batch>.zip`; each run records its snapshot
name in `config.json`. `environment.json` includes hashes inspected inside the image.
The runner cleans up its own containers and private volume. After interruption,
use the `compose_project` recorded in that run's `config.json`:

```powershell
docker compose --project-name <compose_project> --profile noise down -v --remove-orphans
```

When using Compose directly, capture continues until explicitly stopped. Allow
an outstanding probe to finish: `docker compose stop -t 7 noise receiver`.

## Experiments and measurements

`experiments.json` defines 16 runs across six scenarios:

| Scenario | Pulse widths | Purpose |
|---|---|---|
| `framed_clean` | 80/240 ms, `HELLO` | End-to-end framed recovery |
| `raw_clean` | 80/240 ms | Baseline raw recovery |
| `raw_narrow` | 120/135 ms | 15 ms separation with calibration |
| `raw_tight` | 120/124 ms | 4 ms separation with calibration |
| `raw_tighter` | 120/122 ms | 2 ms separation with calibration |
| `raw_noisy` | 80/240 ms plus contention | Effects of unrelated work |

Raw patterns are seeded by scenario seed plus repeat, allowing paired comparisons.
Noise uses its own seed plus repeat. Capture and noise lifetime follow actual sender completion.

- **Error metric:** `bit_error_rate` retains its historical JSON name but means
  Levenshtein distance divided by expected data-bit count. Insertions, deletions
  and substitutions count; it can exceed 1. Equal-length blocks also report
  `hamming_error_rate`. Neither metric silently repairs dropped or extra pulses.
- **Exact:** the entire decoded raw block or framed payload matches ground truth.
  Length-valid framing alone does not guarantee payload integrity; there is no CRC
  or retransmission in this lab.
- **Rates:** `throughput_bps` is attempted data bits per sender span, including the
  framed length field. `payload_throughput_bps` excludes that field. Both include
  time spent on calibration, sync and gaps, and exclude startup/host collection.
  They are not successful-delivery goodput when decoding fails.
- **Log observers:** request-start deltas require subtracting the known gap;
  `read_ms` measures worker occupancy directly. Each observer calibrates from
  its own training measurements. These log decoders assume one isolated frame;
  extra noise requests produce `n/a` rather than a misleading filtered result.
  Both views validate the opening sync; read times also validate the closing
  sync. The timestamp-only view assumes the last work record ends the isolated
  capture because no later arrival exists to measure its width. This assumption
  is explicit in `timestamp_postamble_inferred`.
- **Content:** body hashes establish that work bodies are constant. No packet
  capture or traffic-detector evaluation is performed. Constant content does not
  make timing or the slow-body traffic pattern undetectable.

## Results and historical evidence

The [recorded 16-run report](results/calibrated/RESULTS.md) uses Docker 29.5.3 and
Python 3.12.16, with four public calibration pairs per frame:

| Scenario | Data bits | Mean edit rate | Exact runs |
|---|---:|---:|---:|
| `framed_clean` | 56 (16 length + 40 payload) | 0% | 1/1 |
| `raw_clean` | 192 | 0% | 3/3 |
| `raw_narrow` (15 ms) | 192 | 0% | 3/3 |
| `raw_tight` (4 ms) | 192 | 0.52% | 2/3 |
| `raw_tighter` (2 ms) | 192 | 1.56% | 0/3 |
| `raw_noisy` | 192 | 14.58% | 0/3 |

All 16 runs passed runtime environment/mount isolation checks and recorded an
independent in-container timing decode. Work bodies had one hash and one size
per run. The paired raw scenarios reuse bit patterns; they are not independent
samples of every possible payload or host condition.

Current traces are [baseline](results/calibrated/timing_baseline.png),
[4 ms](results/calibrated/timing_tight_4ms.png), and
[2 ms](results/calibrated/timing_tighter_2ms.png). A source snapshot for this
matrix is saved in `results/calibrated/source_snapshot.zip`; per-run metadata
records image identity and source hashes.

An [additional payload experiment](results/verification/RESULTS.md) recovered
a random 16-byte payload exactly (144 framed data bits) over 45.809 seconds.

Legacy runs and figures under `results/` used fixed thresholds and supplied
expected payload variables to the receiver. Their decoder ignored those variables,
but these recordings do not establish payload isolation. Use the calibrated
dataset for isolation analysis.

Scheduling, polling cadence, threshold estimation and host load all affect these
results. Three 64-bit repeats per raw scenario are a small feasibility sample,
not a general channel-capacity or detectability estimate. The intentionally shared,
serialized worker is essential to this particular channel.

## Layout

- `src/timingchannel/`: configuration, codec, four roles, socket helpers, analysis.
- `scripts/`: Docker runner, aggregate rebuilding, optional trace plotting.
- `tests/`: codec/calibration, isolation, and actual socket integration tests.
- `experiments.json`, `docker-compose.yml`, `Dockerfile`: experiment and container setup.

## License

[MIT](LICENSE).
