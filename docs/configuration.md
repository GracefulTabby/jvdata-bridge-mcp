# 設定

DB接続・クエリ設定は `JVDATA_BRIDGE_MCP_` 接頭辞を持つ環境変数で指定します。
任意導入モデルの保存先と管理は[モデル開発](model-development.md#採用済みjsonの配布)を参照してください。

セットアップとクライアント登録は[README](../README.md)、起動方法は[トランスポート](transports.md)を参照してください。
プロセスの環境変数を優先し、未設定の値を `.env` から読み込みます。

## 環境変数

### 1. データベース接続設定

`JVDATA_BRIDGE_MCP_DATABASE_URL` が設定されている場合は優先使用されます。未設定の場合は個別パラメータが必須です。

| 環境変数 | 既定値 | 説明 |
| --- | --- | --- |
| `JVDATA_BRIDGE_MCP_DATABASE_URL` | — | PostgreSQL 接続 URL（優先） |
| `JVDATA_BRIDGE_MCP_DB_HOST` | — | ホスト名（URL 未指定時必須） |
| `JVDATA_BRIDGE_MCP_DB_PORT` | `5432` | ポート番号 |
| `JVDATA_BRIDGE_MCP_DB_NAME` | `jvdata` | データベース名 |
| `JVDATA_BRIDGE_MCP_DB_USER` | — | 接続ユーザー（URL 未指定時必須） |
| `JVDATA_BRIDGE_MCP_DB_PASSWORD` | — | 接続パスワード（URL 未指定時必須） |
| `JVDATA_BRIDGE_MCP_SSLMODE` | `disable` | PostgreSQL SSL モード |

> ⚠️ **標準スキーマ**: 固定で `jv_data` / `jv_mart` / `jv_ref` を使用します（環境変数による変更不可）。

### 2. 接続プール・タイムアウト・行数上限

| 環境変数 | 既定値 | 説明 |
| --- | --- | --- |
| `JVDATA_BRIDGE_MCP_POOL_SIZE` | `5` | 接続プールの基本サイズ |
| `JVDATA_BRIDGE_MCP_MAX_OVERFLOW` | `5` | プール超過時の一時接続上限 |
| `JVDATA_BRIDGE_MCP_POOL_TIMEOUT_SECONDS` | `10` | プールからの接続取得待ちタイムアウト (秒) |
| `JVDATA_BRIDGE_MCP_CONNECT_TIMEOUT_SECONDS` | `5` | DB 接続タイムアウト (秒) |
| `JVDATA_BRIDGE_MCP_COMMAND_TIMEOUT_SECONDS` | `60` | クエリ実行の既定タイムアウト (秒)。個別ツールの指定があればそちらを使用 |
| `JVDATA_BRIDGE_MCP_MAX_ROWS` | `200` | `custom_sql` の標準取得上限（絶対上限: `1000`） |

---

## `doctor` による動作診断

```bash
uv run jvdata-bridge-mcp doctor
```

診断結果の最上階層にある `"ok": true` で全体の正常性を確認します。

### 正常判定の条件

* `"ok": true`
* `configuration.valid`: `true`
* `database.connected` & `database.transaction_read_only`: `true`
* `read_models` (`races`, `race_cards`, `latest_win_place_odds`): すべて `true`
* `tool_count`: `50`
* `resource_count`: `knowledge/manifest.json` の登録件数と一致

`doctor` はモデル精度や全馬の速度行までは検査しません。
対象レースの予測用データは `prediction_model_status(race_code)` で確認してください。
