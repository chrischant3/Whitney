#!/usr/bin/env bash
# PreToolUse gate for Bash calls. Enforces agent-skills:git-workflow-and-versioning:
#   - no commit or push while on main/master, and no push that targets main/master
#     (refspecs such as HEAD:refs/heads/main, --all, --mirror, --tags, and deletes)
#   - no commit or push after a switch to main/master in the same command
#   - no force pushes
#   - no .env files in a commit: staged ones, or tracked ones modified under commit -a
#     (.env.example, .env.sample and .env.template are allowed)
#   - no `gh pr merge`, and no gh api writes: anything but GET, and graphql mutations
#   - no `gh pr create` on main/master, or before the pre-PR review marker exists for this HEAD
#
# Only the word in command position is read as a command, after variable assignments and
# wrappers such as env, command, sudo and `sh -c`. Text that only mentions git, such as a grep
# pattern or a commit message, is not mistaken for a git call.
#
# Best effort, not a security boundary: this is a token scan, not a shell parser, so unusual
# command forms (aliases, variables, eval, paths with spaces) can get past it or be refused.
# GitHub branch protection on main is the real control.
#
# Exit 2 blocks the tool call and sends stderr back to Claude. Claude Code treats any other
# non-zero exit as a non-blocking error, so every failure path exits 2, and the EXIT trap turns
# an unexpected crash into a block rather than a silent pass.

set -uo pipefail

trap 'rc=$?; if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then echo "git-workflow-gate: internal error (exit $rc); refusing the command." >&2; exit 2; fi' EXIT

block() {
  printf 'git-workflow-gate: %s\n' "$1" >&2
  exit 2
}

command -v jq >/dev/null 2>&1 || block "jq is required by this gate and was not found on PATH."
input=$(cat) || block "could not read hook input."
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty') || block "could not parse hook input."
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty') || block "could not parse hook input."
[ -n "$cmd" ] || exit 0
[ -n "$cwd" ] || cwd=$PWD

# A git -c value quoted with ; | or & inside (the shell-function credential.helper form) would be
# split into separate commands below, hiding the git call. Refuse it before splitting.
q="['\"]"; nq="[^'\"]"
spill_re="(^|[[:space:]/])git[[:space:]]+(-[^[:space:]]+[[:space:]]+([^[:space:]-][^[:space:]]*[[:space:]]+)?)*-c[[:space:]]+[^[:space:]]*${q}${nq}*[;|&]"
if [[ "$cmd" =~ $spill_re ]]; then
  block "a git -c value containing shell separators (; | &) cannot be checked. Set it with git config instead."
fi

is_protected() { [[ "$1" == main || "$1" == master ]]; }
branch_at() { git -C "$1" branch --show-current 2>/dev/null; }

# on_protected DIR: true when the command will act on main/master in DIR. An earlier switch in the
# same command wins over the branch checked out now, because the gate runs before the command.
on_protected() {
  case "$switched_to" in
    protected) return 0 ;;
    other) return 1 ;;
  esac
  is_protected "$(branch_at "$1")"
}

