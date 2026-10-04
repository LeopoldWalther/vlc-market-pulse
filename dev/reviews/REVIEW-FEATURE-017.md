# Review — FEATURE-017: Subdomain-scoped certificates; hand domain ownership to the website repository

**Reviewer:** `@reviewer` · **Date:** 2026-10-04 · **Plan:** [FEATURE-017](../plans/FEATURE-017-subdomain-scoped-certificates.md)
**Verdict:** ⚠️ Changes Recommended

## Summary

The plan is sound, small, and correctly ordered. Phase 1 adds per-environment certificates while the
wildcard is still live. Phase 2 retires the shared stack without touching the shared validation
record. I checked its main factual claims against the code and live DNS, and nearly all hold. Two
are wrong, but neither blocks. **First, there is no CAA record on `leopoldwalther.com` today.**
**Second, the CI-role risk does not apply:** `deploy-lambda.yml` cannot plan these stacks even now,
so applies are already local. Three items need tightening before implementation, and all three are
written into the technical plan: wire CloudFront to the *validation* resource's ARN, verify that the
website state owns `_dcd1388…` before `state rm`, and coordinate the CAA record with the website
repository.

## Strengths

- ✅ **Correct ordering for a live distribution.** The new certificate is issued and validated
  while the wildcard keeps serving. The swap is an in-place `viewer_certificate` update, and the
  wildcard stays as the rollback target until phase 2.
- ✅ **Correct diagnosis of the shared record.** Both `for_each` keys in `shared/dns` resolve to
  `_dcd1388178dbf9136ed514dd4f696783.leopoldwalther.com`. ACM reuses a domain's validation CNAME
  across certificates in the same account. So the website's apex certificate validates through the
  same record, and the plan's `state rm` before `destroy` is the right fix.
- ✅ **Alias records stay put.** `data "aws_route53_zone"` returns the same zone ID that
  `shared/dns` outputs today, so `frontend_a` and `frontend_aaaa` plan as no-ops.
- ✅ **Live state matches the plan.** `openssl` on both subdomains today shows
  `CN=*.leopoldwalther.com`, SAN `*.leopoldwalther.com, leopoldwalther.com`, and an expiry of
  2026-12-21 23:59:59 GMT. `dig` shows `_dcd1388…` resolving to `…acm-validations.aws.`.
- ✅ **Low cost and easy to reverse.** ACM certificates are free, there's no new module, and nothing
  in application code changes.

## Findings

### 🟡 M1 — CloudFront must consume the validation resource's ARN, not the certificate's

- **Problem:** The plan says "pass that certificate's ARN to `module.frontend`". If the implementer
  wires `aws_acm_certificate.frontend.arn`, nothing makes the distribution update wait for
  `aws_acm_certificate_validation`. Terraform can then call `UpdateDistribution` while the
  certificate is still `PENDING_VALIDATION`.
- **Impact:** CloudFront rejects the certificate (`InvalidViewerCertificate`) and the apply fails
  halfway. HTTPS stays up, because the old certificate is kept, but the plan's main safety claim
  ("validated before the distribution switches") would only hold if the timing happens to work out.
- **Recommendation:** `certificate_arn = aws_acm_certificate_validation.frontend.certificate_arn`.
  This is the same pattern `shared/dns/outputs.tf` uses today. It is an acceptance criterion in 17.1
  and 17.3.
- **Effort:** S.

### 🟡 M2 — There is no CAA record today, and a future one must allow Amazon

- **Problem:** The plan's Context says the website repository "has a CAA record restricting
  issuance to Amazon". Querying the authoritative Route 53 nameserver
  (`dig CAA leopoldwalther.com @ns-1342.awsdns-39.org`) and 8.8.8.8 returns no answer. Only the SOA
  comes back. The website FEATURE-004 has probably not applied it yet.
- **Impact:** None today, since without CAA any CA may issue. But CAA climbs the DNS tree, so the
  apex record will govern `vlc-market-pulse*.leopoldwalther.com`. If the website repository adds
  CAA without `amazon.com`, ACM's managed renewal of *these* certificates fails. The only warning is
  an ACM renewal notification about 45 days before expiry.
