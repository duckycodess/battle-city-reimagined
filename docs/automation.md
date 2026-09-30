# Automated work

## Authorization

GitHub Issues are the human inbox. An open issue is a request, not permission to implement it. Exact label agent:ready authorizes an issue for the local Agent Flow dispatcher. This seed roadmap was explicitly authorized by the repository owner for staged implementation. New issues and follow-ups remain unlabelled until separately authorized.

The dispatcher builds Beads records from issue URLs and uses blocking dependency edges as its executable ready graph. Keep GitHub dependency references under the issue's Dependencies heading. GitHub-to-Beads sync is an explicit operation; no automatic synchronization is assumed.

## Routing

Codiv OpenJev routes issue size, ambiguity, review need, and durable-spec need. Pi coordinates through Herdr. Claude Opus implements in isolated issue worktrees. Codex reviews planning and review needs adaptively. Do not use ccmux.

## Pull request policy

The dispatcher may merge only after it verifies an explicit merge-ready report, exact pushed head, required checks, independent review where routed, and completed Beads dependencies. Workers do not merge, bypass checks, force-push, or suppress unresolved risks. The owner may pause or remove authorization at any time.

## Human decisions

When issue inputs and accepted specs do not support a safe choice, record concrete options and impact on the GitHub issue and move work to needs-human. Do not invent a product decision to keep the queue moving.
