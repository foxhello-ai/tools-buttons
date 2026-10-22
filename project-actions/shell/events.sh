# Source this file from a script started by Project Actions.
# All values are passed as arguments; log text is never evaluated as shell code.
_pa_emit() {
  "${PROJECT_ACTIONS_PYTHON:?}" "${PROJECT_ACTIONS_CLI:?}" emit "$@"
}
pa_log() { _pa_emit log "$@"; }
pa_node() { _pa_emit node "$@"; }
pa_progress() { _pa_emit progress "$@"; }
pa_artifact() { _pa_emit artifact "$@"; }
pa_event_json() { _pa_emit json; }

pa_checkpoint() { _pa_emit checkpoint "$@"; }
pa_reuse() { _pa_emit reuse "$@"; }
