# 予測モデルの学習・評価・配布

採用済みJSONは `src/jvdata_bridge_mcp/ml/models/` でGit管理し、wheelとsdistに同梱する。
追加JSONがない場合は同梱線形モデルを使う。使用するモデルの出所と状態は
`prediction_model_status(race_code)` または `uv run jvdata-bridge-mcp models status` で確認できる。

`ml/` はJSONの係数・木による推論、`ml/research/` は学習・評価を担当する。

## 同梱モデル

配布ソースには次の固定係数を含む。採用済み能力モデルJSONがない構成では、この表のモデルを使う。

| 予測対象 | 入力・計算 | 学習・選択・開発評価期間 | 確率の扱い |
| --- | --- | --- | --- |
| 勝率・複合分析 | 過去成績、適性、時計、速度、過去市場評価、騎手・調教師などの11指標を線形合成。勝率はSoftmaxで正規化 | 学習2021〜2025年、設定選択2023〜2024年。2026年は開発で参照済み | 勝率・上位着順・組合せ確率は独立した実証較正が必要。複合分析の既定スコアと勝率は同じ係数による |
| 前半3Fの相対ペース | 代表脚質、過去1コーナー順位、履歴被覆率、頭数による線形モデル | 学習2021〜2024年、開発評価2025年 | 連続指数と相対区分を返す。スロー・ミドル・ハイ確率は未較正のためnull |
| 最初の記録コーナー・4コーナー | 馬の最大20走・騎手の最大50騎乗などの38特徴量、順位回帰と先頭分類による分布生成 | 最終学習2012〜2024年、設定選択2023〜2024年。開発で参照済みの評価は2025年と2026年9月27日まで | 位置確率・80%区間は未較正。ペースとの同時確率は未検証 |

開発で参照済みの期間は未見の最終評価に含めない。位置モデルは8〜18頭の芝・ダート平地戦で、
新潟芝直線1000mを除く。位置モデルの詳しい出典・評価対象は
`simulate_race_scenarios.position_model_provenance` に保持する。

