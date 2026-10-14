# Recorded results: 2026-10-14

All numbers below came from the actual Windows PC. The public data contains selected metrics and complete benchmark answers; original host paths, GPU UUIDs, owner PIDs and cooling-controller state are not included.

## Hardware and software

| Component | Recorded setup |
|---|---|
| CPU | AMD Ryzen7 9850X3D,8 cores/16 threads |
| RAM |32GB installed,31.1GiB visible to Windows |
| OS |Windows x64,10.0.26200 |
| Main GPU |RTX5070,12,227MiB exposed, WDDM, PCIe Gen5x16 |
| Peer GPU |Tesla V100 PCIe32GB, TCC, PCIe Gen3x4 |
| Driver |581.80 on both cards |
| Strata |v0.1.44.1,296b59d2f73c8f04a805df29c2fa97655adace78 |
| Dual-GPU engine |Stock CUDA12.9, sm70 and PTX; RTX5070 uses PTX |
| Flash weights |ISTA-DASLab GSQ-RCO IQ3_XXS, revision ed59f92082b1e93c0e96d60a8b11aab089b52f09 |
| V100-only model |Qwen3.8-27B RANA Q4_K_M + separate Q8_0 MTP and FP16 mmproj |
| llama.cpp |b10964/b29c606e2, Windows CUDA12.4 release |

Both GPUs had PnP Code0 and passed FP16 CUDA numerical checks. Cross-GPU P2P access was false in both directions. A pinned64MiB transfer,12 repetitions, measured host→V1003.20GB/s and host→RTX507056.64GB/s. These are transfer measurements, not inference token rates.

## Original Strata experiment

Japanese explanation, Python code and37×29, each3 runs, temperature0, Thinking OFF. Japanese output cap192 tokens; code cap160. Speeds are engine timings, TTFT is observed by the HTTP client.

| Configuration | Maximum context | Japanese tok/s | Code tok/s | Mixed TTFT |
|---|---:|---:|---:|---:|
|RTX5070 CUDA13|32K|25.1|15.6|2.423s|
|RTX5070 CUDA12.9|32K|23.9|14.9|2.537s|
|RTX5070+V100 peer, prefill on primary|32K|61.4|66.9|0.521s|
|RTX5070+V100 peer, prefill shared|128K|50.5|50.0|0.673s|

The shared-prefill profile uses `--peer-prefill-rows 10240` (prefill1024×10 active experts). The V100 is a stock peer expert cache **and compute device**, not a custom CUDA13 storage-only implementation. All37×29 answers were1073. Short capped replies do not establish complete-answer quality.

Synthetic long prompts repeat ordinary records and put `SPECIAL_CERTIFICATE=EMBER-2746` in the middle. Each length was run once, with zero cached input reuse.

| Profile | Actual API input tokens | Prefill tok/s | TTFT | Retrieval |
|---|---:|---:|---:|---|
|Prefill on primary|3,896|111.6|34.932s|correct|
|Prefill on primary|64,792|106.2|610.183s|correct|
|Shared prefill|3,900|336.0|11.653s|correct|
|Shared prefill|64,796|346.1|187.257s|correct|
|Shared prefill|128,808|332.2|387.862s|correct|

The primary-only-prefill128K request was interrupted after64K success to try the improved profile; it was not a completed128K result. The improved profile completed128K. Long outputs were only a short retrieved string. Real-document reasoning and extended continuous load were not evaluated.

The primary expert cache shrank from about4.11GiB at32K to2.68GiB at128K; the peer cache was about30.48GiB. `--expert-cache-device1 auto` plus resident experts was rejected, and a separate mmap/helper attempt hit the RAM guard during initialization. No successful helper performance is claimed. Layer split was prepared but not measured.

## New32K comparison against RANA

Same input, Thinking OFF, greedy sampling,3 runs per speed task,256 output-token cap. Fresh requests, zero input cache reuse. This comparison uses shared prefill at32K; its results are distinct from the earlier192/160-token Strata experiment.

| Metric | V10027B RANA | RTX5070+V100 Flash |
|---|---:|---:|
|Japanese decode median|41.0 tok/s|59.2 tok/s|
|Python decode median|63.1 tok/s|71.8 tok/s|
|Mixed TTFT median|0.382s|0.759s|
|Japanese256-token request wall time|6.68s|5.18s|
|Python256-token request wall time|4.34s|4.20s|
|~4K first token, one run|5.42s|9.62s|
|~16K first token, one run|23.81s|38.15s|
|Server startup, one run|12.6s|45.5s|

