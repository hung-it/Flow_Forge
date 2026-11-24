#!/bin/sh
set -eu

if [ "${CI_PIPELINE_SOURCE:-}" != "merge_request_event" ] ||
   [ "${CI_MERGE_REQUEST_EVENT_TYPE:-}" != "detached" ] ||
   [ "${CI_MERGE_REQUEST_SOURCE_PROJECT_ID:-}" != "${CI_PROJECT_ID:?}" ] ||
   [ "${CI_MERGE_REQUEST_TARGET_BRANCH_PROTECTED:-}" != "true" ] ||
   [ "${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-}" != "integration/guardian" ]; then
    echo "Guardian requires a same-project detached merge request targeting protected integration/guardian." >&2
    exit 5
fi

cd "${CI_PROJECT_DIR:?}"
mkdir reports/guardian
inputs=$(mktemp -d)
trap 'rm -f "$inputs/base.json" "$inputs/candidate.json" "$inputs/policy.yml"; rmdir "$inputs"' EXIT

repo_git() {
    git -c "safe.directory=$CI_PROJECT_DIR" "$@"
}

extract_if_present() {
    entry=$(repo_git ls-tree "$1" -- "$2")
    case "${entry%% *}" in
        100644|100755) repo_git show "$1:$2" > "$3" ;;
    esac
}

repo_git fetch --no-tags --depth=1 origin refs/heads/integration/guardian
GUARDIAN_BASELINE_SHA=$(repo_git rev-parse 'FETCH_HEAD^{commit}')
GUARDIAN_CANDIDATE_SHA=$(repo_git rev-parse "${CI_COMMIT_SHA:?}^{commit}")
export GUARDIAN_BASELINE_SHA GUARDIAN_CANDIDATE_SHA
printf 'Guardian baseline %s; candidate %s\n' "$GUARDIAN_BASELINE_SHA" "$GUARDIAN_CANDIDATE_SHA"

extract_if_present "$GUARDIAN_BASELINE_SHA" openapi/flowforge.json "$inputs/base.json"
extract_if_present "$GUARDIAN_BASELINE_SHA" .guardian.yml "$inputs/policy.yml"
extract_if_present "$GUARDIAN_CANDIDATE_SHA" openapi/flowforge.json "$inputs/candidate.json"
if ! cmp -s reports/openapi/flowforge.json "$inputs/candidate.json"; then
    echo 'Generated OpenAPI artifact does not match the candidate commit.' >&2
    rm -f "$inputs/candidate.json"
fi

guardian compare --base "$inputs/base.json" --candidate "$inputs/candidate.json" \
    --policy "$inputs/policy.yml" --input-root "$inputs" --out reports/guardian