線形モデルの勝率・特徴量・複合分析には、全出走候補馬の対象日・モデル版に一致する速度行が必要となる。
ペース・位置は速度を計算入力に使わず、速度表示の欠測だけでは停止しない。
速度read modelの定義と検査は[データベース契約](database-contract.md#予測用の速度read-model)を参照する。

## 市場なし候補モデルの特徴量

学習・評価コードは57特徴量と87特徴量の市場なしモデルを扱う。
採用済みJSONを導入した場合、勝率と `get_horse_feature_matrix` はその特徴量・欠測処理を使う。
複合分析、ペース、位置はそれぞれの同梱係数を使う。

候補モデルは対象レースの前日までに完了した確定結果と調教を使う。集計関数は学習と推論で共有し、
同日の全レースの特徴量を計算してから履歴を更新する。対象結果、対象オッズ、現在マスタの累計成績は
能力特徴量へ含めない。定義は[基準特徴量](../src/jvdata_bridge_mcp/ml/features.py)と
[拡張特徴量](../src/jvdata_bridge_mcp/ml/expanded_features.py)を正とする。

基準57特徴量は過去走、時計差、距離・コース、騎手・調教師、斤量、休養、調教、位置変化など。
`prior-date-market-free-v2` は次の30特徴量を含む87特徴量の契約で、選択時に群ごとの除外再学習を行う。

- **公式条件**: RAの年齢別条件を使う。保存版で値が一致しなければ不明とする。
- **過去時計と相手**: 同場・同走路・同距離の先行400レースが基準。
  過去馬場の一致標本が20件以上あればその集団を使い、当日結果は基準に入れない。
- **調教母集団**: 種別・トレセン・コースをそろえ、調教当日より前の365日で標準化する。
- **出走構成**: 他馬の序盤先頭・前方傾向と、過去のペース条件別成績を使う。

研究用抽出は履歴を2010年から、学習対象を履歴準備期間を除く2012年から構築する。
速度指標の対象期間は2018年以降であり、各レースの収録を保証しない。欠測フラグ・履歴件数・被覆率を保持する。
欠測は学習時に確定した補完またはネイティブ欠測処理に従う。
現在の馬体重・天候馬場を対象結果側の値で補完しない。保存出馬表の当時の発表版と実際の取得時刻は
不明であり、完全な過去時点の再現は認証しない。

## 開発環境とデータ保存

Git checkoutのルートで実行する。研究用依存は通常のインストールと分離する。

```bash
uv sync --extra research --group dev
```

DB接続は `JvDataDatabase.read_connection` のread-only transactionを使う。
生データ、候補モデル、レース別の予測・結果、評価レポートはGit管理外の `archive/` に置く。
保存時に無視設定と追跡ファイルがないことを検査し、wheelとsdistからも除外する。
JSON形式でも、採用・公開確認を経ていない研究成果物は配布対象にしない。

## 学習とモデル選択

`<開発終了日>` と `<最終評価終了日>` は排他的な上限日、`<最終評価開始日>` は含む。
開発終了日は2023年以降で、その年の7月1日より後かつ実行日以前を指定する。
最終評価は実行日より後に始まる未使用期間とし、研究ごとに新しい保存先を使う。

```bash
uv run --extra research python -m jvdata_bridge_mcp.ml.research.extract --output archive/study/base --end-date <開発終了日>
uv run --extra research python -m jvdata_bridge_mcp.ml.research.data --output archive/study/extended --end-date <開発終了日>
uv run --extra research python -m jvdata_bridge_mcp.ml.research.dataset --source archive/study/base --output archive/study/reference
uv run --extra research python -m jvdata_bridge_mcp.ml.research.expanded_dataset --source archive/study/base --extract archive/study/extended --reference archive/study/reference --output archive/study/data
uv run --extra research python -m jvdata_bridge_mcp.ml.research.train --output archive/study --phase freeze --development-end <開発終了日> --final-start <最終評価開始日> --final-end <最終評価終了日>
uv run --extra research python -m jvdata_bridge_mcp.ml.research.train --output archive/study --phase fold --year <開発年>
uv run --extra research python -m jvdata_bridge_mcp.ml.research.train --output archive/study --phase finalize
```

`freeze` はデータとソースのハッシュ、期間、候補群、採用基準を固定する。
`protocol.json` の4つの開発年それぞれで `fold` を実行してから `finalize` する。
LightGBMの学習窓選択では開始年2012・2015・2018と基準57・全87特徴量を組み合わせ、減衰重みも比較する。
選択した窓で他のモデル群、特徴量群のアブレーション、順位スコアの除外を比較する。
候補は条件付き線形、LightGBMの二値分類・LambdaRank、CatBoostのLoglossである。

| 用途 | 開発年Yに対する期間 |
| --- | --- |
| 初期学習 | 選択した開始年〜Y-2年末 |
| 早期停止 | Y-1年1〜3月 |
| 仮の確率較正 | Y-1年4〜6月 |
| モデル・特徴量・較正方式の選択 | Y-1年7〜9月 |
| 独立した確率較正 | Y-1年10〜12月 |
| Walk-forward開発評価 | Y年、開発終了日より前 |

同一レースを複数の分割へ分けない。選択後はY-1年9月末までで再学習する。
選択基準は較正後のレース平均Log Lossで、同期間の線形対照よりBrierを悪化させる構成を除く。
較正は無補正、全体温度、頭数依存温度、履歴の浅さに応じた温度補正を比較する。
Softmaxや温度適合だけで較正済みとは扱わない。最終候補は最新foldの選択に従い、
開発年の6月末までで学習し、7月から開発終了日までで較正する。
開発foldの結果はモデル選定用であり、未見の最終評価とは扱わない。
同梱線形・位置係数の学習期間と開発で参照した期間は、比較時に必ず確認する。

## 評価と採用

現行SQLによる予測を、全頭ロスターが一致する共通レースで保存する。
市場比較は発走10分前までに発表されたstage1/2の単勝観測を使い、最終オッズで欠測を埋めない。
これは研究用の判断境界であり、実際の取得・購入時刻を復元した値ではない。

```bash
uv run --extra research python -m jvdata_bridge_mcp.ml.research.baseline --features archive/study/data/features.parquet --output archive/study/reference --start-date <比較開始日> --end-date <開発終了日>
uv run --extra research python -m jvdata_bridge_mcp.ml.research.market --output archive/study/reference --start-date <比較開始日> --end-date <開発終了日>
uv run --extra research python -m jvdata_bridge_mcp.ml.research.evaluate --output archive/study --reference archive/study/reference
uv run --extra research python -m jvdata_bridge_mcp.ml.research.feature_audit --source archive/study/base --output archive/study
uv run --extra research python -m jvdata_bridge_mcp.ml.research.ancillary --source archive/study/base --study archive/study
```

欠損率・日付リーク、勝率Log Loss・Brier・較正曲線、NDCG、勝馬Top3包含、上位着順・組合せの
確率スコア、条件別・月別成績を確認する。Brierは馬ごとの二乗誤差のレース内平均をレース平均する。
信頼区間は開催日単位の対応付きブートストラップで計算する。
別研究との共通レース比較には `evaluate --previous` を使える。
同梱線形係数への較正だけを検証する対照実験は、同じプロトコルの最新foldで実行する。
初期較正・選択・再較正の期間も含むSQL予測を `archive/study/reference/exact-baseline.parquet`
に用意し、`uv run --extra research python -m jvdata_bridge_mcp.ml.research.incumbent_calibration --study archive/study`
を実行する。

数値欠測、未知カテゴリ、分岐境界を含めてネイティブ推論とJSON推論の一致を検査する。
CatBoostはone-hotカテゴリ分岐とfloat32入力を使い、ターゲット統計分岐を配布形式へ変換しない。
各モデルを別プロセスで測り、推論時間・サイズ・RSS・ピークメモリを記録する。
測定はスコア計算の範囲で、DB取得・組合せ列挙・MCP応答全体の時間とは区別する。

ペース・位置の比較は同じプロトコルの最新開発年を使う。Macro F1、クラス別Recall、Log Loss、
前半3FのMAE、コーナー順位MAE、先頭・前方3頭のBrierと較正を確認する。
最初の記録コーナーと4コーナーを分け、順位変化によるまくり候補を序盤の逃げと混同しない。
記述的な整合性だけでペースと戦術の同時確率が較正されたとは扱わず、採用は別に検証する。

最終評価は固定期間の終了後、再学習せずに行う。同じ抽出・特徴量構築手順で新しい
`archive/final/` を作り、同じ期間の現行SQL予測も保存する。

```bash
uv run --extra research python -m jvdata_bridge_mcp.ml.research.future --output archive/study --features archive/final/data/features.parquet --baseline archive/final/reference/exact-baseline.parquet
```

候補・ソース・評価ルール・入力データのハッシュを検査し、期間終了前には正解を読まない。
採用には全頭一致200レース以上、開発4fold中3fold以上のLog Loss改善と平均差の改善、
最終期間の現行SQLに対するLog Loss差の95%上限が0未満、Brier・3着内・組合せスコアの非悪化を要求する。
未達なら同梱線形モデルを維持する。市場確率との差と回収率は別に評価し、確率とオッズの積が
1を超えるだけで収益性を主張しない。固定研究の評価には登録時のソースを使い、
結果を見て期間・基準・ハッシュを書き換えない。

## 着順確率と市場比較

`predict_race_probabilities` の勝率・2着内・3着内は、馬を重複選択しないPlackett–Luce型の
同じ着順分布から計算する。採用済みJSONの2・3着の相対強度は独立した較正期間で適合する。
同梱線形モデルは補正係数を1とする標準PL分布を使い、組合せ確率を較正済みとは扱わない。
線形モデルの `historical_market_diagnostics` は過去市場項を除いた未再学習の診断値で、
市場なし採用モデルではnullとなる。同じ確率を別名で重複出力しない。

馬連・ワイド・3連複・3連単は既定で各上位20件を返し、表示部分を再正規化しない。
任意の `combinations` で計200件まで指定でき、最大18×17×16通りの上位3頭分布から求める。
勝率、馬連、3連複、3連単の総和はそれぞれ1。ワイドは3組が同時的中するため的中確率の総和は3で、
総和1の `normalized_selection_mass` と区別する。分布計算は1〜18頭に対応し、2頭時はワイド総和1、
3連系は未成立となる。採用済みJSONによる能力予測はJRA芝・ダート平地の出走可能馬3〜18頭を対象とする。
同梱線形モデルには同じ適用域検査がなく、計算の成功だけでは対象条件での精度を保証しない。

勝率・組合せ確率は、時刻確認済みAVによる取消・除外を現在ロスターから外して正規化する。
特徴量・複合分析・位置分布が返す候補範囲は[予想コンテキスト](forecast-context.md#モデル出力とライブの事実)を参照する。
通知なしは収録完了の保証ではない。
同着の勝馬評価は勝者間で正解質量を分け、厳密着順の組合せ評価から同着等を除外する。
競走中止は勝利・3着内の正例にせず、推論は3頭以上が着順を得る前提とする。
中止・失格・同着・返還・特払いを独立イベントとしてモデル化してはいない。

独立した馬評価を固定してから `odds_table` に `model_probabilities` を渡すと、観測オッズに
研究用の期待払戻倍率を付ける。能力モデルは再実行しない。較正誤差、発表時刻と鮮度、
最終プールまでの変動、実際の払戻による回収率・件数・信頼区間・ドローダウンを確認する。
表示オッズは払戻込みであり、控除率を再度掛けない。標準の比較レポートは確率評価を対象とし、
売買規則・資金配分を固定した回収率の検証結果を付属しない。期待払戻倍率は研究用の参考指標として扱う。

## 採用済みJSONの配布

```bash
uv run jvdata-bridge-mcp models package --model archive/study/approved-model.json --output archive/model-bundle
uv run python scripts/validate_model_bundle.py archive/model-bundle
# 自分で学習した承認済みモデルをローカル導入する場合
uv run jvdata-bridge-mcp models install --bundle archive/model-bundle
```

採用基準を満たすと `approved-model.json` が生成される。配布物はモデルJSONとmanifestの2ファイルで、
サイズ・SHA-256・特徴量契約・採用状態・学習期間を検証する。
破損・不完全・未承認のモデルはエラーとし、別モデルへ黙って切り替えない。
ローカル導入はキャッシュの `jvdata-bridge-mcp/models/` を使い、同梱JSONより優先する。
ベースディレクトリは `LOCALAPPDATA`、`XDG_CACHE_HOME`、`~/.cache` の順で決める。
開発者が公開内容を確認して2ファイルを `ml/models/` へ配置する。UTF-8/LFでGit管理し、
通常CIで検証する。学習・採用判定・通常CIからコミットや公開を自動実行しない。
