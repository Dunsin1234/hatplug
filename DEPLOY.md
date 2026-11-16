# Automatic deployment (GitHub Actions to Cloud Run)

Pushing or merging to `main` runs the tests (`python-checks.yml`), and if they
pass, builds the container with Cloud Build and deploys it to the `hat-plug`
Cloud Run service. This is the same build-and-deploy that `manual-deploy.sh` does by hand.

## One-time setup

1. Open Google Cloud Shell on project `oluwadunsin-adesanya-fisk`, clone this
   repo, and run:
   ```bash
   ./scripts/setup-github-deploy.sh
   ```
   It creates a deploy service account and lets only `Dunsin1234/hatplug`
   use it through Workload Identity Federation. No keys are stored anywhere.
2. The script prints two values. Save them as **repository variables** in GitHub
   (Settings > Secrets and variables > Actions > Variables):
   - `DEPLOY_SERVICE_ACCOUNT`
   - `WORKLOAD_IDENTITY_PROVIDER`
3. Push to `main`, or open the Actions tab and re-run the latest run. A green
   "Cloud Run Continuous Deployment" run means it worked.

If you merge before step 2, the deploy job fails at the sign-in step. Nothing
is deployed and nothing breaks; set the variables and re-run it.

## Notes

- The service keeps its existing environment variables and secrets when the
  workflow deploys a new revision.
- Tests must pass first: a failing test blocks the deploy.
- `manual-deploy.sh` still works as a fallback.
