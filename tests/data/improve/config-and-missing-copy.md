## How to improve the files under test

Fixed rules, listed with their sources in `docs/improvement-rules.md`; no model wrote this section. What the runs showed is kept apart from what the file's text suggests.

### Team rule

File under test: `kilo.jsonc` · eval file `kilo-jsonc.json`

**What the runs showed**

The template held in all 2 conclusive runs, but no baseline ran, so the runs can't show the file made the difference.

**What the file's text suggests**

The text rules read Markdown instruction files only, so they have nothing to say about `kilo.jsonc`.

### Data 'rules' \# one

File under test: `.kilo/rules/data.md` · eval file `rules/data.json`

**What the runs showed**

The template held in all 2 conclusive runs, but no baseline ran, so the runs can't show the file made the difference.

**What the file's text suggests**

The template's log holds no copy of `.kilo/rules/data.md`, so the text rules couldn't read it.
