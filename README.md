# Strata: RTX 5070 + Tesla V100 再現キット

[English](README.en.md)

[Zenn実験記事：RTX 5070＋V100で大きなMoEを動かす](https://zenn.dev/lube8163/articles/87856ef9fffd66)

**Windows・RTX 5070 12GB + V100 PCIe 32GB・RAM32GBで、Qwen3.8-Flash-Next IQ3_XXSを実行した実験の再現用リポジトリです。** 5070を主GPU、V100を追加エキスパートの保持・計算に使います。V100単体のQwen3.8 27B RANAとの比較測定もできます。

Strata本体へのパッチはありません。固定コミットのStrata、CUDA12.9配布エンジン、モデルの固定revision/SHA256を使い、PCごとの保存先とGPU UUIDだけをローカルに設定します。モデル本体・ドライバー・個人用の冷却ソフトは同梱しません。

## 実測の要点

同じ32K会話枠、Thinking OFF、temperature0。短文は256tokenで打ち切り、各3回の中央値です。資源はその短文試験中に測定しました。

| 項目 | V100単体・27B RANA | 5070主 + V100・Flash-Next |
|---|---:|---:|
| 日本語生成 | 41.0 tok/s | 59.2 tok/s |
| Python生成 | 63.1 tok/s | 71.8 tok/s |
| 最初の文字まで | 0.382秒 | 0.759秒 |
| 約16K入力の最初の文字まで（各1回） | 23.81秒 | 38.15秒 |
| 5070の空きVRAM | 11.01 GiB | 1.04 GiB |
| PC全体の空きRAM | 21.67 GiB | 8.27 GiB |
| PC全体の平均CPU使用率 | 9.3% | 50.4% |
| 24問の課題達成（形式込み） | 10/24 | 14/24 |

誤答した同じ4問をThinking ONにすると両方4/4正答、平均完了は約6秒でした。24問の達成数は一般的な正答率ではありません。27BはRANA派生/Q4_K_M、FlashはIQ3_XXSで、量子化・バックエンドも異なります。

**27Bは他用途との併用に余裕があり、Flashは短文生成が速い**という結果でした。27Bの空きRAMは会話キャッシュとモデルmmapを抑えた試験設定です。ゲーム/VR/画像生成の同時実行性能は未測定です。

[全測定と限界](docs/results.md) / [回答・timings](results/2026-10-14/answers-and-timings.json) / [比較集計](results/2026-10-14/comparison-summary.json)

## 必要な環境

- Windows x64、Python3.12以上、Git、`nvidia-smi`が使えること。
- RTX 5070級12GB + **V100 PCIe32GB**、RAM32GB以上。V10016GB版はこの設定の対象外です。
- 両GPUを認識する共通NVIDIAドライバー。測定時は両方581.80、5070はWDDM、V100はTCCでした。ドライバー導入やTCC切替はこのキットから行いません。
- Flashの取得は75.84GB、生成するpacks等を含め約120〜140GBのディスク容量。RANA比較は追加約20.64GB＋llama.cpp配布物が必要です。
- V100の実装に適した冷却が稼働していること。実行中は独立したNVMLでコア/HBM温度を監視し、80℃または空きRAM2GiB未満が10秒続いたら、開始したサーバーだけを停止します。

**リポジトリとデータは英数字のパスに置いてください。** Windowsの日本語パスでは、今回のStrata配布エンジンのPLE読み込みに失敗しました。例は`C:\AI`を使っていますが、書き込める英数字の別ドライブでも構いません。

## 最短手順

PowerShellで実行します。以下のセットアップはモデルの大容量取得を明示的に許可するコマンドです。

```powershell
git clone https://github.com/lube8163-lab/strata-rtx5070-v100-repro.git C:\AI\strata-rtx5070-v100-repro
cd C:\AI\strata-rtx5070-v100-repro
python repro.py check
python repro.py bootstrap --root C:\AI\strata-runtime --accept-large-download
python repro.py configure --root C:\AI\strata-runtime
python repro.py run --engine flash
```

`http://127.0.0.1:8097/`を開きます。OpenAI互換APIは`http://127.0.0.1:8097/v1`、モデル名は`local-agent`です。localhost限定、APIキーなし。コンソールのCtrl+Cで、このコマンドが開始したサーバーを停止してVRAMを解放します。

GPUが複数ある場合は、`check`と`configure`に`--primary INDEX_OR_UUID --peer INDEX_OR_UUID`を追加してください。未指定では名前から5070とV100を選び、曖昧な場合は停止します。GPU UUIDと絶対パスを保存する`.local/`はGitの対象外です。

### 128KのFlashを使う

サーバーを停止してから実行します。

```powershell
python repro.py run --engine flash --context 131072
```

128,808tokenの合成入力から短い文字列を回収できた実験があります。実文書の理解精度や長時間の安定性の証明ではありません。KV拡大で5070のexpert cacheは減るため、32Kと同じ速度にはなりません。

### 27Bとの比較

```powershell
python repro.py bootstrap-rana --root C:\AI\strata-runtime --accept-large-download
python repro.py configure --root C:\AI\strata-runtime
python repro.py benchmark --smoke
python repro.py benchmark --thinking
```

RANA→RANAのThinking追加試験→Flashの順に起動し、同時にGPUを奪い合わないよう順番に停止します。24問、短文6件、日本語説明2件、約4K/16Kの合成長文と、追加Thinking4問を保存します。`.local/runs/<実行時刻>/`に全回答、設定、ログ、0.5秒テレメトリ、`summary.json`、`answers.md`ができます。既存結果は上書きしません。

Flashだけの測定は`python repro.py benchmark --engines flash --thinking`。27BのWeb/API起動は`python repro.py run --engine rana`で、ポートは8098です。通常使用中の8088設定には触れません。

## 既存環境を使う

ダウンロード済みの同じ版を使う場合はセットアップを省き、保存先を指定できます。

```powershell
python repro.py configure --root C:\AI\strata-runtime `
  --strata-dir D:\AI\Strata `
  --data-dir D:\AI\Strata-data `
  --cuda-dir D:\AI\cuda12-runtime `
  --rana-model D:\AI\rana\Qwen3.8-27B-RANA-abliterated-Q4_K_M.gguf `
  --rana-mtp D:\AI\rana\mtp-Qwen3.8-27B-RANA-abliterated-Q8_0.gguf `
  --rana-mmproj D:\AI\rana\mmproj-Qwen3.8-27B-RANA-abliterated-F16.gguf `
  --llama-exe D:\AI\llama\llama-server.exe
python repro.py verify --engine flash
python repro.py verify --engine rana
```

`verify`はモデルのSHA256を実際に読み直します。既存資産の起動チェックはサイズ・Strataコミット・engine BUILD情報であり、毎回75GBのハッシュ計算はしません。既存フォルダーのモデルを使う場合は先に`verify`を実行してください。

## 設定の理由と確認状況

- CUDA13の配布エンジンにはV100用sm70が含まれないため、両GPUの実行はCUDA12.9の配布エンジン（sm70+PTX）を使います。5070側はPTX経由です。
- `--peer-device 1 --peer-prefill-rows 10240`でV100側のexpertを保持・計算します。prefill1024×active experts10です。V100は純粋な倉庫専用ではありません。
- `--resident-budget-gib 12`、INT8 KV、5070 reserve1200MiB、V100 reserve1024MiBを使用。helper方式とlayer splitはこの再現手順には入れていません。
- 27Bは全層V100・FP16 KV・MTP3・画像projectorもロード。`--cache-ram 0 --load-mode none`でCPU側の保存を抑えます。元の試験ではこれを付けない連続リクエストでRAM不足の保護停止が起きました。
- Thinking ONの指定名は、RANAのテンプレートが`xhigh`、Strataは`high`。両方最大2048token/思考予算1536tokenで追加診断します。同じ名前の思考レベルの比較とは扱いません。

このPCで元の実験に加え、公開用コードから既存資産を使った全比較を完走しました。24問の達成数は元と同じ10/24・14/24、Thinking追加4問は両方4/4でした。12件のチェックも通過しています。ゼロからのモデル再取得を再実行した検証や、別PCでの確認は含みません。CIはGPU・モデル取得を使わないチェックのみです。

[測定方法](docs/benchmark-method.md) / [トラブル対処](docs/troubleshooting.md) / [固定版・SHA256](models.lock.json) / [公開用コードの検証記録](results/2026-10-14/reproduction-validation.json)

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

このリポジトリの再現用コードはMIT。Strata、llama.cpp、NVIDIAライブラリ、モデルはそれぞれの配布元のライセンスを参照してください。[NOTICE](NOTICE)
