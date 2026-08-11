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
- Ollama runtime と GGUF model は事前に S3 へ持ち込み
- Agent / Tools / Terraform は軽量な App bundle として分離
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
  +---- S3 Gateway Endpoint ---- S3
                                  |
                                  |- runtime/
                                  |    |- ollama-root.tar.gz
                                  |    `- model.gguf
                                  |
                                  `- app/
                                       `- vigil-app.tar.gz

Internet egress: NONE
```

## 0. Prerequisites

ローカル側:

- AWS CLI v2
- Terraform
- Git
- Bash / WSL2 / Linux 推奨
- `curl`
- `tar`
- `zstd` または `tar --zstd`
- Python 3

AWS 側:

- EC2 / VPC / IAM / S3 / Systems Manager を作成できる権限
- 使用リージョンで対象 EC2 インスタンスタイプが利用可能であること

モデルは Git に含めません。

Tool Calling に対応した GGUF モデルを別途用意してください。

例:

```text
~/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf
```

---

## 1. Infrastructure

Terraform で閉域AWS環境を構築します。

```bash
cd infra

cp terraform.tfvars.example terraform.tfvars

terraform init
terraform plan
terraform apply
```

出力値を確認します。

```bash
terraform output
```

リポジトリルートから確認する場合:

```bash
terraform -chdir=infra output
```

この時点では EC2 は起動しますが、Ollama / model / Agent はまだ配備されません。

---

## 2. Prepare the Ollama runtime

Internet に接続できるローカル端末で実行します。

Ollama runtime は重いため、原則として初回だけ準備します。

```bash
./scripts/prepare_runtime.sh
```

生成物:

```text
dist/
`- runtime/
   `- ollama-root.tar.gz
```

このファイルには閉域EC2へ持ち込む Ollama Linux runtime が含まれます。

---

## 3. Upload runtime and model to S3

Terraform output から artifact bucket 名を取得します。

```bash
BUCKET=$(terraform -chdir=infra output -raw artifact_bucket_name)
```

Ollama runtime と GGUF model をアップロードします。

```bash
./scripts/upload_runtime.sh \
  "$BUCKET" \
  ~/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf
```

S3 上では次のように配置されます。

```text
runtime/
|- ollama-root.tar.gz
`- model.gguf
```

Runtime と model は重いため、通常は Agent コードを変更しても再アップロードしません。

アップロード元の PC は通常 Internet 経由で S3 API を利用します。

閉域なのは **EC2 側の VPC** です。

---

## 4. Package the VIGIL application

Agent / Tools / Terraform は runtime や model と分離し、軽量な App bundle としてまとめます。

```bash
./scripts/package_app.sh
```

生成物:

```text
dist/
`- app/
   `- vigil-app.tar.gz
```

App bundle には主に以下が含まれます。

- `agent/`
  - Agent Runtime
  - Tools
  - unit tests
- `infra/`
  - Agent が自分の Terraform 構成を調査するための workspace

---

## 5. Upload the application to S3

```bash
./scripts/upload_app.sh "$BUCKET"
```

S3 上では次のように配置されます。

```text
app/
`- vigil-app.tar.gz
```

以後、`agent.py` や `tools.py` を変更した場合は、runtime や model を再転送する必要はありません。

---

## 6. Bootstrap the private EC2 instance

Session Manager で閉域EC2へ接続します。

```bash
INSTANCE_ID=$(terraform -chdir=infra output -raw instance_id)

aws ssm start-session \
  --target "$INSTANCE_ID"
```

EC2 上で初回bootstrapを実行します。

```bash
sudo /usr/local/bin/vigil-bootstrap
```

Bootstrap では、S3 Gateway Endpoint 経由で以下を取得します。

```text
runtime/ollama-root.tar.gz
runtime/model.gguf
app/vigil-app.tar.gz
```

その後、

1. Ollama runtime をインストール
2. Ollama service を起動
3. GGUF model を Ollama へ import
4. VIGIL App を `/opt/vigil/repo` へ配置
5. Agent launcher を配置

します。

確認:

```bash
systemctl status ollama
```

```bash
ollama list
```

```bash
/opt/vigil/bin/vigil-agent --help
```

---

## 7. Update the Agent

`agent.py`、`tools.py`、Terraform workspace などの App 側だけを変更した場合、EC2 の作り直しや model の再転送は不要です。

ローカル側:

```bash
./scripts/package_app.sh
./scripts/upload_app.sh "$BUCKET"
```

EC2 側:

```bash
sudo /usr/local/bin/vigil-update-app
```

更新対象は基本的に、

```text
/opt/vigil/repo
```

だけです。

開発中は次のループになります。

```text
agent.py / tools.py を修正
        |
        v
package_app.sh
        |
        v
upload_app.sh
        |
        v
S3 app/vigil-app.tar.gz
        |
        v
vigil-update-app
        |
        v
Agent再実行
```

---

## 8. Run the Agent

例えば、Agent 自身に Terraform と実環境を調査させます。

```bash
/opt/vigil/bin/vigil-agent \
  --workspace /opt/vigil/repo/infra \
  "このTerraform環境からInternetへ出られる経路が存在するか調査し、構成と実通信の両面から根拠を示してください。"
```

想定される Agent Loop:

```text
User
 |
 v
LLM
 |
 | next action
 v
Agent Runtime
 |
 | execute
 v
Tool
 |
 | result
 v
Agent Runtime
 |
 | result
 v
LLM
 |
 | next action
 v
...
```

実行例:

```text
LLM -> list_files -> result
LLM -> grep -> result
LLM -> read_file -> result
LLM -> check_internet -> result
LLM -> check_s3 -> result
LLM -> final answer
```

