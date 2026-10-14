# Strata on an RTX 5070 + Tesla V100

[日本語](README.md)

A Windows reproduction kit for **Qwen3.8-Flash-Next IQ3_XXS on an RTX5070 12GB + V100 PCIe32GB with32GB system RAM**, plus a comparison against **Qwen3.8-27B RANA Q4_K_M running entirely on the V100**.

The RTX runs the main engine. The V100 holds and computes additional experts using Strata's stock peer path. There is no patch to upstream Strata. The recipe pins its source commit, CUDA12.9 engine, model revisions and SHA256 hashes in [models.lock.json](models.lock.json).

## Quick start

Windows x64, Python3.12+, Git, both GPUs visible in `nvidia-smi`, a working common NVIDIA driver,32GB-class RAM, and suitable V100 cooling are required. The tested driver was581.80. Driver installation and TCC changes are manual; the kit does not perform them.

Use an **ASCII path** for both the repository and runtime. The recorded Windows Strata build failed to read PLE data from a Japanese path. Flash GGUF download is75.84GB; allow roughly120–140GB for model files and generated packs. The explicit download flag acknowledges this.

```powershell
git clone https://github.com/lube8163-lab/strata-rtx5070-v100-repro.git C:\AI\strata-rtx5070-v100-repro
cd C:\AI\strata-rtx5070-v100-repro
python repro.py check
python repro.py bootstrap --root C:\AI\strata-runtime --accept-large-download
python repro.py configure --root C:\AI\strata-runtime
python repro.py run --engine flash
```

Open `http://127.0.0.1:8097/`. OpenAI-compatible API: `http://127.0.0.1:8097/v1`, model `local-agent`. Localhost only, no API key. Ctrl+C stops only the server started by this command. NVML independently monitors core/fresh HBM temperatures and available RAM;80C or less than2GiB available RAM for10seconds stops the owned server.

To select cards explicitly, add `--primary INDEX_OR_UUID --peer INDEX_OR_UUID` to `check` and `configure`. Default detection uses GPU names and refuses ambiguous matches. Host paths and GPU UUIDs remain in ignored `.local/`.

For the tested128K Flash profile: `python repro.py run --engine flash --context 131072`. A128,808-token synthetic needle task completed; this is not proof of long-document reasoning or production stability.

## Run the comparison

```powershell
python repro.py bootstrap-rana --root C:\AI\strata-runtime --accept-large-download
python repro.py configure --root C:\AI\strata-runtime
python repro.py benchmark --smoke
python repro.py benchmark --thinking
```

RANA assets require another20.64GB plus llama.cpp binaries. Models run sequentially. The full comparison saves24 scored tasks,6 short generation measurements,2 Japanese review replies, synthetic4K/16K inputs, and optionally the same4 Thinking ON diagnostic tasks. Results, full answers, commands and0.5second telemetry are written to a fresh `.local/runs/<timestamp>/` directory.

Flash-only benchmark: `python repro.py benchmark --engines flash --thinking`. V100-only RANA web/API: `python repro.py run --engine rana` on port8098. Existing applications on8088 are not reconfigured.

You can reuse prepared assets with `configure --strata-dir --data-dir --cuda-dir --rana-model --rana-mtp --rana-mmproj --llama-exe`; see the [Japanese README example](README.md#既存環境を使う). Use `verify --engine flash` and `verify --engine rana` to rehash reused GGUFs. Startup checks size/build metadata rather than rehashing75GB on every run.

## Recorded results

Same32K context, Thinking OFF, temperature0; each short speed is the median of3 runs, capped at256 output tokens.

| Metric | V10027B RANA | RTX5070 + V100 Flash |
|---|---:|---:|
| Japanese decode |41.0 tok/s|59.2 tok/s|
| Python decode |63.1 tok/s|71.8 tok/s|
| First token |0.382s|0.759s|
|~16K first token, one run|23.81s|38.15s|
|Free RTX5070 VRAM|11.01GiB|1.04GiB|
|Available system RAM|21.67GiB|8.27GiB|
|Host CPU mean|9.3%|50.4%|
|24-task success, format included|10/24|14/24|

The same4 previously failed tasks became4/4 for both with Thinking ON, taking about6seconds on average. These small task counts are not general accuracy scores. RANA is a modified27B atQ4_K_M; Flash usesIQ3_XXS and another backend. System disk counters include other host processes. Concurrent VR/game/image-generation performance was not tested.

The27B RAM result uses `--cache-ram 0 --load-mode none`. Without those additions, the recorded run reached the memory guard because CPU conversation cache and mapped weights occupied RAM, despite full GPU offload.

[Measurements and limits](docs/results.md), [raw answers/timings](results/2026-10-14/answers-and-timings.json), [method](docs/benchmark-method.md), [troubleshooting](docs/troubleshooting.md).

The portable wrapper completed the full suite on this machine using existing verified assets: the same10/24 and14/24 main task scores,4/4 Thinking diagnostics for both, and12 passing checks. A fresh full redownload and a second PC were not tested. Hosted CI is offline and has no GPU. This repository contains no weights or engine binaries. Reproduction helpers are MIT; dependencies and models retain their upstream licenses. See [NOTICE](NOTICE).
