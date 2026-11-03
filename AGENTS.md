# AGENTS.md

## Agent-led live forecasts

- Forecast upcoming races using the connected JVData MCP tools. Historical point-in-time reconstruction and backtests are outside this project's supported workflow. Past race results remain essential forecast evidence.
- Choose the target `race_code` and record its race date, timezone, scheduled start when available, and the analysis time. Call `race_forecast_context`; the tool determines the eligible range from the target race and current time. Do not supply arbitrary `as_of_date`, `as_of_datetime`, or observation modes.
- Use completed results preceding the target race and available now, including earlier same-day races at other venues. Require a known scheduled start for same-day ordering, exclude the target race's results and later races, and distinguish complete provisional results from official results. Never treat partial top-three or top-five reports as complete.
- Training observations may include same-day sessions whose recorded time precedes the target race and now. Do not infer times for undated or time-missing same-day observations. Keep unknown observations explicit.
- Use current entry fields, weights and WF weather/ground announcements. Keep these observations and their provenance separate from past performance. Do not replace missing current observations with target-race result fields, final odds, or post-race status.
- Horse, jockey, trainer and course tools accept `race_code` to choose the boundary automatically. Omitting it on general browsing tools means current stored information, not a historical reconstruction. Race numbers and race-code sorting do not establish chronological order across venues.
- Read connected knowledge MCP Resources relevant to the race before selecting horses. Record each URI, content hash and evidence period when provided, and how it affected ranking. Resource publication/registration dates are not evidence periods. Only the developer authors or publishes knowledge Resources.
- First assess race structure and each horse's ability, fit, supporting evidence, counterevidence and uncertainty without target-race odds. Freeze the independent horse evaluation before using `odds_table`. Market evidence may change ticket value, combinations, stakes or abstention while remaining distinct from horse evaluation.
- Freeze selections, ticket combinations and stakes before reading the target result. Preserve forecast revisions separately. Do not substitute repository model output for the agent's own reasoning. Fixed model features retain their documented prior-date aggregation contract; this differs from the broader live descriptive evidence.
- Do not count a fact twice when it appears in history and same-day summaries. Small same-day samples do not prove jockey fitness. State missing data, delayed result coverage, model domain limits and uncertainty without inventing probabilities or expected returns.
- After a race, keep the frozen forecast intact when reviewing structure, pace, position, condition, market and bet construction. Record candidate insights under `archive/` for developer review.

## Forecast and backtest artifacts

- Save race forecasts, frozen selections and stakes, forecast revisions, MCP data snapshots, outcomes, scores, review notes, and aggregate reports only under `archive/`. These are local internal artifacts, not repository deliverables.
- Before saving artifacts, ensure `archive/.gitignore` contains `*`, confirm the destination is ignored with `git check-ignore`, and confirm no artifact under `archive/` is tracked with `git ls-files archive`. Keep `archive/` excluded from both wheel and source distributions.
- Never stage, commit, or push forecast or backtest artifacts, including by using `git add -f`. Do not put individual race outputs into tracked documentation, tests, or knowledge Resources. Record proposed insights under `archive/` for a developer to assess separately.
- A request to forecast or backtest authorizes analysis and local artifact creation only. It does not authorize staging, committing, pushing, publishing, or rewriting Git history. Leave related source, documentation, and `AGENTS.md` changes uncommitted unless the user separately requests their commit or push; an explicit instruction not to commit takes precedence.
- Before reporting completion, check Git status and the staged diff to verify that run artifacts have not entered Git tracking. Ignoring or deleting files from the current tree does not remove copies already present in Git history; do not claim historical results have been purged without verifying that separately.

## Commit sign-off

- When creating a commit in this repository, include a `Signed-off-by` commit-message trailer using the configured committer identity.
- Add the trailer with `git commit --signoff` (or `git commit -s`). This is a sign-off trailer; it is separate from a cryptographic commit signature such as `git commit -S`.
- Before committing, confirm the configured committer name and email are the intended identity for the repository. Do not hard-code a personal name or email in repository instructions.
- After committing, verify the message contains one `Signed-off-by` trailer with the configured identity.

# エージェント行動規範：宣言的ドキュメント＆コード管理ルール

本リポジトリでは、AIエージェントおよび開発者が遵守すべき「過去の経緯や過渡期の残骸を残さない（常に現在の正しい状態のみを宣言的に保つ）」ためのルールを定義する。

## 1. Markdownドキュメントの原則（Declarative Markdown）

* **「現在どうなっているか」の唯一の正（Single Source of Truth）を記述する。**
* **履歴・変遷の完全排除:**
  * 「以前はこうだった」「旧バージョンでは〜だったが新バージョンで変更した」といった、過去の仕様や移行のストーリー、変遷の経緯をMarkdownファイルに一切記述してはならない。
* **過渡期セクションの即時パージ:**
  * 「〜への移行手順」「旧仕様からの移行ガイド」などの過渡期のドキュメントやセクションは、移行期間・リリースが完了した時点で速やかに削除する。
  * 現在の仕様説明へ完全に統合・一本化し、重複する案内（再接続の注意事項など複数箇所に散らばる記述）を排除する。
* **ターゲット:** リポジトリ内のすべてのコンテキストファイル、仕様書、README、および `docs/` 配下のドキュメント全般。

## 2. Pythonコード・Docstring・コメントの原則

* **履歴コメントの禁止:**
  * Pythonのコード内コメントやDocstring（関数/クラス説明）に、日付やバージョンごとの修正履歴、変更理由のストーリー（例: `# 旧仕様対応のため追加` など）を記載しない。
* **ゾンビコード・旧仕様の排除:**
  * 非推奨（Deprecated）となった古い処理や、移行用の一時的なフォールバック（旧実装）は、移行完了後にコードベースから完全に削除する。コメントアウトして残さない。
* **型ヒントとコード自体の自己文書化:**
  * 履歴や複雑な背景をコメントで説明するのではなく、Pythonの型ヒント（Type Hints）やクリーンな関数名、簡潔なPydanticモデル定義などによって「コード自体が現在の仕様を語る」ように設計する。

## 3. 「なぜその仕様になったか」のオフロード先

* 変更の経緯、なぜその設計に至ったかの議論、移行のプロセスなどは、ドキュメントやコードではなく、以下の外部資産に委ねる：
  * **Gitのコミットログ**（適切な粒度のコミットメッセージ）
  * **プルリクエスト（PR）のディスクリプション**
  * **Issue / Discussion**
* リポジトリ内の成果物（コード・Markdown）は、常に「新しく参加した開発者やエージェントが読むべき現在地」だけに洗練された状態を保つ。

## 4. エージェント実行時のセルフチェックリスト

ドキュメントやコードを生成・編集する際、エージェントは出力前に以下のチェックを行うこと：

1. **過去形・比較表現のチェック:** 「以前は」「〜から移行した」「かつては」「旧〇〇」といった表現がないか？（あれば現在の断定形に書き換えるか削除する）
2. **重複・冗長性のチェック:** 同じ文脈の説明や案内が複数のセクションに分断されていないか？（あれば1箇所に統合する）
3. **バージョン残骸のチェック:** すでに完了した過去のバージョン名や過渡期の移行ガイドが仕様書に残存していないか？
