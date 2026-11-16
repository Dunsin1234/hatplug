#!/bin/bash
# One-time setup so GitHub Actions can deploy Hat Plug to Cloud Run when you
# push to main. Run this in Google Cloud Shell (or any terminal where you are
# logged in with `gcloud auth login` as a project owner). Safe to re-run.
#
# It creates a dedicated deploy service account, lets ONLY this GitHub repo
# act as it (Workload Identity Federation, no stored keys), and prints the two
# values to save as GitHub repository variables.

PROJECT_ID=oluwadunsin-adesanya-fisk
GITHUB_REPO=Dunsin1234/hatplug          # owner/repo
DEPLOY_SA_NAME=hatplug-deployer
POOL_NAME=hatplug-github
PROVIDER_NAME=hatplug-repo

set -u
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)') || exit 1
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GITHUB_OWNER="${GITHUB_REPO%%/*}"

gcloud config set project "${PROJECT_ID}" || exit 1

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com iamcredentials.googleapis.com \
    secretmanager.googleapis.com || exit 1

# 1. Deploy service account
gcloud iam service-accounts describe "${DEPLOY_SA}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${DEPLOY_SA_NAME}" \
    --display-name="Hat Plug GitHub deployer" || exit 1

# 2. What the deployer needs: submit builds, deploy to Cloud Run, act as the
#    Cloud Run runtime account, and read build logs.
for ROLE in roles/cloudbuild.builds.editor roles/run.admin \
            roles/iam.serviceAccountUser roles/storage.admin \
            roles/logging.viewer roles/serviceusage.serviceUsageConsumer \
            roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${DEPLOY_SA}" --role="${ROLE}" \
      --condition=None >/dev/null || exit 1
done

# 3. Workload Identity pool + provider limited to this one repository
gcloud iam workload-identity-pools describe "${POOL_NAME}" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "${POOL_NAME}" \
    --location=global --display-name="Hat Plug GitHub pool" || exit 1

gcloud iam workload-identity-pools providers describe "${PROVIDER_NAME}" \
    --location=global --workload-identity-pool="${POOL_NAME}" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_NAME}" \
    --location=global --workload-identity-pool="${POOL_NAME}" \
    --display-name="Hat Plug repo" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository == '${GITHUB_REPO}'" \
    --issuer-uri="https://token.actions.githubusercontent.com" || exit 1

POOL_ID="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}"
gcloud iam service-accounts add-iam-policy-binding "${DEPLOY_SA}" \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_REPO}" >/dev/null || exit 1

PROVIDER="${POOL_ID}/providers/${PROVIDER_NAME}"

echo
echo "Done. In GitHub: ${GITHUB_REPO} > Settings > Secrets and variables > Actions > Variables,"
echo "add these two REPOSITORY VARIABLES (not secrets):"
echo
echo "  DEPLOY_SERVICE_ACCOUNT      = ${DEPLOY_SA}"
echo "  WORKLOAD_IDENTITY_PROVIDER  = ${PROVIDER}"
echo
echo "Or, with the GitHub CLI:"
echo "  gh variable set DEPLOY_SERVICE_ACCOUNT     --repo ${GITHUB_REPO} --body '${DEPLOY_SA}'"
echo "  gh variable set WORKLOAD_IDENTITY_PROVIDER --repo ${GITHUB_REPO} --body '${PROVIDER}'"
