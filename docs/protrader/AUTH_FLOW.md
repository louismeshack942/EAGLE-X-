# Authentication Flow — EAGLE-X (Phase 1)

## Principle
**EAGLE-X NEVER collects, stores, or processes the user's Deriv password.**
Authorization uses the legitimate, publicly documented **Deriv OAuth2 Authorization Code flow
with PKCE**. The user authenticates and consents on Deriv's own hosted pages.

## ProTrader observable experience (class-wide reference)
- Login entry → credential screen → Deriv consent page → authorized app redirect → connected
  account shown → logout.

## EAGLE-X OAuth flow implementation (Phase 1)
```
[User]  Connect Deriv  →  redirect to Deriv OAuth2 authorize URL
        (deriv.com sign-in + consent on Deriv host; EAGLE-X never sees the password)

[Deriv] issues authorization code → redirects to EAGLE-X /auth/deriv/callback?code=...

[EAGLE-X] server exchanges code (+ PKCE verifier, app id) with Deriv token endpoint
        → receives access_token / refresh_token / expiry
        → stores tokens ENCRYPTED at rest, keyed by a random session id
        → session cookie (HttpOnly) / bearer returned to frontend (NOT the tokens)

[EAGLE-X] WS/data connector authorizes the WS with the token server-side
        → account confirmed → cockpit shows connected loginid + scopes
```

## States
- `logged_out` — shown landing/login
- `redirecting` — PKCE generated, going to Deriv
- `exchanging` — code → token
- `connected` — session established, data authorized
- `expired` — token expired → refresh or re-authorize
- `failed` — auth error message, safe back to login
- `logged_out_via_ui` — explicit logout destroys session server-side

## Security notes
- redirect_uri must match the registered app exactly (Deriv requirement).
- PKCE verifier stored server-side; never logged.
- Tokens never appear in frontend responses or logs; opaque session handle only.
- CORS restricted to the EAGLE-X origin.
- Session expiry → clean `AUTHORIZATION REQUIRED` state (never a silent failure).

## Placeholders
- Real Deriv app registration requires a `DERIV_OAUTH_CLIENT_ID` + `DERIV_OAUTH_CLIENT_SECRET`
  env vars. When unset, the auth endpoints return `NOT CONFIGURED` rather than faking success.