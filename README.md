# VIGIL — Closed Local LLM Agent on AWS

Terraform で **Internet egress を持たない AWS 環境**を作り、Ollama 上のローカル LLM に Tool Calling と Agent Loop を与えて、閉域内で調査・実行できる AI Agent を構築するハンズオン用リポジトリです。

## Architecture

- VPC / Private Subnet
- Public IPv4 なし
- Internet Gateway / NAT Gateway なし
- Systems Manager は Interface VPC Endpoint 経由
- S3 は Gateway VPC Endpoint 経由
- CloudWatch Logs は Interface VPC Endpoint 経由
- EC2 は Amazon Linux 2023
- Ollama と GGUF モデルは事前に S3 へ持ち込み
- Agent Runtime は Python 標準ライブラリのみで実装
- Tools: `list_files`, `read_file`, `grep`, `check_internet`, `check_s3`

```text
User
  |
  | HTTPS / AWS control plane
  v
Systems Manager
  |
  | AWS PrivateLink
  v
VPC Endpoint
  |
  v
Private EC2
  |- Agent Runtime
  |- Ollama / Local LLM
  |- Tools
  |- Terraform workspace
  |
  +---- S3 Gateway Endpoint ---- S3 (offline bundle)

Internet egress: NONE
```

## 0. Prerequisites

ローカル側:

- AWS CLI v2
- Terraform
- Git
- Bash / WSL2 / Linux 推奨
- `curl`, `tar`, `zstd` または `tar --zstd`

AWS 側:

- EC2 / VPC / IAM / S3 / Systems Manager を作成できる権限
- 使用リージョンで対象 EC2 インスタンスタイプが利用可能であること

モデルは Git に含めません。Tool Calling をサポートする GGUF モデルを自分で用意してください。Ollama の公式 Tool Calling 例では Qwen3 が使われています。

## 1. Infrastructure

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

出力された値を確認します。

```bash
terraform output
```

この時点では EC2 は起動しますが、Ollama / モデル / Agent はまだ配備されません。

## 2. Prepare an offline bundle

Internet に接続できるローカル端末で実行します。

```bash
./scripts/prepare_bundle.sh /path/to/model.gguf
```

`artifacts/vigil-bundle.tar.gz` が作られます。バンドルには以下を含みます。

- Ollama Linux runtime
- GGUF model
- Modelfile
- `agent/`
- `infra/`（Agent が自分の Terraform を調査するため）

## 3. Upload the bundle to S3

Terraform output から bucket 名を取得してアップロードします。

```bash
BUCKET=$(terraform -chdir=infra output -raw artifact_bucket_name)
./scripts/upload_bundle.sh "$BUCKET"
```

アップロード元の PC は通常 Internet 経由で S3 API を利用します。閉域なのは **EC2 側の VPC** です。

## 4. Bootstrap the private EC2 instance

Session Manager で接続します。

```bash
INSTANCE_ID=$(terraform -chdir=infra output -raw instance_id)
aws ssm start-session --target "$INSTANCE_ID"
```

EC2 上で:

```bash
sudo /usr/local/bin/vigil-bootstrap
```

このコマンドは S3 Gateway Endpoint 経由で bundle を取得し、Ollama と Agent を配備します。

確認:

```bash
systemctl status ollama
ollama list
/opt/vigil/bin/vigil-agent --help
```

## 5. Run the Agent

```bash
cd /opt/vigil/repo
/opt/vigil/bin/vigil-agent \
  "このTerraform環境からInternetへ出られる経路が存在するか調査し、構成と実通信の両面から根拠を示してください。"
```

想定される流れ:

```text
LLM -> list_files -> result
LLM -> grep -> result
LLM -> read_file -> result
LLM -> check_internet -> result
LLM -> check_s3 -> result
LLM -> final answer
```

## Security model

このサンプルでは、次を意図しています。

- EC2 に Public IP を付与しない
- NAT Gateway を作らない
- Internet Gateway を作らない
- `0.0.0.0/0` の Internet route を作らない
- SSH 22/tcp を公開しない
- SSM Agent から AWS Systems Manager への通信は Interface Endpoint 経由
- S3 への通信は Gateway Endpoint 経由
- Interface Endpoint の Security Group は EC2 の Security Group からの 443/TCP のみ許可
- EC2 の IAM Role は必要な S3 object と SSM 管理権限に限定
- Agent の runtime check は allow-list 済みの専用 Tool のみ

**閉域 = 何とも通信しない、ではありません。必要な AWS サービスへの経路だけを明示的に作る設計です。**

## Important limitations

- このリポジトリは LT / 学習用です。Production のセキュリティ基準を自動的に満たすものではありません。
- `terraform plan` のコードだけで実環境の閉域性を完全証明できるわけではありません。実環境の Route / ENI / SG / NACL / DNS / Organization policy 等も監査対象になり得ます。
- GGUF モデルのライセンス・再配布条件はモデルごとに確認してください。
- GPU ドライバを閉域で導入する場合は、ドライバや依存 RPM も offline bundle に含める設計が必要です。このサンプルは CPU で動かせる最小構成を基本にしています。

## Repository layout

```text
.
├── agent/
│   ├── agent.py
│   ├── tools.py
│   └── tests/
├── infra/
│   ├── network.tf
│   ├── endpoints.tf
│   ├── iam.tf
│   ├── compute.tf
│   ├── storage.tf
│   └── ...
├── scripts/
│   ├── prepare_bundle.sh
│   └── upload_bundle.sh
└── docs/
    └── final-mission.md
```
