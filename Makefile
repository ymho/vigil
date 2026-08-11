.PHONY: fmt validate test init plan apply destroy

fmt:
	terraform -chdir=infra fmt -recursive

init:
	terraform -chdir=infra init

validate: init
	terraform -chdir=infra validate

test:
	python3 -m unittest discover -s agent/tests -v

plan:
	terraform -chdir=infra plan

apply:
	terraform -chdir=infra apply

destroy:
	terraform -chdir=infra destroy
