# The API credential — blueprint

Date: 2026-11-02. Scope: close the §29.6/§30 finding that the local API
accepts unauthenticated callers. This blueprint fixes the contract; the
implementation follows it.

Passing this slice does not establish that the application is safe to
release. The remaining findings are untouched by it.

---

## 1. The problem

The API refuses a *mutating* request from a foreign origin, and the refusal
is a good one: the origin has to be the API's own, worked out per request
and anchored on the loopback interface.

It is not authentication. A caller that is not a browser simply omits
`Origin`, and the request is answered as if it came from the operator. The
desktop client does exactly that, because it is not a browser.

So the boundary is only as strong as the assumption that nothing else on
the machine speaks HTTP to the loopback interface. That assumption holds
for a desktop on a desk and stops holding the moment the API is bound to a
LAN address — which is exactly the configuration the origin rules were
written to allow.

The gap is named in `ARCHITECTURE.md` §29.6 and §30: *the API still accepts
unauthenticated callers without an `Origin` header, workflow approval is
not authenticated*. Closing it means one thing: a request has to carry a
credential that identifies the operator, and the server has to refuse the
request without one.

## 2. The credential

A bearer token. The API's routes are all under `/api/`, and the
credential travels in the `Authorization` header:

```
Authorization: Bearer <token>
```

The token is 32 random bytes, written as URL-safe base64 without padding
(`secrets.token_urlsafe(32)`). It is opaque: it carries no meaning, no
operator name and no timestamp, so it says nothing to a caller that reads
it.

### 2.1 Where the token comes from

The effective token is decided once, at startup, in this order:

1. `LMEDITOR_API_TOKEN`, when it is set and not blank. The operator's own
   token, used as it was written — apart from the whitespace around it,
   which is trimmed, because HTTP trims the whitespace around a header
   value anyway and a token pasted out of a file would otherwise arrive
   with a newline on the end of it.
2. The contents of the token file, when the file exists and holds a
   non-blank token. This is what makes the token survive a restart.
3. A newly generated token.

The effective token is then **written to the token file**, whatever it was.
This is the channel between the two halves of the application: the server
and the desktop client are separate processes, and the client is not
necessarily started with the server's environment.

Two consequences, both deliberate:

- The token file is rewritten when the operator changes `LMEDITOR_API_TOKEN`,
  so the client picks the new token up on its next start.
- A file that already holds the effective token is **not** rewritten. The
  write is idempotent, so the file's modification time is evidence of when
  the token last changed and nothing else.

### 2.2 Where the token file is

`LMEDITOR_API_TOKEN_FILE` when it is set, otherwise
`<home>/.lmeditor/api-token`. A named path that begins with `~` is the
operator's home directory.

The named file **replaces** the default rather than being tried after it:
the server and the client have to name the same file, and a client that
fell back to the default would offer the server a token the server never
issued.

The operator's home directory is already where operator-level state lives:
`LMEDITOR_VAULT_BASE` and `LMEDITOR_NOTEBOOKS_BASE` both default to it.
The token belongs with it.

The file is created with mode `0600` and its directory with `0700`, on
every platform that has the modes. On Windows the file inherits the
operator's profile ACL, which is the platform's own answer to the same
question.

### 2.3 When the file cannot be written

- If the operator named a token (`LMEDITOR_API_TOKEN`) and the file cannot
  be written, the server logs a warning and serves. The operator may be
  running the client with the same environment variable.
- If the token had to be generated and the file cannot be written, the
  server **refuses to start**. A generated token that cannot be handed to
  the client is a server the client cannot talk to, and a server that
  refuses to start says so; a server that starts and answers 401 forever
  does not.

A file that exists but cannot be **read** is treated as a file that holds
no token: the server cannot use a token it cannot see. The file is then
written with the effective token, which is how a file left behind by an
earlier run is taken over — and if that write fails as well, the refusal to
start above applies.

## 3. The server

### 3.1 What requires the credential

Every route under `/api/`. That is every route that reads or changes
anything: the health check, the workflow and its approval, the document and
library routes, the vault, the notebook, the sources, the stream.

The API's own documentation — `/docs`, `/redoc`, `/openapi.json` — is not
credentialed. It describes the API and acts on nothing, and the Swagger UI
fetches `openapi.json` without an `Authorization` header, so credentialing
it would break the page rather than protect anything. The operator can
already take the documentation away with `LMEDITOR_EXPOSE_DOCS=off`.

