#!/bin/bash
# Recolección multi-modal de grafos de agentes públicos. Provenance en manifest.jsonl.
set -u
OUT=raw; mkdir -p "$OUT"
M=manifest.jsonl; : > "$M"

fetch() { # repo path query_tag
  local repo="$1" path="$2" tag="$3"
  local safe=$(echo "${repo}__${path}" | tr '/' '_')
  [ -f "$OUT/$safe" ] && return 0
  local json=$(gh api "repos/$repo/contents/$path" 2>/dev/null) || return 1
  local sha=$(echo "$json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('sha',''))" 2>/dev/null) || return 1
  echo "$json" | python3 -c "
import json,sys,base64
d=json.load(sys.stdin)
c=d.get('content','')
sys.stdout.write(base64.b64decode(c).decode('utf-8',errors='ignore'))" > "$OUT/$safe" 2>/dev/null || return 1
  [ -s "$OUT/$safe" ] || { rm -f "$OUT/$safe"; return 1; }
  local stars=$(gh api "repos/$repo" --jq '.stargazers_count' 2>/dev/null || echo 0)
  echo "{\"repo\":\"$repo\",\"path\":\"$path\",\"sha\":\"$sha\",\"stars\":$stars,\"tag\":\"$tag\",\"file\":\"$safe\"}" >> "$M"
  echo "  + $repo/$path (★$stars)"
}

search_and_fetch() { # query tag limit
  local q="$1" tag="$2" lim="$3"
  echo "== search: $q =="
  gh search code "$q" --language=python --limit "$lim" --json repository,path \
    -q '.[] | .repository.nameWithOwner + "\t" + .path' 2>/dev/null | \
  while IFS=$'\t' read -r repo path; do
    fetch "$repo" "$path" "$tag"
    sleep 1.2   # rate limit code-search + contents
  done
}

# LangGraph: distintos ángulos de búsqueda (multi-modal, no un solo query)
search_and_fetch "StateGraph add_conditional_edges" lg_conditional 25
search_and_fetch "StateGraph recursion_limit" lg_reclimit 20
search_and_fetch "langgraph StateGraph compile invoke" lg_compile 25
search_and_fetch "from langgraph.graph import StateGraph add_node" lg_import 25
# CrewAI
search_and_fetch "from crewai import Crew Agent Task" crewai_basic 25
search_and_fetch "crewai max_iter" crewai_maxiter 15
# OpenAI Agents SDK
search_and_fetch "from agents import Agent Runner max_turns" oai_sdk 15

echo "TOTAL: $(wc -l < $M) archivos"
