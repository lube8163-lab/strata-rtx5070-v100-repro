# トラブル対処

## V100が見えない / Windows Code10

最初にデバイスマネージャーで5070とV100の状態を確認し、`nvidia-smi`に両方が出ることを確認します。今回のPCでは共通の581.80へ揃え、通常の再起動後にCode0とCUDA計算まで復旧しました。別PCのドライバーを自動で戻す手順にはしていません。

両GPUのドライバー対応と、V100/Blackwellを同時に使えるパッケージをそのPCで確認してください。インストールやTCC切替は手動です。GPUが無効/エラーの状態でモデルの再ダウンロードを始めても解決しません。

## CUDA13でV100が実行できない

この測定ではstock CUDA12.9 engineのsm70とPTXを使っています。`Strata/engine-cuda12/BUILD.json`のversion0.1.44.1、cuda12.9、archs70、ptx=trueを確認します。5070のCUDA13単体は比較用に動きましたが、V100とのpeerにCUDA13を流用していません。

## PLE / DirectFileの読み込みに失敗

エンジンに渡すパスをASCIIにしてください。リポジトリ、Strata、Strata-data、CUDAライブラリ、ログ保存先も英数字にします。既存の日本語パスを使いたい場合は、自分で検証したASCIIのdirectory junctionを指定できます。今回のキットが既存データを移動することはありません。

## 27BはGPUに収まるのにRAMが足りない

全GPU offloadでも、Windowsのmmap Working Setとllama-serverのCPU会話キャッシュが残ります。この再現用RANAは `--cache-ram 0 --load-mode none` を付けて、別用途へRAMを残しています。通常アプリ側の設定は変更しません。CPU側保存の方式を変えた結果と、モデル自体のVRAM容量を分けて確認してください。

## HTTP500: Unexpected reasoning effort high

今回のRANAテンプレートはxhigh(default)/medium/lowを受理し、highは受理しません。RANAには`reasoning_effort:xhigh`、Flash/Strataにはhighを指定します。API指定で推論開始前に失敗したケースを、モデルの誤答として採点しないでください。

## GPU間P2Pがfalse

このPCでも双方向falseでした。stock peerはhost stagingを経由して実行できています。NVLink/P2Pがあるという前提で組んだ設定ではありません。V100のGen3x4帯域とCPU/RAM共有は残ります。

## RAM/温度ガードで止まる

その実行の`results.json`の`guard_stop`、`telemetry.jsonl`を確認します。NVMLを使って生のコア温度と取得できた新鮮なHBM温度を記録しています。冷却アプリのキャッシュされたActive表示を現在のセンサー値の代わりに使いません。原因を直してから再実行してください。

既存のポート8097/8098が使用中なら起動を拒否し、他プロセスは終了しません。すでにV100で別LLMが動いている場合も、元のアプリから停止してから試してください。

## ダウンロードが中断 / checksum不一致

range downloadは`.part`と`.ranges.json`を保持して再開します。最後にSHA256が合わないファイルを完成品の名前へ変更しません。既存の完成ファイルが固定ハッシュと違う場合も、削除・上書きせず停止します。`models.lock.json`のrevision・サイズ・SHA256と使用する資産を確認してください。

## バージョンの更新

本キットは測定した版へ固定しています。Strataやドライバー、モデルを更新すると結果が変わり得ます。更新版の再現実験は別の記録として扱い、元の数値や固定SHAをそのまま新しい版の結果にしないでください。
