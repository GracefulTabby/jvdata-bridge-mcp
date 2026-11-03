# アーキテクチャ

本パッケージは、`jvdata-bridge` の PostgreSQL データベースを参照する分析ツール群と、予想ナレッジ（Markdown）を提供するスタンドアロンの MCP（Model Context Protocol）サーバーです。

利用目的に応じた説明は次の文書にまとめています。

| 内容 | 参照先 |
| --- | --- |
| DB設定と接続診断 | [設定](configuration.md) |
| stdio・HTTP・Dockerとプロセス管理 | [トランスポート](transports.md) |
| DB参照制約、値の単位、日時の意味 | [データベース契約](database-contract.md) |
| ツール構成と登録 | [ツール構成](tool-catalog.md) |
| 全ツールの引数・出力 | [ツールリファレンス](tool-reference.md)、[スキーマJSON](tool-schemas.json) |
| 発走前の取得範囲と予想手順 | [予想コンテキスト](forecast-context.md) |
| モデルの入力・学習・評価・配布 | [モデル開発](model-development.md) |

---

## 実行フロー

```text
[ MCP クライアント ] (Claude Desktop / Cursor / Codex 等)
  │
  ├─ (stdio / Streamable HTTP)
  │    │
  │    ├─ [ FastMCP Tools ] ── (入力検証: Annotated / 出力検証: Pydantic)
  │    │      │
  │    │      ├─ [ knowledge_list / knowledge_read ] ──> [ ナレッジスナップショット ]
  │    │      ├─ [ 固定 SQL ツール ] ───────────────────────────────┐
  │    │      └─ [ カスタム SQL ツール ] ──> sqlglot AST 検証       │
  │    │                                      │                   │
  │    │                                      ▼                   │
  │    │                    [ 読み取り専用 DB トランザクション ] <─┘
  │    │                                    │
  │    │                                    ▼
  │    │                             [ PostgreSQL DB ]
  │    │
  └─ (resources/list, read)
       │
       └─> [ ナレッジスナップショット ]

```

---

## コンポーネント設計

### 1. ツール設計 (`src/jvdata_bridge_mcp/tools/`)

* **カプセル化**: 1 ファイルにツール関数、型アノテーション（`Annotated`）、docstring、Pydantic 出力モデル、ツール固有の SQL を同梱。
* **登録フロー**: `server.py` 起動時に `tools/all.py` の定義順に従い、FastMCP の `add_tool` メソッドへ自動登録。

### 2. 予想ナレッジ (MCP Resources)

`src/jvdata_bridge_mcp/knowledge/manifest.json` に定義されたMarkdownを起動時に読み込み、
Resourcesと `knowledge_list` / `knowledge_read` で同じ本文・ハッシュを公開します。
ナレッジのメタデータと運用規則は[ナレッジの説明](../src/jvdata_bridge_mcp/knowledge/README.md)を参照してください。

### 3. データベース境界とゲートウェイ

* **`JvDataDatabase`**: 非同期SQLAlchemyエンジンと有界接続プールを管理し、すべてのクエリを読み取り専用トランザクションで実行。
* **`JvDataSqlGateway`**: カスタムSQLを `sqlglot` で構文解析し、[データベース契約](database-contract.md)の安全性・行数制約を適用。
* **データ検証と直列化**: 固定SQLツールはツール固有のPydanticモデルで型を検証。動的SQLはカラム名を持つJSONマッピングで出力。

---

## 対象データ範囲

* **標準スキーマ**: `jv_data`, `jv_mart`, `jv_ref`
* **データ期間**: グローバルな日付条件は保持せず、接続先データベース内の蓄積データに準拠。

## 予測モデル

`tools/prediction/` は同梱係数のSQL計算、速度read modelの被覆検査、位置分布と予測ツールを持ちます。
`ml/` は採用済みJSONの検証・推論、市場なし特徴量集計、着順分布と市場比較を担当します。
`ml/research/` は `research` 追加依存を使う抽出・学習・時系列評価を担当します。
追加モデルの配布対象は採用基準を通過したJSONとmanifestです。
学習期間・較正・採用判定・配布手順は[モデル開発](model-development.md)を参照してください。

## 検証

実行するコマンドは[開発者向けの検証手順](../CONTRIBUTING.md#検証)を参照してください。

通常のテストは合成データでSQLの安全性、日時境界、NULL、分母、応答上限、
stdio・Streamable HTTP・tools/list・doctorの公開契約を確認します。
同日他場、未来、対象自身、発走時刻不明、部分速報、取消・騎手変更を境界条件に含めます。
研究用依存とモデルJSONの検証手順は[モデル開発](model-development.md)にあります。

実DBの検査はread-onlyで実施し、速度指標などの不足を明示します。
公開契約の正常応答と予測精度の評価は区別し、全開催・全入力の網羅を仮定しません。
実行ログ・スナップショット・研究結果はGit管理外の `archive/` にだけ保存します。
