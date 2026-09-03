# Urban Observation Processing

This repository replays stored real or synthetic data, converts it to the
`urban-observation.v1` format, receives it over TCP, and applies shared anomaly
gating, LLM/VLM enrichment, and geocoding.

It does not collect provider data and it does not generate synthetic data.

## How it fits together

On a storage machine, install this repository with `urban-observations` and run
the replay sender against collected files or TAR archives. On a compute machine,
run its receiver and enrichment containers beside SIGMUS or IncidentLens.

```text
stored real data ──┐
                   ├── replay ── TCP:8766 ──> receiver ── HTTP ──> enrichment
stored simulation ─┘                                      |
                                                         JSONL
                                                   SIGMUS / IncidentLens
```

The receiver and enrichment service normally run together on the SIGMUS or
IncidentLens machine. The replay command can run there too, or on the storage
machine containing the collected files. `8766/tcp` is the only cross-machine
port required.

## Runtime and command count

| Part | How it runs | Routine command |
|---|---|---|
| Receiver | Docker container | `./docker-start up` |
| Enrichment | Docker container, started by the same wrapper | `./docker-start up` |
| Real replay sender | Host Python | `python -m replay.replay ...` |
| Synthetic replay sender | Host Python | `python -m replay.synthetic ...` |

After one-time setup, an end-to-end processing run takes **two commands**: one
Docker start on the compute machine and one Python replay command on the machine
with the input data. If the services are already running, only the replay
command is needed. The first Docker setup uses three commands below; the first
host-Python setup uses the four installation commands below.

## Start the receiver and enrichment service

Requirements: Docker with the Compose plugin, an OpenAI API key, and optionally
a Google Places API key for geocoding.

```bash
cp config.example.json config.json
chmod 600 config.json
./docker-start up
```

Set `enrichment.openai_api_key` in `config.json`. Set
`enrichment.google_places_api_key` if geocoding is wanted. The services run in
the background and write enriched records to
`<paths.output_root>/observations.jsonl`.

Useful commands:

```bash
./docker-start ps
./docker-start logs -f
./docker-start down
```

`config.json`, generated output, caches, and credentials are ignored by Git.

## Configuration

Start from `config.example.json`; only `config.json` is read at runtime.

| Setting | Meaning | Required change? |
|---|---|---|
| `paths.data_root` | Current Urban Observations data root. | For real replay |
| `paths.backup_root` | Root containing historical TAR archives. | For historical replay |
| `paths.output_root` | Receiver output and cache directory. | Safe default provided |
| `receiver.host`, `receiver.port` | TCP listener; expose the port only to trusted replay machines. | Safe defaults provided |
| `receiver.max_message_bytes` | Maximum inline observation message size. | Safe default provided |
| `enrichment.openai_api_key` | OpenAI key for LLM/VLM enrichment. | Yes |
| `enrichment.google_places_api_key` | Google Places key for geocoding. | Optional |
| `enrichment.*_model` | Models used for text and images. | Safe defaults provided |
| `real_replay.receiver_*` | Receiver address reached by real replay. | Change for separate machines |
| `synthetic_replay.dataset_root` | Completed simulator run or batch. | For synthetic replay |
| `synthetic_replay.receiver` | Receiver address, timeout, and retry policy. | Change for separate machines |

The example contains placeholders only. It contains no working credentials.

## Install the replay commands

Replay reads existing files; it never changes or deletes collected data.

```bash
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Replay real data

Set `paths.data_root`, `paths.backup_root`, and `real_replay.receiver_host` in
`config.json`. The end date is exclusive.

```bash
python -m replay.replay --from 2026-08-01 --to 2026-08-02
```

This reads current directories and historical TAR archives. To monitor new
files after replaying through yesterday, add `--follow` and omit `--to`.

## Replay completed simulator data

Set `synthetic_replay.dataset_root` to a completed run or batch directory and
set its receiver host and port. Then run:

```bash
python -m replay.synthetic
```

The simulator already chose the incident types, run counts, and number of
steps. Replay only reads the resulting files. A path can be supplied as a
one-time override:

```bash
python -m replay.synthetic /path/to/completed/batch
```

## Verify changes

```bash
python -m pip install -e '.[dev]'
python -m pytest
./docker-start config
```
