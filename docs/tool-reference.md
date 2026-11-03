# 全50ツールの目的・日付基準・入出力

各ツールの参照面、日付基準、入出力を示す。
機械可読の完全な型・入れ子定義は[スキーマJSON](tool-schemas.json)を参照。
スキーマは `python scripts/update_tool_schemas.py` で実装から生成する。
予測モデル・較正・組合せ確率の契約は[モデル開発](model-development.md)を参照。

## 日付基準の種類

| 表記 | 実際の範囲 |
|---|---|
| モデル準備 | 未発走の対象について、対象日・モデル版に一致する速度行の被覆、モデル資産の状態、文書化された適用域を確認する。 |
| 現在収録 | 現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。 |
| レース前実績 | 対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。 |
| レース指定可 | race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。 |
| モデル前日 | 対象開催日より厳密に前の確定・完全な実績を使い、同日別レースを除く。線形SQLの履歴上限は対象開催日で、JSON特徴量には現在日による制限もある。学習時の集計範囲を維持し、当時の発表版・取得時刻は復元しない。 |
| 調教21日 | 対象開催日−21日を下限に、対象レースと現在時刻より前の品質有効な調教を取得。同じ日付の調教は実施時刻が分かるものだけを採用し、時刻不明を朝の調教と推測しない。21日間は現在日ではなく対象開催日から数える。 |
| 検索条件 | 入力のrace_date・yearなどで検索範囲を決める。無指定は全収録日付。upcoming_only=trueかつrace_date省略なら日本時間の今日に限定し、発走予定時刻 > 分析時刻で絞る。 |
| 結果参照 | 指定したレース・検索条件の現在収録の結果を読む。対象レース前の履歴に限定する境界はない。予想対象を指定すると、そのレース自身の結果を取得するため、予想と買い目の固定後に使う。 |
| 現在観測 | 対象レースについて、実行時点に確認できる発走前の発表観測を読む。DBの実際の取得時刻は不明とし、発表・分析時刻で補完しない。過去日の予想再現はしない。 |
| 複合 | レース前実績・調教21日・現在観測を合わせて返す。位置モデルだけはモデル前日。今日の騎手成績は対象開催日ではなく分析当日の成績。同じ事実が複数の項目に現れた場合は重複を表示する。 |
| SQL次第 | 自動の日付境界はない。SQLのWHERE条件とパラメータが範囲を決める。pre_race_mode/contextは参照面の制約であり、race_codeから日時境界を自動注入する機能ではない。 |
| DB構造 | 実行時点のDBメタデータ。レース日・履歴期間の概念はない。 |
| 同梱ナレッジ | サーバー起動時に読み込んだ公開ナレッジの版。根拠期間はsupporting_evidence_periodsで確認し、不明な期間を公開日や登録日で補完しない。対象レースの日付による本文の切り替えや過去版の復元はしない。 |

「前日」は対象開催日より厳密に前。「レース前」は同日の先行レースも含められる。
レースコードの前後関係だけで時間を判定しない。現在収録値は最新実世界への同期を保証しない。

## 全体一覧

