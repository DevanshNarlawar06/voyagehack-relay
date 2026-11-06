# definitions/ (the extension surface)

Everything here is configuration. Adding a workflow, skill, connector or policy pack should need **no change under `relay/core/`** (PRD sections 9 and 20).

| Folder | Contents |
|---|---|
| `workflows/` | `<name>.v<N>.yaml` workflow DSL files |
| `policies/` | policy packs (`reserved`, `approvals`, `labour_rules`, `rbac`, `retention`) and `tests/` |
| `skills/` | skill manifests |
| `prompts/<skill>/<version>.md` | prompt templates |
| `connectors/` | connector manifests |
| `notices/` | versioned DPDP consent notices |
