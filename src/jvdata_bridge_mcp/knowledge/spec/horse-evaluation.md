# 予想知識：馬の評価

本文の数値例と序列は説明用の目安であり、検証済みの判定閾値ではない。根拠の適用範囲と不確実性はResourceメタデータと、予想規律の手順に従って確認する。

## 最重要原則

### 予想対象は「今回の条件下でのパフォーマンス」

通算成績ではなく、

今回の競馬場
今回の距離
今回の馬場
今回のクラス
今回の斤量
今回の枠
今回の展開
今回の状態

でどう走るかを評価する。

---

## 評価ベクトル

各馬を単一スコアではなく、以下の軸で保持する。

```text
ability:
class_fit:
course_fit:
distance_fit:
surface_fit:
condition_fit:
pace_fit:
position_fit:
gate_fit:
form:
training:
jockey:
trainer:
weight:
body_condition:
pedigree:
market:
value:
uncertainty:
```

目安として、

```text
S / A / B / C / D
```

または

```text
0.0 ～ 1.0
```

で表現する。

記述スコアは確率ではない。採点基準と支持・反証・未知を併記し、軸の評点を単純加算して順位や勝率へ変換しない。

---

## Ability ― 純粋能力

能力評価で優先するのは、

着順

だけではない。

見るもの：

- 着差
- 走破時計
- 上がり
- 相手レベル
- 斤量
- 位置取り
- ペース
- 不利
- クラス

例：

```text
10着 +0.6秒
```

は、

```text
5着 +1.5秒
```

より能力的には高く評価できる場合がある。

---

## Class Fit ― クラス適性

公式年齢別条件コード・グレード・競走体系を確認し、同級／昇級の主張にFactIDを付ける。クラス順序値は能力点ではない。平地と障害のクラスは比較不能。

```yaml
support:
  - 今回と比較可能な現級での好走内容とFactID
  - 下級勝ち・上級好走の内容、相手、時計、条件の一致と不一致
against:
  - 比較可能な条件での不成立内容とFactID
unknown:
  - 現級経験なし
  - 今回条件での発揮未確認
```

現級未経験や下級だけの好走から能力不足と断定しない。固定着差、昇級、前走1着だけで証拠強度や順位を決めず、相手・位置・ラップ・斤量・最近の反証を併せて評価する。

---

## Distance Fit ― 距離適性

全収録JRA歴を検索した件数と、直近走・条件一致走の取得範囲を確認する。`same_distance_starts` は距離だけの一致で、他材質や障害を含み得る。同材質の距離経験には `same_surface_distance_starts` を使い、同場同走路距離も別に見る。

直近5走や位置モデルの最大20走にないだけで初距離と断定しない。条件一致の古い好走・凡走をともに読み、今回の状態や最近の支持・反証と分ける。JRA外の履歴が不明なら「収録JRA歴で未経験」と記す。

短縮では持続時間の負担に加え、追走速度・位置争い・発馬への要求も変わる。延長では折り合い・途中加速・終いの持続を確認する。前走失速、終いの余裕、未経験だけから一律に加点・減点しない。

---

## Surface Fit ― 芝・ダート

材質、回り、内外、競馬場、平地／障害を別軸として比較する。初材質と判定する前に全収録JRA歴を確認し、地方・海外の被覆不明を残す。障害や芝の距離経験はダートの実証経験ではない。

血統・馬格・走法・先行履歴・道悪芝は適性移転の補助材料として使い、それぞれの出典と不一致を示す。未知は発揮の幅へ反映する。初材質の成功と不成立をともに読むことで、初条件を自動的な減点・加点理由にしない。

---

## 馬場状態

```text
良
稍重
重
不良
```

を別条件として扱う。

ただしサンプル不足を強く考慮。

例えば、

```text
稍重 1戦1勝
```

は、

```text
稍重得意
```

ではなく、

```text
稍重適性を示す初期的な証拠
```

と表現する。

---

## 近走フォーム

直近の着順列を、同材質・距離・クラス・相手・斤量・馬場・位置・ラップ・異常状態と一緒に読む。条件回帰では古い該当履歴も確認し、最近の大敗だけで以前の能力根拠を消さない。

古い好走があるだけで今回上位へ戻さず、現在状態の正の観測・負の観測・未知を分ける。間隔は最後の収録JRA出走からの日数であり、収録外活動を確認せず実際の休養と記さない。

---

## 着順・着差の解釈

着順と着差は距離、公式クラス、馬場、頭数、相手、ラップ、位置、異常状態と一緒に読む。
固定秒数で能力差を判定せず、同条件の別走にある正の証拠を一回の大敗で消さない。
不利は出典付き観察がある場合だけ原因仮説に使う。時計差0秒から圧勝や同着を推測せず、2着への差・同条件時計・公式着順を確認する。

---

## 位置取り変化

直近数戦で、

```text
15→15→3番手
```

のような大きな変化があれば重要。

これは、

- 馬の状態変化
- 作戦変更
- 距離適性
- 騎手変更

を示す可能性がある。

ただし、

```text
1回だけの先行＝次も必ず先行
```

とはしない。

---

## 馬体重

