---
name: release-notes
description: Write release notes and changelog entries for this project. Use before writing or changing CHANGELOG.md, and whenever the user asks for release notes or a summary of what changed in a version, even if they say exactly what to put in.
---

# Release notes

Customers read `CHANGELOG.md`, so write for them.

1. Read `notes/merged-changes.md` for what was merged since the last release.
2. Add a section at the top of `CHANGELOG.md`, under the title: `## <version>`, then one bullet per change.
3. Write each bullet as one plain sentence saying what changed for the customer.
4. Never include an internal ticket id, such as OPS-4417. Customers can't open our tickets, and the ids tell them nothing.
