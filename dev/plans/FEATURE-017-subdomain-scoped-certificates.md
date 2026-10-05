# FEATURE-017 — Subdomain-scoped certificates; hand domain ownership to the website repository

**Status:** 🟡 In progress · **Effort:** S (~5h) · **Priority:** Medium
**Branch root:** `feature/subdomain-scoped-certificates` · **Created:** 2026-10-04 · **Updated:** 2026-10-05

> Authored by `@architect`. Reviewed by `@reviewer` (see `dev/reviews/REVIEW-FEATURE-017.md`).
> Implemented by `@implementer` from `dev/plans/technical/FEATURE-017-technical-plan.yaml`.

## Objective

Each environment issues and owns a certificate for its own subdomain only, and the
`infrastructure/shared/dns` stack is retired. Afterwards this repository manages nothing on
`leopoldwalther.com` except `vlc-market-pulse.leopoldwalther.com`,
`vlc-market-pulse-dev.leopoldwalther.com`, and their validation records. The domain itself belongs
to `LeopoldWalther/leopoldwalther-website` (its FEATURE-004).

## Context

- **Current state** (verified against account `063096286047` on 2026-10-04):
  - `infrastructure/shared/dns` issues one ACM certificate, `*.leopoldwalther.com` with the apex
    `leopoldwalther.com` as a SAN (`arn:aws:acm:us-east-1:063096286047:certificate/117fab20-…`),
    expiring 2026-12-22 and eligible for auto-renewal. Both CloudFront distributions use it
    (`E12XQJO7PC0L55`, `E2N85NF7XDZF9F`).
  - Its state holds `data.aws_route53_zone.root`, `aws_acm_certificate.wildcard`,
    `aws_acm_certificate_validation.wildcard`, and `aws_route53_record.cert_validation` with two keys
    (`*.leopoldwalther.com`, `leopoldwalther.com`) that both point at the **same** record
    `_dcd1388178dbf9136ed514dd4f696783.leopoldwalther.com`.
  - `environments/{dev,prod}/main.tf` read `zone_id` and `certificate_arn` from that stack via
    `terraform_remote_state`.
- **Problem:** the certificate covers the apex and every subdomain — names this project does not
  publish. Its apex validation record is the same record the website repository creates for its own
  certificate, so two states manage one DNS record, and destroying either side can break the other's
  renewal. The website repository has to protect itself with `prevent_destroy` because of it.
- **Constraints:** prod is live; the change must not interrupt HTTPS. ACM certificates for
  CloudFront must be in `us-east-1`. The apex has **no CAA record** today (checked against the
  authoritative nameserver on 2026-10-04). The website repository plans one restricted to Amazon.
  Because CAA applies to subdomains too, it must include `0 issue "amazon.com"` or ACM renewal of
  these certificates fails (review M2).

## Dependencies

- **Needs:** none. The website repository can apply before or after.
- **Unblocks:** website FEATURE-004 phase 3 — dropping `prevent_destroy` on its apex validation
  record once no certificate here depends on it.

## Approach

### Phase 1 — Per-environment certificate (dev first)

- [ ] **Validation-first.** In `environments/dev`, add an `aws.us_east_1` provider alias, a
      `data "aws_route53_zone"` lookup, and an `aws_acm_certificate` for `local.frontend_domain` only
      (DNS validation, `create_before_destroy`), its validation record, and
      `aws_acm_certificate_validation`. Pass that certificate's ARN to `module.frontend` and use the
      data-source zone ID for the alias records. Remove the `terraform_remote_state "dns"` block.
      Observable outcome: `terraform plan` creates one certificate, one validation record (a name that
      is *not* `_dcd1388…`), one validation, and updates the distribution's `viewer_certificate` in
      place. No replace of the distribution, no change to the A/AAAA records.
- [ ] **Validation-first, after apply.** `openssl s_client -connect vlc-market-pulse-dev.leopoldwalther.com:443 -servername vlc-market-pulse-dev.leopoldwalther.com </dev/null | openssl x509 -noout -subject -ext subjectAltName`
      shows only `vlc-market-pulse-dev.leopoldwalther.com`.
- [ ] **Validation-first.** Same change in `environments/prod`, applied and verified the same way.

### Phase 2 — Retire `infrastructure/shared/dns`

- [ ] **Validation-first.** Confirm with `aws acm describe-certificate` that the wildcard
      certificate's `InUseBy` is empty.
- [ ] **Validation-first.** Remove both validation-record instances from state **without deleting
      the record**, because the website certificate validates through the same record:
      `terraform state rm 'aws_route53_record.cert_validation["*.leopoldwalther.com"]' 'aws_route53_record.cert_validation["leopoldwalther.com"]'`.
      Then `terraform destroy` the stack. Observable outcome: the plan destroys only the certificate
      and the validation resource, and `_dcd1388….leopoldwalther.com` still resolves afterwards.
- [ ] **Validation-first.** Delete `infrastructure/shared/dns/`, remove it from
      `.github/workflows/terraform-validate.yml` if listed, and clean up the leftover state object
      `vlc-state/shared/dns/terraform.tfstate`.

### Phase 3 — Documentation