Actual long inputs were3,908 and16,183 tokens for both. The short generation requests were truncated for speed measurement. Full Japanese review responses, code checks and long retrieval are saved separately.

### Task quality

Expected answers were fixed before sending prompts: numeric8, extraction5, logic5, explicit-format instructions2 and Python4. Generated Python was checked against boundary cases under restricted builtins/AST and a3second subprocess timeout. No model judge was used.

| Assessment | RANA | Flash |
|---|---:|---:|
|Task success, content+format|10/24|14/24|
|Content correct, excluding only JSON Markdown-fence violations|12/24|15/24|
|Numeric|1/8|3/8|
|Extraction|2/5|3/5|
|Logic|3/5|3/5|
|Explicit format|2/2|2/2|
|Python boundary checks|2/4|3/4|

Both models made arithmetic/reasoning mistakes with Thinking OFF. The same four failed tasks were then selected for an **adaptive diagnostic**: library opening hours, invoice calculation, latest order revisions and truth tellers. Max2048 tokens/thinking budget1536. RANA accepts `xhigh`; Strata accepts `high`. These labels are implementation-specific and not equal semantic reasoning levels.

Both achieved4/4 with observed reasoning text. Mean completion: RANA6.07s, Flash6.25s. This small diagnostic cannot establish a broad model ranking. The default27B is a modified RANA model, not the original UD-Q4_K_XL; quantization and backend also differ.

Japanese prose was readable from both. Manual review found technical overgeneralization: RANA proposed an unverified NVLink/PCIe configuration, while Flash implied that offload reduces GPU load and that separate GPUs prevent frame drops. The measured primary GPU was near93% utilization. Review details: [japanese-review.json](../results/2026-10-14/japanese-review.json).

### Resource use during the short speed tests

| Metric | V10027B RANA | RTX5070+V100 Flash |
|---|---:|---:|
|V100 used peak / free minimum|21.75 /10.25GiB|31.46 /0.54GiB|
|RTX5070 used peak / free minimum|0.93 /11.01GiB|10.90 /1.04GiB|
|RTX5070 mean utilization|1.2%|93.3%|
|Available host RAM minimum|21.67GiB|8.27GiB|
|Owned server private commit peak|4.08GiB|27.03GiB|
|Host CPU mean|9.3%|50.4%|
|Owned server CPU mean, normalized to entire PC|5.7%|46.1%|
|Median physical host disk reads per short request|0.460MiB|2.849GiB|
|Mean power, both GPUs only|237.1W|165.2W|

GPU counters include desktop/other processes. Private commit is not physical resident RAM. Disk counters cover the entire host. GPU power excludes CPU and wall power. These are sampled measurements, not benchmarks of a simultaneous VR/game/image-generation workload.

RANA ran all65/65 layers on the V100, including the GPU output layer. Its V100 was near full compute load while the5070 stayed close to desktop idle. This leaves5070 capacity for other uses, while host resources remain shared.

With usual mmap/CPU conversation-cache defaults, a preliminary RANA run reached0.57GiB available RAM and was stopped by the guard. Adding `--cache-ram 0 --load-mode none` let the complete suite finish with at least21.46GiB available RAM. The normal AI application's settings were not changed. GPU residency alone does not guarantee spare host RAM.

Highest core/HBM readings in the completed main runs: RANA60/66C; Flash51/37C. No guard stop occurred in completed main runs. Both model servers were stopped and their model VRAM released afterwards.

## Published evidence

- [Original Strata case summary](../results/2026-10-14/strata-summary.json)
- [Comparison summary](../results/2026-10-14/comparison-summary.json)
- [Complete comparison answers and engine timings](../results/2026-10-14/answers-and-timings.json)
- [Portable wrapper validation](../results/2026-10-14/reproduction-validation.json)
- [Fixed main tasks](../fixtures/fixtures.json) and [Thinking diagnostic](../fixtures/thinking-fixtures.json)

These curated exports preserve measured values. They omit machine identities and private lifecycle files. No screenshots, cooling application files, driver backups, arbitrary workspace data or model weights are included.