- **Recommendation:** Correct the Context line (done). Add a coordination note: any CAA the website
  repository publishes on the apex must include `0 issue "amazon.com"`. Run `dig CAA` as a
  pre-check in 17.2 and 17.4.
- **Effort:** S.

### 🟡 M3 — Verify the website state owns `_dcd1388…` before `terraform state rm`

- **Problem:** `state rm` makes the record unmanaged *here*. The plan assumes the website repository
  already manages it, but this repository can't see that. The dependency section also says the
  website repository "can apply before or after".
- **Impact:** If the website hasn't applied yet, the record is orphaned. It isn't deleted, so
  nothing breaks, but no state owns it until the website imports it, and a later `allow_overwrite`
  or import in the website repository goes unreviewed.
- **Recommendation:** Add a precondition to 17.5: `terraform state list` in the website repository
  shows the record. If it doesn't, either wait for the website apply or hand the record over
  explicitly. The website repository then imports it with
  `terraform import aws_route53_record.<name> <ZONEID>__dcd1388178dbf9136ed514dd4f696783.leopoldwalther.com_CNAME`.
  The `dig` check after destroy still applies.
- **Effort:** S.

### 🟡 M4 — The CI-role risk does not apply; keep applies local and add no grants

- **Problem:** The plan proposes adding Route 53 and ACM read grants to `github-actions-deploy` so
  `deploy-lambda.yml` can plan. That role (`shared/github-oidc/main.tf`) only allows S3
  put/list on the frontend buckets, `cloudfront:CreateInvalidation`, `lambda:UpdateFunctionCode`, and
  read access to the state bucket. It already cannot refresh the environment stacks: there's no
  `lambda:GetFunction`, `iam:GetRole`, `cloudfront:GetDistribution`, `secretsmanager:Describe*`,
  `states:Describe*`, or similar. Separately, `deploy-lambda.yml` writes `lvw_api_key` and the
  other secrets into `secrets.tfvars`, but the stacks declare `idealista_api_key_lvw` and so on.
  `terraform plan` would fail or prompt in either case.
- **Impact:** Adding four Route 53/ACM actions wouldn't make CI work. It would only widen a deploy
  role. `deploy-frontend.yml` runs only `terraform output`, and the state read it already has
  covers that.
- **Recommendation:** Resolve the open question as "applies stay local, as they have since
  FEATURE-005". Make no IAM change in this feature. Fixing `deploy-lambda.yml`'s Terraform path
  belongs in its own feature (see L4).
- **Effort:** S (it removes work).

### 🟢 L1 — Reword the "no state references the apex" success criterion

- **Suggestion:** `data.aws_route53_zone` stores `name = "leopoldwalther.com"` in each environment
  state, so the criterion as written can't be met. Reword it to "No Terraform state in this
  repository **manages** a resource outside the two subdomains and their validation records".
  Done in the plan.
- **Why:** It keeps the success criteria verifiable.

### 🟢 L2 — Record the full wildcard ARN for rollback, and wait for deployment before `openssl`

- **Suggestion:** After 17.1, the `terraform_remote_state` block is gone, so rolling back means
  pasting the wildcard ARN in literally. The plan truncates it (`117fab20-…`). Record the full ARN
  in the progress log in 17.2. Run the `openssl` check only after
  `aws cloudfront wait distribution-deployed --id <id>`, because edges serve the old certificate for
  a few minutes.
- **Why:** It makes rollback a 2-minute edit and avoids a false failure on the check.

### 🟢 L3 — Comments in the environment stacks are also stale

- **Suggestion:** `environments/dev/main.tf:9` and `:123-141`, and `environments/prod/main.tf:114` and
  `:121-139`, describe the wildcard and the remote state. Update them in the same task as the code
  (17.1 and 17.3), not in the docs task. `shared/dns` is not in the `terraform-validate.yml` matrix,
  so "remove it from the workflow if listed" is a no-op. `fmt -check -recursive` simply stops seeing
  the directory.
- **Why:** It keeps stale comments out of reviewed code.

### 🟢 L4 — Follow-ups found during review (out of scope)

- **`deploy-lambda.yml` cannot run Terraform** (M4): the role lacks permissions and the variable
  names don't match. Either fix both or reduce the workflow to `aws lambda update-function-code`.
  It needs its own plan.