A browser's **preflight** is not credentialed either. A preflight is the
`OPTIONS` request a browser sends to ask what it may do, and a browser
attaches the `Authorization` header to the request the preflight is *about*,
not to the preflight itself. A preflight that was refused would stop the
browser from ever making the request it was asking about, so a preflight is
answered without one. A preflight is recognised by the same rule the
server already used for the origin rules: the method is `OPTIONS` and the
request carries `Access-Control-Request-Method`.

### 3.1.1 The order things happen in

The credential is checked **before** anything else. The credential
middleware is the outermost one, so a request is authenticated before it is
authorized: a caller that cannot prove it is the operator is not told
whether the thing it asked for exists, and a mutating request from a
foreign origin is refused by the credential before the origin rules get to
decide anything about it.

Both answers are needed. The origin rules answer what a *page* may do; they
cannot answer what a *process* may do, because a process need only omit the
`Origin` header.

### 3.1.2 When the token is settled

The token is settled **twice**, and the two answers are the same one:

- **At startup**, in the application's lifespan, so that a server which
  cannot keep its token says so by refusing to start rather than by
  answering 401 to every caller for ever.
- **At the first request** if it has not been settled already, so that the
  application object can be built before the operator's environment is in
  place. Importing the server module builds the application, and a token
  settled at import time would be settled before the environment was read.

The settled token is kept on the application, so every request is checked
against the same one.

### 3.2 What a refusal looks like

A missing credential and a wrong credential are refused **identically**: the
same status, the same headers and the same body. A caller that could tell
the two apart would learn whether a token it guessed is the right length,
and a refusal that differs tells a caller what to fix.

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="lmeditor"

{"detail": "This API requires a bearer token."}
```

The comparison of the presented token with the expected one is
constant-time (`hmac.compare_digest`). A caller that guesses tokens one at
a time must not be able to time its way to the right one.

The header's **name** is matched without regard to case, because a header
name is case-insensitive and a caller that wrote `Authorization` has
written the header properly. The token inside it is matched exactly: it is
opaque, and a client that has it right has it right. The scheme is matched
without regard to case as well, because an HTTP authentication scheme is
case-insensitive too.

The refusal says nothing else. It does not name the token file, does not
say whether a token was present, and does not echo any part of what was
presented.

### 3.3 What is never written down

- The token is never logged. The server logs the **path** of the token file
  at startup, and the path is not a secret.
- The token is never in an error message, never in a response body and
  never in a URL. A credential in a URL lands in every access log between
  the client and the server.

## 4. The desktop client

The client reads the credential the same way the server resolves it, in this
order:

1. `LMEDITOR_API_TOKEN`, when it is set and not blank, trimmed.
2. The contents of the token file, trimmed — the file named by
   `LMEDITOR_API_TOKEN_FILE`, or `<home>/.lmeditor/api-token` when it is not
   named.

The token is read **once**, when the client is built, and sent on every
request: the three REST request sites and the SSE stream. A token that
cannot be read is a typed `BackendError`, not a crash, and it names the
path it looked at — a client that cannot say which file it wanted is a
client the operator cannot fix.

Reading the file once is enough: a token that changes on disk changes on
the server's next start too, and the client is restarted with it.

The client refuses a token that holds a control character, because a
control character would split the header the token travels in. The server
does not refuse one — it only ever compares the token, and never sends it —
so the two halves differ here, and they differ on the safe side: the
client declines to send a credential it cannot send.

## 5. What the slice proves

- A request without the credential is refused, over real HTTP, on every
  route under `/api/` — including the workflow approval and the SSE stream.
- A request with the wrong credential is refused identically.
- A request with the right credential is served.
- A header spelled `Authorization` is the header, whatever the case of its
  name.
- The token file is created with the operator's own permissions, is reused
  across restarts, and is replaced when the operator names a new token.
- A generated token that cannot be written stops the server from starting.
- The token never appears in a log, a response body or a URL.
- The desktop client sends the credential on every request, including the
  stream, and reports a missing credential as a typed failure.

## 6. What the slice does not prove

- It does not make the API safe on a LAN address. A bearer token on a
  plain HTTP connection is readable by anything on the path. The API is
  still meant to be reached over the loopback interface, or over a tunnel
  the operator controls.
- It does not identify *which* operator presented the token. There is one
  operator and one token.
- It does not rotate a token that has leaked. To rotate: set
  `LMEDITOR_API_TOKEN`, or delete the token file and restart the server.
- It does not establish production readiness. The installed-wheel
  deployment, the accessibility evidence and the remaining §29.6 items are
  untouched by it.
