.PHONY: fmt init validate test plan apply destroy \
        prepare-runtime upload-runtime \
        package-app upload-app

fmt:
	terraform -chdir=infra fmt -recursive

init:
	terraform -chdir=infra init

validate: init
	terraform -chdir=infra validate

test:
	PYTHONPATH=agent python3 -m unittest discover -s agent/tests -v

plan:
	terraform -chdir=infra plan

apply:
	terraform -chdir=infra apply

destroy:
	terraform -chdir=infra destroy

prepare-runtime:
	./scripts/prepare_runtime.sh

upload-runtime:
	@test -n "$(BUCKET)" || (echo "BUCKET is required"; exit 1)
	@test -n "$(MODEL)" || (echo "MODEL is required"; exit 1)
	./scripts/upload_runtime.sh "$(BUCKET)" "$(MODEL)"

package-app:
	./scripts/package_app.sh

upload-app:
	@test -n "$(BUCKET)" || (echo "BUCKET is required"; exit 1)
	./scripts/upload_app.sh "$(BUCKET)"