- **`dev/tools/validate_workflow.py` silently skips 10 of 14 technical plans.** Its regexes expect
  `for_feature` at 4-space indent, which only matches files wrapped in `technical_plan:` (002–005).
  Plans 006–014 pass the check without being checked. This feature's technical plan uses the
  wrapped shape, so the checker does parse it.

## Alternatives considered

- **Certificate inside `modules/frontend`** (the module takes `zone_id` + `domain_name` and declares
  `configuration_aliases = [aws.us_east_1]`). This removes about 30 duplicated lines between dev and
  prod. Trade-off: provider passing gets added to a module that is otherwise single-region, and
  certificate lifetime gets tied to the distribution. Verdict: keep the plan. The environment stacks
  already own their Route 53 records, so the certificate and its validation record sit naturally
  beside them.
- **`removed { lifecycle { destroy = false } }` blocks instead of `terraform state rm`**
  (Terraform ≥ 1.7, the repository pins 1.14.3). These are declarative and visible in the PR diff.
  Trade-off: the whole stack is deleted right afterwards, so the block would exist for a single
  apply. Verdict: `state rm` is fine. Paste its output into the progress log as evidence.
- **One shared certificate covering both subdomains** (SANs for prod and dev) in a slimmed-down
  `shared/dns`. Trade-off: it keeps a shared stack and cross-environment coupling, which is the
  thing this feature removes. Verdict: rejected.

## Risks

| Risk | Likelihood | Impact | Severity | Mitigation |
| --- | --- | --- | --- | --- |
| Distribution updated before the certificate is issued | Med (without M1) | Low (apply fails, HTTPS stays) | 🟡 | M1: consume `aws_acm_certificate_validation.*.certificate_arn` |
| Future apex CAA without Amazon blocks renewal | Low | High (cert expiry in ~13 months) | 🟡 | M2: coordination note + `dig CAA` pre-check |
| `_dcd1388…` deleted by `destroy` | Low | High (website cert renewal) | 🟡 | `state rm` both keys first; review that the destroy plan shows exactly 2 resources; `dig` afterwards |
| `_dcd1388…` orphaned (website hasn't applied) | Med | Low | 🟢 | M3: check website state first, or hand off via import |
| `InUseBy` not empty at phase 2 (another distribution) | Low | High | 🟢 | `aws acm describe-certificate` gate in 17.5 |
| Old certificate cached at edges during check | High | None | 🟢 | L2: `aws cloudfront wait distribution-deployed` before `openssl` |

## Effort check

- **Plan estimate:** S (~5 h)
- **Reviewer estimate:** S (~4.25 h), confidence High
- **Why it differs:** M4 removes the IAM work. Most of the time goes to waiting on ACM validation and
  CloudFront deployment (about 5–15 min per environment), not to writing code.

## Reuse & conflicts

- **Reuse:** the resource shapes from `infrastructure/shared/dns/main.tf`: the `for_each` over
  `domain_validation_options`, `create_before_destroy`, and the validation waiter on
  `aws.us_east_1`. Also `infrastructure/shared/dns/providers.tf` for the alias block.
- **Coordinate with:** `LeopoldWalther/leopoldwalther-website` FEATURE-004. Its phase 3 (dropping
  `prevent_destroy`) waits for 17.5. Its CAA record must allow `amazon.com` (M2). Its state must own
  `_dcd1388…` before 17.5 (M3).

## Approval criteria

- **Blockers (must fix):** none.
- **Recommended:** M1–M4, all encoded in the technical plan's acceptance criteria.
- **Optional:** L1–L3 applied; L4 left as follow-ups.

## What changed (this review's edits)

- Created `dev/plans/technical/FEATURE-017-technical-plan.yaml` (7 tasks, 4.25 h).
- `dev/plans/FEATURE-017-subdomain-scoped-certificates.md`: corrected the CAA claim in Context,
  resolved the CI-role open question (M4), reworded the "references the apex" success criterion
  (L1), and added a progress-log entry.

## Next step

`@implementer Implement FEATURE-017`. Start with 17.1 (dev). Before 17.5, confirm with the website
repository that it owns `_dcd1388…`.

---

### Post-implementation notes
*Filled in after the task ships.*

- **Worked well:** <…>
- **Missed in review:** <…>
- **Estimated vs. actual:** 4.25 h vs. <Y>