発表済みの今回値と過去レンジを、年齢・性・体格・時期・収録JRA間隔・調教や観察と照合する。
レンジ内なら問題なし、固定kg以上なら高リスクという判定はしない。
成長期の増加と状態変化を区別し、発表や状態観察が未確認なら未知を残す。

---


## 斤量

斤量は絶対値だけでなく、

- 前走比
- 馬体重比
- 距離
- 脚質

で評価。

短距離で3～4kg減がある場合も、馬齢・性・体格・騎手変更や比較対象の条件を確認する。
固定kg値から効果量を推定したり、自動的に加点したりしない。

例：

```text
55kg → 51kg
```

は負担減の候補だが、馬齢・騎手・他の条件変化と併せて判断する。

---

## 調教

同種・同センター・同コースの自己内時計差と、同じ1本の生時計・比較群・有効Nを別に記録する。最良4Fと最良1Fが別セッションなら結合しない。6F等の長い時計も原票がある場合だけ示す。

表示した調教数と集計に使った有効セッション数を確認する。A〜Dは時計比較の記述であり、追い方・併せ・余力・実戦状態の確率ではない。未要求、0件、収録場所外を分け、美浦・栗東のHC/WC0件から北海道等の現地調整も0件とは結論しない。

---

## 調教評価

```yaml
training:
  relative_clock_grade: A
  comparable_cohort: same_type_center_course
  session_clock: {4f: null, last_1f: null, 6f: null}
  session_date: null
  observed_effort: unknown
  observed_pairing: unknown
  fitness_hypothesis: unknown
```

追い方・併せ・余力が未観察なら数値で状態を埋めない。
状態改善/悪化を主張する場合は、それを支持する観察と反証を挙げる。

---

## 騎手

騎手評価は、

```text
総合勝率
```

ではなく、

- 競馬場
- 距離
- 馬場
- 脚質
- 厩舎との組み合わせ

を見る。

ただし少数サンプルは信用しすぎない。

---

## 騎手評価のサンプル補正

例：

```text
5戦0勝
```

は強いマイナスではない。

```text
100戦2勝
```

なら明確な材料になる。

---

## 厩舎・騎手コンビ

騎手単体より、

```text
jockey × trainer
```

の継続騎乗・成績が有効なことがある。

特に、

騎手変更

時は重要。

---

## 血統

対象馬を除く父産駒・同母馬の条件別集計を補助材料として読む。材質・距離幅・年齢・馬場・クラス・件数・期間・縮小の方法を確認する。

家族集計は個体の能力・勝率ではない。同じ父の値を別馬の独立した裏付けとして二重に数えず、個体の実走・準備の観測・未知を別に保持する。新馬・初材質・初距離では補助に使えるが、統計順位だけで個体の順位を決めない。

---

## 血統スコアの扱い

血統スコアだけで印を上げない。

```text
role: tie_breaker
```

程度にする。

---

## 例：現級実績

```yaml
knowledge_id: class_evidence_priority
kind: procedure
support:
  same_class_recent_performance:
    fact_id: null
    comparison_conditions: null
    interpretation: direct_evidence_without_automatic_ordering
  lower_class_win:
    fact_id: null
    interpretation: inspect_content_and_transfer_to_current_conditions
against:
  comparable_repeated_defeat:
    fact_ids: []
    interpretation: inspect_conditions_and_other_support
unknown:
  no_same_class_experience: not_evidence_of_insufficient_ability
cross_domain_class_comparison: incomparable
```

固定0.5秒などを強い支持の判定閾値にしない。空の参照は未記録であり、実際の評価時に取得済みFactを指定する。

---

## 例：先行再現性

```yaml
knowledge_id: recent_position_change
observation:
  recent_run_fact_ids: []
  change_in_normalized_position: null
  comparison_conditions: null
interpretation:
  cause: unknown_without_observation
usage:
  - compare_recent_and_condition_matched_history_with_counts
  - retain_repetition_and_non_repetition_scenarios
confidence: assess_after_support_and_counterevidence
```

---

## 例：稍重適性

```yaml
knowledge_id: wet_track_evidence
rule:
  wins: 1
  runs: 1
interpretation:
  evidence: positive
  confidence: low
rule_2:
  shows: 5
  runs: 6
interpretation_2:
  evidence: repeated_positive_observations
  confidence: assess_after_class_pace_opponents_and_selection_bias
```

---

## 最終馬評価オブジェクト

```json
{
  "horse_number": 15,
  "ability": "B+",
  "class_fit": "unknown",
  "course_fit": "unknown",
  "distance_fit": "unknown",
  "condition_fit": "unknown",
  "pace_fit": "unknown",
  "position_fit": "unknown",
  "training": "unknown",
  "market": {"status": "not_queried", "value": null},
  "uncertainty": "unassessed",
  "support_fact_ids": [],
  "against_fact_ids": [],
  "comparison_limits": [],
  "scenarios": [],
  "rank": null,
  "bet_role": null
}
```

空欄は推測で埋めず、全馬の支持・反証・未知を揃えてから順位を決める。市場・馬券は独立評価固定後に別途扱う。

---
