# 測定手順と結果の読み方

1. `python repro.py check`で両GPUと共通ドライバー、RAMを確認する。
2. 同じ固定版の資産を用意し、`configure --context 32768`で主比較を選ぶ。再利用のGGUFは`verify`で照合する。
3. 他のLLMを停止し、冷却が稼働した状態で`benchmark --smoke`。これは起動・生成・終了の確認だけ。
4. `benchmark --thinking`で全試験を順番に実行する。RANAとFlashを同時ロードしない。
5. `.local/runs/<timestamp>/summary.json`、`answers.md`、各モデルの`results.json`、`telemetry.jsonl`とログを確認する。

`fixtures/fixtures.json`は元の課題をバイト単位で保持しています。同じプロンプトSHA256とfixture SHA256で入力の一致を確認できます。品質の正解・コード境界例は`reprolib/evaluation.py`。新しい結果を元の実測に上書きせず、別の実行フォルダーに保存します。

## 固定条件

- メイン試験: temperature0/top-k1、Thinking OFF、同時リクエスト1、32,768context、`cache_prompt:false`。
- 短文: 日本語とPythonを3回ずつ、256出力token。打ち切りによる速度測定と完成回答の品質を分ける。
- 品質:24問を各1回。正しい数値でもJSONコード枠を付けた場合、形式込みの達成では不合格。内容のみの数と分ける。
- 日本語: 説明と運用案の2件。自動採点の24問には含めず、人が本文を確認する。
- 長文: 同じ反復記録の中央の値を回収。約4K/16K各1回。これは検索・入力処理の確認で、長文推論の精度ではない。
- Thinking追加: 同じ4問、最大2048token、思考予算1536token。RANA=xhigh、Flash=high。OFF失敗から選んだ診断なので、独立した品質評価と混ぜない。

## 数値

- `decode_tps`: サーバーの`timings.predicted_per_second`。原則engine由来。fallbackのクライアント値はsourceを区別する。
- `ttft_s`: HTTP要求から最初の非空content/思考チャンクまで。Thinking ONでは思考が始まる時間を含む。
- `total_s`: 最後まで受信した時間。思考・prefill・HTTPの処理も含む。
- `prompt_tps`: engineの入力処理。キャッシュ再利用の有無を`reused_tokens`と`read_tokens`で確認する。
- `drafts_offered`/`drafts_accepted`: MTPの動作を確認する値。受理率が高いことと回答の正確さは別。
- RAM、CPU、GPU温度/VRAM/電力は約0.5秒の標本。短い1問のピークを完全に捕捉するものではない。
- CPUはPC全体の論理コア数で正規化する。記録時のコア数を保存し、別PCで集計してもそのコア数を使う。
- NVML HBMはfield82の成功・型・値・時刻の新鮮さを確認する。取得できない場合に古い冷却ソフトの値で補わない。
- SSDの物理読込はPC全体のカウンター。prefillも含む要求全体で差を取る。バックグラウンド処理の寄与を完全には除去できない。
- Strataの`file_mb`/`ram_blobs`等はdecode開始からの差分。prefillを含む全SSD読込量と置き換えない。
- GPU timelineとhost stagingは重なり得るため、単純加算しない。NVML PCIe値は瞬間サンプルで、転送量の厳密な積分ではない。

## 共有する結果

再測定の結果を共有する際はGPU名/VRAM、CPU/RAM、OS、ドライバー、PCIeの実速度/幅、context、量子化、Strata/llama.cppの版、Thinking、出力capを付けてください。実際の回答も確認します。

`.local`の設定・ログには各PCの絶対パスとGPU UUIDが入るため、そのままGitへ追加せず、必要な測定・回答の項目だけを書き出します。公開済みデータはその方式で選択したものです。