| # | ツール | 目的 | 日付基準 | 入力名（必須は*） | 主な出力 |
|---:|---|---|---|---|---|
| 1 | [`race_summary`](#race_summary) | 開催条件と発走予定を確認する | 現在収録 | `race_code`* | レース名・開催日・発走予定・場・距離・走路・登録頭数・グレード |
| 2 | [`entries_list`](#entries_list) | 基本出馬表を取得する | 現在収録 | `race_code`* | 全馬の馬番・枠・ID・馬名・性齢・騎手・調教師・斤量と取得方針 |
| 3 | [`track_condition_summary`](#track_condition_summary) | 現在の天候・芝馬場・ダート馬場を確認する | 現在観測 | `race_code`* | conditions: 条件コード/名称・観測日・発表時刻・項目別出典・欠測状態 |
| 4 | [`search_races`](#search_races) | 候補レースを検索する | 検索条件 | `grade`, `limit`, `race_name`, `racecourse_name`, `year`, `race_date`, `upcoming_only` | races: コード・日付・発走予定・場・条件・距離・登録頭数等 |
| 5 | [`get_race_results`](#get_race_results) | 結果または全頭台帳を取得する | 結果参照 | `race_code`, `race_name`, `year`, `include_all_entries`, `result_scope` | results: 着順・時計・最終単勝オッズ/人気・結果段階・出走状態、件数/省略情報 |
| 6 | [`get_graded_races`](#get_graded_races) | 重賞候補を検索する | 検索条件 | `grade`, `limit`, `racecourse_name`, `year`, `race_date`, `upcoming_only` | races: 重賞コード・開催日・発走予定・グレード・場・距離等 |
| 7 | [`race_evidence_collector`](#race_evidence_collector) | レース後の着順・時計・ラップと状態をまとめて取得する | 結果参照 | `race_code`*, `result_scope` | lapsとitems: 着順の原文/数値・時計・差・上がり・通過順・人気・状態等 |
| 8 | [`analyze_similar_races`](#analyze_similar_races) | 対象と条件が近い過去レースを探す | レース前実績 | `race_code`*, `limit`, `max_entry_difference`, `same_distance`, `same_grade`, `same_track_condition`, `same_weather` | races: 類似競走の条件・頭数差・天候馬場等 |
| 9 | [`race_recent_form_facts`](#race_recent_form_facts) | 全出走馬の近走と条件一致履歴を取得する | レース前実績 | `race_code`*, `recent_runs`, `condition_runs` | runs/condition_runs: 着順・時計・ラップ・通過順・クラス・出典・Fact ID・履歴被覆 |
| 10 | [`race_opponent_class_facts`](#race_opponent_class_facts) | 過去の相手関係とクラスを確認する | レース前実績 | `race_code`*, `recent_runs` | items: 過去走、同走上位馬/今回同走馬、相手の対象前実績・完全速報状態 |
| 11 | [`race_condition_change_facts`](#race_condition_change_facts) | 前走からの条件変化と全経歴を確認する | レース前実績 | `race_code`*, `recent_runs` | items: 今回条件・前走・距離/材質/場/クラス/騎手/斤量差・間隔・全経歴集計 |
| 12 | [`race_forecast_context`](#race_forecast_context) | 独立した馬評価の材料を一括取得する入口 | 複合 | `race_code`*, `sections`, `include_position_branches`, `training_history_limit`, `observations`, `condition_history_limit`, `course_profile_options` | 共通事実、boundary、現在出走変更、当日騎手成績、馬体重、WF、観測被覆・経過時間、モデル準備状態 |
| 13 | [`search_horses`](#search_horses) | 馬名から競走馬を探す | 現在収録 | `horse_name`*, `limit` | horses: ID・馬名・性別・生年月日・所属・馬主/生産者・賞金等 |
| 14 | [`get_horse_details`](#get_horse_details) | 競走馬のプロフィールと累計情報を確認する | 現在収録 | `horse_name`, `ketto_toroku_bango` | horses: 登録状態・所属・所有/生産・賞金・脚質累計等 |
| 15 | [`compare_horses`](#compare_horses) | 複数の馬のプロフィール・累計を比較する | 現在収録 | `horse_names`* | horses: 指定順のプロフィール・賞金・登録出走数・脚質累計 |
| 16 | [`analyze_horse_race_history`](#analyze_horse_race_history) | 馬の完了した過去走を新しい順で取得する | レース指定可 | `horse_name`, `ketto_toroku_bango`, `limit`, `race_code`, `result_scope` | history: 日付・条件・着順・時計・過去の最終単勝オッズ/人気・脚質・結果段階、boundary |
| 17 | [`analyze_horse_performance`](#analyze_horse_performance) | 馬の出走・勝利・3着内などを集計する | レース指定可 | `horse_name`, `ketto_toroku_bango`, `race_code`, `result_scope` | performances: 出走数・勝数・3着内数・平均着順/人気/上がり・根拠期間・速報数、boundary |
| 18 | [`odds_table`](#odds_table) | 全8券種の発走前オッズと取得状況を読む | 現在観測 | `race_code`*, `bet_type`*, `combinations`, `limit`, `model_probabilities` | items: 組合せ・オッズ/幅・状態、発表時刻・出典・source_timing・頭数被覆・欠測・省略 |
| 19 | [`get_jockey_profile`](#get_jockey_profile) | 騎手の登録プロフィールを確認する | 現在収録 | `jockey_code`, `jockey_name` | profiles: 名称・資格・見習減量・所属・現役状態・免許/抹消日等 |
| 20 | [`get_jockey_stats`](#get_jockey_stats) | 騎手の条件別成績を実出走から集計する | レース指定可 | `jockey_code`*, `distance_max`, `distance_min`, `is_graded`, `racecourse_name`, `track_type`, `period`, `race_code` | statistics: 出走・勝利・3着内・賞金・未完走・完全速報数・根拠期間、boundary |
| 21 | [`get_top_jockeys`](#get_top_jockeys) | 騎手の累計ランキングを取得する | 現在収録 | `limit`, `sort_by`, `min_starts` | rankings: 出走・勝利・3着内・生の勝率・平地賞金 |
| 22 | [`jockey_recent_form`](#jockey_recent_form) | 対象騎手の対象レース前の近況を読む | レース前実績 | `jockey_code`*, `race_code`* | form: 直近最大30騎乗の着順・人気・脚質・完走/異常・速報状態、actual_count |
| 23 | [`race_pace_running_styles`](#race_pace_running_styles) | 出走各馬の脚質と位置変化を整理する | レース前実績 | `race_code`* | items: 主脚質・前走脚質・得意距離区分・位置変化・脚質別成績 |
| 24 | [`race_pace_identify_beneficiaries`](#race_pace_identify_beneficiaries) | 脚質別に展開を検討する候補を整理する | レース前実績 | `race_code`* | closers/front_runners、騎手位置相関の候補、脚質不明馬 |
| 25 | [`race_pace_jockey_tactics`](#race_pace_jockey_tactics) | 騎手の過去の前方/後方騎乗と位置を集計する | レース前実績 | `race_code`*, `reference_races` | tactics: 参照数・前方/後方騎乗数・平均1角位置・平均着順 |
| 26 | [`race_pace_course_profile`](#race_pace_course_profile) | 同コース・同距離の序盤/終盤時計を集計する | レース前実績 | `race_code`*, `lookback_days`, `ground`, `grade_code`, `condition_code`, `horse_age`, `history_start_date`, `include_matched_reference`, `use_current_configuration` | profiles/matched_profiles、件数・平均/ばらつき・有効N・根拠期間・条件/コース世代情報 |
| 27 | [`prediction_model_status`](#prediction_model_status) | 予測モデル4ツールの必要データ・不足理由・適用域を確認する | モデル準備 | `race_code`* | 速度行の被覆、モデル資産の出所・状態、4ツールの必要データ・適用域・不足理由 |
| 28 | [`get_horse_feature_matrix`](#get_horse_feature_matrix) | 出走各馬のモデル特徴量を取得する | モデル前日 | `race_code`* | features: 馬別の指標・履歴被覆。採用済みJSONではability_features・model_provenanceも返す |
| 29 | [`simulate_race_scenarios`](#simulate_race_scenarios) | ペース指標とコーナー位置のシナリオを計算する | モデル前日 | `race_code`* | scenarios、各馬の序盤/4角位置分布、先頭/前2頭固定分岐、未較正/適用域 |
| 30 | [`predict_race_probabilities`](#predict_race_probabilities) | 出走各馬の勝率・着順周辺確率・組合せ確率を返す | モデル前日 | `race_code`*, `combinations` | 勝率・2着内・3着内、モデル由来の組合せ確率、正規化確認、較正・出走変更の出典 |
| 31 | [`get_composite_analysis`](#get_composite_analysis) | 11指標を重み付けして総合比較する | モデル前日 | `race_code`*, `aptitude_weight`, `recent_form_weight`, `distance_aptitude_weight`, `course_aptitude_weight`, `closing_weight`, `time_quality_weight`, `historical_market_weight`, `jockey_form_weight`, `trainer_form_weight`, `horse_jockey_form_weight`, `speed_quality_weight` | horses: 各指標・市場あり/市場なし総合点・過去市場寄与・履歴信頼度 |
| 32 | [`bloodline_scoreboard`](#bloodline_scoreboard) | 出走各馬の父母と血統の収録状況を一覧する | 現在収録 | `race_code`*, `limit` | scoreboard: 父母名・祖先件数・第1世代件数・異なる祖先数 |
| 33 | [`bloodline_fetch_pedigree`](#bloodline_fetch_pedigree) | 対象馬の血統表を取得する | 現在収録 | `horse_number`*, `race_code`* | pedigrees: 祖先名・世代・父母側・祖先との関係コード |
| 34 | [`bloodline_distance_bias`](#bloodline_distance_bias) | 父産駒・同母馬の距離帯成績を確認する | レース前実績 | `race_code`*, `distance_tolerance`, `horse_age`, `ground`, `first_stored_run_only` | biases: 家族集団・標本数・出走/勝利/3着内・生/縮小勝率・根拠期間 |
| 35 | [`bloodline_form_profile`](#bloodline_form_profile) | 対象馬の血統と近走を併記する | レース前実績 | `horse_number`*, `race_code`* | profiles: 第2世代までの祖先と直近最大5走の着順・距離・上がり・速報状態 |
| 36 | [`course_advantage_analyzer`](#course_advantage_analyzer) | 指定コース条件の完了成績を集計する | レース指定可 | `racecourse_code`*, `distance`*, `race_code`, `track_code` | advantages: レース数・出走/勝利/3着内・平均着順/単勝オッズ、boundary |
| 37 | [`course_distance_insight`](#course_distance_insight) | 指定コース・距離の時計と頭数を集計する | レース指定可 | `racecourse_code`*, `distance`*, `track_code`, `race_code` | insights: レース数・序盤/終盤平均時計・平均頭数・根拠期間、boundary |
| 38 | [`course_frame_style_stats`](#course_frame_style_stats) | 同コース条件の枠・脚質成績を過去日と今日に分けて確認する | レース前実績 | `race_code`* | historical_stats/today_stats: 枠/脚質別成績・速報/確定件数、参照期間・各レース数・今日の根拠コード |
| 39 | [`race_training_evaluation`](#race_training_evaluation) | 出走各馬の調教時計を自己内比較する | 調教21日 | `race_code`* | items: 馬別ランク・拠点・比較可能件数・欠測理由・状態推論の範囲 |
| 40 | [`training_condition_summary`](#training_condition_summary) | 出走馬全体の調教比較スコアを要約する | 調教21日 | `race_code`*, `limit` | 平均/最高/最低・スコア帯分布・上位/下位候補・抽出規則・スコアの意味 |
| 41 | [`training_entry_detail`](#training_entry_detail) | 1頭または全頭の調教詳細を確認する | 調教21日 | `horse_number`, `race_code`, `all_horses`, `history_limit` | 履歴・区間時計・最良時計・拠点/コース・自己内比較・有効件数・欠測、全頭時items |
| 42 | [`training_intensity_analysis`](#training_intensity_analysis) | 調教本数と時計強度を整理する | 調教21日 | `race_code`* | items: 馬別本数・強度スコア/ラベル、score_semantics |
| 43 | [`analyze_trainer_performance`](#analyze_trainer_performance) | 調教師の平地成績を取得する | レース指定可 | `trainer_code`*, `period`, `race_code` | performances: プロフィール・出走/勝利/3着内・賞金・速報数、集計範囲/period/boundary |
| 44 | [`compare_race_trainers`](#compare_race_trainers) | 対象出走馬の調教師の実績を比較する | レース前実績 | `race_code`* | trainers: 調教師別の出走・勝利・3着内・出走別賞金合計 |
| 45 | [`analyze_trainer_distance_specialty`](#analyze_trainer_distance_specialty) | 調教師の走路・距離帯成績を確認する | レース指定可 | `distance_category`*, `track_type`*, `trainer_code`*, `period`, `race_code` | specialties: 距離帯別の出走/勝利/3着内・速報数、集計範囲/period/boundary |
| 46 | [`sql_execute_custom_query`](#sql_execute_custom_query) | 許可された参照面に読み取りSQLを実行する | SQL次第 | `sql`*, `max_rows`, `parameters_json`, `pre_race_mode`, `context`, `cursor` | rows: 任意列の行、要求/実効上限・返却数・省略・次カーソル |
| 47 | [`sql_get_all_tables`](#sql_get_all_tables) | 利用可能なDB参照面を一覧する | DB構造 |  | tables: schema・relation名・種別・説明 |
| 48 | [`sql_get_table_schema`](#sql_get_table_schema) | 参照面の列・キー・索引を確認する | DB構造 | `table_name`* | 列名/型/NULL可否・主キー・外部キー・索引 |
| 49 | [`knowledge_list`](#knowledge_list) | 予想・レビュー用ナレッジのURIと根拠情報を一覧する | 同梱ナレッジ |  | resources/count: URI・名前・タイトル・概要・種別・公開状態・本文SHA-256・根拠期間 |
| 50 | [`knowledge_read`](#knowledge_read) | 登録済みナレッジのMarkdown全文と根拠情報を取得する | 同梱ナレッジ | `uri`* | content: Markdown全文と、URI・名前・タイトル・概要・種別・公開状態・本文SHA-256・根拠期間 |


## ツール別の完全な入力と出力

入力の必須はMCPスキーマ上の必須を示す。実行時の条件付き必須は注意事項に明記する。
出力の必須キーはスキーマのrequiredであり、非必須のキーが実際の応答で必ず省略される意味ではない。nullとキーの省略も別である。
動的なobjectの中身はスキーマだけで全フィールドを列挙できない。該当箇所は目的・主な出力・実装を合わせて確認する。

<a id="race_summary"></a>

### 1. `race_summary`

**目的**: 開催条件と発走予定を確認する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: race_codeは検索キー。未発走の検査はしない。starter_countは常にnullで、結果側の実出走頭数を概要へ入れない。

**実装**: [src/jvdata_bridge_mcp/tools/race/summary.py:104](../src/jvdata_bridge_mcp/tools/race/summary.py#L104)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: レース名・開催日・発走予定・場・距離・走路・登録頭数・グレード

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `scheduled_start_at` | string / null | いいえ |  |
| `timezone` | string | いいえ |  |
| `race_name` | string | はい | レース名 |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `distance` | integer / null | いいえ | 距離。メートル単位 |
| `track_type` | string / null | いいえ | 走路種別 |
| `track_code` | string / null | いいえ | 走路コード |
| `declared_entry_count` | integer / null | いいえ | 登録頭数 |
| `starter_count` | integer / null | いいえ | 出走頭数 |
| `grade` | RaceGrade / null | いいえ | レース格 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`RaceGrade`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `code` | string | はい | グレードコード |
| `name` | string | はい | グレード名 |

</details>

<a id="entries_list"></a>

### 2. `entries_list`

**目的**: 基本出馬表を取得する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: 対象の現在収録の基本項目を馬番順に返す。結果・対象オッズ・馬体重を含めない。未発走の検査はしない。

**実装**: [src/jvdata_bridge_mcp/tools/race/entries.py:57](../src/jvdata_bridge_mcp/tools/race/entries.py#L57)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: 全馬の馬番・枠・ID・馬名・性齢・騎手・調教師・斤量と取得方針

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `entries` | EntryItem[] | はい | 馬番順の復元出馬表 |
| `race_date` | string | はい |  |
| `market_fields_included` | boolean | いいえ |  |
| `target_results_included` | boolean | いいえ |  |
| `reconstruction_status` | string | いいえ |  |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`EntryItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `horse_number` | integer | はい |  |
| `horse_id` | string | はい |  |
| `horse_name` | string | はい |  |
| `frame_number` | integer / null | いいえ |  |
| `sex_name` | string / null | いいえ |  |
| `horse_age` | integer / null | いいえ |  |
| `jockey_id` | string / null | いいえ |  |
| `jockey_name` | string / null | いいえ |  |
| `trainer_id` | string / null | いいえ |  |
| `trainer_name` | string / null | いいえ |  |
| `assigned_weight_kg` | number / null | いいえ |  |

</details>

<a id="track_condition_summary"></a>

### 3. `track_condition_summary`

**目的**: 現在の天候・芝馬場・ダート馬場を確認する。

**日付基準**: 現在観測。対象レースについて、実行時点に確認できる発走前の発表観測を読む。DBの実際の取得時刻は不明とし、発表・分析時刻で補完しない。過去日の予想再現はしない。

**注意事項**: 実行当日の同競馬場のWFだけを項目別に復元。発表時刻は分析時刻以前かつ対象発走前。翌日レースに対して今日の観測が返ることがあるが、対象日の馬場とは扱わない。観測日を確認する。当日発走済みは有効観測が欠測になる。

**実装**: [src/jvdata_bridge_mcp/tools/race/track_condition.py:61](../src/jvdata_bridge_mcp/tools/race/track_condition.py#L61)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: conditions: 条件コード/名称・観測日・発表時刻・項目別出典・欠測状態

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `conditions` | TrackConditionItem[] | はい | 天候と馬場状態 |
| `analysis_at` | string / null | いいえ |  |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`TrackConditionItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 対象開催日 |
| `observation_date` | string / null | いいえ |  |
| `information_policy` | string | いいえ |  |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `weather_code` | string / null | いいえ | 天候コード |
| `weather_name` | string / null | いいえ | 天候 |
| `turf_condition_code` | string / null | いいえ | 芝馬場状態コード |
| `turf_condition_name` | string / null | いいえ | 芝馬場状態 |
| `dirt_condition_code` | string / null | いいえ | ダート馬場状態コード |
| `dirt_condition_name` | string / null | いいえ | ダート馬場状態 |
| `track_code` | string / null | いいえ | 走路コード |
| `track_name` | string / null | いいえ | 走路名 |
| `observation_status` | string | いいえ |  |
| `field_observations` | object | いいえ |  |
| `source` | string / null | いいえ |  |
| `announced_at` | string / null | いいえ | 発表日時 |
| `current_weather_code` | string / null | いいえ | 最新天候コード |
| `current_weather_name` | string / null | いいえ | 最新天候 |
| `current_turf_condition_code` | string / null | いいえ | 最新芝馬場状態コード |
| `current_turf_condition_name` | string / null | いいえ | 最新芝馬場状態 |
| `current_dirt_condition_code` | string / null | いいえ | 最新ダート馬場状態コード |
| `current_dirt_condition_name` | string / null | いいえ | 最新ダート馬場状態 |

</details>

<a id="search_races"></a>

### 4. `search_races`

**目的**: 候補レースを検索する。

**日付基準**: 検索条件。入力のrace_date・yearなどで検索範囲を決める。無指定は全収録日付。upcoming_only=trueかつrace_date省略なら日本時間の今日に限定し、発走予定時刻 > 分析時刻で絞る。

**注意事項**: race_dateはYYYY-MM-DD/today/tomorrow。年と開催日を同時指定するとAND条件。既定のupcoming_onlyはfalse。

**実装**: [src/jvdata_bridge_mcp/tools/race/search.py:83](../src/jvdata_bridge_mcp/tools/race/search.py#L83)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `grade` | string | 省略可 / `""` | レース格。G1、G2、G3など |
| `limit` | integer | 省略可 / `30` | 返却する最大件数 |
| `race_name` | string | 省略可 / `""` | レース名による部分一致検索条件 |
| `racecourse_name` | string | 省略可 / `""` | 競馬場名による絞り込み条件 |
| `year` | integer | 省略可 / `0` | 開催年。0の場合は年で絞り込まない |
| `race_date` | string | 省略可 / `""` | 開催日 YYYY-MM-DD、today、tomorrow。省略は全日付 |
| `upcoming_only` | boolean | 省略可 / `false` | 未発走に限定。日付省略時は今日 |

**出力の概要**: races: コード・日付・発走予定・場・条件・距離・登録頭数等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `races` | RaceSearchItem[] | はい | 条件に一致したレース一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`RaceSearchItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `year` | integer / null | いいえ | 開催年 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `race_number` | integer / null | いいえ | レース番号 |
| `scheduled_start_at` | string / null | いいえ |  |
| `declared_entry_count` | integer / null | いいえ |  |
| `timezone` | string | いいえ |  |
| `race_name` | string / null | いいえ | レース名 |
| `race_condition_name` | string / null | いいえ | レース条件名 |
| `grade_code` | string / null | いいえ | グレードコード |
| `grade_name` | string / null | いいえ | グレード名 |
| `distance` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `track_name` | string / null | いいえ | 走路名 |
| `starters` | integer / null | いいえ | 出走頭数 |

</details>

<a id="get_race_results"></a>

### 5. `get_race_results`

**目的**: 結果または全頭台帳を取得する。

**日付基準**: 結果参照。指定したレース・検索条件の現在収録の結果を読む。対象レース前の履歴に限定する境界はない。予想対象を指定すると、そのレース自身の結果を取得するため、予想と買い目の固定後に使う。

**注意事項**: 既定は完了結果の完走馬。result_scope=completeは完全速報も含む。include_all_entries=trueは取消・中止・部分速報等を含む全段階の台帳で、この場合result_scopeの段階限定は適用しない。

**実装**: [src/jvdata_bridge_mcp/tools/race/results.py:119](../src/jvdata_bridge_mcp/tools/race/results.py#L119)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 省略可 / `""` | 取得対象レースのレースコード。省略時は他の条件で検索 |
| `race_name` | string | 省略可 / `""` | レース名による部分一致検索条件 |
| `year` | integer | 省略可 / `0` | 開催年。0の場合は年で絞り込まない |
| `include_all_entries` | boolean | 省略可 / `false` | 取消・除外・中止・未確定も含む全頭台帳 |
| `result_scope` | string | 省略可 / `"complete"` | complete: 完全な成績と全頭揃った速報。official: 成績段階だけ。速報は改訂可能 |

**出力の概要**: results: 着順・時計・最終単勝オッズ/人気・結果段階・出走状態、件数/省略情報

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_scope` | string | いいえ |  |
| `returned_rows` | integer | いいえ |  |
| `effective_limit` | integer | いいえ |  |
| `truncated` | boolean | いいえ |  |
| `status_counts_scope` | string | いいえ |  |
| `status_counts` | object<string, integer> | いいえ |  |
| `result_policy` | string | いいえ |  |
| `provisional_rows` | integer | いいえ |  |
| `results` | RaceResultItem[] | はい | レース結果一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`RaceResultItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `race_number` | integer / null | いいえ | レース番号 |
| `race_name` | string / null | いいえ | レース名 |
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string / null | いいえ | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `abnormality_code` | string / null | いいえ |  |
| `abnormality_name` | string / null | いいえ |  |
| `runner_status` | string | いいえ |  |
| `finish_position` | integer / null | いいえ | 着順 |
| `elapsed_seconds` | number / null | いいえ | 走破タイム。秒 |
| `final_win_odds` | number / null | いいえ | 最終単勝オッズ |
| `final_win_popularity` | integer / null | いいえ | 最終単勝人気 |
| `is_final_result` | boolean / null | いいえ | 確定結果か |
| `is_complete_result` | boolean / null | いいえ |  |
| `result_stage_code` | string / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `is_starter` | boolean / null | いいえ | 出走馬か |
| `is_finisher` | boolean / null | いいえ | 完走馬か |

</details>

<a id="get_graded_races"></a>

### 6. `get_graded_races`

**目的**: 重賞候補を検索する。

**日付基準**: 検索条件。入力のrace_date・yearなどで検索範囲を決める。無指定は全収録日付。upcoming_only=trueかつrace_date省略なら日本時間の今日に限定し、発走予定時刻 > 分析時刻で絞る。

**注意事項**: grade=allは平地・障害の重賞を対象とする。既定では今日・未発走に限定されない。

**実装**: [src/jvdata_bridge_mcp/tools/race/graded.py:80](../src/jvdata_bridge_mcp/tools/race/graded.py#L80)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `grade` | string | 省略可 / `"all"` | G1、G2、G3、またはall |
| `limit` | integer | 省略可 / `50` | 返却する最大件数 |
| `racecourse_name` | string | 省略可 / `""` | 競馬場名による絞り込み条件 |
| `year` | integer | 省略可 / `0` | 開催年。0の場合は年で絞り込まない |
| `race_date` | string | 省略可 / `""` | 開催日 YYYY-MM-DD、today、tomorrow。省略は全日付 |
| `upcoming_only` | boolean | 省略可 / `false` | 未発走に限定。日付省略時は今日 |

**出力の概要**: races: 重賞コード・開催日・発走予定・グレード・場・距離等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `races` | GradedRaceItem[] | はい | 重賞レース一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`GradedRaceItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `race_number` | integer / null | いいえ | レース番号 |
| `scheduled_start_at` | string / null | いいえ |  |
| `declared_entry_count` | integer / null | いいえ |  |
| `timezone` | string | いいえ |  |
| `race_name` | string / null | いいえ | レース名 |
| `grade_code` | string / null | いいえ | グレードコード |
| `grade_name` | string / null | いいえ | グレード名 |
| `distance` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `track_name` | string / null | いいえ | 走路名 |

</details>

<a id="race_evidence_collector"></a>

### 7. `race_evidence_collector`

**目的**: レース後の着順・時計・ラップと状態をまとめて取得する。

**日付基準**: 結果参照。指定したレース・検索条件の現在収録の結果を読む。対象レース前の履歴に限定する境界はない。予想対象を指定すると、そのレース自身の結果を取得するため、予想と買い目の固定後に使う。

**注意事項**: 指定レースの結果を直接読む。complete/officialを選択。ラップは完了結果が確認できる場合に返す。

**実装**: [src/jvdata_bridge_mcp/tools/race/evidence.py:289](../src/jvdata_bridge_mcp/tools/race/evidence.py#L289)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `result_scope` | string | 省略可 / `"complete"` | complete: 完全な成績と全頭揃った速報。official: 成績段階だけ。速報は改訂可能 |

**出力の概要**: lapsとitems: 着順の原文/数値・時計・差・上がり・通過順・人気・状態等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `race_name` | string / null | いいえ | レース名 |
| `track_code` | string / null | いいえ | JRAトラックコード |
| `distance` | integer / null | いいえ | 距離。m |
| `laps` | RaceLaps / null | いいえ | レース全体のラップ |
| `items` | RaceEvidenceItem[] | はい | 馬別の確定事実 |
| `count` | integer | はい | 馬別事実の件数 |
| `result_policy` | string | いいえ |  |

<details>
<summary>出力の入れ子定義（6型）</summary>

**`CornerPositionsFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `first_corner` | integer / null | いいえ | 第1コーナー |
| `second_corner` | integer / null | いいえ | 第2コーナー |
| `third_corner` | integer / null | いいえ | 第3コーナー |
| `fourth_corner` | integer / null | いいえ | 第4コーナー |

**`FinishPositionFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `raw` | string / null | いいえ | JRAデータの着順原文 |
| `value` | integer / null | いいえ | 確定着順 |
| `status` | string | はい | 着順データの状態 |
| `excluded_from_aggregates` | boolean | いいえ | 集計対象外か |
| `excluded_reason` | string / null | いいえ | 集計対象外の理由 |

**`RaceEvidenceItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | はい | 馬番 |
| `horse_name` | string / null | いいえ | 馬名 |
| `is_final_result` | boolean / null | いいえ |  |
| `is_complete_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `finish_position` | FinishPositionFact | はい | 着順または取消・除外状態 |
| `running_time` | RunningTimeFact | はい | 走破タイム |
| `time_difference` | TimeDifferenceFact | はい | 勝ち馬とのタイム差 |
| `last_3f` | number / null | いいえ | 上がり3ハロン。秒 |
| `corner_positions` | CornerPositionsFact | はい | 各コーナー通過順位 |

**`RaceLaps`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `front_3f` | number / null | いいえ | 前半3ハロン。秒 |
| `front_4f` | number / null | いいえ | 前半4ハロン。秒 |
| `back_3f` | number / null | いいえ | 後半3ハロン。秒 |
| `back_4f` | number / null | いいえ | 後半4ハロン。秒 |
| `lap_times` | number[] | いいえ | 区間ごとのラップ。秒 |
| `lap_segment_lengths_meters` | integer[] / null | いいえ |  |
| `front_three_segment_meters` | integer / null | いいえ |  |
| `front_four_segment_meters` | integer / null | いいえ |  |
| `lap_windows` | object<string, object> | いいえ |  |
| `source_field_semantics` | string | いいえ |  |

**`RunningTimeFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `raw` | string / null | いいえ | 走破タイム原文 |
| `seconds` | number / null | いいえ | 走破タイム。秒 |
| `formatted` | string / null | いいえ | 走破タイム。分:秒.1 |

**`TimeDifferenceFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `raw` | string / null | いいえ | タイム差原文 |
| `seconds` | number / null | いいえ | タイム差。秒 |

</details>

<a id="analyze_similar_races"></a>

### 8. `analyze_similar_races`

**目的**: 対象と条件が近い過去レースを探す。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 同じ場・走路を基礎に、距離/グレードは既定で一致。same_track_condition/same_weather=trueには対象当日のWF観測が必要で、未知の場合はエラー。

**実装**: [src/jvdata_bridge_mcp/tools/race/similar.py:104](../src/jvdata_bridge_mcp/tools/race/similar.py#L104)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `limit` | integer | 省略可 / `30` | 返却する最大件数 |
| `max_entry_difference` | integer | 省略可 / `0` | 出走頭数差の許容値 |
| `same_distance` | boolean | 省略可 / `true` | 距離が同じレースに限定するか |
| `same_grade` | boolean | 省略可 / `true` | グレードが同じレースに限定するか |
| `same_track_condition` | boolean | 省略可 / `false` | 馬場状態が同じレースに限定するか |
| `same_weather` | boolean | 省略可 / `false` | 天候が同じレースに限定するか |

**出力の概要**: races: 類似競走の条件・頭数差・天候馬場等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `races` | SimilarRaceItem[] | はい | 類似レース一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`SimilarRaceItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `race_number` | integer / null | いいえ | レース番号 |
| `race_name` | string / null | いいえ | レース名 |
| `grade_code` | string / null | いいえ | グレードコード |
| `grade_name` | string / null | いいえ | グレード名 |
| `distance` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `track_name` | string / null | いいえ | 走路名 |
| `weather_name` | string / null | いいえ | 天候 |
| `turf_condition_name` | string / null | いいえ | 芝馬場状態 |
| `dirt_condition_name` | string / null | いいえ | ダート馬場状態 |
| `registered_horses` | integer / null | いいえ | 登録頭数 |
| `starters` | integer / null | いいえ | 出走頭数 |
| `entry_count_difference` | integer / null | いいえ | 出走頭数差 |

</details>

<a id="race_recent_form_facts"></a>

### 9. `race_recent_form_facts`

**目的**: 全出走馬の近走と条件一致履歴を取得する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: recent_runsは既定5。condition_runsは既定0で、指定すると直近の外にある同材質/距離/コース履歴も探す。

**実装**: [src/jvdata_bridge_mcp/tools/race/recent_form.py:205](../src/jvdata_bridge_mcp/tools/race/recent_form.py#L205)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `recent_runs` | integer | 省略可 / `5` | 集計対象にする直近走数 |
| `condition_runs` | integer | 省略可 / `0` | 全経歴の材質・距離・同場同走路一致から各条件の直近N件。0は取得しない |

**出力の概要**: runs/condition_runs: 着順・時計・ラップ・通過順・クラス・出典・Fact ID・履歴被覆

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `runs` | RaceRecentFormItem[] | はい | 出走馬別の直近走事実 |
| `condition_runs` | RaceRecentFormItem[] | いいえ | 全収録期間から選んだ条件一致履歴。好走・凡走とも返す |
| `condition_history_coverage` | object<string, object> | いいえ |  |
| `condition_history_limit` | integer | いいえ |  |
| `condition_history_scope` | string | いいえ |  |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`OfficialClassFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `condition_codes_by_age` | object<string, string> | いいえ |  |
| `applicable_condition_code` | string / null | いいえ |  |
| `applicable_horse_age` | integer / null | いいえ |  |
| `class_name` | string / null | いいえ |  |
| `class_rank` | integer / null | いいえ |  |
| `grade_code` | string / null | いいえ |  |
| `grade_name` | string / null | いいえ |  |
| `classification_status` | string | いいえ |  |
| `classification_rule_version` | string | いいえ |  |
| `source_fields` | string[] | いいえ |  |
| `source_race_code` | string / null | いいえ |  |
| `source_natural_key` | string / null | いいえ |  |
| `source_artifact_hashes` | string[] | いいえ |  |

**`RaceRecentFormItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `recent_race_code` | string / null | いいえ | 直近レースコード |
| `recent_race_date` | string / null | いいえ | 直近レース開催日 |
| `recent_racecourse_code` | string / null | いいえ | 直近競馬場コード |
| `recent_race_name` | string / null | いいえ | 直近レース名 |
| `recent_class_name` | string / null | いいえ | 過去レースの年齢別条件クラス名。グレードはclass_factに分離 |
| `class_fact` | OfficialClassFact / null | いいえ |  |
| `fact_id` | string / null | いいえ |  |
| `track_condition` | string / null | いいえ |  |
| `horse_age` | integer / null | いいえ |  |
| `assigned_weight_kg` | number / null | いいえ |  |
| `jockey_id` | string / null | いいえ |  |
| `jockey_name` | string / null | いいえ |  |
| `trainer_name` | string / null | いいえ |  |
| `body_weight_kg` | integer / null | いいえ |  |
| `abnormality_code` | string / null | いいえ |  |
| `is_final_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `is_finisher` | boolean / null | いいえ |  |
| `margin_code` | string / null | いいえ |  |
| `winner_to_second_margin_seconds` | number / null | いいえ |  |
| `race_laps` | number / null[] / null | いいえ |  |
| `track_code` | string / null | いいえ |  |
| `lap_segment_lengths_meters` | integer[] / null | いいえ |  |
| `lap_source` | string | いいえ |  |
| `source_record_id` | integer / string / null | いいえ |  |
| `field_size` | integer / null | いいえ | 過去レースの公式出走頭数 |
| `finish_position` | integer / null | いいえ | 着順 |
| `elapsed_seconds` | number / null | いいえ | 走破タイム。秒 |
| `time_difference_seconds` | number / null | いいえ | 勝馬とのタイム差。秒 |
| `last_three_furlongs_seconds` | number / null | いいえ | 上がり3ハロン。秒 |
| `corner_1_position` | integer / null | いいえ | 1コーナー通過順位 |
| `corner_2_position` | integer / null | いいえ | 2コーナー通過順位 |
| `corner_3_position` | integer / null | いいえ | 3コーナー通過順位 |
| `corner_4_position` | integer / null | いいえ | 4コーナー通過順位 |
| `final_win_odds` | number / null | いいえ | 最終単勝オッズ |
| `final_win_popularity` | integer / null | いいえ | 最終単勝人気 |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `track_name` | string / null | いいえ | 走路名 |
| `running_style_code` | string / null | いいえ | データソースの脚質コード |
| `running_style` | string | はい | 正規化脚質 |
| `run_number` | integer / null | いいえ | 対象馬内の直近走順位 |
| `selection_reasons` | string[] | いいえ |  |

</details>

<a id="race_opponent_class_facts"></a>

### 10. `race_opponent_class_facts`

**目的**: 過去の相手関係とクラスを確認する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 相手の実績も対象レース前かつ現在までの完了実績で集計する。クラス順序値を能力点と解釈しない。

**実装**: [src/jvdata_bridge_mcp/tools/race/opponent_class.py:250](../src/jvdata_bridge_mcp/tools/race/opponent_class.py#L250)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `recent_runs` | integer | 省略可 / `5` | 馬ごとに調べる過去走数。1〜10、既定5 |

**出力の概要**: items: 過去走、同走上位馬/今回同走馬、相手の対象前実績・完全速報状態

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `race_code` | string | はい | 今回のレースコード |
| `recent_runs` | integer | はい | 馬ごとに検索した過去走数 |
| `items` | RaceOpponentClassItem[] | はい | 出走馬ごとの過去走と同走上位馬 |

<details>
<summary>出力の入れ子定義（4型）</summary>

**`OfficialClassFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `condition_codes_by_age` | object<string, string> | いいえ |  |
| `applicable_condition_code` | string / null | いいえ |  |
| `applicable_horse_age` | integer / null | いいえ |  |
| `class_name` | string / null | いいえ |  |
| `class_rank` | integer / null | いいえ |  |
| `grade_code` | string / null | いいえ |  |
| `grade_name` | string / null | いいえ |  |
| `classification_status` | string | いいえ |  |
| `classification_rule_version` | string | いいえ |  |
| `source_fields` | string[] | いいえ |  |
| `source_race_code` | string / null | いいえ |  |
| `source_natural_key` | string / null | いいえ |  |
| `source_artifact_hashes` | string[] | いいえ |  |

**`OpponentRecordBeforeTarget`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `starts` | integer | はい | 完了結果の完走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内の回数 |
| `provisional_starts` | integer | いいえ |  |
| `graded_top3_finishes` | integer | はい | G/Jpnグレード表記の競走で3着以内に入った回数 |

**`PastRaceTopThreeOpponent`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_id` | string | はい | 相手馬の競走馬ID |
| `horse_name` | string / null | いいえ | 相手馬名 |
| `is_final_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `finish_position` | integer | はい | その過去レースでの着順 |
| `time_difference_seconds` | number / null | いいえ | 勝馬とのタイム差。秒 |
| `record_before_target` | OpponentRecordBeforeTarget / null | いいえ | 今回のレース日より前の確定完走成績。欠損時はnull |

**`RaceOpponentClassItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer | はい | 今回の馬番 |
| `horse_name` | string / null | いいえ | 今回の馬名 |
| `run_number` | integer | はい | 対象馬内の過去走順位。1が直近 |
| `past_race_code` | string | はい | 過去レースのコード |
| `past_race_date` | string | はい | 過去レースの開催日 |
| `past_race_name` | string / null | いいえ | 過去レース名 |
| `class_name` | string / null | いいえ | 過去レースのグレードまたはクラス名 |
| `class_rank` | integer / null | いいえ | クラス表記の順序値。相手馬の能力点や勝率ではない。名称を分類できなければnull |
| `class_fact` | OfficialClassFact / null | いいえ |  |
| `fact_id` | string / null | いいえ |  |
| `current_field_opponents` | object[] | いいえ |  |
| `field_size` | integer / null | いいえ | 過去レースの公式出走頭数 |
| `is_final_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `horse_finish_position` | integer / null | いいえ | 対象馬の着順 |
| `horse_time_difference_seconds` | number / null | いいえ | 対象馬の勝馬とのタイム差。秒 |
| `top_three_opponents` | PastRaceTopThreeOpponent[] | はい | 対象馬を除く、過去レースで3着以内に入った相手馬と今回前の確定実績。全相手の能力評価ではない |

</details>

<a id="race_condition_change_facts"></a>

### 11. `race_condition_change_facts`

**目的**: 前走からの条件変化と全経歴を確認する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: recent_runsは説明に返す近走数。全経歴の集計は別に行う。間隔は最後に収録されたJRA出走からの日数。

**実装**: [src/jvdata_bridge_mcp/tools/race/condition_changes.py:819](../src/jvdata_bridge_mcp/tools/race/condition_changes.py#L819)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `recent_runs` | integer | 省略可 / `4` | 比較用に取得する近走数 |

**出力の概要**: items: 今回条件・前走・距離/材質/場/クラス/騎手/斤量差・間隔・全経歴集計

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `race_code` | string | はい | 対象レースコード |
| `recent_runs_requested` | integer | はい | 比較用に取得した近走数 |
| `race_context` | RaceContext / null | いいえ | 対象レース条件 |
| `items` | RaceConditionChangeItem[] | はい | 馬ごとの条件替わり事実 |
| `count` | integer | はい | 条件比較を返した馬数 |

<details>
<summary>出力の入れ子定義（13型）</summary>

**`ConditionValueChange`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `current` | integer / number / string / null | いいえ | 今回の値 |
| `previous` | integer / number / string / null | いいえ | 前走の値 |
| `delta` | integer / number / null | いいえ | 今回と前走の差 |
| `direction` | string / null | いいえ | 変更方向 |
| `changed` | boolean / null | いいえ | 値が変わったか |

**`CornerPositionsFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `first_corner` | integer / null | いいえ | 第1コーナー |
| `second_corner` | integer / null | いいえ | 第2コーナー |
| `third_corner` | integer / null | いいえ | 第3コーナー |
| `fourth_corner` | integer / null | いいえ | 第4コーナー |

**`FinishPositionFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `raw` | string / null | いいえ | JRAデータの着順原文 |
| `value` | integer / null | いいえ | 着順 |
| `status` | string | いいえ | 着順データの状態 |

**`OfficialClassFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `condition_codes_by_age` | object<string, string> | いいえ |  |
| `applicable_condition_code` | string / null | いいえ |  |
| `applicable_horse_age` | integer / null | いいえ |  |
| `class_name` | string / null | いいえ |  |
| `class_rank` | integer / null | いいえ |  |
| `grade_code` | string / null | いいえ |  |
| `grade_name` | string / null | いいえ |  |
| `classification_status` | string | いいえ |  |
| `classification_rule_version` | string | いいえ |  |
| `source_fields` | string[] | いいえ |  |
| `source_race_code` | string / null | いいえ |  |
| `source_natural_key` | string / null | いいえ |  |
| `source_artifact_hashes` | string[] | いいえ |  |

**`RaceClassChange`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `current` | string / null | いいえ | 今回のクラス |
| `previous` | string / null | いいえ | 前走のクラス |
| `change` | string | いいえ | クラス変更 |
| `current_rank` | integer / null | いいえ | 今回のクラス順位 |
| `previous_rank` | integer / null | いいえ | 前走のクラス順位 |
| `rank_delta` | integer / null | いいえ | クラス順位の差 |
| `comparable` | boolean / null | いいえ |  |
| `comparability_reason` | string | いいえ |  |
| `current_domain` | string / null | いいえ |  |
| `previous_domain` | string / null | いいえ |  |

**`RaceConditionChangeItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | はい | 馬番 |
| `horse_name` | string / null | いいえ | 馬名 |
| `horse_id` | string | はい | 競走馬ID |
| `current_conditions` | RaceCurrentConditions | はい | 今回の条件 |
| `last_start` | RaceLastStart / null | いいえ | 直近確定走 |
| `condition_changes` | RaceConditionChanges | はい | 前走からの条件変更 |
| `career` | object | いいえ |  |
| `recent_contexts` | RecentConditionContext[] | はい | 比較に使った近走条件 |

**`RaceConditionChanges`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `distance` | ConditionValueChange | はい | 距離変更 |
| `surface` | ConditionValueChange | はい | 走路の材質のみ。回り・内外は別項目 |
| `race_domain` | ConditionValueChange | いいえ |  |
| `course_direction` | ConditionValueChange | いいえ |  |
| `course_layout` | ConditionValueChange | いいえ |  |
| `track_configuration` | ConditionValueChange | いいえ |  |
| `racecourse` | ConditionValueChange | はい | 競馬場変更 |
| `race_class` | RaceClassChange | はい | クラス変更 |
| `track_condition` | ConditionValueChange | はい | 馬場状態変更 |
| `interval_days` | integer / null | いいえ | 前走からの間隔。日 |
| `interval_category` | string / null | いいえ | 間隔区分 |
| `jockey_changed` | boolean / null | いいえ | 騎手が変わったか |
| `assigned_weight_change` | number / null | いいえ | 負担重量の変更。kg |
| `assigned_weight_comparable` | boolean / null | いいえ |  |
| `distance_comparable` | boolean / null | いいえ |  |
| `interval_semantics` | string | いいえ |  |

**`RaceContext`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `race_name` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `start_time` | string / null | いいえ |  |
| `racecourse_name` | string / null | いいえ |  |
| `track` | string / null | いいえ |  |
| `distance` | integer / null | いいえ |  |
| `class_name` | string / null | いいえ |  |
| `track_condition` | string / null | いいえ |  |
| `field_size` | integer / null | いいえ |  |
| `declared_entry_count` | integer / null | いいえ |  |
| `starter_count` | integer / null | いいえ |  |
| `active_entry_count` | integer / null | いいえ |  |

**`RaceCurrentConditions`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `jockey_name` | string / null | いいえ | 今回の騎手 |
| `trainer_name` | string / null | いいえ | 今回の調教師 |
| `assigned_weight` | number / null | いいえ | 今回の負担重量。kg |
| `racecourse_name` | string / null | いいえ | 今回の競馬場 |
| `track` | string / null | いいえ | 今回の芝・ダート・回り |
| `distance` | integer / null | いいえ | 今回の距離。m |
| `class_fact` | OfficialClassFact / null | いいえ |  |
| `class_name` | string / null | いいえ | 今回のクラス |
| `track_condition` | string / null | いいえ | 今回の馬場状態 |

**`RaceLastStart`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `is_final_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `fact_id` | string / null | いいえ |  |
| `run_index` | integer | いいえ | 対象馬内の近走順位 |
| `race_code` | string | はい | 前走レースコード |
| `race_date` | string / null | いいえ | 前走開催日 |
| `racecourse_name` | string / null | いいえ | 前走競馬場 |
| `race_name` | string / null | いいえ | 前走レース名 |
| `class_fact` | OfficialClassFact / null | いいえ |  |
| `class_name` | string / null | いいえ | 前走クラス |
| `track` | string / null | いいえ | 前走の芝・ダート・回り |
| `distance` | integer / null | いいえ | 前走距離。m |
| `track_condition` | string / null | いいえ | 前走馬場状態 |
| `field_size` | integer / null | いいえ | 前走の分析対象頭数 |
| `finish_position` | FinishPositionFact | はい | 前走着順 |
| `popularity` | integer / null | いいえ | 前走単勝人気 |
| `win_odds` | number / null | いいえ | 前走単勝オッズ |
| `running_time` | RunningTimeFact | はい | 前走走破タイム |
| `time_difference` | TimeDifferenceFact | はい | 前走タイム差 |
| `last_3f` | number / null | いいえ | 前走上がり3ハロン |
| `corner_positions` | CornerPositionsFact | はい | 前走コーナー通過順位 |
| `jockey_name` | string / null | いいえ | 前走騎手 |
| `trainer_name` | string / null | いいえ | 前走調教師 |
| `assigned_weight` | number / null | いいえ | 前走負担重量。kg |
| `body_weight` | string / null | いいえ | 前走馬体重。未収録時null |

**`RecentConditionContext`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `fact_id` | string / null | いいえ |  |
| `run_index` | integer | はい | 対象馬内の近走順位 |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_name` | string / null | いいえ | 競馬場 |
| `track` | string / null | いいえ | 芝・ダート・回り |
| `distance` | integer / null | いいえ | 距離。m |
| `class_fact` | OfficialClassFact / null | いいえ |  |
| `class_name` | string / null | いいえ | クラス |

**`RunningTimeFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `raw` | string / null | いいえ | 走破タイムの原文 |
| `seconds` | number / null | いいえ | 走破タイム。秒 |
| `formatted` | string / null | いいえ | 走破タイム。分:秒.1 |

**`TimeDifferenceFact`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `raw` | string / null | いいえ | タイム差の原文 |
| `seconds` | number / null | いいえ | タイム差。秒 |

</details>

<a id="race_forecast_context"></a>

### 12. `race_forecast_context`

**目的**: 独立した馬評価の材料を一括取得する入口。

**日付基準**: 複合。レース前実績・調教21日・現在観測を合わせて返す。位置モデルだけはモデル前日。今日の騎手成績は対象開催日ではなく分析当日の成績。同じ事実が複数の項目に現れた場合は重複を表示する。

**注意事項**: 当日の対象は既知の発走予定で未発走を検査。未来日の時刻不明は許容。AV/JC通知で現在の非出走・変更後騎手を別枠に返し、全候補の既往証拠を残す。sectionsで7項目を選択。対象結果・市場なし。

**実装**: [src/jvdata_bridge_mcp/tools/race/live_context.py:208](../src/jvdata_bridge_mcp/tools/race/live_context.py#L208)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 未発走の対象レースを識別する16桁コード |
| `sections` | string[] / null | 省略可 / `null` |  |
| `include_position_branches` | boolean | 省略可 / `false` |  |
| `training_history_limit` | integer | 省略可 / `3` |  |
| `observations` | Observation[] / null | 省略可 / `null` |  |
| `condition_history_limit` | integer | 省略可 / `3` |  |
| `course_profile_options` | CourseProfileOptions / null | 省略可 / `null` |  |

**出力の概要**: 共通事実、boundary、現在出走変更、当日騎手成績、馬体重、WF、観測被覆・経過時間、モデル準備状態

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `horizon` | string | はい |  |
| `analysis_at` | string | はい |  |
| `boundary` | RaceBoundary | はい |  |
| `race_context` | RaceForecastContextOutput | はい |  |
| `current_entry_observations` | CurrentEntryObservations | はい |  |
| `model_availability` | PredictionModelStatusOutput | はい |  |
| `current_observation_coverage` | object | はい |  |
| `today_jockey_results` | object[] | いいえ |  |
| `today_horse_weight_observations` | object[] | いいえ |  |
| `today_weather_observations` | object[] | いいえ |  |
| `today_result_policy` | string | いいえ |  |
| `same_day_overlap_race_codes` | string[] | いいえ |  |
| `same_day_evidence_already_in_base` | boolean | はい |  |
| `market_included` | boolean | いいえ |  |
| `target_results_included` | boolean | いいえ |  |
| `information_policy` | string | いいえ |  |
| `limitations` | string[] | いいえ |  |

<details>
<summary>出力の入れ子定義（10型）</summary>

**`CurrentEntryObservations`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `runners` | CurrentRunner[] | はい |  |
| `changes` | EntryChangeObservation[] | はい |  |
| `confirmed_nonrunner_count` | integer | はい |  |
| `unverified_notice_count` | integer | はい |  |
| `coverage_status` | string | はい |  |
| `upstream_coverage_verified` | boolean | いいえ |  |
| `history_entries_removed` | boolean | いいえ |  |
| `limitations` | string[] | いいえ |  |

**`CurrentRunner`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer | はい |  |
| `horse_id` | string | はい |  |
| `horse_name` | string | はい |  |
| `jockey_id` | string / null | いいえ |  |
| `jockey_name` | string / null | いいえ |  |
| `assigned_weight_kg` | number / null | いいえ |  |
| `runner_status` | string | いいえ |  |
| `betting_eligible` | boolean / null | いいえ | 確認済みAVはfalse。通知なしを購入可能の保証にしない |
| `withdrawal_notice` | EntryChangeObservation / null | いいえ |  |
| `jockey_change_notice` | EntryChangeObservation / null | いいえ |  |

**`EntryChangeObservation`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `source_record_type` | string | はい |  |
| `change_type` | string | はい |  |
| `horse_number` | integer / null | いいえ |  |
| `horse_name` | string / null | いいえ |  |
| `announced_at` | string / null | いいえ |  |
| `artifact_hash` | string / null | いいえ |  |
| `reason_code` | string / null | いいえ |  |
| `reason_name` | string / null | いいえ |  |
| `jockey_before_id` | string / null | いいえ |  |
| `jockey_before_name` | string / null | いいえ |  |
| `jockey_after_id` | string / null | いいえ |  |
| `jockey_after_name` | string / null | いいえ |  |
| `weight_before_kg` | number / null | いいえ |  |
| `weight_after_kg` | number / null | いいえ |  |
| `applied` | boolean | はい |  |
| `unapplied_reason` | string / null | いいえ |  |
| `source` | string | いいえ |  |
| `source_timing` | SourceTiming | はい |  |

**`ModelToolStatus`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `tool_name` | string | はい |  |
| `prerequisites_ready` | boolean | はい |  |
| `blockers` | string[] | いいえ |  |
| `position_domain_supported` | boolean / null | いいえ |  |
| `limitations` | string[] | いいえ |  |

**`Observation`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `evidence_race_code` | string / null | いいえ |  |
| `horse_number` | integer | はい |  |
| `kind` | string | はい |  |
| `observed_date` | string | はい |  |
| `observed_at` | string / null | いいえ |  |
| `source` | string | はい |  |
| `description` | string | はい |  |
| `reliability` | string | いいえ |  |

**`PredictionModelStatusOutput`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `analysis_at` | string | はい |  |
| `speed_quality` | SpeedQualityStatus / null | いいえ |  |
| `check_error` | string / null | いいえ |  |
| `tools` | ModelToolStatus[] | はい |  |
| `independent_forecast_requires_speed_quality` | boolean | いいえ |  |
| `check_scope` | string | いいえ |  |
| `ability_model` | object | いいえ |  |

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

**`RaceForecastContextOutput`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `timezone` | string | いいえ |  |
| `race_date` | string | はい |  |
| `history_policy` | string | いいえ |  |
| `information_policy` | string | いいえ |  |
| `market_included` | boolean | いいえ |  |
| `target_results_included` | boolean | いいえ |  |
| `race` | object | はい |  |
| `entries` | object[] | はい |  |
| `facts` | object<string, object> | いいえ |  |
| `horses` | object[] | いいえ |  |
| `sections` | object | いいえ |  |
| `coverage` | object | いいえ |  |
| `missing_data` | string[] | いいえ |  |
| `domain` | object | いいえ |  |
| `observations` | Observation[] | いいえ |  |
| `knowledge_resources` | object[] | いいえ |  |
| `tools_used` | string[] | いいえ |  |
| `database_query_count` | integer | いいえ |  |

**`SourceTiming`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `analysis_at` | string | はい |  |
| `published_at` | string / null | いいえ |  |
| `age_seconds` | number / null | いいえ | 発表から分析時刻までの秒数。取得時刻から推測しない |
| `age_status` | string | はい |  |
| `advisory_max_age_seconds` | integer / null | いいえ | 経過時間の表示用目安。観測の除外条件ではない |
| `upstream_latest_verified` | boolean | いいえ | 保存済み最新観測だけでは配信元の最新性を保証しない |

**`SpeedQualityStatus`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `analysis_at` | string | はい |  |
| `model_version` | string | はい |  |
| `history_before_date` | string / null | いいえ |  |
| `track_code` | string / null | いいえ |  |
| `venue_code` | string / null | いいえ |  |
| `distance_meters` | integer / null | いいえ |  |
| `declared_entries` | integer | いいえ |  |
| `available_entries` | integer | いいえ |  |
| `missing_or_invalid_entries` | integer | いいえ |  |
| `status` | string | はい |  |
| `ready` | boolean | はい |  |
| `observation_scope` | string | いいえ |  |
| `limitations` | string[] | いいえ |  |

</details>

<a id="search_horses"></a>

### 13. `search_horses`

**目的**: 馬名から競走馬を探す。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: 馬マスターの現在収録値。賞金や登録出走数は対象レース前へ再集計しない。

**実装**: [src/jvdata_bridge_mcp/tools/horse/search.py:65](../src/jvdata_bridge_mcp/tools/horse/search.py#L65)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_name` | string | 必須 | 競走馬名による検索条件 |
| `limit` | integer | 省略可 / `20` | 返却する最大件数 |

**出力の概要**: horses: ID・馬名・性別・生年月日・所属・馬主/生産者・賞金等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horses` | HorseSearchItem[] | はい | 検索に一致した競走馬 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`HorseSearchItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `horse_name_kana` | string / null | いいえ | 馬名かな |
| `sex_name` | string / null | いいえ | 性別 |
| `birth_date` | string / null | いいえ | 生年月日 |
| `is_active` | boolean / null | いいえ | 現役か |
| `trainer_id` | string / null | いいえ | 調教師ID |
| `trainer_name` | string / null | いいえ | 調教師名 |
| `owner_name` | string / null | いいえ | 馬主名 |
| `breeder_name` | string / null | いいえ | 生産者名 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |
| `registered_race_count` | integer / null | いいえ | 登録レース数 |

</details>

<a id="get_horse_details"></a>

### 14. `get_horse_details`

**目的**: 競走馬のプロフィールと累計情報を確認する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: 入力スキーマ上は両方省略可だが、実行時にhorse_nameかketto_toroku_bangoの少なくとも一方が必要。

**実装**: [src/jvdata_bridge_mcp/tools/horse/details.py:98](../src/jvdata_bridge_mcp/tools/horse/details.py#L98)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_name` | string | 省略可 / `""` | 競走馬名による検索条件 |
| `ketto_toroku_bango` | string | 省略可 / `""` | 競走馬の血統登録番号 |

**出力の概要**: horses: 登録状態・所属・所有/生産・賞金・脚質累計等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horses` | HorseDetailsItem[] | はい | 競走馬詳細一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`HorseDetailsItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `horse_name_kana` | string / null | いいえ | 馬名かな |
| `horse_name_english` | string / null | いいえ | 英字馬名 |
| `sex_code` | string / null | いいえ | 性別コード |
| `sex_name` | string / null | いいえ | 性別 |
| `breed_code` | string / null | いいえ | 品種コード |
| `breed_name` | string / null | いいえ | 品種名 |
| `coat_color_code` | string / null | いいえ | 毛色コード |
| `coat_color_name` | string / null | いいえ | 毛色 |
| `birth_date` | string / null | いいえ | 生年月日 |
| `registered_date` | string / null | いいえ | 登録日 |
| `deregistered_date` | string / null | いいえ | 抹消日 |
| `is_active` | boolean / null | いいえ | 現役か |
| `registration_status` | string / null | いいえ | 登録状態 |
| `trainer_id` | string / null | いいえ | 調教師ID |
| `trainer_name` | string / null | いいえ | 調教師名 |
| `breeder_id` | string / null | いいえ | 生産者ID |
| `breeder_name` | string / null | いいえ | 生産者名 |
| `owner_id` | string / null | いいえ | 馬主ID |
| `owner_name` | string / null | いいえ | 馬主名 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |
| `jump_prize_money_yen` | integer / null | いいえ | 障害賞金。円 |
| `front_runner_count` | integer / null | いいえ | 逃げ件数 |
| `stalker_count` | integer / null | いいえ | 先行件数 |
| `closer_count` | integer / null | いいえ | 差し件数 |
| `deep_closer_count` | integer / null | いいえ | 追込件数 |
| `registered_race_count` | integer / null | いいえ | 登録レース数 |

</details>

<a id="compare_horses"></a>

### 15. `compare_horses`

**目的**: 複数の馬のプロフィール・累計を比較する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: horse_namesは馬名/馬IDをカンマ・読点・空白で区切った文字列。履歴を対象レース前へ再集計する比較ではない。

**実装**: [src/jvdata_bridge_mcp/tools/horse/compare.py:82](../src/jvdata_bridge_mcp/tools/horse/compare.py#L82)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_names` | string | 必須 | 比較する競走馬名または馬IDをカンマ、読点、空白で指定 |

**出力の概要**: horses: 指定順のプロフィール・賞金・登録出走数・脚質累計

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horses` | HorseComparisonItem[] | はい | 比較対象の競走馬一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`HorseComparisonItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `sex_name` | string / null | いいえ | 性別 |
| `birth_date` | string / null | いいえ | 生年月日 |
| `is_active` | boolean / null | いいえ | 現役か |
| `trainer_name` | string / null | いいえ | 調教師名 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |
| `registered_race_count` | integer / null | いいえ | 登録レース数 |
| `front_runner_count` | integer / null | いいえ | 逃げ件数 |
| `stalker_count` | integer / null | いいえ | 先行件数 |
| `closer_count` | integer / null | いいえ | 差し件数 |
| `deep_closer_count` | integer / null | いいえ | 追込件数 |

</details>

<a id="analyze_horse_race_history"></a>

### 16. `analyze_horse_race_history`

**目的**: 馬の完了した過去走を新しい順で取得する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: horse_name/ketto_toroku_bangoで馬を絞る。result_scope=officialで確定に限定できる。

**実装**: [src/jvdata_bridge_mcp/tools/horse/history.py:100](../src/jvdata_bridge_mcp/tools/horse/history.py#L100)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_name` | string | 省略可 / `""` | 競走馬名による検索条件 |
| `ketto_toroku_bango` | string | 省略可 / `""` | 競走馬の血統登録番号 |
| `limit` | integer | 省略可 / `10` | 返却する最大件数 |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |
| `result_scope` | string | 省略可 / `"complete"` | complete: 完全な成績と全頭揃った速報。official: 成績段階だけ。速報は改訂可能 |

**出力の概要**: history: 日付・条件・着順・時計・過去の最終単勝オッズ/人気・脚質・結果段階、boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `boundary` | RaceBoundary / null | いいえ |  |
| `result_policy` | string | いいえ |  |
| `history` | HorseRaceHistoryItem[] | はい | 競走馬のレース履歴 |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`HorseRaceHistoryItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `race_id` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `race_number` | integer / null | いいえ | レース番号 |
| `race_name` | string / null | いいえ | レース名 |
| `grade_code` | string / null | いいえ | グレードコード |
| `grade_name` | string / null | いいえ | グレード名 |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `track_name` | string / null | いいえ | 走路名 |
| `horse_number` | integer / null | いいえ | 馬番 |
| `finish_position` | integer / null | いいえ | 着順 |
| `elapsed_seconds` | number / null | いいえ | 走破タイム。秒 |
| `time_difference_seconds` | number / null | いいえ | 着差。秒 |
| `final_win_odds` | number / null | いいえ | 最終単勝オッズ |
| `final_win_popularity` | integer / null | いいえ | 最終単勝人気 |
| `running_style_code` | string / null | いいえ | データソースの脚質コード |
| `running_style` | string | はい | 正規化脚質 |
| `result_status_code` | string / null | いいえ |  |
| `is_complete_result` | boolean / null | いいえ |  |
| `is_final_result` | boolean / null | いいえ | 確定結果か |

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

</details>

<a id="analyze_horse_performance"></a>

### 17. `analyze_horse_performance`

**目的**: 馬の出走・勝利・3着内などを集計する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: result_scope=completeは完全速報も含む。馬名/IDを両方省略すると馬を限定しない集計になる。

**実装**: [src/jvdata_bridge_mcp/tools/horse/performance.py:76](../src/jvdata_bridge_mcp/tools/horse/performance.py#L76)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_name` | string | 省略可 / `""` | 競走馬名による検索条件 |
| `ketto_toroku_bango` | string | 省略可 / `""` | 競走馬の血統登録番号 |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |
| `result_scope` | string | 省略可 / `"complete"` | complete: 完全な成績と全頭揃った速報。official: 成績段階だけ。速報は改訂可能 |

**出力の概要**: performances: 出走数・勝数・3着内数・平均着順/人気/上がり・根拠期間・速報数、boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `boundary` | RaceBoundary / null | いいえ |  |
| `result_policy` | string | いいえ |  |
| `performances` | HorsePerformanceItem[] | はい | 全頭揃ったJRA結果による競走馬の成績集計一覧 |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`HorsePerformanceItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `starts` | integer | はい | 全頭揃ったJRA結果の出走数 |
| `wins` | integer | はい | 全頭揃ったJRA結果の勝利数 |
| `top3_finishes` | integer | はい | 全頭揃ったJRA結果の3着以内数 |
| `average_finish_position` | number / null | いいえ | 全頭揃ったJRA結果の平均着順 |
| `average_win_odds` | number / null | いいえ | 全頭揃ったJRA結果の平均単勝オッズ |
| `average_popularity` | number / null | いいえ | 全頭揃ったJRA結果の平均人気 |
| `average_last_three_furlongs` | number / null | いいえ | 全頭揃ったJRA結果の平均上がり3ハロン。秒 |
| `first_race_date` | string / null | いいえ | 全頭揃ったJRA結果に含まれる初出走日 |
| `provisional_starts` | integer | いいえ |  |
| `latest_race_date` | string / null | いいえ | 全頭揃ったJRA結果に含まれる最終出走日 |

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

</details>

<a id="odds_table"></a>

### 18. `odds_table`

**目的**: 全8券種の発走前オッズと取得状況を読む。

**日付基準**: 現在観測。対象レースについて、実行時点に確認できる発走前の発表観測を読む。DBの実際の取得時刻は不明とし、発表・分析時刻で補完しない。過去日の予想再現はしない。

**注意事項**: 同一発表の発走前観測。任意の凍結model_probabilitiesと倍率を研究用に比較。モデルを再実行せず、投資価値は断定しない。

**実装**: [src/jvdata_bridge_mcp/tools/odds/table.py:129](../src/jvdata_bridge_mcp/tools/odds/table.py#L129)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 16桁のレースコード |
| `bet_type` | string | 必須 | trio=3連複、trifecta=3連単、quinella=馬連、wide=ワイド等 |
| `combinations` | string[] / null | 省略可 / `null` | 省略は全組合せ。例: ['3-6-7','2-6-7'] |
| `limit` | integer | 省略可 / `1000` | 返却上限。1〜10000、切り詰めを明示 |
| `model_probabilities` | object<string, number> / null | 省略可 / `null` | 凍結した組合せの的中確率を必要時だけ指定。ワイドは表示用正規化値ではなく的中確率。モデルは再実行しない |

**出力の概要**: items: 組合せ・オッズ/幅・状態、発表時刻・出典・source_timing・頭数被覆・欠測・省略

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `bet_type` | string | はい |  |
| `observation_scope` | string | はい |  |
| `analysis_at` | string | はい |  |
| `snapshot_at` | string / null | いいえ |  |
| `data_stage_code` | string / null | いいえ |  |
| `source` | string | いいえ |  |
| `source_artifact_hash` | string / null | いいえ |  |
| `pre_race_verified` | boolean | いいえ | 発走前の発表観測であること。情報の新しさはsource_timingで確認 |
| `source_timing` | SourceTiming | はい |  |
| `missing_reason` | string / null | いいえ |  |
| `snapshot_row_count` | integer | いいえ |  |
| `matching_row_count` | integer | いいえ |  |
| `returned_count` | integer | いいえ |  |
| `truncated` | boolean | いいえ |  |
| `missing_combinations` | string[] | いいえ |  |
| `items` | CombinationOdds[] | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `expected_entry_count` | integer / null | いいえ |  |
| `field_coverage_complete` | boolean / null | いいえ |  |
| `missing_horse_numbers` | integer[] | いいえ |  |
| `unexpected_horse_numbers` | integer[] | いいえ |  |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`CombinationOdds`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `combination` | string | はい |  |
| `horse_number_1` | integer / null | いいえ |  |
| `horse_number_2` | integer / null | いいえ |  |
| `horse_number_3` | integer / null | いいえ |  |
| `frame_number_1` | integer / null | いいえ |  |
| `frame_number_2` | integer / null | いいえ |  |
| `odds` | number / null | いいえ |  |
| `odds_minimum` | number / null | いいえ |  |
| `odds_maximum` | number / null | いいえ |  |
| `odds_status` | string | はい |  |
| `popularity` | integer / null | いいえ |  |
| `horse_name` | string / null | いいえ |  |
| `probability_comparison` | object / null | いいえ | 明示入力された凍結確率との研究用比較。投資価値の確認ではない |

**`SourceTiming`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `analysis_at` | string | はい |  |
| `published_at` | string / null | いいえ |  |
| `age_seconds` | number / null | いいえ | 発表から分析時刻までの秒数。取得時刻から推測しない |
| `age_status` | string | はい |  |
| `advisory_max_age_seconds` | integer / null | いいえ | 経過時間の表示用目安。観測の除外条件ではない |
| `upstream_latest_verified` | boolean | いいえ | 保存済み最新観測だけでは配信元の最新性を保証しない |

</details>

<a id="get_jockey_profile"></a>

### 19. `get_jockey_profile`

**目的**: 騎手の登録プロフィールを確認する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: スキーマ上は省略可だが、jockey_codeかjockey_nameの少なくとも一方が実行時に必要。成績集計ではない。

**実装**: [src/jvdata_bridge_mcp/tools/jockey/profile.py:70](../src/jvdata_bridge_mcp/tools/jockey/profile.py#L70)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `jockey_code` | string | 省略可 / `""` | 騎手コード |
| `jockey_name` | string | 省略可 / `""` | 騎手名 |

**出力の概要**: profiles: 名称・資格・見習減量・所属・現役状態・免許/抹消日等

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `profiles` | JockeyProfileItem[] | はい | 騎手プロフィール |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`JockeyProfileItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `jockey_code` | string | はい | 騎手コード |
| `jockey_name` | string / null | いいえ | 騎手名 |
| `jockey_name_kana` | string / null | いいえ | 騎手名かな |
| `jockey_short_name` | string / null | いいえ | 騎手略称 |
| `sex_name` | string / null | いいえ | 性別 |
| `qualification_name` | string / null | いいえ | 資格名 |
| `apprentice_name` | string / null | いいえ | 見習区分 |
| `apprentice_weight_reduction` | string / null | いいえ | 見習減量の表記 |
| `affiliation_name` | string / null | いいえ | 所属 |
| `is_active` | boolean / null | いいえ | 現役か |
| `registration_status` | string / null | いいえ | 登録状態 |
| `licensed_date` | string / null | いいえ | 免許日 |
| `deregistered_date` | string / null | いいえ | 抹消日 |
| `birth_date` | string / null | いいえ | 生年月日 |

</details>

<a id="get_jockey_stats"></a>

### 20. `get_jockey_stats`

**目的**: 騎手の条件別成績を実出走から集計する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: periodはcareer/current_year/previous_year。race_code指定時は対象年、省略時は現在年で年度を決める。距離・場・走路・重賞条件はAND。

**実装**: [src/jvdata_bridge_mcp/tools/jockey/condition_stats.py:58](../src/jvdata_bridge_mcp/tools/jockey/condition_stats.py#L58)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `jockey_code` | string | 必須 | 騎手コード |
| `distance_max` | integer | 省略可 / `0` | 対象距離の上限。0の場合は指定しない |
| `distance_min` | integer | 省略可 / `0` | 対象距離の下限。0の場合は指定しない |
| `is_graded` | boolean | 省略可 / `false` | 重賞に限定するか |
| `racecourse_name` | string | 省略可 / `""` | 競馬場名による絞り込み条件 |
| `track_type` | string | 省略可 / `""` | 芝・ダートなどの走路種別 |
| `period` | string | 省略可 / `"career"` | career/current_year/previous_year |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |

**出力の概要**: statistics: 出走・勝利・3着内・賞金・未完走・完全速報数・根拠期間、boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `aggregation_scope` | string | いいえ |  |
| `period` | string | いいえ |  |
| `boundary` | RaceBoundary / null | いいえ |  |
| `statistics` | JockeyConditionStatsItem[] | はい | 指定条件の騎手成績 |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`JockeyConditionStatsItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `jockey_code` | string | はい | 騎手コード |
| `jockey_name` | string / null | いいえ | 騎手名 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `track_type` | string / null | いいえ | 走路種別 |
| `distance_band_code` | string / null | いいえ | 距離帯コード |
| `evidence_start_date` | string / null | いいえ |  |
| `evidence_end_date` | string / null | いいえ |  |
| `non_finishers` | integer | いいえ |  |
| `provisional_starts` | integer | いいえ |  |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

</details>

<a id="get_top_jockeys"></a>

### 21. `get_top_jockeys`

**目的**: 騎手の累計ランキングを取得する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: people_statisticsのperiod_index=3、flat_totalを利用する。現在年ランキングや当日速報の再集計ではなく、原票累計の現在収録値。

**実装**: [src/jvdata_bridge_mcp/tools/jockey/top.py:83](../src/jvdata_bridge_mcp/tools/jockey/top.py#L83)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `limit` | integer | 省略可 / `20` | 返却する最大件数 |
| `sort_by` | string | 省略可 / `"win_rate"` | 結果を並べる基準 |
| `min_starts` | integer | 省略可 / `30` | ランキング対象にする最小出走数 |

**出力の概要**: rankings: 出走・勝利・3着内・生の勝率・平地賞金

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `rankings` | TopJockeyItem[] | はい | 成績上位の騎手 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`TopJockeyItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `jockey_code` | string | はい | 騎手コード |
| `jockey_name` | string / null | いいえ | 騎手名 |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `win_rate` | number / null | いいえ | 勝率 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |

</details>

<a id="jockey_recent_form"></a>

### 22. `jockey_recent_form`

**目的**: 対象騎手の対象レース前の近況を読む。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 返却数の入力はなく最大30。騎手の今日の作戦や好不調を直接示すものではない。

**実装**: [src/jvdata_bridge_mcp/tools/jockey/recent_form.py:121](../src/jvdata_bridge_mcp/tools/jockey/recent_form.py#L121)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `jockey_code` | string | 必須 | 騎手コード |
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: form: 直近最大30騎乗の着順・人気・脚質・完走/異常・速報状態、actual_count

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `actual_count` | integer | いいえ |  |
| `scope` | string | いいえ |  |
| `form` | JockeyRecentFormItem[] | はい | 騎手の直近成績 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`JockeyRecentFormItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `jockey_code` | string | はい | 騎手コード |
| `jockey_name` | string / null | いいえ | 騎手名 |
| `race_id` | string | はい | レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `race_name` | string / null | いいえ | レース名 |
| `horse_id` | string / null | いいえ | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `finish_position` | integer / null | いいえ | 着順 |
| `final_win_odds` | number / null | いいえ | 最終単勝オッズ |
| `final_win_popularity` | integer / null | いいえ | 最終単勝人気 |
| `is_final_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `is_finisher` | boolean / null | いいえ |  |
| `abnormality_code` | string / null | いいえ |  |
| `running_style_code` | string / null | いいえ | データソースの脚質コード |
| `running_style` | string | はい | 正規化脚質 |

</details>

<a id="race_pace_running_styles"></a>

### 23. `race_pace_running_styles`

**目的**: 出走各馬の脚質と位置変化を整理する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 履歴は共通境界で再集計。一方、主脚質は対象レースのentry_performances保存累計があれば優先し、履歴由来値を補助に使う。全項目が履歴再集計値ではない。

**実装**: [src/jvdata_bridge_mcp/tools/pace/running_styles.py:280](../src/jvdata_bridge_mcp/tools/pace/running_styles.py#L280)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: items: 主脚質・前走脚質・得意距離区分・位置変化・脚質別成績

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `race_code` | string | はい | レースコード |
| `items` | RunningStyleItem[] | いいえ | 馬別の代表脚質一覧 |
| `count` | integer | いいえ | 脚質を返した頭数 |
| `style_method` | string | いいえ | 脚質の意味 |
| `style_version` | string | いいえ | 脚質分類ロジックのバージョン |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`RunningStyleItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | はい | zero-padded馬番 |
| `horse_name` | string | はい | 馬名 |
| `ketto_toroku_bango` | string | はい | 血統登録番号 |
| `main_style` | string / null | いいえ | 対象日前の確定履歴から求めた代表脚質 |
| `previous_style` | string / null | いいえ | 前走の脚質 |
| `preferred_distance` | string / null | いいえ | 対象日前の距離別成績で複勝率が最も高い距離区分 |
| `avg_positions_gained` | number / null | いいえ | 最初に記録されたコーナーから最後に記録されたコーナーまでの平均順位変化（正=前進） |
| `style_stats` | object / null | いいえ | 脚質別の過去成績 |

</details>

<a id="race_pace_identify_beneficiaries"></a>

### 24. `race_pace_identify_beneficiaries`

**目的**: 脚質別に展開を検討する候補を整理する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 馬は直近最大15走、騎手は最大80騎乗を参照。主脚質は対象出馬表の保存累計を優先。特定ペースでの好走確率や確定した恩恵を示すモデルではない。

**実装**: [src/jvdata_bridge_mcp/tools/pace/beneficiaries.py:359](../src/jvdata_bridge_mcp/tools/pace/beneficiaries.py#L359)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: closers/front_runners、騎手位置相関の候補、脚質不明馬

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `race_code` | string | はい | 対象レースコード |
| `race_name` | string / null | いいえ | 対象レース名 |
| `closers` | PaceBeneficiaryItem[] | いいえ | 差し・追込で恩恵を受けやすい上位候補 |
| `front_runners` | PaceBeneficiaryItem[] | いいえ | 逃げ・先行で恩恵を受けやすい上位候補 |
| `positional_specialists` | JockeyPositionSpecialist[] | いいえ | 位置取り相関を計算できた騎手のうち、相対的に安定性が高い上位候補 |
| `uncertain_style_horses` | UncertainStyleHorse[] | いいえ | 脚質履歴が十分でない出走馬 |
| `count` | integer | いいえ | 脚質が判定された展開恩恵候補数 |

<details>
<summary>出力の入れ子定義（3型）</summary>

**`JockeyPositionSpecialist`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | いいえ | 馬番 |
| `horse_name` | string | いいえ | 馬名 |
| `jockey_code` | string / null | いいえ | 騎手コード |
| `jockey_name` | string / null | いいえ | 騎手名 |
| `position_stability` | number / null | いいえ | 1コーナーと4コーナーの通過順位の相関係数（-1〜1。高いほど位置取りが安定） |
| `position_stability_label` | string / null | いいえ | 位置取りの安定性評価 |
| `average_positions_gained` | number / null | いいえ | 対象日前の確定過去走で、最初に記録されたコーナーから最終コーナーまでに押し上げた平均順位（正の値は順位上昇） |

**`PaceBeneficiaryItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | いいえ | 馬番 |
| `horse_name` | string | いいえ | 馬名 |
| `main_style` | string | いいえ | 主な脚質 |
| `average_positions_gained` | number / null | いいえ | 対象日前の確定過去走で、最初に記録されたコーナーから最終コーナーまでに押し上げた平均順位（正の値は順位上昇） |
| `average_fourth_corner_position` | number / null | いいえ | 対象日前の確定過去走における4コーナー平均通過順位 |

**`UncertainStyleHorse`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | いいえ | 馬番 |
| `horse_name` | string | いいえ | 馬名 |
| `main_style` | string | いいえ | 判定された主な脚質 |

</details>

<a id="race_pace_jockey_tactics"></a>

### 25. `race_pace_jockey_tactics`

**目的**: 騎手の過去の前方/後方騎乗と位置を集計する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: reference_racesは既定80。実際に発表された対象レースの作戦は取得していない。

**実装**: [src/jvdata_bridge_mcp/tools/pace/jockey_tactics.py:87](../src/jvdata_bridge_mcp/tools/pace/jockey_tactics.py#L87)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `reference_races` | integer | 省略可 / `80` | 参照する過去レース数 |

**出力の概要**: tactics: 参照数・前方/後方騎乗数・平均1角位置・平均着順

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `tactics` | JockeyTacticsItem[] | はい | 騎手別戦術傾向 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`JockeyTacticsItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `jockey_code` | string | はい | 騎手コード |
| `jockey_name` | string / null | いいえ | 騎手名 |
| `reference_starts` | integer | はい | 参照出走数 |
| `forward_rides` | integer | はい | 前方脚質騎乗数 |
| `closing_rides` | integer | はい | 差し・追込騎乗数 |
| `average_first_corner_position` | number / null | いいえ | 平均1コーナー順位 |
| `average_finish_position` | number / null | いいえ | 平均着順 |

</details>

<a id="race_pace_course_profile"></a>

### 26. `race_pace_course_profile`

**目的**: 同コース・同距離の序盤/終盤時計を集計する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: lookback_days=0は日数制限なし。history_start_dateは下限。既定では確認済みコース変更境界を適用し、条件一致の別集団も返す。馬場を省略しても当日の値を勝手に入れない。

**実装**: [src/jvdata_bridge_mcp/tools/pace/course_profile.py:167](../src/jvdata_bridge_mcp/tools/pace/course_profile.py#L167)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `lookback_days` | integer | 省略可 / `0` |  |
| `ground` | string | 省略可 / `""` |  |
| `grade_code` | string | 省略可 / `""` |  |
| `condition_code` | string | 省略可 / `""` |  |
| `horse_age` | integer / null | 省略可 / `null` |  |
| `history_start_date` | string | 省略可 / `""` |  |
| `include_matched_reference` | boolean | 省略可 / `true` |  |
| `use_current_configuration` | boolean | 省略可 / `true` |  |

**出力の概要**: profiles/matched_profiles、件数・平均/ばらつき・有効N・根拠期間・条件/コース世代情報

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `filters` | object | いいえ |  |
| `profiles` | PaceCourseProfileItem[] | はい | 過去レースのペースプロフィール |
| `matched_profiles` | PaceCourseProfileItem[] | いいえ | 公式年齢別条件とグレードが対象と一致する参照 |
| `matched_reference_status` | string | いいえ |  |
| `target_condition_signature` | object | いいえ |  |
| `course_configuration` | object | いいえ |  |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`PaceCourseProfileItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `metric_valid_counts` | object<string, integer> | いいえ |  |
| `metric_standard_deviations` | object<string, number / null> | いいえ |  |
| `evidence_start_date` | string / null | いいえ |  |
| `evidence_end_date` | string / null | いいえ |  |
| `opening_three_segment_meters` | integer / null | いいえ |  |
| `opening_four_segment_meters` | integer / null | いいえ |  |
| `source_field_semantics` | string | いいえ |  |
| `race_count` | integer | はい | 対象レース数 |
| `average_first_three_furlongs` | number / null | いいえ | 平均前半3ハロン。秒 |
| `average_first_four_furlongs` | number / null | いいえ | 平均前半4ハロン。秒 |
| `average_last_three_furlongs` | number / null | いいえ | 平均上がり3ハロン。秒 |
| `average_last_four_furlongs` | number / null | いいえ | 平均上がり4ハロン。秒 |
| `average_starters` | number / null | いいえ | 平均出走頭数 |
| `reference_scope` | string | いいえ |  |
| `lap_windows` | object<string, object> | いいえ |  |

</details>

<a id="prediction_model_status"></a>

### 27. `prediction_model_status`

**目的**: 予測モデル4ツールの必要データ・不足理由・適用域を確認する。

**日付基準**: モデル準備。未発走の対象について、対象日・モデル版に一致する速度行の被覆、モデル資産の状態、文書化された適用域を確認する。

**注意事項**: 保存された速度行・宣言頭数と資産の状態を確認する。現在ロスター適用後の完全性・精度・較正を保証しない。

**実装**: [src/jvdata_bridge_mcp/tools/prediction/status.py:119](../src/jvdata_bridge_mcp/tools/prediction/status.py#L119)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 未発走の対象レースを識別する16桁コード |

**出力の概要**: 速度行の被覆、モデル資産の出所・状態、4ツールの必要データ・適用域・不足理由

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `analysis_at` | string | はい |  |
| `speed_quality` | SpeedQualityStatus / null | いいえ |  |
| `check_error` | string / null | いいえ |  |
| `tools` | ModelToolStatus[] | はい |  |
| `independent_forecast_requires_speed_quality` | boolean | いいえ |  |
| `check_scope` | string | いいえ |  |
| `ability_model` | object | いいえ |  |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`ModelToolStatus`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `tool_name` | string | はい |  |
| `prerequisites_ready` | boolean | はい |  |
| `blockers` | string[] | いいえ |  |
| `position_domain_supported` | boolean / null | いいえ |  |
| `limitations` | string[] | いいえ |  |

**`SpeedQualityStatus`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい |  |
| `analysis_at` | string | はい |  |
| `model_version` | string | はい |  |
| `history_before_date` | string / null | いいえ |  |
| `track_code` | string / null | いいえ |  |
| `venue_code` | string / null | いいえ |  |
| `distance_meters` | integer / null | いいえ |  |
| `declared_entries` | integer | いいえ |  |
| `available_entries` | integer | いいえ |  |
| `missing_or_invalid_entries` | integer | いいえ |  |
| `status` | string | はい |  |
| `ready` | boolean | はい |  |
| `observation_scope` | string | いいえ |  |
| `limitations` | string[] | いいえ |  |

</details>

<a id="get_horse_feature_matrix"></a>

### 28. `get_horse_feature_matrix`

**目的**: 出走各馬のモデル特徴量を取得する。

**日付基準**: モデル前日。対象開催日より厳密に前の確定・完全な実績を使い、同日別レースを除く。線形SQLの履歴上限は対象開催日で、JSON特徴量には現在日による制限もある。学習時の集計範囲を維持し、当時の発表版・取得時刻は復元しない。

**注意事項**: 同梱線形モデルは対象日・モデル版が一致する全候補馬の速度行が必要。採用済みJSONは現在ロスターと学習時の欠測処理を使い、ability_featuresにモデル入力を返す。

**実装**: [src/jvdata_bridge_mcp/tools/prediction/features.py:103](../src/jvdata_bridge_mcp/tools/prediction/features.py#L103)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: features: 馬別の指標・履歴被覆。採用済みJSONではability_features・model_provenanceも返す

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `features` | HorseFeatureItem[] | はい | 出走馬ごとの予測特徴量 |
| `ability_features` | object[] | いいえ | 採用済みモデルの市場なし特徴量。欠測はnull |
| `model_provenance` | object | いいえ |  |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`HorseFeatureItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `history_before_date` | string / null | いいえ | 過去情報の排他的な基準日。対象レース日 |
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `historical_starts` | integer | はい | 過去出走数 |
| `historical_wins` | integer | はい | 過去勝利数 |
| `historical_top3` | integer | はい | 過去3着以内数 |
| `average_finish_position` | number / null | いいえ | 平均着順 |
| `average_win_odds` | number / null | いいえ | 平均単勝オッズ |
| `average_last_three_furlongs` | number / null | いいえ | 平均上がり3ハロン。秒 |
| `aptitude_score` | number | はい | 複勝実績の同レース内0〜1順位 |
| `recent_form_score` | number | はい | 直近5走の着順品質の同レース内0〜1順位 |
| `distance_aptitude_score` | number | はい | 距離適性の同レース内0〜1順位 |
| `course_aptitude_score` | number | はい | 競馬場適性の同レース内0〜1順位 |
| `closing_score` | number | はい | 末脚の同レース内0〜1順位 |
| `time_quality_score` | number | はい | タイム差品質の同レース内0〜1順位 |
| `speed_quality_score` | number / null | はい | 過去時計の速度残差の同レース内0〜1順位。欠測対応モデルではnull可 |
| `speed_quality_coverage` | number / null | はい | 速度残差を得られた直近5走の割合。0〜1。未収録はnull |
| `speed_baseline_level` | string / null | いいえ | 速度基準の選択階層。履歴不足はunavailable |
| `indicator_version` | string | はい | 指標設計バージョン |
| `has_history` | boolean | はい | 過去成績が存在するか |

</details>

<a id="simulate_race_scenarios"></a>

### 29. `simulate_race_scenarios`

**目的**: ペース指標とコーナー位置のシナリオを計算する。

**日付基準**: モデル前日。対象開催日より厳密に前の確定・完全な実績を使い、同日別レースを除く。線形SQLの履歴上限は対象開催日で、JSON特徴量には現在日による制限もある。学習時の集計範囲を維持し、当時の発表版・取得時刻は復元しない。

**注意事項**: 代表脚質と序盤先頭を区別。ペースと位置は別の周辺モデル。速度表示の欠測で停止しない。ペース確率は未較正のためnull。位置は8〜18頭の芝・ダート平地戦（新潟芝直線1000mを除く）。

**実装**: [src/jvdata_bridge_mcp/tools/prediction/scenarios.py:283](../src/jvdata_bridge_mcp/tools/prediction/scenarios.py#L283)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: scenarios、各馬の序盤/4角位置分布、先頭/前2頭固定分岐、未較正/適用域

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `scenarios` | RaceScenarioItem[] | はい | 脚質別の予測シナリオ |
| `prediction_safe` | boolean | いいえ | 対象レース日より前の履歴に限定するか。未発走の検査結果ではない |
| `race_code` | string / null | いいえ | レースコード |
| `history_before_date` | string / null | いいえ | 過去情報の排他的な基準日。対象レース日 |
| `model_version` | string | いいえ | 相対ペースに使ったlocalモデル版 |
| `position_model_version` | string / null | いいえ | 馬別コーナー位置を計算できた場合のlocalモデル版 |
| `pace_probabilities` | object<string, number> / null | いいえ | 相対ペースの確率は未校正のためnull |
| `simulations` | integer / null | いいえ | 各コーナー位置の仮想順位生成回数。ペースのシミュレーション回数ではない |
| `seed` | integer / null | いいえ | 馬別位置シミュレーションの再現用seed |
| `horses` | HorsePositionScenario[] / null | いいえ | 馬別の最初に記録されたコーナーと4コーナーの位置分布 |
| `leader_branches` | AlternativeLeaderScenario[] / null | いいえ | 最初の記録コーナーで先頭と仮定する上位3候補の条件付き隊列。4コーナーやペースの条件付き予測ではない |
| `front_pair_scenarios` | FrontPairScenario[] / null | いいえ | 先頭候補上位3頭から二頭ずつを前2頭に固定した序盤隊列。競り合いの発生確率やペース変化は未推定 |
| `other_leaders_probability` | number / null | いいえ | 上位3候補以外が最初の記録コーナーで先頭になる確率の合計 |
| `lead_uncertainty_index` | number / null | いいえ | 先頭候補確率の分散度。0〜1。競り合いによるペース上昇確率ではない |
| `position_unavailable_reason` | string / null | いいえ | 馬別位置が検証範囲外で計算されなかった理由 |
| `output_granularity` | string | いいえ | 脚質別ペースのみ、または馬別位置を含む粒度 |
| `probability_status` | string | いいえ | 馬別位置だけ確率を計算した状態、または検証範囲外で未計算の状態 |
| `simulation_status` | string | いいえ | 馬別コーナー位置の仮想順位生成の実行状態 |
| `calibration_status` | string | いいえ |  |
| `position_model_provenance` | object | いいえ |  |
| `pace_input_semantics` | string | いいえ |  |
| `pace_position_consistency` | string | いいえ |  |

<details>
<summary>出力の入れ子定義（5型）</summary>

**`AlternativeLeaderScenario`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `conditional_scope` | string | いいえ |  |
| `unavailable_components` | string[] | いいえ |  |
| `leader_horse_number` | integer | はい | 先頭と仮定した馬番 |
| `leader_horse_name` | string | はい | 先頭と仮定した馬名 |
| `leader_probability` | number | はい | 基準モデルでその馬が先頭になる確率 |
| `seed` | integer | はい | この分岐の順位生成に用いたseed |
| `simulations` | integer | はい | この分岐の仮想順位生成回数 |
| `horses` | ConditionalEarlyPosition[] | はい | その馬が先頭の場合の各馬の最初の記録コーナーの位置 |

**`ConditionalEarlyPosition`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer | はい | 馬番 |
| `horse_name` | string | はい | 馬名 |
| `expected_rank` | number | はい | 条件付きの最初の記録コーナーでの期待順位 |
| `top3_probability` | number | はい | 条件付きの同コーナーで前3頭に入る確率 |
| `position_band_probabilities` | object<string, number> | はい | 条件付きの同コーナーの位置帯確率 |

**`FrontPairScenario`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `conditional_scope` | string | いいえ |  |
| `unavailable_components` | string[] | いいえ |  |
| `horse_numbers` | integer[] | はい | 最初の記録コーナーで前2頭と仮定した馬番 |
| `horse_names` | string[] | はい | その二頭の馬名 |
| `seed` | integer | はい | この分岐の順位生成に用いたseed |
| `simulations` | integer | はい | この分岐の仮想順位生成回数 |
| `horses` | ConditionalEarlyPosition[] | はい | 二頭が前2頭の場合の各馬の同コーナーの位置 |

**`HorsePositionScenario`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer | はい | 馬番 |
| `horse_name` | string | はい | 馬名 |
| `history_races` | integer | はい | 特徴量に使えた馬の過去走数。最大20 |
| `history_coverage` | number | はい | 馬の過去20走に対する履歴被覆率 |
| `jockey_history_rides` | integer | はい | 特徴量に使えた騎手の過去騎乗数。最大50 |
| `early_expected_rank` | number | はい | 最初に記録されたコーナーの仮想順位の平均 |
| `early_lead_probability` | number | はい | 最初に記録されたコーナーで先頭となる確率 |
| `early_top3_probability` | number | はい | 最初に記録されたコーナーで前3頭に入る確率 |
| `early_position_band_probabilities` | object<string, number> | はい | 最初の記録コーナーの位置帯。lead=1位、forward=2位〜頭数の上位1/3、midfield=次の1/3、rear=残り |
| `fourth_expected_rank` | number | はい | 4コーナーの仮想順位の平均 |
| `fourth_lead_probability` | number | はい | 4コーナーで先頭となる確率 |
| `fourth_top3_probability` | number | はい | 4コーナーで前3頭に入る確率 |
| `fourth_position_band_probabilities` | object<string, number> | はい | 4コーナーの位置帯。区切りはearlyと同じ |
| `early_rank_probabilities` | number[] | いいえ |  |
| `fourth_rank_probabilities` | number[] | いいえ |  |
| `early_rank_interval_80` | integer[] | いいえ |  |
| `fourth_rank_interval_80` | integer[] | いいえ |  |
| `interval_semantics` | string | いいえ |  |
| `zero_history` | boolean | いいえ |  |
| `same_surface_history_races` | integer | いいえ |  |
| `domain_shift_flags` | string[] | いいえ |  |
| `historical_average_advance` | number / null | いいえ |  |
| `historical_makuri_proxy_rate` | number / null | いいえ |  |
| `historical_makuri_proxy_count` | integer | いいえ |  |
| `historical_makuri_proxy_observations` | integer | いいえ |  |
| `makuri_semantics` | string | いいえ |  |
| `position_history_summary` | object<string, object> | いいえ |  |
| `prediction_scope` | string | いいえ |  |
| `progression_semantics` | string | いいえ |  |

**`RaceScenarioItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `history_before_date` | string / null | いいえ | 過去情報の排他的な基準日。対象レース日 |
| `running_style_code` | string / null | いいえ | 直近5走の代表脚質コード |
| `running_style` | string | はい | 正規化した代表脚質 |
| `runner_count` | integer | はい | 該当馬数 |
| `horse_numbers` | integer[] | はい | 該当馬番 |
| `expected_front_runner_count` | number | はい | 過去の代表脚質が逃げだった頻度の縮小推定値の合計。序盤先頭確率とは別 |
| `expected_forward_runner_count` | number | はい | 過去の代表脚質が逃げ・先行だった頻度の縮小推定値の合計 |
| `style_coverage_rate` | number | はい | 直近脚質履歴を1走以上持つ出走馬の割合。0〜1 |
| `average_first_corner_position` | number / null | いいえ | 直近5走の平均1コーナー順位 |
| `average_last_three_furlongs` | number / null | いいえ | 直近5走の平均上がり3ハロン。秒 |
| `speed_quality_score` | number / null | はい | 収録済みの馬の速度残差順位の脚質別平均。未収録はnull |
| `speed_quality_coverage` | number / null | はい | 収録済みの馬で速度残差を得られた直近5走の割合の平均。未収録はnull |
| `speed_known_runner_count` | integer | はい | 脚質内で速度指標を参照できた馬の数。runner_countと比較する |
| `speed_baseline_level` | string / null | いいえ | 脚質別で最多の速度基準階層。基準が得られない場合はunavailable |
| `indicator_version` | string | はい | 共有指標設計バージョン |
| `pace_pressure_index` | number | はい | コース・距離別の実測前半3Fに対する予測標準化指数。正ほど速い |
| `scenario_label` | string / null | いいえ | 対応範囲の相対ペース区分。対象外ならnull |

</details>

<a id="predict_race_probabilities"></a>

### 30. `predict_race_probabilities`

**目的**: 出走各馬の勝率・着順周辺確率・組合せ確率を返す。

**日付基準**: モデル前日。対象開催日より厳密に前の確定・完全な実績を使い、同日別レースを除く。線形SQLの履歴上限は対象開催日で、JSON特徴量には現在日による制限もある。学習時の集計範囲を維持し、当時の発表版・取得時刻は復元しない。

**注意事項**: 現在ロスターの同じ着順分布から勝率・2着内・3着内と4券種を返す。3頭以上のワイド的中確率の総和は3。combinationsは計200件以内。線形は未較正の標準PL、採用済みJSONは保存された較正値を使う。

**実装**: [src/jvdata_bridge_mcp/tools/prediction/probabilities.py:191](../src/jvdata_bridge_mcp/tools/prediction/probabilities.py#L191)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `combinations` | object<string, string[]> / null | 省略可 / `null` | 必要時だけ指定。quinella/wide/trio/trifectaの組合せ計200件以内。省略は券種ごと上位20件 |

**出力の概要**: 勝率・2着内・3着内、モデル由来の組合せ確率、正規化確認、較正・出走変更の出典

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `probabilities` | RaceProbabilityItem[] | はい | 出走馬ごとの予測確率 |
| `model_version` | string / null | いいえ |  |
| `calibration_status` | string | いいえ |  |
| `combination_status` | string | いいえ |  |
| `combination_probabilities` | object[] | いいえ |  |
| `distribution_checks` | object | いいえ |  |
| `provenance` | object | いいえ |  |
| `limitations` | string[] | いいえ |  |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`HistoricalMarketDiagnostics`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `model_version` | string | はい |  |
| `description` | string | はい |  |
| `raw_score` | number | はい |  |
| `utility` | number | はい |  |
| `probability` | number | はい |  |
| `support_utility` | number | はい |  |
| `support_delta` | number | はい |  |

**`RaceProbabilityItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `history_before_date` | string / null | いいえ | 過去情報の排他的な基準日。対象レース日 |
| `model_version` | string / null | いいえ | モデルバージョン |
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `starts` | integer | はい | 過去出走数 |
| `average_finish_position` | number / null | いいえ | 平均着順 |
| `average_win_odds` | number / null | いいえ | 平均単勝オッズ |
| `average_last_three_furlongs` | number / null | いいえ | 平均上がり3ハロン。秒 |
| `history_reliability` | number | はい | 過去走数に基づく0〜1の情報信頼度。能力点には加算しない |
| `aptitude_score` | number | はい | ベイズ平滑化した複勝実績の同レース内0〜1順位 |
| `recent_form_score` | number | はい | 直近5走の着順品質を時間減衰した同レース内0〜1順位 |
| `distance_aptitude_score` | number | はい | 同じ馬場系統・距離帯実績の同レース内0〜1順位 |
| `course_aptitude_score` | number | はい | 同じ競馬場・馬場系統実績の同レース内0〜1順位 |
| `closing_score` | number | はい | 過去走ごとに出走馬間正規化した末脚の同レース内0〜1順位 |
| `time_quality_score` | number | はい | 直近の勝馬タイム差を減衰変換した同レース内0〜1順位 |
| `speed_quality_score` | number / null | はい | 過去時計の階層基準から作った速度残差の同レース内0〜1順位。欠測対応モデルではnull可 |
| `speed_quality_coverage` | number / null | はい | 速度残差を得られた直近5走の割合。0〜1。未収録はnull |
| `speed_baseline_level` | string / null | いいえ | 直近の速度残差で選んだ基準階層。履歴不足はunavailable |
| `historical_market_score` | number / null | はい | 過去人気を頭数補正した同レース内0〜1順位。市場なし採用モデルでは未計算 |
| `jockey_form_score` | number | はい | 騎手の事前成績を縮小推定した同レース内0〜1順位 |
| `trainer_form_score` | number | はい | 調教師の事前成績を縮小推定した同レース内0〜1順位 |
| `horse_jockey_form_score` | number | はい | 馬・騎手の事前成績を縮小推定した同レース内0〜1順位 |
| `raw_score` | number | はい | 採用モデルの正の生スコア。線形モデルでは効用の指数 |
| `win_probability` | number | はい | 実際に採用されたモデルの勝率。版と較正状態を併せて確認 |
| `top2_probability` | number / null | いいえ | 同じ着順分布から計算した2着以内率 |
| `top3_probability` | number / null | いいえ | 同じ着順分布から計算した3着以内率。複勝発売条件とは区別 |
| `historical_market_diagnostics` | HistoricalMarketDiagnostics / null | いいえ | 線形モデルの過去市場支持を除いた診断値。独立学習・較正はしていない。市場なし採用モデルではnull |
| `prediction_source` | string / null | いいえ | 予測ソース |

</details>

<a id="get_composite_analysis"></a>

### 31. `get_composite_analysis`

**目的**: 11指標を重み付けして総合比較する。

**日付基準**: モデル前日。対象開催日より厳密に前の確定・完全な実績を使い、同日別レースを除く。線形SQLの履歴上限は対象開催日で、JSON特徴量には現在日による制限もある。学習時の集計範囲を維持し、当時の発表版・取得時刻は復元しない。

**注意事項**: 11個のweightを指定でき、負値は0に制限。既定スコアは線形勝率と同じ係数で、別の独立モデルではない。採用済みJSONの有無にかかわらず同梱線形係数を使い、全候補馬の速度行が必要。

**実装**: [src/jvdata_bridge_mcp/tools/misc/composite.py:151](../src/jvdata_bridge_mcp/tools/misc/composite.py#L151)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `aptitude_weight` | number | 省略可 / `0.576935` | バックテスト済み指標へ掛ける非負の重み |
| `recent_form_weight` | number | 省略可 / `0.573728` | バックテスト済み指標へ掛ける非負の重み |
| `distance_aptitude_weight` | number | 省略可 / `0.042949` | バックテスト済み指標へ掛ける非負の重み |
| `course_aptitude_weight` | number | 省略可 / `0.144058` | バックテスト済み指標へ掛ける非負の重み |
| `closing_weight` | number | 省略可 / `0.126436` | バックテスト済み指標へ掛ける非負の重み |
| `time_quality_weight` | number | 省略可 / `0.809266` | バックテスト済み指標へ掛ける非負の重み |
| `historical_market_weight` | number | 省略可 / `0.871966` | バックテスト済み指標へ掛ける非負の重み |
| `jockey_form_weight` | number | 省略可 / `0.891236` | バックテスト済み指標へ掛ける非負の重み |
| `trainer_form_weight` | number | 省略可 / `0.479686` | バックテスト済み指標へ掛ける非負の重み |
| `horse_jockey_form_weight` | number | 省略可 / `0.228294` | バックテスト済み指標へ掛ける非負の重み |
| `speed_quality_weight` | number | 省略可 / `0.567713` | バックテスト済み指標へ掛ける非負の重み |

**出力の概要**: horses: 各指標・市場あり/市場なし総合点・過去市場寄与・履歴信頼度

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horses` | CompositeAnalysisItem[] | はい | 出走馬別の総合分析 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`CompositeAnalysisItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string | はい | 馬名 |
| `jockey_id` | string / null | いいえ | 騎手ID |
| `trainer_id` | string / null | いいえ | 調教師ID |
| `average_finish_position` | number / null | いいえ | 対象日より前の平均着順 |
| `historical_starts` | integer | はい | 対象日より前の出走数 |
| `historical_top3` | integer | はい | 対象日より前の3着以内数 |
| `average_last_three_furlongs` | number / null | いいえ | 参考用の過去平均上がり3F。秒 |
| `average_win_odds` | number / null | いいえ | 参考用の過去平均単勝オッズ |
| `assigned_weight_kg` | number / null | いいえ | 負担重量。能力点には直接加算しない |
| `history_reliability` | number | はい | 過去走数による0〜1の情報信頼度。能力点ではない |
| `aptitude_score` | number | はい | ベイズ平滑化した複勝実績の同レース内0〜1順位 |
| `recent_form_score` | number | はい | 直近5走の着順品質を時間減衰した同レース内0〜1順位 |
| `distance_aptitude_score` | number | はい | 同じ馬場系統・距離帯実績の同レース内0〜1順位 |
| `course_aptitude_score` | number | はい | 同じ競馬場・馬場系統実績の同レース内0〜1順位 |
| `closing_score` | number | はい | 過去走ごとに出走馬間正規化した末脚の同レース内0〜1順位 |
| `time_quality_score` | number | はい | 直近の勝馬タイム差を減衰変換した同レース内0〜1順位 |
| `speed_quality_score` | number | はい | 過去時計の階層基準から作った速度残差の同レース内0〜1順位 |
| `speed_quality_coverage` | number | はい | 速度残差を得られた直近5走の割合。0〜1 |
| `speed_baseline_level` | string / null | いいえ | 直近の速度残差で選んだ基準階層。履歴不足はunavailable |
| `historical_market_score` | number | はい | 過去人気を頭数補正した同レース内0〜1順位 |
| `jockey_form_score` | number | はい | 騎手の事前成績を縮小推定した同レース内0〜1順位 |
| `trainer_form_score` | number | はい | 調教師の事前成績を縮小推定した同レース内0〜1順位 |
| `horse_jockey_form_score` | number | はい | 馬・騎手の事前成績を縮小推定した同レース内0〜1順位 |
| `composite_score` | number | はい | 過去市場支持を含む総合比較スコア |
| `market_free_composite_score` | number | はい | 過去市場支持を除いた市場なし診断用総合比較スコア |
| `market_support_utility` | number | はい | 過去市場支持が市場あり総合効用へ加えた値 |
| `market_support_delta` | number | はい | 市場あり総合点から市場なし総合点を引いた差分 |
| `indicator_version` | string | はい | 指標設計バージョン |
| `market_free_model_version` | string | はい | 市場なし診断モデルのバージョン |
| `market_free_model_description` | string | はい | 市場なし診断モデルの説明。独立再学習・再校正済みではないことを含む |
| `selected_race_code` | string | はい | 対象レースコード |

</details>

<a id="bloodline_scoreboard"></a>

### 32. `bloodline_scoreboard`

**目的**: 出走各馬の父母と血統の収録状況を一覧する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: 対象race_codeで出馬表を選ぶが、血統にレース前の日付境界は掛けない。血統の能力点ではない。

**実装**: [src/jvdata_bridge_mcp/tools/bloodline/scoreboard.py:62](../src/jvdata_bridge_mcp/tools/bloodline/scoreboard.py#L62)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `limit` | integer | 省略可 / `100` | 返却する最大件数 |

**出力の概要**: scoreboard: 父母名・祖先件数・第1世代件数・異なる祖先数

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `scoreboard` | BloodlineScoreboardItem[] | はい | 出走馬別血統指標 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`BloodlineScoreboardItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `pedigree_ancestor_count` | integer | はい | 血統祖先件数 |
| `first_generation_count` | integer | はい | 1世代目祖先数 |
| `distinct_ancestor_count` | integer | はい | 異なる祖先数 |
| `sire_name` | string / null | いいえ | 父馬名 |
| `dam_name` | string / null | いいえ | 母馬名 |

</details>

<a id="bloodline_fetch_pedigree"></a>

### 33. `bloodline_fetch_pedigree`

**目的**: 対象馬の血統表を取得する。

**日付基準**: 現在収録。現在DBに収録された基本情報・原票の値。対象レース以前への履歴再集計や時刻による足切りはしない。現在の実世界や当日速報まで同期済みであることは保証しない。

**注意事項**: race_codeとhorse_numberで馬を特定。現在収録の系譜で、出走結果の集計ではない。

**実装**: [src/jvdata_bridge_mcp/tools/bloodline/pedigree.py:74](../src/jvdata_bridge_mcp/tools/bloodline/pedigree.py#L74)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_number` | string | 必須 | 出走表上の馬番 |
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: pedigrees: 祖先名・世代・父母側・祖先との関係コード

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `pedigrees` | PedigreeItem[] | はい | 世代別血統表 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`PedigreeItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `pedigree_position` | integer / null | いいえ | 血統表上の位置 |
| `relation_code` | string / null | いいえ | 関係コード |
| `relation_name` | string / null | いいえ | 関係名 |
| `generation` | integer / null | いいえ | 世代 |
| `parent_side` | string / null | いいえ | 父系・母系 |
| `ancestor_breeding_id` | string / null | いいえ | 祖先血統ID |
| `ancestor_name` | string / null | いいえ | 祖先名 |

</details>

<a id="bloodline_distance_bias"></a>

### 34. `bloodline_distance_bias`

**目的**: 父産駒・同母馬の距離帯成績を確認する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 対象馬自身は除外。distance_toleranceは既定±200m。first_stored_run_onlyは保存履歴の最初を意味し、生涯初走を保証しない。

**実装**: [src/jvdata_bridge_mcp/tools/bloodline/distance_bias.py:118](../src/jvdata_bridge_mcp/tools/bloodline/distance_bias.py#L118)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `distance_tolerance` | integer | 省略可 / `200` |  |
| `horse_age` | integer / null | 省略可 / `null` |  |
| `ground` | string | 省略可 / `""` |  |
| `first_stored_run_only` | boolean | 省略可 / `false` |  |

**出力の概要**: biases: 家族集団・標本数・出走/勝利/3着内・生/縮小勝率・根拠期間

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `biases` | BloodlineDistanceBiasItem[] | はい | 血統祖先の距離別成績 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`BloodlineDistanceBiasItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `ancestor_name` | string / null | いいえ | 祖先名 |
| `ancestor_breeding_id` | string / null | いいえ |  |
| `family_scope` | string | いいえ |  |
| `target_excluded` | boolean | いいえ |  |
| `sample_horses` | integer | いいえ |  |
| `track_type` | string / null | いいえ |  |
| `distance_min` | integer / null | いいえ |  |
| `distance_max` | integer / null | いいえ |  |
| `horse_age` | integer / null | いいえ |  |
| `ground` | string / null | いいえ |  |
| `first_stored_run_only` | boolean | いいえ |  |
| `evidence_start_date` | string / null | いいえ |  |
| `evidence_end_date` | string / null | いいえ |  |
| `raw_win_rate` | number / null | いいえ |  |
| `shrunk_win_rate` | number / null | いいえ |  |
| `reference_win_rate` | number / null | いいえ |  |
| `reference_scope` | string | いいえ |  |
| `shrinkage_prior_starts` | integer | いいえ |  |
| `uncertainty` | string | いいえ |  |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `average_finish_position` | number / null | いいえ | 平均着順 |

</details>

<a id="bloodline_form_profile"></a>

### 35. `bloodline_form_profile`

**目的**: 対象馬の血統と近走を併記する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 血統は現在収録の系譜。近走だけが共通のレース前境界。

**実装**: [src/jvdata_bridge_mcp/tools/bloodline/form_profile.py:91](../src/jvdata_bridge_mcp/tools/bloodline/form_profile.py#L91)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_number` | string | 必須 | 出走表上の馬番 |
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: profiles: 第2世代までの祖先と直近最大5走の着順・距離・上がり・速報状態

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `profiles` | BloodlineFormProfileItem[] | はい | 血統と近走フォーム |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`BloodlineFormProfileItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | integer / null | いいえ | 馬番 |
| `horse_id` | string | はい | 競走馬ID |
| `horse_name` | string / null | いいえ | 馬名 |
| `ancestor_name` | string / null | いいえ | 祖先名 |
| `generation` | integer / null | いいえ | 世代 |
| `parent_side` | string / null | いいえ | 父系・母系 |
| `recent_race_code` | string / null | いいえ | 直近レースコード |
| `race_date` | string / null | いいえ | 開催日 |
| `is_final_result` | boolean / null | いいえ |  |
| `result_status_code` | string / null | いいえ |  |
| `finish_position` | integer / null | いいえ | 着順 |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `track_name` | string / null | いいえ | 走路名 |
| `last_three_furlongs_seconds` | number / null | いいえ | 上がり3ハロン。秒 |

</details>

<a id="course_advantage_analyzer"></a>

### 36. `course_advantage_analyzer`

**目的**: 指定コース条件の完了成績を集計する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: 同じ場・距離・任意走路全体の集計。特定馬のコース適性を推定するツールではない。

**実装**: [src/jvdata_bridge_mcp/tools/course/advantage.py:72](../src/jvdata_bridge_mcp/tools/course/advantage.py#L72)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `racecourse_code` | string | 必須 | 競馬場コード |
| `distance` | integer | 必須 | 距離。メートル単位 |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |
| `track_code` | string | 省略可 / `""` | 走路コード |

**出力の概要**: advantages: レース数・出走/勝利/3着内・平均着順/単勝オッズ、boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `advantages` | CourseAdvantageItem[] | はい | コース条件別の優位性 |
| `boundary` | RaceBoundary / null | いいえ |  |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`CourseAdvantageItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `race_count` | integer | はい | 対象レース数 |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `average_finish_position` | number / null | いいえ | 平均着順 |
| `average_win_odds` | number / null | いいえ | 平均単勝オッズ |
| `race_code` | string / null | いいえ | 基準レースコード |

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

</details>

<a id="course_distance_insight"></a>

### 37. `course_distance_insight`

**目的**: 指定コース・距離の時計と頭数を集計する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: 開催予定は実績件数へ含めず、完了結果だけを使う。

**実装**: [src/jvdata_bridge_mcp/tools/course/distance_insight.py:70](../src/jvdata_bridge_mcp/tools/course/distance_insight.py#L70)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `racecourse_code` | string | 必須 | 競馬場コード |
| `distance` | integer | 必須 | 距離。メートル単位 |
| `track_code` | string | 省略可 / `""` | 走路コード |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |

**出力の概要**: insights: レース数・序盤/終盤平均時計・平均頭数・根拠期間、boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `insights` | CourseDistanceInsightItem[] | はい | 距離別コース傾向 |
| `boundary` | RaceBoundary / null | いいえ |  |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`CourseDistanceInsightItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `racecourse_code` | string / null | いいえ | 競馬場コード |
| `racecourse_name` | string / null | いいえ | 競馬場名 |
| `distance_meters` | integer / null | いいえ | 距離。m |
| `track_code` | string / null | いいえ | 走路コード |
| `track_name` | string / null | いいえ | 走路名 |
| `evidence_start_date` | string / null | いいえ |  |
| `evidence_end_date` | string / null | いいえ |  |
| `race_count` | integer | はい | 対象レース数 |
| `average_first_three_furlongs` | number / null | いいえ | 平均前半3ハロン。秒 |
| `average_last_three_furlongs` | number / null | いいえ | 平均上がり3ハロン。秒 |
| `average_starters` | number / null | いいえ | 平均出走頭数 |

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

</details>

<a id="course_frame_style_stats"></a>

### 38. `course_frame_style_stats`

**目的**: 同コース条件の枠・脚質成績を過去日と今日に分けて確認する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 分析当日より前と今日の同条件成績を重複なく分離する。小標本や能力・クラス・流れの影響を残すため、当日の恒常的な馬場バイアスと断定しない。

**実装**: [src/jvdata_bridge_mcp/tools/course/frame_style_stats.py:134](../src/jvdata_bridge_mcp/tools/course/frame_style_stats.py#L134)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: historical_stats/today_stats: 枠/脚質別成績・速報/確定件数、参照期間・各レース数・今日の根拠コード

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `race_code` | string | はい |  |
| `analysis_at` | string | はい |  |
| `historical_stats` | FrameStyleStatsItem[] | はい | 分析当日より前の枠・脚質別成績 |
| `today_stats` | FrameStyleStatsItem[] | はい | 今日の対象・現在より前の完全結果による同条件成績 |
| `historical_race_count` | integer | はい |  |
| `today_race_count` | integer | はい |  |
| `history_start_date` | string / null | いいえ |  |
| `history_end_date` | string / null | いいえ |  |
| `today_race_codes` | string[] | いいえ |  |
| `cohort_policy` | string | いいえ |  |
| `limitations` | string[] | いいえ |  |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`FrameStyleStatsItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `frame_number` | integer / null | いいえ | 枠番 |
| `running_style_code` | string / null | いいえ | データソースの脚質コード |
| `running_style` | string | はい | 正規化脚質 |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `provisional_starts` | integer | いいえ | 改訂可能な完全速報の出走数 |
| `official_starts` | integer | いいえ | 公式確定結果の出走数 |
| `finish_position_count` | integer | いいえ | 平均着順の有効件数 |
| `average_finish_position` | number / null | いいえ | 平均着順 |

</details>

<a id="race_training_evaluation"></a>

### 39. `race_training_evaluation`

**目的**: 出走各馬の調教時計を自己内比較する。

**日付基準**: 調教21日。対象開催日−21日を下限に、対象レースと現在時刻より前の品質有効な調教を取得。同じ日付の調教は実施時刻が分かるものだけを採用し、時刻不明を朝の調教と推測しない。21日間は現在日ではなく対象開催日から数える。

**注意事項**: 時計の自己内相対評価。追い方/併せや好調確率を自動で補完しない。

**実装**: [src/jvdata_bridge_mcp/tools/training/evaluation.py:229](../src/jvdata_bridge_mcp/tools/training/evaluation.py#L229)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: items: 馬別ランク・拠点・比較可能件数・欠測理由・状態推論の範囲

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `items` | RaceTrainingEvaluationItem[] | はい | 出走馬別の調教評価 |
| `count` | integer | はい | 評価対象の出走馬数 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`RaceTrainingEvaluationItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | はい | 出走表上の馬番 |
| `horse_name` | string | はい | 馬名 |
| `ketto_toroku_bango` | string | はい | 競走馬登録番号 |
| `training_center` | string / null | いいえ | 最新の比較可能な調教拠点 |
| `rank` | string / null | いいえ | 自己内相対4F評価。A〜D、比較不能時はnull |
| `comparable_session_count` | integer | いいえ |  |
| `missing_reason` | string / null | いいえ |  |
| `fitness_inference` | string / null | いいえ |  |
| `effort_and_pairing` | string | いいえ |  |
| `rank_reason` | string | はい | 評価ランクの根拠またはサンプル不足理由 |
| `score_semantics` | string | はい | rankの意味 |

</details>

<a id="training_condition_summary"></a>

### 40. `training_condition_summary`

**目的**: 出走馬全体の調教比較スコアを要約する。

**日付基準**: 調教21日。対象開催日−21日を下限に、対象レースと現在時刻より前の品質有効な調教を取得。同じ日付の調教は実施時刻が分かるものだけを採用し、時刻不明を朝の調教と推測しない。21日間は現在日ではなく対象開催日から数える。

**注意事項**: combined_scoreは利用可能なcondition/trendの平均。limitは上位/下位表示数で、母集団の期間指定ではない。

**実装**: [src/jvdata_bridge_mcp/tools/training/condition_summary.py:254](../src/jvdata_bridge_mcp/tools/training/condition_summary.py#L254)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |
| `limit` | integer | 省略可 / `100` | 上位・下位候補の最大件数 |

**出力の概要**: 平均/最高/最低・スコア帯分布・上位/下位候補・抽出規則・スコアの意味

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `average_score` | number / null | いいえ | 各馬のcombined_scoreの平均 |
| `highest_score` | number / null | いいえ | combined_scoreの最高値 |
| `lowest_score` | number / null | いいえ | combined_scoreの最低値 |
| `distribution` | object<string, integer> | はい | combined_score帯別件数 |
| `high_performers` | object[] | はい | combined_score 8以上の上位馬 |
| `low_performers` | object[] | はい | combined_score 3以下の下位馬 |
| `selection` | object | はい | 候補抽出ルール |
| `score_semantics` | string | はい | サマリー集計値と各馬のスコア項目の意味 |

<a id="training_entry_detail"></a>

### 41. `training_entry_detail`

**目的**: 1頭または全頭の調教詳細を確認する。

**日付基準**: 調教21日。対象開催日−21日を下限に、対象レースと現在時刻より前の品質有効な調教を取得。同じ日付の調教は実施時刻が分かるものだけを採用し、時刻不明を朝の調教と推測しない。21日間は現在日ではなく対象開催日から数える。

**注意事項**: race_codeは形式上省略可だが、有効な取得には対象コードが必要。all_horses=falseではhorse_numberが実行時必須。history_limit=0で21日内の全取得履歴を表示する。

**実装**: [src/jvdata_bridge_mcp/tools/training/entry_detail.py:215](../src/jvdata_bridge_mcp/tools/training/entry_detail.py#L215)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `horse_number` | string | 省略可 / `""` | 出走表上の馬番 |
| `race_code` | string | 省略可 / `""` | 対象レースを識別する16桁のレースコード |
| `all_horses` | boolean | 省略可 / `false` | 全出走馬を一括取得。この場合馬番は省略可能 |
| `history_limit` | integer | 省略可 / `3` | 返す履歴数。0なら21日内を全件返す |

**出力の概要**: 履歴・区間時計・最良時計・拠点/コース・自己内比較・有効件数・欠測、全頭時items

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `horse_number` | string | いいえ | 出走表上の馬番。一括取得時は空 |
| `horse_name` | string | いいえ | 馬名。一括取得時は空 |
| `condition_score` | number / null | いいえ | 自己内相対condition score。比較不能時はnull |
| `trend_score` | number / null | いいえ | 自己内相対trend score。比較不足時は5 |
| `best_times` | object<string, object> | いいえ | 直前期間における最良時計 |
| `recent_history` | object[] | いいえ | 指定数の調教履歴 |
| `score_semantics` | string | いいえ | condition/trend scoreの意味 |
| `session_count` | integer | いいえ | 有効調教の件数。一括取得時は全頭の合計 |
| `comparable_session_count` | integer | いいえ | 自己内比較に使える調教の件数。一括取得時は全頭の合計 |
| `all_history_returned` | boolean | いいえ | 対象馬の履歴を省略なく返したか。一括取得時は全頭を判定 |
| `best_sessions_by_cohort` | object[] | いいえ |  |
| `missing_reason` | string / null | いいえ |  |
| `coverage` | string | いいえ |  |
| `observed_effort` | string / null | いいえ |  |
| `fitness_inference` | string / null | いいえ |  |
| `items` | TrainingHorseDetail[] / null | いいえ |  |
| `count` | integer / null | いいえ |  |
| `scope` | string | いいえ |  |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`TrainingHorseDetail`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `horse_number` | string | いいえ | 出走表上の馬番。一括取得時は空 |
| `horse_name` | string | いいえ | 馬名。一括取得時は空 |
| `condition_score` | number / null | いいえ | 自己内相対condition score。比較不能時はnull |
| `trend_score` | number / null | いいえ | 自己内相対trend score。比較不足時は5 |
| `best_times` | object<string, object> | いいえ | 直前期間における最良時計 |
| `recent_history` | object[] | いいえ | 指定数の調教履歴 |
| `score_semantics` | string | いいえ | condition/trend scoreの意味 |
| `session_count` | integer | いいえ | 有効調教の件数。一括取得時は全頭の合計 |
| `comparable_session_count` | integer | いいえ | 自己内比較に使える調教の件数。一括取得時は全頭の合計 |
| `all_history_returned` | boolean | いいえ | 対象馬の履歴を省略なく返したか。一括取得時は全頭を判定 |
| `best_sessions_by_cohort` | object[] | いいえ |  |
| `missing_reason` | string / null | いいえ |  |
| `coverage` | string | いいえ |  |
| `observed_effort` | string / null | いいえ |  |
| `fitness_inference` | string / null | いいえ |  |

</details>

<a id="training_intensity_analysis"></a>

### 42. `training_intensity_analysis`

**目的**: 調教本数と時計強度を整理する。

**日付基準**: 調教21日。対象開催日−21日を下限に、対象レースと現在時刻より前の品質有効な調教を取得。同じ日付の調教は実施時刻が分かるものだけを採用し、時刻不明を朝の調教と推測しない。21日間は現在日ではなく対象開催日から数える。

**注意事項**: 本数と有効4F時計による記述的スコアで、好走確率や仕上がりの保証ではない。

**実装**: [src/jvdata_bridge_mcp/tools/training/intensity.py:136](../src/jvdata_bridge_mcp/tools/training/intensity.py#L136)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: items: 馬別本数・強度スコア/ラベル、score_semantics

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string | はい | レースコード |
| `lookback_days` | integer | はい | 参照したレース前日数 |
| `items` | TrainingIntensityItem[] | はい | 出走馬別の調教強度 |
| `count` | integer | はい | 対象出走馬数 |
| `score_semantics` | string | はい | intensity scoreの意味 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`TrainingIntensityItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `horse_number` | string | はい | 出走表上の馬番 |
| `horse_name` | string | はい | 馬名 |
| `intensity_score` | number / null | いいえ | 調教強度score。時計不足時はnull |
| `intensity_label` | string / null | いいえ | 調教強度ラベル |
| `session_count` | integer | はい | 直前21日の有効品質セッション数 |

</details>

<a id="analyze_trainer_performance"></a>

### 43. `analyze_trainer_performance`

**目的**: 調教師の平地成績を取得する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: race_code指定時は対象年を基準に実出走から再集計。省略時はpeople_statisticsのcareer/current_year/previous_year原票区分を読むため、現在までの実出走再集計ではない。

**実装**: [src/jvdata_bridge_mcp/tools/trainer/performance.py:66](../src/jvdata_bridge_mcp/tools/trainer/performance.py#L66)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `trainer_code` | string | 必須 | 調教師コード |
| `period` | string | 省略可 / `"career"` |  |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |

**出力の概要**: performances: プロフィール・出走/勝利/3着内・賞金・速報数、集計範囲/period/boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `aggregation_scope` | string | いいえ |  |
| `period` | string | いいえ |  |
| `boundary` | RaceBoundary / null | いいえ |  |
| `performances` | TrainerPerformanceItem[] | はい | 調教師プロフィールと成績 |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

**`TrainerPerformanceItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `trainer_code` | string | はい | 調教師コード |
| `trainer_name` | string / null | いいえ | 調教師名 |
| `is_active` | boolean / null | いいえ | 現役か |
| `affiliation_name` | string / null | いいえ | 所属 |
| `licensed_date` | string / null | いいえ | 免許日 |
| `provisional_starts` | integer | いいえ |  |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |

</details>

<a id="compare_race_trainers"></a>

### 44. `compare_race_trainers`

**目的**: 対象出走馬の調教師の実績を比較する。

**日付基準**: レース前実績。対象レース自身を除き、対象開催日と日本時間の現在日の早い方まで。前の日付は採用し、同日の結果は既知の発走予定時刻が対象発走予定時刻と現在時刻より前のものだけを採用する。完了実績を対象とし、完全速報も含める。任意の過去時点を復元する機能ではない。

**注意事項**: 同じ調教師をまとめ、対象レース以前の完了実出走から集計する。

**実装**: [src/jvdata_bridge_mcp/tools/trainer/compare.py:68](../src/jvdata_bridge_mcp/tools/trainer/compare.py#L68)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `race_code` | string | 必須 | 対象レースを識別する16桁のレースコード |

**出力の概要**: trainers: 調教師別の出走・勝利・3着内・出走別賞金合計

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `result_policy` | string | いいえ |  |
| `trainers` | TrainerComparisonItem[] | はい | 調教師比較 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`TrainerComparisonItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `trainer_code` | string | はい | 調教師コード |
| `trainer_name` | string / null | いいえ | 調教師名 |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `earned_prize_money_yen` | integer / null | いいえ | 獲得賞金。円 |

</details>

<a id="analyze_trainer_distance_specialty"></a>

### 45. `analyze_trainer_distance_specialty`

**目的**: 調教師の走路・距離帯成績を確認する。

**日付基準**: レース指定可。race_code指定時は未発走の対象レースを確認し、対象レースと現在時刻から範囲を自動決定。省略時は各ツールによる（馬・騎手・コースは現在までの完了実績、調教師2本は原票集計）。実出走を集計する場合、現在日の実績は発走予定時刻が分析時刻より前のものだけを採用する。

**注意事項**: race_code指定時は実出走から再集計。省略時はpeople_statisticsの原票区分。距離帯は粗く、短距離とマイルはいずれも1600m以下を使う。

**実装**: [src/jvdata_bridge_mcp/tools/trainer/distance_specialty.py:83](../src/jvdata_bridge_mcp/tools/trainer/distance_specialty.py#L83)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `distance_category` | string | 必須 | 距離区分 |
| `track_type` | string | 必須 | 芝・ダートなどの走路種別 |
| `trainer_code` | string | 必須 | 調教師コード |
| `period` | string | 省略可 / `"career"` |  |
| `race_code` | string | 省略可 / `""` | 予想対象のレースコード。省略は現在までの収録済み成績 |

**出力の概要**: specialties: 距離帯別の出走/勝利/3着内・速報数、集計範囲/period/boundary

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `aggregation_scope` | string | いいえ |  |
| `period` | string | いいえ |  |
| `boundary` | RaceBoundary / null | いいえ |  |
| `specialties` | TrainerDistanceSpecialtyItem[] | はい | 距離・走路別調教師成績 |

<details>
<summary>出力の入れ子定義（2型）</summary>

**`RaceBoundary`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `race_code` | string / null | いいえ |  |
| `race_date` | string / null | いいえ |  |
| `scheduled_start_at` | string / null | いいえ |  |
| `analysis_at` | string | はい |  |
| `history_before_at` | string | はい |  |
| `timezone` | string | いいえ |  |
| `policy` | string | いいえ |  |

**`TrainerDistanceSpecialtyItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `trainer_code` | string | はい | 調教師コード |
| `trainer_name` | string / null | いいえ | 調教師名 |
| `distance_band_code` | string / null | いいえ | 距離帯コード |
| `track_type` | string / null | いいえ | 走路種別 |
| `provisional_starts` | integer | いいえ |  |
| `starts` | integer | はい | 出走数 |
| `wins` | integer | はい | 勝利数 |
| `top3_finishes` | integer | はい | 3着以内数 |
| `flat_prize_money_yen` | integer / null | いいえ | 平地賞金。円 |

</details>

<a id="sql_execute_custom_query"></a>

### 46. `sql_execute_custom_query`

**目的**: 許可された参照面に読み取りSQLを実行する。

**日付基準**: SQL次第。自動の日付境界はない。SQLのWHERE条件とパラメータが範囲を決める。pre_race_mode/contextは参照面の制約であり、race_codeから日時境界を自動注入する機能ではない。

**注意事項**: 単一SELECT/WITH、read-only、許可参照面と行数を検証。pre_race_mode=trueまたはcontext=pre_race_predictionで事後参照面を遮断するが、日時範囲はSQLに明示する。継続には一意なORDER BYが必要。

**実装**: [src/jvdata_bridge_mcp/tools/misc/custom_sql.py:58](../src/jvdata_bridge_mcp/tools/misc/custom_sql.py#L58)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `sql` | string | 必須 | 許可されたread modelを参照する単一のSELECTまたはWITH SQL |
| `max_rows` | integer | 省略可 / `100` | 返却する最大行数 |
| `parameters_json` | string | 省略可 / `""` | SQLバインドパラメータのJSONオブジェクト |
| `pre_race_mode` | boolean | 省略可 / `false` | 発走前に利用可能なread modelだけへ制限するか |
| `context` | string / null | 省略可 / `null` | SQL実行コンテキスト。development、pre_race_prediction、post_race_reviewのいずれか |
| `cursor` | string / null | 省略可 / `null` | 同じSQL・パラメータ・上限で継続するカーソル |

**出力の概要**: rows: 任意列の行、要求/実効上限・返却数・省略・次カーソル

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `rows` | object[] | はい | 検証済みSELECTが返した列名付きの行 |
| `requested_limit` | integer | はい |  |
| `effective_limit` | integer | はい |  |
| `returned_count` | integer | はい |  |
| `truncated` | boolean | はい |  |
| `next_cursor` | string / null | いいえ |  |
| `pagination_note` | string | いいえ |  |

<a id="sql_get_all_tables"></a>

### 47. `sql_get_all_tables`

**目的**: 利用可能なDB参照面を一覧する。

**日付基準**: DB構造。実行時点のDBメタデータ。レース日・履歴期間の概念はない。

**注意事項**: 日時条件やレースコードは入力にない。

**実装**: [src/jvdata_bridge_mcp/tools/misc/all_tables.py:38](../src/jvdata_bridge_mcp/tools/misc/all_tables.py#L38)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|

**出力の概要**: tables: schema・relation名・種別・説明

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `tables` | RelationItem[] | はい | 利用可能なrelation一覧 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`RelationItem`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `table_schema` | string | はい | schema名 |
| `relation_name` | string | はい | relation名 |
| `relation_kind` | string | はい | PostgreSQLのrelation種別 |
| `description` | string | はい | relationの説明 |

</details>

<a id="sql_get_table_schema"></a>

### 48. `sql_get_table_schema`

**目的**: 参照面の列・キー・索引を確認する。

**日付基準**: DB構造。実行時点のDBメタデータ。レース日・履歴期間の概念はない。

**注意事項**: table_nameでrelationを指定。レース実績の取得ではない。

**実装**: [src/jvdata_bridge_mcp/tools/misc/table_schema.py:71](../src/jvdata_bridge_mcp/tools/misc/table_schema.py#L71)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `table_name` | string | 必須 | relation名。schema.relation形式を推奨 |

**出力の概要**: 列名/型/NULL可否・主キー・外部キー・索引

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `table_schema` | string | はい | schema名 |
| `relation_name` | string | はい | relation名 |
| `description` | string | はい | relationの説明 |
| `columns` | TableSchemaColumn[] | はい | 列定義 |
| `primary_key` | string[] | はい | 主キー列 |
| `foreign_keys` | TableSchemaForeignKey[] | はい | 外部キー定義 |
| `indexes` | TableSchemaIndex[] | はい | 索引定義 |

<details>
<summary>出力の入れ子定義（3型）</summary>

**`TableSchemaColumn`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `column_name` | string | はい | 列名 |
| `data_type` | string | はい | SQLのデータ型 |
| `udt_name` | string | はい | PostgreSQLの内部型名 |
| `is_nullable` | string | はい | NULL許容。YESまたはNO |
| `column_default` | string / null | いいえ | 列のデフォルト式 |
| `ordinal_position` | integer | はい | 列の順序 |
| `comment` | string | はい | 列コメント |
| `table_description` | string | はい | relationの説明 |

**`TableSchemaForeignKey`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `constraint_name` | string | はい | 外部キー制約名 |
| `constrained_columns` | string[] | はい | 制約対象列 |
| `referred_table` | string | はい | 参照先relation。schema.relation形式 |
| `referred_columns` | string[] | はい | 参照先列 |

**`TableSchemaIndex`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `name` | string | はい | 索引名 |
| `is_unique` | boolean | はい | 一意索引かどうか |
| `indexdef` | string | はい | PostgreSQLの索引定義 |

</details>

<a id="knowledge_list"></a>

### 49. `knowledge_list`

**目的**: 予想・レビュー用ナレッジのURIと根拠情報を一覧する。

**日付基準**: 同梱ナレッジ。サーバー起動時に読み込んだ公開ナレッジの版。根拠期間はsupporting_evidence_periodsで確認し、不明な期間を公開日や登録日で補完しない。対象レースの日付による本文の切り替えや過去版の復元はしない。

**注意事項**: 引数なし。本文は含まない。knowledge_readで関連するURIを読む。DB接続不要。Resourcesと同じ起動時の版を返す。

**MCP注釈**: `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`。

**実装**: [src/jvdata_bridge_mcp/tools/knowledge.py:64](../src/jvdata_bridge_mcp/tools/knowledge.py#L64)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|

**出力の概要**: resources/count: URI・名前・タイトル・概要・種別・公開状態・本文SHA-256・根拠期間

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `resources` | KnowledgeResourceInfo[] | はい | 公開ナレッジのURI・概要・根拠情報の一覧。本文は含まない |
| `count` | integer | はい | 公開ナレッジの件数 |

<details>
<summary>出力の入れ子定義（1型）</summary>

**`KnowledgeResourceInfo`**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `uri` | string | はい | knowledge_readへ渡す登録済みResource URI |
| `name` | string | はい | Resource名 |
| `title` | string | はい | ナレッジのタイトル |
| `description` | string | はい | 内容と適用範囲の説明 |
| `mime_type` | string | はい | 本文のMIME型 |
| `kind` | string | はい | 手順または判断知識 |
| `status` | string | はい | Resourceの公開状態 |
| `content_sha256` | string | はい | UTF-8本文バイト列のSHA-256。利用した版の識別に使う |
| `max_evidence_race_date` | string / null | はい | 確認済みの最大根拠日。不明はnull |
| `supporting_evidence_periods` | object[] | はい | 根拠資料ごとの期間・標本数・適用範囲 |

</details>

<a id="knowledge_read"></a>

### 50. `knowledge_read`

**目的**: 登録済みナレッジのMarkdown全文と根拠情報を取得する。

**日付基準**: 同梱ナレッジ。サーバー起動時に読み込んだ公開ナレッジの版。根拠期間はsupporting_evidence_periodsで確認し、不明な期間を公開日や登録日で補完しない。対象レースの日付による本文の切り替えや過去版の復元はしない。

**注意事項**: knowledge_listが返した登録済みURIを指定する。任意のファイルや外部URLを拒否する。DB接続不要。Resourcesと同じ本文・メタデータを返す。

**MCP注釈**: `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`。

**実装**: [src/jvdata_bridge_mcp/tools/knowledge.py:81](../src/jvdata_bridge_mcp/tools/knowledge.py#L81)

**入力**

| 項目 | 型 | 必須 / 省略時 | 説明 |
|---|---|---|---|
| `uri` | string | 必須 | knowledge_listが返したResource URI |

**出力の概要**: content: Markdown全文と、URI・名前・タイトル・概要・種別・公開状態・本文SHA-256・根拠期間

**出力のトップレベル**

| 項目 | 型 | 必須キー | 説明 |
|---|---|---|---|
| `uri` | string | はい | knowledge_readへ渡す登録済みResource URI |
| `name` | string | はい | Resource名 |
| `title` | string | はい | ナレッジのタイトル |
| `description` | string | はい | 内容と適用範囲の説明 |
| `mime_type` | string | はい | 本文のMIME型 |
| `kind` | string | はい | 手順または判断知識 |
| `status` | string | はい | Resourceの公開状態 |
| `content_sha256` | string | はい | UTF-8本文バイト列のSHA-256。利用した版の識別に使う |
| `max_evidence_race_date` | string / null | はい | 確認済みの最大根拠日。不明はnull |
| `supporting_evidence_periods` | object[] | はい | 根拠資料ごとの期間・標本数・適用範囲 |
| `content` | string | はい | 登録済みResourceと同一のMarkdown全文 |
