# 予想手順：レース後レビュー・知識管理

本文の数値例と序列は説明用の目安であり、検証済みの判定閾値ではない。根拠の適用範囲と不確実性はResourceメタデータと、予想規律の手順に従って確認する。

## Knowledge Record標準形

すべてのナレッジは以下の形式を推奨。

```yaml
knowledge_id:
title:
kind: hypothesis
status: experimental
resource_uri: null
resource_content_sha256: null
evidence_period:
  start: null
  end: null
sample_size: null
comparison_definition: null
scope:
  racecourse:
  surface:
  distance:
  class:
  condition:
trigger:
observation:
interpretation:
positive_effect:
negative_effect:
exceptions:
counter_conditions:
minimum_sample_size:
confidence:
data_sources:
counterexamples: []
applicability_limits: []
last_validated_at:
```

---

## 印変更履歴

```yaml
revision_history:
  initial_forecast_id: null
  initial_history_before_date: null
  initial_content_sha256: null
  revision_time: null
  revision_analysis_at: null
  new_fact_ids: []
  before: null
  after: null
  reason: null
```

初回と発走前改訂は別版として保存する。結果後の誤記・来歴訂正は追記にし、初期理由・順位・証拠ハッシュ・採点を上書きしない。固定後の取消や追加履歴で、元の選択を遡って差し替えない。

---

## Post-Race Review

レース後は、

```text
当たった / 外れた
```

だけを評価してはいけない。

見るもの：

- ペース予測
- 位置取り予測
- 馬場評価
- 能力評価
- 市場評価
- 買い目構築

---

## 予想が当たり・判断が悪い例

たまたま穴馬が来て当たったが、

根拠が弱かった

なら学習対象として成功にしない。

---

## 予想が外れ・判断が良い例

予想した構造・条件と実際の決着を分けて確認する。出遅れ・落馬・不利を失敗原因とするには、別の出典付き観察を挙げる。中止や順位変化だけで原因を作らない。

外れが偶然・不利によるものだったと証明できなければ、原因は未確認のままにする。外れだけで判断を否定せず、想定した枝の範囲、能力の支持と反証、当時の未知を検討する。

---

## レビュー分類

```text
race_structure_error:
pace_error:
position_error:
horse_ability_error:
condition_error:
market_error:
bet_construction_error:
random_event:
```

---

## ナレッジ更新

同じ誤りが蓄積した場合のみルール変更。

1レースでルールを作り替えない。

---

## 保存済みライブ予想の結果照合

任意の過去時点を再現するバックテストは行わない。発走前に保存した初期予想について照合する。

評価する期間・競馬場・季節・比較手順・目的・指標、標本選択・境界・除外／欠測の方針、各knowledge_idの適用と順位への作用を結果閲覧前に固定する。エージェント予想は、取得Factをエージェントが評価した初期選択を採点する。

| 指標 | 条件 |
|---|---|
| 標本数、本命勝率、勝馬が選択3頭に含まれた率、選択3頭の実際の3着内頭数 | 初期順位と公式完走着順から計算。公式同着の全該当馬を含める |
| 確率の採点 | 対象事象の確率を結果前に提示した場合だけ。位置と勝敗は別事象 |
| 回収率 | 券種・組合せ・金額を結果前に固定し、公式払戻と照合できる場合だけ |
| 市場価値・事前期待収益 | 有効な時点オッズと同じ事象の確率評価が必要。順位差だけから推定しない |

発表時刻不明の取消・除外が結果で判明しても、元の分母と選択を保持し状態を別記録する。時計差0秒から同着を作らない。後日の過去走に先の対象結果が現れる場合は、先の予想が既に固定済みかと露出範囲を記録する。

---

## Knowledge Confidence

証拠強度は対象条件との一致、資料品質、独立した開催日の数、標本の偏り、反例、未見／既見の範囲から評価する。的中数や同じ決着の件数だけで高い確信に昇格しない。

問題発見・変更に使った探索標本と、採用候補を固定した後の未見評価を分ける。モデルの最終学習・設定選択・過去の開発で既見の期間を確認し、学習期間の外であるだけで開発全体の未見評価とは呼ばない。未較正の仮想位置分布から勝敗性能・収益改善を主張しない。

---

## 動的ナレッジと静的ナレッジ

コース形状の資料は適用する世代・確認済み変更境界とともに保持する。長期的な脚質傾向も参照集団・期間・反例が必要で、永久に有効な静的事実とは扱わない。

観察・根拠レースが対象レースと現在より前にあることを確認し、先行同日結果・当日調教には既知の時刻を必要とする。今日の成績・馬体・WF馬場・出走変更はrace_forecast_contextから取得し、既往証拠と現在観測を分ける。過去時点の情報を再現しない。実際のDB取得・取り込み時刻は不明として扱う。形状、記述統計、今回への仮説を別に保持し、公開・登録日時を適用判断の代わりに使わない。

---

## ナレッジの寿命

```yaml
course_geometry:
  scope: confirmed_configuration_era
  boundary_source: null
  later_changes: unknown_until_verified
long_term_bias:
  scope: recorded_reference_period_and_conditions
  recheck: comparability_selection_and_counterexamples
meeting_bias:
  scope: observed_meeting_and_conditions
same_day_bias:
  scope: completed_races_before_target_and_now; provisional_explicit; deduplicate_history
odds:
  scope: single_prefinal_observation_before_start_and_now; current_day_allowed; age_reported
```

固定のTTLだけで適用・信頼度を決めない。コース世代や条件が変わった場合は再確認し、未知の変更境界や時点を推測しない。

---

## ナレッジ検索

RAG検索時は、

```text
中山1200
```

だけでなく、

```text
中山
ダート
1200
稍重
1勝クラス
16頭
先行馬少数
```

のように条件検索する。

---

## Knowledge Conflict Resolution

競合する知識は、対象条件の一致、資料品質、独立した標本、参照期間、反例、時点の適格性を比較する。新しい資料だけを優先しない。

同じ過去走・同じ家族集計・同じ開催の結果を独立した複数の証拠として加算しない。両方に適用限界が残る場合は別の枝と未知を保持し、どの部分が順位に作用したかを記録する。

---

## 例

長期の前方好走と、当日の後方決着が異なる場合は、距離・材質・クラス・年齢・馬場・頭数・能力・ペースが比較可能かを先に読む。当日の数件だけで長期知識を上書きしない。

予想は未発走レースが対象で、歴史バックテストは行わない。race_forecast_contextが返す先行同日結果は完全速報と確定を分け、収録遅れ・少数標本・履歴と当日集計の重複を明示する。結果後に取得した同日比較を初期予想の証拠へ戻さない。

---

## 成果物の保存と公開

個別予想・買い目・実行ログ・採点・集計レポートはGit管理外のarchive/にだけ保存する。
公開するResourceは開発者が採用した判断知識であり、生データや個別のレース出力を含めない。
