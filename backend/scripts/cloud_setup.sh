#!/usr/bin/env bash
# Sets up the service and what it needs around it: the deploy itself, then the call queue's
# limits and the scheduled jobs. The deploy comes first because the jobs call routes that
# exist only on the new revision.
#
# Without RUN it changes nothing: it runs read-only lookups (the service's address, and whether
# each job already exists) and PRINTS the commands it would run. Run with RUN=1 to carry them out.
#
#   bash scripts/cloud_setup.sh            # look things up, show the commands
#   RUN=1 bash scripts/cloud_setup.sh      # run them
#
# It never prints the internal secret: the commands read it from Secret Manager when they run.
# Change the values below if yours differ.
set -euo pipefail

PROJECT="${PROJECT:-chhaya-pilot}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-chhaya}"
QUEUE="${QUEUE:-chhaya-calls}"
RUNNER="${RUNNER:-chhaya-run@${PROJECT}.iam.gserviceaccount.com}"
# Set CONFIGURATION to use a named gcloud configuration (and so its account) instead of the active one.
GCLOUD="gcloud ${CONFIGURATION:+--configuration ${CONFIGURATION}} --project ${PROJECT}"

step() {
  echo
  echo "# $1"
  shift
  echo "$*"
  if [ "${RUN:-0}" = "1" ]; then eval "$*"; fi
}

URL="$(${GCLOUD} run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"
echo "Service address: ${URL}"
SECRET_HEADER='X-Internal-Secret=$('"${GCLOUD}"' secrets versions access latest --secret internal-secret)'

# A call can last five minutes and its socket must outlive it: Cloud Run's default request
# timeout would cut it.
step "Deploy, with the queue, the text model and the forecast key switched on" \
  "${GCLOUD} run deploy ${SERVICE} --source . --region ${REGION} --quiet --timeout 900 --update-env-vars TASKS_QUEUE=${QUEUE},TASKS_LOCATION=${REGION},TASKS_TARGET=${URL},TASKS_SERVICE_ACCOUNT=${RUNNER},TEXT_MODEL=gemini-3.8-flash,VIEW_CACHE_SECONDS=2,REGISTER_CACHE_SECONDS=86400 --update-secrets WEATHER_API_KEY=weather-api-key:latest"

step "The queue retries a refused dial a few times, then gives up (the sweep takes over)" \
  "${GCLOUD} tasks queues update ${QUEUE} --location ${REGION} --max-attempts 5 --min-backoff 30s --max-backoff 300s --max-dispatches-per-second 2 --max-concurrent-dispatches 5"

job() {  # name, schedule, path, description
  local verb="create"
  if ${GCLOUD} scheduler jobs describe "$1" --location "${REGION}" >/dev/null 2>&1; then verb="update"; fi
  local headers="--headers"
  if [ "${verb}" = "update" ]; then headers="--update-headers"; fi
  step "$4" \
    "${GCLOUD} scheduler jobs ${verb} http $1 --location ${REGION} --schedule '$2' --time-zone Asia/Kolkata --uri \"${URL}$3\" --http-method POST ${headers} \"${SECRET_HEADER}\" --oidc-service-account-email ${RUNNER} --oidc-token-audience ${URL} --attempt-deadline 300s"
}

step "The old fifteen-minute forecast job is replaced by the twice-daily fetch below"   "${GCLOUD} scheduler jobs delete chhaya-ingest --location ${REGION} --quiet || true"

job chhaya-weather-morning   "0 6 * * *"    /internal/weather/fetch        "Every morning at 06:00: fetch Google's forecast for every ward, raise the state rule's alerts and open live rounds"
job chhaya-weather-afternoon "0 14 * * *"   /internal/weather/fetch        "Every afternoon at 14:00: fetch again; an alert can go up, never down"
job chhaya-sweep             "*/5 * * * *"  "/internal/sweep?mode=live"     "Every five minutes: finish stale calls, re-queue overdue ones, write missing notes (live dataset)"
job chhaya-sweep-demo        "*/5 * * * *"  "/internal/sweep?mode=demo"     "Every five minutes: the same for the demo dataset"
job chhaya-demo-rebuild      "0 7 * * *"    "/internal/demo/rebuild?mode=demo" "Every morning at 07:00: run the heat scenario through the trigger rules and open the day's demo rounds"

cat <<EOF

# Two permissions that a project owner grants once, by hand (this script does not):
#   1. The runner may call the service through Cloud Run's own door (needed while the service
#      does not allow unauthenticated requests):
#      ${GCLOUD} run services add-iam-policy-binding ${SERVICE} --region ${REGION} --member serviceAccount:${RUNNER} --role roles/run.invoker
#   2. The runner may ask for identity tokens as itself (for the queue and the jobs):
#      ${GCLOUD} iam service-accounts add-iam-policy-binding ${RUNNER} --member serviceAccount:${RUNNER} --role roles/iam.serviceAccountUser
EOF
