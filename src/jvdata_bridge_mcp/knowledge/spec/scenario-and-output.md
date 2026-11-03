# 予想手順：シナリオ・出力・分析の役割

本文の数値例と序列は説明用の目安であり、検証済みの判定閾値ではない。根拠の適用範囲と不確実性はResourceメタデータと、予想規律の手順に従って確認する。

## シナリオ分析

遅い・中間・速い流れと、先頭候補・前2頭・途中進出が変わる枝を、支持するFactと反証に基づいて検討する。少頭数や逃げ候補数だけで流れを固定しない。障害や欠測で速遅を判断できなければ未知とする。

枝に経験的な確率がない場合は数値の重みを作らない。位置分布の確率と手動のペース仮説を、同じ確率モデルとして合成しない。

---

## Base Scenario

比較的支持が多い基本枝と、その根拠・反証・未知を示す。速い流れでの前残り、遅い冒頭からの後方進出などの代替枝も確認し、基本枝だけで馬を消さない。

枝間の確率を検証していなければ「最も可能性が高い」と数値的に保証しない。能力の支持と、今回その位置・ペースで発揮する仮説を分けて順位へ反映する。

---

## Alternative Scenario

穴馬は、

どのシナリオなら来るのか

を必ず示す。

---

## レース直前更新

ユーザーが更新を求めた場合はrace_forecast_contextから現在の観測を取得し、初回予想と改訂の実行時刻・内容ハッシュ・新Fact・順位や買い目の変更理由を保存する。初回を上書きせず、過去時刻の再現とも主張しない。

当日馬体・WF馬場・AV/JC出走変更はrace_forecast_contextの現在観測を出典と経過時間付きで別枠に記録する。取消は結果側から遡及せず、今日の完全速報・確定成績にも収録遅れと少数標本の限界を付ける。市場は独立した馬評価の固定後に扱い、有効な未確定観測がなければ価値を推測しない。

---

## 予想出力形式

```yaml
forecast:
  race_code: null
  scheduled_start_at: null
  timezone: Asia/Tokyo
  boundary: null  # 対象レースと現在時刻から自動決定
  analysis_at: null  # 予想を分析した実行時刻
  current_entry_observations: null
  current_observation_coverage: null
  revision_of: null
  knowledge_resources: []  # URI・ハッシュ・根拠期間・順位への作用
  race_structure:
    scenarios: []
    uncertainty: []
  ranking: []
  each_horse:
    support_fact_ids: []
    against_fact_ids: []
    unknown: []
    comparison_limits: []
    class_and_domain: null
  horse_evaluation_frozen_at: null
  market:
    status: not_queried
    snapshot_at: null
    availability_confirmed: false
  bets:
    status: not_constructed
    objective: null
    budget: null
    combinations: []
    stakes: []
```

順位と馬券戦略の印を別に保持する。未取得の市場や未構築の買い目は評価済みに見せず、確率を提示する場合は対象事象・算出法・較正の状況を併記する。

---

## 推奨アーキテクチャ

```text
JRA / JVData
    ↓
Fact Tools
    ↓
Fact Store
    ↓
Knowledge Retriever
    ↓
Race Structure Agent
    ↓
Horse Evaluation Agent
    ↓
Counter Evidence Agent
    ↓
Market Evaluation Agent
    ↓
Bet Construction Agent
    ↓
Final Synthesizer
```

---

## Race Structure Agent

コース世代・材質・内外・距離・馬場・年齢／クラス・頭数、位置履歴の範囲、ペースの枝、有利位置の仮説と反証を分析する。対象オッズ・人気を独立した構造評価へ混入しない。

同日傾向は利用可能性を確認できた場合だけ扱う。分担は責務の説明であり、別エージェントの実行を必須にするものではない。

---

## Horse Evaluation Agent

市場を知らずに、

純粋能力
今回適性
展開適性

を評価。

これにより人気へのアンカリングを防ぐ。

---

## Market Agent

最後に市場データを投入する。

能力評価
vs
市場評価

を比較。

---

## Counter Evidence Agent

各主張と原Factの一致、比較条件の不一致、最近の不成立、同じ根拠の重複、未取得情報を点検する。初条件や資料不足は未知として保持し、それだけで能力不足の反証を作らない。

支持材料にも反例にも選択偏りがあり得る。古い能力を消さず、古い好走だけで今回上位に戻さず、現在状態の観測と未知を区別する。

---

## Final Synthesizer

以下だけを受け取る。

Race Structure
Horse Evaluations
Counter Evidence
Market Evaluation

そして印・馬券を作る。
