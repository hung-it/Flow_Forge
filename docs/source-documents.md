# Document versions and authority

Updated 2026-11-15.

The repository is the maintained source of truth, in this order:
1. Implemented code/migrations and passing verification establish current behavior.
2. docs/architecture and docs/adr define accepted target contracts; planned sections remain explicit.
3. tasks/plan.md is **plan v2**, with daily progress, evidence, pending work and the 10-week roadmap.
4. tasks/todo.md is the short acceptance checklist, linked to the plan for details.

Original files outside the repository:
- FlowForge_Technical_Design_and_Roadmap.md: historical proposal v1, 9-week FlowForge scope.
- Roadmap_FlowForge_API_Contract_Guardian_AWS_Optional.docx: historical combined 18-week proposal.

Those files remain background material, not implementation status. The Word file is preserved
unchanged; this catalog records its historical status. Current accepted changes: 10-week FlowForge,
19 weeks combined, key/handler/edges DAG v1, continue-independent-branches failure policy,
immediate logical cancellation, scheduler including recovery, and same-generation queued redrive.
The original files are outside the Git repository; fresh clones rely on the maintained repository docs.

After each work session update tasks/plan.md with date, concrete changes, commands/results and
remaining work. Never turn a designed contract into an implemented checkbox without runtime evidence.
