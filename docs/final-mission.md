# FINAL MISSION — 自ら閉域性を調査せよ

Agent に以下を入力します。

```text
このTerraform環境からInternetへ出られる経路が存在するか調査してください。
Terraformの構成だけで断定せず、実通信も確認してください。
一方で、許可されたS3への通信は利用できるか確認してください。
最後に、観測した根拠だけを箇条書きにしてください。
```

Agent が使える Tool:

1. `list_files` — Terraform ファイルを発見
2. `grep` — `internet_gateway`, `nat_gateway`, `0.0.0.0/0`, `associate_public_ip_address` 等を検索
3. `read_file` — 該当ファイルを確認
4. `check_internet` — public host への TCP 443 を runtime test
5. `check_s3` — controlled artifact bucket へのアクセスを runtime test

## Expected observation

Terraform intent:

- Internet Gateway resource なし
- NAT Gateway resource なし
- Public IP 自動付与なし
- EC2 Public IP なし
- S3 Gateway Endpoint あり
- SSM / SSM Messages 等の Interface Endpoint あり

Runtime:

- Public Internet TCP connection: failure expected
- Controlled S3 bucket access: success expected

これは「数学的な絶対証明」ではなく、**IaC の意図と実通信を Agent が自律的に照合するデモ**です。