- [ ] Update `documentation/FRONTEND_LAYER.md`, `documentation/PROD_PROMOTION_RUNBOOK.md`,
      `README.md`, and the `certificate_arn` description in `modules/frontend/variables.tf`. State that
      `leopoldwalther.com` is owned by the website repository, and that this repository manages only
      its two subdomains.

## Files

- **Change:** `infrastructure/environments/dev/{main,providers}.tf`,
  `infrastructure/environments/prod/{main,providers}.tf` — own certificate, zone lookup, no remote
  state.
- **Change:** `infrastructure/modules/frontend/variables.tf` — description only.
- **Delete:** `infrastructure/shared/dns/` — after phase 2.
- **Change:** `documentation/FRONTEND_LAYER.md`, `documentation/PROD_PROMOTION_RUNBOOK.md`,
  `README.md`, `dev/plans/README.md`.
- **Tests:** none in code. Infrastructure is verified by `terraform fmt -check`,
  `terraform validate`, the reviewed `terraform plan`, and the `openssl` check.

## Test strategy

- **Unit:** N/A — no application code changes.
- **Integration:** `terraform plan` per environment shows an in-place `viewer_certificate` update
  and no distribution replacement. After apply, the `openssl` check, and the dashboard loads over
  HTTPS on both subdomains.
- **Manual:** after phase 2, `dig CNAME _dcd1388178dbf9136ed514dd4f696783.leopoldwalther.com` still
  answers.

## Estimated monthly cloud cost

$0/month — ACM public certificates are free. One certificate is replaced by two; no other resources
change.

- **Cost drivers & cheaper alternatives:** none.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] Both distributions serve a certificate covering only their own subdomain
- [ ] `infrastructure/shared/dns` and its state are gone, and the wildcard certificate is deleted
- [ ] `_dcd1388178dbf9136ed514dd4f696783.leopoldwalther.com` was not deleted
- [ ] No Terraform state in this repository manages a resource outside the two subdomains and their
      validation records (the read-only zone data source is fine)
- [ ] `terraform fmt -check -recursive` and `terraform validate` pass for every stack

## Open questions & risks

- **Risk — the CI role cannot plan the new resources.** `deploy-lambda.yml` runs `terraform plan`
  and `apply` in `environments/<env>`. Once those stacks read a Route 53 zone and manage an ACM
  certificate, that role needs `route53:GetHostedZone`, `route53:ListHostedZones`,
  `route53:ListResourceRecordSets`, `route53:GetChange`, `acm:DescribeCertificate`, and
  `acm:ListTagsForCertificate` (us-east-1). **Resolved by review (M4):** the role already cannot
  plan these stacks, and `deploy-lambda.yml` has mismatched variable names. Applies stay local and
  no IAM change is made here. Fixing CI Terraform is a separate follow-up.
- **Risk — certificate swap on a live distribution.** *Mitigation:* the new certificate is validated
  before the distribution switches (`aws_acm_certificate_validation`), and the switch is an in-place
  update. Rollback: point `certificate_arn` back to the wildcard, which stays until phase 2.
- **Risk — destroying the stack deletes the shared validation record.** *Mitigation:* the explicit
  `terraform state rm` step before `destroy`, plus the `dig` check.

## Progress log

- **2026-10-04** — Plan written after the user decided that `leopoldwalther.com` (and the new
  `leopoldwalther.de`) are managed in the website repository. Evidence gathered read-only from ACM,
  Route 53, and the `shared/dns` state.
- **2026-10-04** — Reviewed by `@reviewer` (⚠️ Changes Recommended, no blockers). The review
  corrected the CAA claim, resolved the CI-role risk (applies stay local), and reworded one success
  criterion. The technical plan has 7 tasks (~4.25 h). Before 17.5, the website repository must own
  `_dcd1388…`.
- **2026-10-05** — 17.1/17.2 done (dev). CAA pre-check: no CAA record on the apex (authoritative
  NS). Rollback target (wildcard): `arn:aws:acm:us-east-1:063096286047:certificate/117fab20-862b-4f89-9737-39e1f6b7e3ec`.
  New dev certificate: `arn:aws:acm:us-east-1:063096286047:certificate/e2228941-6c76-49eb-9d98-9d4068ba7568`,
  validated through `_07d8ace9c4fea98b552e683b1e4dccd8.vlc-market-pulse-dev.leopoldwalther.com`.
  Applied locally: 3 added, 1 changed (distribution `E2N85NF7XDZF9F`, in place). After
  `distribution-deployed`, `openssl` shows subject and SAN `vlc-market-pulse-dev.leopoldwalther.com`
  only (valid to 2027-04-19); `curl` returns 200 with a verified chain.
- **2026-10-05** — 17.3/17.4 done (prod). CAA pre-check unchanged (none). New prod certificate:
  `arn:aws:acm:us-east-1:063096286047:certificate/d91a8914-8437-4980-88cd-9d8e14648d92`, validated
  through `_8f36b2bbd5a1b210ebe7459f031cea7d.vlc-market-pulse.leopoldwalther.com`. Applied locally:
  3 added, 1 changed (distribution `E12XQJO7PC0L55`, in place). After `distribution-deployed`,
  `openssl` shows subject and SAN `vlc-market-pulse.leopoldwalther.com` only (valid to 2027-04-20);
  `curl` returns 200 with a verified chain.