# Resolve $2 (may be relative or start with ~) against directory $1. Prints the absolute path.
resolve() {
  local base=$1 target=${2/#\~/$HOME}
  (cd "$base" 2>/dev/null && cd -- "$target" 2>/dev/null && pwd -P)
}

# env_files LIST: prints the .env files in a newline-separated LIST of paths, leaving out templates.
env_files() {
  printf '%s\n' "$1" \
    | grep -E '(^|/)\.env(\.[^/]*)?$' \
    | grep -vE '(^|/)\.env\.(example|sample|template)$' || true
}

# Quote characters are dropped so refspecs written as 'HEAD:main' are still seen. Brackets and
# backticks become spaces so $(git ...) and (git ...) split into words. Separators become newlines,
# one command per line.
segments=$(printf '%s' "$cmd" \
  | tr -d "\"'" \
  | tr '(){}`' '     ' \
  | sed -E 's/&&|\|\||[;|&]/\n/g')

dir=$cwd
switched_to=""   # "", protected, or other: the branch an earlier switch in this command lands on

while IFS= read -r seg; do
  read -ra t <<< "$seg"
  n=${#t[@]}
  [ "$n" -gt 0 ] || continue

  # Find the word in command position. Skip assignments, wrappers, and `sh -c`, whose payload
  # follows in the same segment once quotes are dropped.
  k=0
  while [ "$k" -lt "$n" ]; do
    case "${t[k]}" in
      [A-Za-z_]*=*) k=$((k+1)) ;;
      env|command|builtin|sudo|nohup|exec|time|nice|if|then|else|do|while|until|!) k=$((k+1)) ;;
      sh|bash|zsh|dash|*/sh|*/bash|*/zsh|*/dash)
        if [ "${t[k+1]:-}" = -c ]; then k=$((k+2)); else break; fi ;;
      -*) k=$((k+1)) ;;   # an option to a wrapper above, such as env -i
      *) break ;;
    esac
  done
  [ "$k" -lt "$n" ] || continue
  base=${t[k]##*/}
  gdir=$dir

  case "$base" in
    cd|pushd)
      target=${t[k+1]:-}
      [ -n "$target" ] || block "a bare $base cannot be checked; give the target directory explicitly."
      case "$target" in
        \$*) block "$base target $target is a substitution the gate cannot read; use an explicit path." ;;
      esac
      dir=$(resolve "$dir" "$target") || block "cannot resolve $base target $target."
      continue ;;
  esac

  if [ "$base" = git ]; then
    j=$((k+1)); sub=""; may_spill=0
    while [ "$j" -lt "$n" ]; do
      case "${t[j]}" in
        -C)
          [ $((j+1)) -lt "$n" ] || block "git -C needs a directory."
          gdir=$(resolve "$gdir" "${t[j+1]}") || block "cannot resolve git -C ${t[j+1]}."
          j=$((j+2)) ;;
        -c|--git-dir|--work-tree|--namespace|--attr-source|--config-env) may_spill=1; j=$((j+2)) ;;
        --git-dir=*|--work-tree=*|--namespace=*|--attr-source=*|--config-env=*|--exec-path=*) may_spill=1; j=$((j+1)) ;;
        -*) j=$((j+1)) ;;
        *) sub=${t[j]}; break ;;
      esac
    done
    # Quotes are dropped above, so an option value with spaces (credential.helper='!gh auth ...',
    # --git-dir='/a b') spills into words that look like the subcommand. When that can happen,
    # take the first later word the gate checks; at worst a mention elsewhere is checked too.
    # A spilled word that is itself commit/push/switch/checkout still wins; that form is
    # contrived and left to branch protection.
    if [ "$may_spill" = 1 ]; then
      case "$sub" in
        commit|push|switch|checkout) ;;
        *)
          x=$((j+1))
          while [ "$x" -lt "$n" ]; do
            case "${t[x]}" in
              commit|push|switch|checkout) sub=${t[x]}; j=$x; break ;;
            esac
            x=$((x+1))
          done ;;
      esac
    fi
    # ${rest[@]+...} below: bash 3.2 treats an empty array as unbound under set -u.
    rest=("${t[@]:j+1}")

    case "$sub" in
      commit)
        if on_protected "$gdir"; then
          block "main/master is protected. Create a feature branch first: git switch -c feature/<short-description>"
        fi
        found=$(env_files "$(git -C "$gdir" diff --cached --name-only 2>/dev/null)")
        commit_all=0
        for a in ${rest[@]+"${rest[@]}"}; do
          case "$a" in
            --all) commit_all=1 ;;
            --*) ;;
            -?*) case "$a" in *a*) commit_all=1 ;; esac ;;   # -a, or a cluster such as -am
          esac
        done
        if [ "$commit_all" = 1 ]; then
          found=$(printf '%s\n%s\n' "$found" "$(env_files "$(git -C "$gdir" diff --name-only 2>/dev/null)")" | sed '/^$/d')
        fi
        [ -z "$found" ] || block ".env file(s) would be committed: $(printf '%s ' $found). Unstage them with git restore --staged, or leave them out of commit -a."
        ;;
      push)
        if on_protected "$gdir"; then
          block "refusing to push from main/master. Create a feature branch and push that instead."
        fi
        for a in ${rest[@]+"${rest[@]}"}; do
          case "$a" in
            --all|--mirror|--tags)
              block "$a can reach main/master. Push a named feature branch instead." ;;
            --force|--force-*|-f)
              block "force pushes are not allowed." ;;
            --*) ;;
            -?*)
              # A cluster of short flags such as -uf or -fu.
              case "$a" in *f*) block "force pushes are not allowed (combined short flags: $a)." ;; esac ;;
            +*)
              block "forced refspec $a is not allowed." ;;
            *)
              dst=${a##*:}
              dst=${dst#refs/heads/}
              if is_protected "$dst"; then
                block "refusing to push to $dst. Push a feature branch and open a PR."
              fi ;;
          esac
        done
        ;;
      switch|checkout)
        # Creating a branch (switch -c new main) checks out the new branch, not main. A plain
        # `git switch name` lands on name. A plain `git checkout name` is not trusted as a switch
        # away from main, because name may be a file path.
        creating=0
        for a in ${rest[@]+"${rest[@]}"}; do
          case "$a" in -c|-C|-b|-B|--create|--force-create|--orphan) creating=1 ;; esac
        done
        if [ "$creating" = 1 ]; then
          switched_to=other
        else
          for a in ${rest[@]+"${rest[@]}"}; do
            case "$a" in
              -*) ;;
              *)
                if is_protected "$a"; then switched_to=protected
                elif [ "$sub" = switch ]; then switched_to=other
                fi ;;
            esac
          done
        fi
        ;;
    esac
  fi

  if [ "$base" = gh ]; then
    j=$((k+1)); sub1=""; sub2=""
    while [ "$j" -lt "$n" ]; do
      case "${t[j]}" in
        -R|--repo|--hostname) j=$((j+2)) ;;
        -*) j=$((j+1)) ;;
        *)
          if [ -z "$sub1" ]; then sub1=${t[j]}; else sub2=${t[j]}; break; fi
          j=$((j+1)) ;;
      esac
    done

    if [ "$sub1" = api ]; then
      # gh api sends POST once fields or input are given, unless the method says otherwise.
      method=""; has_fields=0; graphql=0; mutation=0
      x=$((k+1))
      while [ "$x" -lt "$n" ]; do
        a=${t[x]}
        case "$a" in
          -X|--method) method=${t[x+1]:-}; x=$((x+2)); continue ;;
          --method=*) method=${a#--method=} ;;
          -X?*) method=${a#-X} ;;
          -f|-F|--field|--raw-field|--input|--field=*|--raw-field=*|--input=*) has_fields=1 ;;
          graphql) graphql=1 ;;
          *pulls/*/merge*) block "merging PRs through gh api is left to a person." ;;
          *branches/*/protection*) block "changing branch protection through gh api is left to a person." ;;
        esac
        case "$a" in *[Mm]utation*) mutation=1 ;; esac
        x=$((x+1))
      done
      if [ "$graphql" = 1 ]; then
        # graphql always POSTs; reads are queries, writes are mutations.
        [ "$mutation" = 0 ] || block "gh api writes are left to a person: graphql mutations are not allowed."
      else
        method=$(printf '%s' "$method" | tr '[:lower:]' '[:upper:]')
        if [ -z "$method" ]; then
          if [ "$has_fields" = 1 ]; then method=POST; else method=GET; fi
        fi
        [ "$method" = GET ] || block "gh api writes are left to a person: $method requests are not allowed."
      fi
    fi

    if [ "$sub1" = pr ] && [ "$sub2" = merge ]; then
      block "merging PRs is left to a human after review. Open the PR and stop."
    fi

    if [ "$sub1" = pr ] && [ "$sub2" = create ]; then
      if on_protected "$dir"; then
        block "cannot open a PR from main/master. Switch to a feature branch."
      fi
      sha=$(git -C "$dir" rev-parse HEAD 2>/dev/null) || block "cannot read HEAD in $dir."
      marker=$(git -C "$dir" rev-parse --git-path "claude-pr-ready-$sha") || block "cannot locate the review marker in $dir."
      [[ "$marker" == /* ]] || marker="$dir/$marker"
      if [ ! -f "$marker" ]; then
        block "run the pre-PR review before opening this PR. Run these skills in order on the diff against main, fixing findings as you go:
  1. agent-skills:code-review-and-quality
  2. agent-skills:code-simplification
  3. agent-skills:security-and-hardening
  4. agent-skills:documentation-and-adrs
  5. agent-skills:shipping-and-launch
When all five pass, record that for this commit:
  touch \"\$(git rev-parse --git-path claude-pr-ready-\$(git rev-parse HEAD))\"
then retry gh pr create."
      fi
    fi
  fi
done <<< "$segments"

exit 0
