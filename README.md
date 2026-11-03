# jvdata-bridge-mcp

AIエージェントに競馬データベース（PostgreSQL）への直接アクセスを提供するMCP（Model Context Protocol）サーバーです。

> ⚠️ 本サーバーの利用には、**[JVData Bridge](https://github.com/GracefulTabby/jvdata-bridge-releases)** で構築された PostgreSQL データベースが必要です。

---

## 💡 概要

`jvdata-bridge-mcp` をMCPクライアント（AIエージェント）に接続することで、競馬データの検索・分析や予想ナレッジの参照が可能になります。

* **標準 50 ツール (canonical tools)**: レース・出馬表・オッズ・血統・調教・予測指標などの検索
* **予想ナレッジ (MCP Resources)**: `resources/list`, `resources/read` と `knowledge_list`, `knowledge_read` で判断知識を参照
* **カスタム SQL 実行**: Read-only トランザクション＆安全フィルタ付きの分析機能

### 提供データ領域

| 領域 | 主な機能 |
| --- | --- |
| **レース・競走馬** | 概要、出馬表、確定結果、ラップ、近走・類似レース、過去実績 |
| **オッズ** | 当日の単勝・複勝・3連複など8券種、発表時点と欠測・取消の表示 |
| **騎手・調教師** | プロフィール、条件別・近況成績、調教師の距離/コース実績 |
| **ペース・コース** | 展開恩恵、騎手位置統計、コース傾向、枠・脚質バイアス |
| **予測指標** | 能力・適性の特徴量、勝率・上位着順・組合せ確率、脚質別ペースと馬別コーナー位置・代替先頭分岐、対象日前の履歴に基づく複合分析 |
| **血統・調教** | 血統表、距離傾向、直近21日の調教評価・強度パターン |
| **SQL** | 安全な参照用 `SELECT` 実行およびテーブル構造の確認 |

発走前の独立評価には `race_forecast_context(race_code)` で出馬表・過去走・相手・調教・位置・コース・血統と
現在の観測をまとめて取得できます。取得範囲は対象レースと現在時刻から自動で決まり、
対象自身・未来・部分速報を除外します。過去時点の予想再現には対応しません。
今日の候補は `search_races(race_date="today", upcoming_only=True)` で発走予定とともに検索できます。
馬評価を固定した後、`odds_table(race_code, bet_type="trio", combinations=["3-6-7"])` で買い目を照会できます。
馬・騎手・調教師・コースの個別集計にも `race_code` を渡すと取得範囲が自動で決まります。
全ツールの参照面と入出力は[ツールリファレンス](docs/tool-reference.md)、検証方針は[アーキテクチャ](docs/architecture.md#検証)を参照してください。
入力・出力・取得の順序は[発走前予想コンテキスト](docs/forecast-context.md)を参照してください。

---

## 🚀 クイックスタート

### 1. 前提条件

* **Python 3.11+**（`uv` 推奨）
* **[JVData Bridge](https://github.com/GracefulTabby/jvdata-bridge-releases)** で構築した PostgreSQL データベース

### 2. セットアップ

```bash
git clone https://github.com/GracefulTabby/jvdata-bridge-mcp.git
cd jvdata-bridge-mcp
uv sync

```

予測モデルはパッケージに同梱しています。
線形モデルの勝率・複合分析には、対象の出馬表と全候補馬の `jv_mart.horse_speed_quality` が必要です。
ペース・位置は速度表示の欠測だけでは停止しません。必要なDBデータは[データベース契約](docs/database-contract.md#予測用の速度read-model)、
モデルの適用域・較正状態・配布は[モデル開発](docs/model-development.md)を参照してください。

`.env.example` をコピーして `.env` を作成し、DB接続情報を指定します。

```dotenv
JVDATA_BRIDGE_MCP_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/jvdata

```

Dockerで起動する場合は、DB接続用の `.env` を用意して `docker compose up --build -d` を実行します。
MCP接続先は `http://127.0.0.1:8080/mcp` です。ホスト上のDBには `host.docker.internal` を使います。
HTTP・プロキシ設定は[トランスポート](docs/transports.md)を参照してください。

### 3. 動作確認

```bash
uv run jvdata-bridge-mcp doctor

```

※ JSON出力の `"ok": true` および DB接続・ツール数が正常であることを確認してください。

---

## 🔌 MCPクライアント設定

お使いのMCPクライアントに合わせて設定を追加してください（`cwd` は実際のプロジェクトパスに置換）。

### stdio モード (推奨)

**JSON形式 (Claude Desktop / Cursor / VS Code / Claude Code など):**

```json
{
  "mcpServers": {
    "jvdata-bridge-mcp": {
      "command": "uv",
      "args": ["run", "jvdata-bridge-mcp", "stdio"],
      "cwd": "/path/to/jvdata-bridge-mcp"
    }
  }
}

```

**TOML形式 (OpenAI Codex など):**

```toml
[mcp_servers."jvdata-bridge-mcp"]
command = "uv"
args = ["run", "jvdata-bridge-mcp", "stdio"]
cwd = "/path/to/jvdata-bridge-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 60

```

### Streamable HTTP モード (ネットワーク経由・ChatGPT等)

```bash
uv run jvdata-bridge-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765

```

* **MCP Endpoint**: [http://127.0.0.1:8765/mcp](http://127.0.0.1:8765/mcp)
* **Health check**: [http://127.0.0.1:8765/health](http://127.0.0.1:8765/health)

---

## 🛠️ カスタムSQL分析

標準ツールでカバーできない複雑な集計は、AIエージェントが `sql_get_table_schema` でテーブル定義を確認のうえ、`sql_execute_custom_query` でクエリを実行できます。

* **安全設計**: トランザクションは Read-only で固定されており、`sqlglot` 解析により単一の `SELECT`/`WITH` 文のみを許可します（書き込み・スキーマ変更・危険な関数の呼び出しは自動拒否）。

---

## 📚 ドキュメント & ライセンス

予測モデルの仕様と学習・評価手順は
[予測モデルの開発・配布](docs/model-development.md) を参照してください。

* **詳細仕様**: [アーキテクチャ](docs/architecture.md) | [設定](docs/configuration.md) | [DB仕様](docs/database-contract.md) | [ツール一覧](docs/tool-catalog.md) | [トランスポート](docs/transports.md) | [発走前予想コンテキスト](docs/forecast-context.md)
* **ライセンス**: [Apache License 2.0](LICENSE) (Copyright 2026 Ryuya Matsumoto)
* ※ 競馬データ、JV-Link、外部モデル等の権利はそれぞれの提供元に帰属します。
