# トランスポート

このサーバーは、MCPクライアントとの接続方法として `stdio` と Streamable HTTP を
提供します。用途に応じて次のように選びます。

- ローカルのAIクライアントからサーバーを直接起動する場合は `stdio`。
- 別プロセスとして起動したサーバーへ接続する場合や、HTTP経由で接続する場合は
  Streamable HTTP。

## stdio

MCPクライアントがサーバープロセスを所有する場合は `stdio` を使用します。
標準入力と標準出力をMCPプロトコル専用に使うため、サーバーのログは標準エラー出力へ
送られます。

```text
jvdata-bridge-mcp stdio
```

MCPクライアントへのJSON・TOML形式の登録例は[README](../README.md)を参照してください。

## Streamable HTTP

明示的に管理するローカルエンドポイントや、HTTPで接続するMCPクライアントには
Streamable HTTPを使用します。

```text
jvdata-bridge-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765
```

MCPの接続先は次のURLです。

```text
http://127.0.0.1:8765/mcp
```

`GET /health` はMCP接続とは別の稼働確認用エンドポイントです。データベースへ問い合わせず、
プロセスの状態・識別情報、サーバー名、公開ツール・Resourceの件数を返します。
DB接続と主要read modelの検査は[設定のdoctor手順](configuration.md#doctor-による動作診断)を使います。

```text
http://127.0.0.1:8765/health
```

ローカルプロセスをPIDファイルで管理する場合は、起動時に `--pid-file` を指定します。

```text
jvdata-bridge-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765 --pid-file .run/jvdata-bridge-mcp.pid
jvdata-bridge-mcp status --pid-file .run/jvdata-bridge-mcp.pid
jvdata-bridge-mcp stop --pid-file .run/jvdata-bridge-mcp.pid
```

PIDファイルはPID整数だけではなく、`server`、`pid`、`host`、`port`、`started_at`、
ランダムな`instance_id`、`transport`を持つJSONです。`status`と`stop`はPIDの生存確認だけで
判断せず、記録されたportの`GET /health`が同じ`server`、`pid`、`host`、`port`、`started_at`、
`instance_id`、`transport`を返す場合だけ、そのプロセスを管理対象として扱います。PIDが再利用されていたり、
別のサーバーが同じportを使っていたりしてhealthの識別情報が一致しない場合は、誤停止を避けるため
停止せず、`status: "unknown"`として失敗します。

Docker Composeでホストの`8080`ポートへ公開する場合は、`.env`を用意して次を実行します。

```bash
docker compose up --build -d
```

コンテナ内では`0.0.0.0:8080`にバインドし、MCP接続先は
`http://127.0.0.1:8080/mcp`、health endpointは`http://127.0.0.1:8080/health`です。
ホスト上のPostgreSQLへ接続する場合、DBホストには`host.docker.internal`を指定します。

### Nginx Proxy Managerでの公開

Docker Composeは`8080:8080`でポートを公開するため、Nginx Proxy Managerから
DockerホストのIPアドレスとポート`8080`へ転送できます。
次の設定で認証なしのHTTPSエンドポイントを公開します。ドメインは実際の公開用ドメインに置き換えてください。

| Proxy Hostの設定 | 値 |
| --- | --- |
| Domain Names | `mcp.example.com` |
| Scheme | `http` |
| Forward Hostname / IP | Nginx Proxy Managerから到達できるDockerホストのIPアドレス |
| Forward Port | `8080` |
| Access List | `Publicly Accessible` |
| Cache Assets | OFF |
| Websockets Support | OFF（Streamable HTTPでは不要） |

Nginx Proxy Managerもコンテナで動かす場合、その`127.0.0.1`はNginx Proxy Manager自身を指します。
Forward Hostname / IPにはDockerホストのLANアドレスなど、Nginx Proxy Managerから到達できるアドレスを指定してください。

SSLタブで公開用ドメインの証明書を設定し、Force SSLをONにします。
Advancedタブには次を追加します。SSEを含むStreamable HTTPの応答をバッファせず逐次転送し、
長時間の応答待ちにも対応します。

```nginx
proxy_http_version 1.1;
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

MCPクライアントには`https://mcp.example.com/mcp`を登録します。
`/mcp`のパスはそのまま転送します。MCPサーバーは認証なしで動作し、TLS終端はNginx Proxy Managerが担当します。

参考: [Nginx Proxy Managerの設定テンプレート](https://github.com/NginxProxyManager/nginx-proxy-manager/blob/develop/backend/templates/proxy_host.conf)、
[NGINXのproxy_buffering設定](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)。
