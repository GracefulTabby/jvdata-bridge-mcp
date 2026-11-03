# コントリビューション

## 開発環境

```text
uv sync
cp .env.example .env
```

データベースの値はローカルの `.env` ファイルにだけ設定してください。
このファイル、認証情報、本番データ、生成されたレポートをコミットしてはいけません。

## 検証

変更内容に応じて、次の検証を実行してください。

```text
uv run pytest -q --capture=no
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright src
uv run python scripts/validate_model_bundle.py
uv lock --check
git diff --check
```

MCPクライアントから見える `tools/list` のツール名、説明、入力スキーマ、出力スキーマは
公開面です。変更した場合は `uv run python scripts/update_tool_schemas.py` で全ツールの
JSONスキーマとツールリファレンスを再生成し、用途・取得範囲などの説明も確認してください。
すべての変更に専用の契約テストを追加する必要はありません。

各ツールは独立した実行単位として保ちます。1ツール1モジュールを基本とし、入力の
`Annotated` 定義、Pydantic出力モデル、docstring、ツールが所有するSQLを同じファイルに
置きます。ツールは自分のSQLを直接実行し、別のツールをMCP層経由で呼び出してはいけません。

登録と共有処理の配置は[ツール構成](docs/tool-catalog.md#ツールを追加するとき)を参照してください。

## ライセンスとDCO

このリポジトリのコードと自作ドキュメントはApache License 2.0で公開します。
貢献を提出することで、貢献内容をApache License 2.0の条件で公開する権利を持っていること、
および同ライセンスで公開することに同意したものとします。著作権の譲渡を求めるものでは
ありません。

すべてのコミットには、Developer Certificate of Origin（DCO）に基づく
`Signed-off-by` 行を付けてください。

```text
git commit -s
```

DCOの全文は [DCO.txt](DCO.txt) にあります。署名は、貢献者本人の名前とメールアドレスを
使ってください。認証情報や本番データをコミットへ含めてはいけません。
