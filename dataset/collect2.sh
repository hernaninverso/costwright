#!/bin/bash
# Recolección v2 (D6): repos por topic/keyword con filtros anti-tutorial, clone shallow, barrido local.
set -u
mkdir -p repos
M=repos_manifest.jsonl; : > "$M"
EXCL='awesome|tutorial|example|template|course|quickstart|demo|starter|boilerplate|cookbook|learn'

pick_repos() { # query_type query tag limit
  local qt="$1" q="$2" tag="$3" lim="$4"
  if [ "$qt" = topic ]; then
    gh search repos --topic="$q" --stars=">9" --sort=stars --limit "$lim" \
      --json fullName,stargazersCount,pushedAt,description -q '.[] | [.fullName,(.stargazersCount|tostring),.pushedAt,(.description//"")] | @tsv'
  else
    gh search repos "$q" --stars=">9" --sort=stars --limit "$lim" \
      --json fullName,stargazersCount,pushedAt,description -q '.[] | [.fullName,(.stargazersCount|tostring),.pushedAt,(.description//"")] | @tsv'
  fi 2>/dev/null | while IFS=$'\t' read -r full stars pushed desc; do
    # filtros D6: nombre/desc anti-tutorial + actividad 12 meses
    if echo "$full $desc" | grep -qiE "$EXCL"; then continue; fi
    case "$pushed" in 2025-0[7-9]*|2025-1*|2026-*) ;; *) continue;; esac
    echo -e "$full\t$stars\t$tag"
  done
}

{ pick_repos topic langgraph lg_topic 40
  pick_repos topic crewai crew_topic 30
  pick_repos kw "langgraph agent" lg_kw 25
  pick_repos kw "crewai agents" crew_kw 20
  pick_repos topic openai-agents-sdk oai_topic 15
} | sort -u -t$'\t' -k1,1 > candidates.tsv
echo "candidatos únicos: $(wc -l < candidates.tsv)"

# clone shallow hasta 45 repos (suficiente para >50 grafos)
n=0
while IFS=$'\t' read -r full stars tag; do
  [ $n -ge 45 ] && break
  safe=$(echo "$full" | tr '/' '__')
  [ -d "repos/$safe" ] && { n=$((n+1)); continue; }
  if git clone --depth 1 --quiet "https://github.com/$full" "repos/$safe" 2>/dev/null; then
    sha=$(git -C "repos/$safe" rev-parse HEAD)
    # ¿tiene material? (StateGraph o crewai o agents sdk en .py)
    hits=$(grep -rl --include='*.py' -E "StateGraph\(|from crewai|import crewai|from agents import" "repos/$safe" 2>/dev/null | grep -v -E "(^|/)(\.venv|venv|node_modules|site-packages)/" | wc -l | tr -d ' ')
    if [ "$hits" -gt 0 ]; then
      echo "{\"repo\":\"$full\",\"sha\":\"$sha\",\"stars\":$stars,\"tag\":\"$tag\",\"py_hits\":$hits}" >> "$M"
      echo "  ✓ $full (★$stars, $hits archivos)"
      n=$((n+1))
    else
      rm -rf "repos/$safe"
    fi
  fi
done < <(sort -t$'\t' -k2,2 -rn candidates.tsv)
echo "REPOS CON MATERIAL: $(wc -l < $M)"
