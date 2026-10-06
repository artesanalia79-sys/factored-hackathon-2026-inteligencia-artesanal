# Dispute intake architecture

The root README embeds `dispute-intake.svg`, exported with Archify's **SVG · Auto** option.
The standalone interactive HTML is generated locally with the command below. Open it in a
browser to explore source references, themes and exports. Only the SVG and editable specification
are versioned: the generated HTML exceeds the repository's 500 KB file limit.

`candidate.json` is the editable Archify specification. Its source references are pinned to
repository revision `2763d44a106a0fbddabb7dc58c952b15baff255c`. This is a runtime overview:
the offline data pipeline, evaluation harness and learned router are outside its scope.
Auth, policy and execution logging also access the stores, as documented in
`src/bankagent/api/wiring.py` and `src/bankagent/orchestrator/wiring.py`.

## Regenerate

With Archify installed, run from the repository root, replacing `<archify>` with the installed
skill directory:

```powershell
node <archify>/bin/archify.mjs finalize architecture docs/architecture/candidate.json .archify/architecture-dispute-intake-20261005-194136/dispute-intake.html --repo-root . --quality showcase --json
```

The output path matches `meta.output` in the specification. After a successful run, open the
generated HTML and use **Export → SVG · Auto** to replace the README image.
When updating the architecture, inspect the affected source, update the pinned revision and
source ranges, and rerun validation. Preserve earlier browser evidence with a fresh `--out-dir`
when changing an already validated candidate.

## Validation receipt

Generated with Archify 3.0.1. Specification validation, delivery, strict artifact checks and
real-browser checks passed with no diagnostics. The 1440 × 900 light-theme capture was visually
reviewed: labels are readable, routes are clear and nodes do not overlap.

- Specification SHA-256: `9edcca6cd5078f29e539cba3060ebb34c67e9c6fcd0ea2f43ef81266e8aecbcb`
- HTML SHA-256: `d9290983f8afe9da10e67168d130139968c90df79301ec424165a784bdf55a41`

Full automated receipts and light/dark captures remain in the local `.archify/` working folder.
The SVG is a viewer export; the validation hashes above bind the specification and HTML.
