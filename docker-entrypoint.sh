#!/bin/sh
# Cloud Run injects the Streamlit secrets (Google login settings) as an
# environment variable from Secret Manager. Streamlit reads them from a file,
# so write the file before starting the app. If the variable is unset, the app
# starts in demo mode with no login.
if [ -n "$STREAMLIT_SECRETS_TOML" ]; then
  mkdir -p /app/.streamlit
  printf '%s\n' "$STREAMLIT_SECRETS_TOML" > /app/.streamlit/secrets.toml
fi
exec streamlit run app.py --server.port=8080 --server.address=0.0.0.0
