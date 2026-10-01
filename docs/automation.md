# Automated work

## Authorization

GitHub Issues are the human inbox. An open issue is a request, not permission to implement it. Exact label agent:ready authorizes an issue for the local Agent Flow dispatcher. This seed roadmap was explicitly authorized by the repository owner for staged implementation. New issues and follow-ups remain unlabelled until separately authorized.

The dispatcher builds Beads records from issue URLs and uses blocking dependency edges as its executable ready graph. Keep GitHub dependency references under the issue's Dependencies heading. GitHub-to-Beads sync is an explicit operation; no automatic synchronization is assumed.

## Routing

Codiv OpenJev routes issue size, ambiguity, review need, and durable-spec need. Pi coordinates through Herdr. Claude Opus implements in isolated issue worktrees. Codex reviews planning and review needs adaptively. Do not use ccmux.

## Pull request policy

The dispatcher may merge only after it verifies an explicit merge-ready report, exact pushed head, required checks, independent review where routed, and completed Beads dependencies. Workers do not merge, bypass checks, force-push, or suppress unresolved risks. The owner may pause or remove authorization at any time.

## Delegated decisions

Exact agent:ready delegates that issue's ordinary design, implementation, and spec-reconciliation decisions to the worker, and the delegation survives the dispatcher's transition to agent:running. The worker takes the conservative, reversible option that fits the issue goal, the accepted specs, and the allowed files, records the choice and its rationale in the pull request — and on the issue when acceptance criteria changed — and continues. It does not ask the owner to choose among ordinary alternatives. Work found outside the issue's scope is narrowed out of the issue and raised as an unlabelled follow-up, never absorbed. The delegation covers one authorized issue: it never authorizes a new issue, a label, or an edit outside that issue's allowed files, and it does not relax exact-ready dispatch or the merge gates above.

## Human decisions

Escalate when narrowing scope leaves no coherent in-scope option, or when the work needs authority, credentials, or permissions the owner has not delegated — including decisions the specs reserve to an accepted change proposal. Then record concrete options and impact on the GitHub issue and move work to needs-human. Do not invent a product decision to keep the queue moving. Do not convert ordinary ambiguity or resolvable issue/spec tension into needs-human either.
