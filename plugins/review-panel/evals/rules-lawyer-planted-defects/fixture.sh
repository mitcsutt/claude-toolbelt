#!/usr/bin/env bash
# Seeds a small Terraform repo whose agent instructions carry planted defects.
# The ground truth for each one is in case.yaml (one grader per defect).
set -euo pipefail

mkdir -p .claude/skills/tf-preview .claude/hooks .claude/agents envs/preview envs/prod modules/network scripts docs

cat > CLAUDE.md <<'EOF'
# acme-infra

Terraform for Acme's per-branch preview environments and production.

## Project structure

- `modules/` reusable modules
- `envs/preview/` per-branch preview stacks
- `envs/prod/` production
- `scripts/` helper scripts
- `docs/` architecture notes

## Rules

- Write clean, maintainable code and handle edge cases.
- IMPORTANT: ALWAYS run preview plans in the `staging` workspace.
- Run `make lint` before every commit.
- Only change files inside the directory you were asked to work on. If the change needs files anywhere else, ask first.
- For incident procedures, follow @docs/runbook.md
EOF

cat > .claude/skills/tf-preview/SKILL.md <<'EOF'
---
name: tf-preview
description: Runs terraform init, then terraform plan, then posts the plan to the PR, then cleans up when the PR closes. I can help with anything Terraform.
---

# Terraform previews

Terraform is an infrastructure-as-code tool made by HashiCorp. You describe cloud
resources in HCL files, Terraform works out the difference between that description
and what exists, and then creates, updates or deletes resources to match.

## Steps

1. `cd envs/preview && terraform init`
2. Select the workspace `preview-<branch>`, creating it with `terraform workspace new preview-<branch>` if it doesn't exist.
3. Run `terraform fmt -check` to format the files before planning.
4. Skip the plan if the change is small.
5. Run `terraform plan -out=plan.tfplan`, then post it to the PR with `bash scripts/post-plan.sh plan.tfplan`.
6. If you see "Error acquiring the state lock", run `terraform force-unlock -force <LOCK_ID>` and retry.
7. When the PR closes, run `terraform plan -destroy` to tear the preview environment down.

For cost estimates of a preview, see references/costs.md.
EOF

cat > .claude/settings.json <<'EOF'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "bash",
        "hooks": [
          { "type": "command", "command": "bash .claude/hooks/block-apply.sh" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash .claude/hooks/require-plan.sh" }
        ]
      }
    ]
  }
}
EOF

cat > .claude/hooks/block-apply.sh <<'EOF'
#!/usr/bin/env bash
# Stop the agent from applying Terraform changes.
cmd=$(jq -r '.tool_input.command')
if echo "$cmd" | grep -q 'terraform apply'; then
  echo "Blocked." >&2
  exit 1
fi
exit 0
EOF

cat > .claude/hooks/require-plan.sh <<'EOF'
#!/usr/bin/env bash
# Don't let the agent finish without a saved preview plan.
if [ ! -f envs/preview/plan.tfplan ]; then
  echo '{"decision": "block", "reason": "Create a plan first."}'
fi
exit 0
EOF

cat > .claude/agents/tf-reviewer.md <<'EOF'
---
name: tf-reviewer
description: Reviews Terraform.
---

You are a read-only reviewer. Never modify files. Point out risky changes in Terraform diffs.
EOF

printf '.PHONY: plan fmt\nplan:\n\tcd envs/preview && terraform plan\nfmt:\n\tterraform fmt -recursive\n' > Makefile

cat > envs/preview/main.tf <<'EOF'
module "network" {
  source = "../../modules/network"
  name   = "preview-${terraform.workspace}"
}
EOF

cat > envs/prod/main.tf <<'EOF'
module "network" {
  source = "../../modules/network"
  name   = "prod"
}
EOF

cat > modules/network/main.tf <<'EOF'
variable "name" {
  type = string
}

output "name" {
  value = var.name
}
EOF

cat > scripts/post-plan.sh <<'EOF'
#!/usr/bin/env bash
# Posts a rendered plan as a PR comment.
set -euo pipefail
terraform show -no-color "$1" | gh pr comment --body-file -
EOF

cat > docs/architecture.md <<'EOF'
# Architecture

Each pull request gets its own preview stack in a workspace named after the branch.
EOF

git init -q -b main
git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "acme-infra: initial agent setup"