LLM 自身が Python 関数を直接実行しているわけではありません。

LLM が利用する Tool を判断し、Agent Runtime が実際の Tool を実行して、その結果を LLM へ返します。

---

## 9. Run tests

Tool の unit tests を実行します。

```bash
PYTHONPATH=agent \
python3 -m unittest discover \
  -s agent/tests \
  -v
```

Makefile を使う場合:

```bash
make test
```

Terraform:

```bash
make fmt
make validate
```

または:

```bash
terraform -chdir=infra fmt -recursive
terraform -chdir=infra validate
```

Shell script の構文確認:

```bash
bash -n scripts/*.sh
```

---

## Security model

このサンプルでは、次を意図しています。

- EC2 に Public IP を付与しない
- NAT Gateway を作らない
- Internet Gateway を作らない
- `0.0.0.0/0` の Internet route を作らない
- SSH 22/tcp を公開しない
- 外部 LLM API を利用しない
- SSM Agent から AWS Systems Manager への通信は Interface VPC Endpoint 経由
- S3 への通信は Gateway VPC Endpoint 経由
- Interface VPC Endpoint の Security Group は EC2 の Security Group からの 443/TCP のみに限定
- EC2 の IAM Role は必要な S3 object と Systems Manager 利用権限に限定
- Agent の runtime check は allow-list 済みの専用 Tool のみ
- 任意 shell command を Tool としてそのまま公開しない
- Tool の file access は指定 workspace 配下に限定

**閉域 = 何とも通信しない、ではありません。必要な AWS サービスへの経路だけを明示的に作る設計です。**

また、VPC Endpoint は「IPアドレスが秘密だから安全」という仕組みではありません。

EC2 自身を Internet へ公開せず、

```text
Private IP
+ VPC Endpoint
+ Security Group
+ IAM
```

を組み合わせて、必要な AWS サービスへの通信だけを許可します。

---

## Artifact model

VIGIL では、重い artifact と頻繁に変更する artifact を分離しています。

```text
S3
|
|- runtime/
|  |- ollama-root.tar.gz
|  `- model.gguf
|
`- app/
   `- vigil-app.tar.gz
```

### Runtime / Model

更新頻度:

```text
低い
```

含むもの:

- Ollama runtime
- GGUF model

基本的には初回bootstrap時のみ使用します。

### App

更新頻度:

```text
高い
```

含むもの:

- Agent Runtime
- Tools
- Tests
- Terraform workspace

Agent 開発中は App bundle のみ更新します。

---

## Important limitations

- このリポジトリは LT / 学習用です。Production のセキュリティ基準を自動的に満たすものではありません。
- Terraform コードだけで実環境の閉域性を完全に証明できるわけではありません。
- 実環境では Route Table / ENI / Security Group / NACL / DNS / IAM / VPC Endpoint Policy / Organization Policy 等も監査対象になり得ます。
- `check_internet` は実通信の補助確認であり、それ単独でネットワーク全体の安全性を証明するものではありません。
- GGUF モデルのライセンス・再配布条件はモデルごとに確認してください。
- GPU を利用する場合、GPU driver や依存 package も閉域へ持ち込める設計が必要です。
- このサンプルは CPU で動かせる最小構成を基本にしています。
- Tool Calling の品質は使用する LLM に依存します。
- Agent に強い Tool 権限を与えるほど、Agent 自体の権限制御も重要になります。

---

## Repository layout

```text
.
├── .github/
│   └── workflows/
│       └── terraform.yml
│
├── agent/
│   ├── agent.py
│   ├── tools.py
│   └── tests/
│       └── test_tools.py
│
├── infra/
│   ├── compute.tf
│   ├── endpoints.tf
│   ├── iam.tf
│   ├── network.tf
│   ├── outputs.tf
│   ├── provider.tf
│   ├── storage.tf
│   ├── user_data.sh.tftpl
│   ├── variables.tf
│   ├── versions.tf
│   └── terraform.tfvars.example
│
├── scripts/
│   ├── prepare_runtime.sh
│   ├── upload_runtime.sh
│   ├── package_app.sh
│   ├── upload_app.sh
│   ├── start_session.sh
│   └── final_mission.sh
│
├── docs/
│   └── final-mission.md
│
├── Makefile
└── README.md
```

## Typical workflow

初回:

```bash
terraform -chdir=infra init
terraform -chdir=infra apply

BUCKET=$(terraform -chdir=infra output -raw artifact_bucket_name)

./scripts/prepare_runtime.sh

./scripts/upload_runtime.sh \
  "$BUCKET" \
  ~/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf

./scripts/package_app.sh
./scripts/upload_app.sh "$BUCKET"

./scripts/start_session.sh
```

EC2:

```bash
sudo /usr/local/bin/vigil-bootstrap
```

Agent 開発時:

```bash
make test

./scripts/package_app.sh
./scripts/upload_app.sh "$BUCKET"
```

EC2:

```bash
sudo /usr/local/bin/vigil-update-app
```

実行:

```bash
/opt/vigil/bin/vigil-agent \
  --workspace /opt/vigil/repo/infra \
  "このTerraform環境からInternetへ出られる経路が存在するか調査してください。"
```

---

## Final Mission

VIGIL の最終ミッションは、構築した Agent 自身に閉域環境を調査させることです。

```text
MISSION 01
区域を隔離する

MISSION 02
閉域への管理経路を確保する

MISSION 03
ローカルLLMを配備する

MISSION 04
LLMをToolを使えるAgentにする

FINAL MISSION
Agent自身に閉域性を調査させる
```

Agent は Terraform を読み、必要な Tool を選択し、実通信を確認して、根拠を示した回答を生成します。
