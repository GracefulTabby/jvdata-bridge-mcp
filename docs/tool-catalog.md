# ツール構成

全50ツールの目的、SQLに基づく日付基準、全入力の型・必須・既定値、出力の入れ子定義は
[ツール別リファレンス](tool-reference.md)にまとめています。
機械可読の完全な入力・出力定義は[スキーマJSON](tool-schemas.json)を参照してください。

## MCPクライアントから見えるもの

サーバーは、読み取り専用のMCPツールを50個公開します。
MCPクライアントは `tools/list` で、
各ツールの名前、説明、入力スキーマ、構造化された出力スキーマを取得できます。

`GET /health` と `doctor` は、公開ツール・Resourceの件数を返します。
ツールの変更時は、実際の `tools/list`、生成スキーマ、リファレンス、関連テストを一致させます。

## 公開ツール

カテゴリごとの内訳は次のとおりです。

| カテゴリ | 件数 | 役割 |
| --- | ---: | --- |
| Race | 12 | レース概要、出馬表、結果、条件変化、近走事実、同走上位馬とクラス、過去条件の比較 |
| Horse | 5 | 馬の検索、詳細、比較、履歴、実績 |
| Odds | 1 | 8券種のオッズ表と取得状況 |
| Jockey | 4 | 騎手プロフィール、成績、ランキング、対象レース以前の近況 |
| Pace | 4 | 対象レース以前の脚質、前方位置、騎手戦術、コースペース事実 |
| Prediction | 5 | モデル準備状態、特徴量、確率、シナリオ、複合分析 |
| Bloodline | 4 | 血統一覧、血統表、距離傾向、フォーム |
| Course | 3 | コース優位性、距離傾向、過去日と今日の枠・脚質成績 |
| Training | 4 | 調教評価、条件、対象馬詳細、強度 |
| Trainer | 3 | 調教師実績、対象レース比較、距離・走路適性 |
| SQL | 3 | 安全なSELECT、テーブル一覧、テーブルスキーマ |
| Knowledge | 2 | 公開Resourceと同じナレッジ一覧・本文 |
| 合計 | **50** | |

公開順を含む正確な一覧は次のとおりです。

```text
race_summary
entries_list
track_condition_summary
search_races
get_race_results
get_graded_races
race_evidence_collector
analyze_similar_races
race_recent_form_facts
race_opponent_class_facts
race_condition_change_facts
race_forecast_context
search_horses
get_horse_details
compare_horses
analyze_horse_race_history
analyze_horse_performance
odds_table
get_jockey_profile
get_jockey_stats
get_top_jockeys
jockey_recent_form
race_pace_running_styles
race_pace_identify_beneficiaries
race_pace_jockey_tactics
race_pace_course_profile
prediction_model_status
get_horse_feature_matrix
simulate_race_scenarios
predict_race_probabilities
get_composite_analysis
bloodline_scoreboard
bloodline_fetch_pedigree
bloodline_distance_bias
bloodline_form_profile
course_advantage_analyzer
course_distance_insight
course_frame_style_stats
race_training_evaluation
training_condition_summary
training_entry_detail
training_intensity_analysis
analyze_trainer_performance
compare_race_trainers
analyze_trainer_distance_specialty
sql_execute_custom_query
sql_get_all_tables
sql_get_table_schema
knowledge_list
knowledge_read
```

予想は `race_forecast_context(race_code)` で事実を取得し、独立した馬評価を固定してから
`odds_table` で市場を確認します。取得範囲・現在観測・欠測の扱いは[予想コンテキスト](forecast-context.md)を参照してください。
モデルの入力・学習期間・較正・組合せ確率は[モデル開発](model-development.md)、
各ツールの引数・出力・注意事項は[ツールリファレンス](tool-reference.md)を参照してください。

## 公開定義の正

ツールの公開定義は、`src/jvdata_bridge_mcp/tools/` 以下の各モジュールにある非同期ツール関数と、
その関数に付随する型定義です。

- Pythonの関数名がMCPのツール名になります。
- `Annotated` と `Field(description=...)` が入力スキーマの引数説明になります。
- docstringがツールの説明になります。
- 戻り値のPydanticモデルが出力スキーマになります。

`tools/all.py` は50ツールを上記の順序で構築します。
SQL gatewayを使う3ツールも、同じ経路で登録します。

## 固定ツールとカスタムSQL

固定ツールは、ツール自身が所有するSQLを実行し、DBの行をツール固有のPydanticモデルへ変換します。
成功した結果はMCPの構造化出力として返し、実行エラーはMCP標準のエラーで表現されます。

`sql_execute_custom_query` は、SELECT句の列が実行時まで決まらないため例外です。出力は
`rows: list[dict[str, object]]` という動的な行一覧で、固定ツールと同じ列モデルは持ちません。
SQL本文は `sqlglot` で検証され、許可されたread modelだけを読み取る単一のSELECT/CTEに制限されます。
詳細は[データベース契約](database-contract.md)を参照してください。

## ツールを追加するとき

1. 適切な `tools/<domain>/` ディレクトリに1つのモジュールを追加します。その中に、非同期関数、
   `Annotated` 引数、Pydantic出力モデル、docstring、ツールが所有するSQLを置きます。
2. `tools/all.py` の一覧へ追加し、カテゴリ件数と `CANONICAL_TOOL_NAMES` を更新します。
3. SQL安全性、対象日による情報境界、NULL処理、実行時間、出力変換の確認を追加します。
4. tools/list、stdio、Streamable HTTP、doctorの公開面テストを更新します。

複数のツールに共通するDB接続やJSON値変換だけを `_runtime.py` またはSQL gatewayへ置きます。
ツール固有の業務SQLは、各ツールモジュールが所有します。

## 取得範囲

レースを対象にする分析では `race_code` から取得範囲を自動で決めます。馬・騎手・調教師・コースの
汎用ツールで省略した場合は、現在収録の値または完了実績を返します。対象レースによる履歴制限を
持たないプロフィールや血統、結果参照、カスタムSQLは、各ツールの日付基準を確認してください。

日時と結果段階は[データベース契約](database-contract.md#2-日時の意味とオッズタイムスタンプ)、
予想時の利用順序は[予想コンテキスト](forecast-context.md#予想の順序)にまとめています。
