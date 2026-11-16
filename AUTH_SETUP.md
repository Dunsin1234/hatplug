# Turning on Google sign-in

Hat Plug uses Streamlit's built-in OpenID Connect login. Until you finish these
steps the app runs in **demo mode** (everyone shares the guest account), so
nothing breaks if you deploy first and configure later.

Once login is on:
- Visitors can browse the catalog and read reviews and Q&A without signing in.
- Cart, Orders, Style Coach, checkout, reviews and Q&A posting need sign-in.
- Each person is identified by their Google email, so carts, orders and
  Style Coach history are per user and survive refreshes and other devices.

## 1. Create a Google OAuth client (about 5 minutes)

In the Google Cloud console for project `oluwadunsin-adesanya-fisk`:

1. **APIs & Services > OAuth consent screen**: choose **External**, fill in the app name
   ("Hat Plug") and your email. Scopes: the defaults (`openid`, `email`, `profile`).
2. On the same screen, set the publishing status to **In production** so any
   Google account can sign in. With only those basic scopes no Google review is needed.
   (In "Testing" status only emails you list as test users can sign in.)
3. **APIs & Services > Credentials > Create credentials > OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URIs (add both):
     - `https://hat-plug-64058716019.us-central1.run.app/oauth2callback`
     - `http://localhost:8501/oauth2callback` (for local testing)
4. Copy the **client ID** and **client secret**.

## 2. Write the secrets file

Create `.streamlit/secrets.toml` locally. It is git-ignored; never commit it.

```toml
[auth]
redirect_uri = "https://hat-plug-64058716019.us-central1.run.app/oauth2callback"
cookie_secret = "PASTE_A_RANDOM_STRING"
client_id = "PASTE_CLIENT_ID"
client_secret = "PASTE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Generate the cookie secret with:
`python -c "import secrets; print(secrets.token_urlsafe(32))"`

For local testing set `redirect_uri = "http://localhost:8501/oauth2callback"`
and run `streamlit run app.py`.

## 3. Give the secrets to Cloud Run

The container's `docker-entrypoint.sh` writes the `STREAMLIT_SECRETS_TOML`
environment variable to `.streamlit/secrets.toml` at startup. Store the file in
Secret Manager and attach it as that variable (run in Cloud Shell):

```bash
gcloud secrets create hatplug-streamlit-secrets --data-file=secrets.toml

gcloud secrets add-iam-policy-binding hatplug-streamlit-secrets \
  --member="serviceAccount:64058716019-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud run services update hat-plug --region us-central1 \
  --update-secrets=STREAMLIT_SECRETS_TOML=hatplug-streamlit-secrets:latest
```

This makes a new revision. The image must already contain this version of the
code (merge to `main` and let the deploy finish first, or run `./manual-deploy.sh`).
To change the secrets later, add a new version with
`gcloud secrets versions add hatplug-streamlit-secrets --data-file=secrets.toml`
and redeploy the service.

## Troubleshooting

- **"redirect_uri_mismatch" from Google**: the URI in step 1 must match
  `redirect_uri` exactly, including `https` and `/oauth2callback`.
- **Still in demo mode after setup**: the `[auth]` section is missing or the
  secret isn't attached to the new revision. Check
  `gcloud run services describe hat-plug --region us-central1`.
- **Users table**: sign-in does not write to the `users` table. Identity comes
  from the Google email.
