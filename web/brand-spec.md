# Payment Desk design system

Reference: https://github.com/ConardLi/garden-skills/tree/main/skills/web-design-engineer

Design direction approved by Nixon: graphite navigation, white workspace,
emerald actions, sans-serif headings, compact records, clear request/report split.

Mode: Redesign / overhaul of presentation; preserve application behaviour.
Audience: payment operations staff reviewing evidence and exceptions.
Design Read dials: variance 4, motion 2, density 8, assets 2, fidelity 6.
Narrative role: operational data register and evidence investigation.
Viewing distance: laptop and phone. Temperature: quiet and authoritative.

Palette: sidebar #202724, ground #f6f8f7, surface white, ink #202523,
secondary #68716c, line #e4e8e5, action #087f5b. Semantic amber/red statuses.
Typography: Geist 400–650; monospace reserved for identifiers in full build.
Spacing: 4/8/12/16/24/32. Radius: 5–8px. Shadows: dialogs only.
Motion: state feedback 150ms; reduced-motion path in full build.
Assets: icon.svg copied unchanged from existing web/static/icon.svg.

Preserve: routes, element IDs, form contracts, tenant scope, access code,
payment details, filtering/sorting/CSV, investigation history and report actions.
Improve: readability, grouping, fewer oversized cards, useful information density.
Remove: excessive uppercase eyebrows, serif heading drift, decorative section numbers.
Highest risk: accidentally changing authentication/session handling during visual work.
Rollback: original HTML, CSS and JavaScript are saved under the redesign staging folder in the resume workspace. The approved v0 is retained separately.

Implementation: the existing application retains all 83 element IDs and all investigation templates. The full redesign covers loading, empty, error, result, dialog, disabled and focus states, plus responsive layouts and reduced motion. Geist regular and semibold are bundled in web/static/fonts with OFL.txt; no third-party font request is required. The original icon is unchanged. No product features, fabricated business statistics or security claims were added.

Validation: 26 existing web/API tests passed; node syntax check passed; static references, unique IDs and script references checked. Frontend-only release through the existing S3/CloudFront publishing command; no backend, IAM or tenant changes.
