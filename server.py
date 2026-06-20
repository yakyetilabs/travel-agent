from google.adk.cli.fast_api import get_fast_api_app

app = get_fast_api_app(
    agents_dir="agents",
    web=True,
    # ADK's dev UI rejects cross-origin *mutating* requests (e.g. POST
    # /apps/.../sessions) by default — a CSRF guard. When the deployed service
    # is reached via `gcloud run services proxy`, the browser sends
    # Origin: http://localhost:<port>, which is not the service URL, so those
    # POSTs get a 403 ("Failed to create session" in the UI) even though GETs
    # and curl calls succeed.
    #
    # Allowing all origins is safe HERE specifically because Cloud Run IAM
    # (--no-allow-unauthenticated) is the real auth gate: a cross-origin
    # attacker cannot mint a valid OIDC ID token to invoke the service, so
    # there is no CSRF surface to protect. (If this service were ever made
    # public with --allow-unauthenticated, tighten this to the known origins.)
    allow_origins=["*"],
)
