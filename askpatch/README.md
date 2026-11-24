# Ask as an add-on for an existing Chiron

Point Ask at a Chiron that is already running, anywhere, and get the Ask tab, without
changing Chiron: one container, one route in the web server, one switch in the UI. Take
the three away and Chiron is as it was. The models Ask answers with are configuration.

| What | Where it goes | What it touches in Chiron |
| --- | --- | --- |
| The Ask server | A container built from the deployment's own Chiron source, with chiron-mcp and the Claude Code CLI added; reads the deployment's settings module and its two databases | Nothing. It reads Chiron's metadata and warehouse as the person who is logged in |
| The `/ask` route | One snippet in the web server that already serves Chiron ([proxy/](proxy/)) | Nothing. Ask is served on Chiron's address, which is how it knows who is logged in |
| The Ask tab | A build-time switch in the Chiron UI, `VITE_ASK_URL` ([ui-ask-tab.patch](ui-ask-tab.patch)); for the built-in Django UI, a navbar include ([templates/](templates/askpatch/ask_navbar.html)) | Unset, there is no tab and nothing else changes |

[guide.md](guide.md) is the step-by-step guide for the person who looks after a Chiron
deployment: what people get, adding Ask, checking it, who can see what, turning it off,
removing it, and what to do when something goes wrong. [Chiron-upstream-changes.pdf](Chiron-upstream-changes.pdf)
is the write-up of the two changes above for the Chiron developers, with the diffs
(`guide/make_upstream.py` rebuilds it from the patch files).

Two fixes Chiron needs for this (a mutable default in `CohortDefProcessor`, and event
dates kept in lookup tables for the relative-date rule) travel inside chiron-mcp and are
applied in Ask's own process when the host Chiron lacks them
(`chiron_mcp/chiron_patches.py`); Chiron's files are never written to. Chiron's own query
builder gets them only when they are merged upstream, or when a deployment applies
[chiron-fixes.patch](chiron-fixes.patch) to its checkout itself (three files, the content
of the upstream pull request).

## In five steps

1. Get it and describe your Chiron:

   ```bash
   git clone https://github.com/TojiFushiguro/chiron-mcp.git
   cd chiron-mcp/askpatch
   cp env.example .env && chmod 600 .env
   # fill in .env: CHIRON_SRC, CHIRON_PROJECT_DIR, CHIRON_MCP_METADATA_DB, CHIRON_MCP_WAREHOUSE_URL, PUBLIC_URL
   ```

2. Start the Ask container:

   ```bash
   docker compose up -d --build
   docker compose ps          # "healthy"
   ```

3. Route `/ask` in the web server already in front of Chiron: paste the snippet for it from
   [proxy/](proxy/) (`ask.caddy`, `ask-nginx.conf` or `ask-apache.conf`) into Chiron's site
   block, then reload:

   ```bash
   sudo nginx -t && sudo nginx -s reload        # nginx
   sudo apachectl graceful                      # Apache
   caddy reload --config /etc/caddy/Caddyfile   # Caddy
   ```

4. Turn the tab on in the Chiron UI and rebuild it: copy [ui/ask.tsx](ui/ask.tsx) in and wrap
   the settings in `src/overrideConfig.tsx` with it (two lines, see "The Ask tab" below):

   ```bash
   cd /path/to/is4r-chiron-ui
   cp /path/to/chiron-mcp/askpatch/ui/ask.tsx src/ask.tsx
   # src/overrideConfig.tsx:  import { withAsk } from "./ask";
   #                          export const overrideConfig = withAsk({ ...as before... });
   VITE_ASK_URL=/ask npm run build      # deploy the build as you normally do
   ```

5. Connect a model and check. In `.env`: Claude (`ANTHROPIC_API_KEY` or the Bedrock
   lines), OpenAI or Gemini (through LiteLLM), or a model of your own (see "The model"
   below; with PHI, only a model approved for PHI), then `docker compose up -d`:

   ```bash
   python3 check.py                     # every line PASS
   ```

**Remove it** in three:

```bash
cd chiron-mcp/askpatch && docker compose down -v && docker image rm chiron-askpatch   # container, login, conversations, image
# delete the /ask snippet from the web server and reload it (same reload command as step 3)
cd /path/to/is4r-chiron-ui && npm run build    # without VITE_ASK_URL: no tab
```

**Pause it** with `docker compose stop` and `docker compose start`: everything is kept, and
meanwhile the tab shows the web server's error page. `docker compose down` without `-v`
keeps the conversations and usage history for a later `up -d`.

Chiron's code, settings and databases are never touched, so there is nothing else to undo.
The rest of this page explains each step.

## What the deployment needs

- **Chiron 6.5.x on Postgres**, its metadata in Postgres too (SQLite file locks do not
  cross the container boundary; a SQLite deployment runs Ask on the host instead, below).
- The Chiron **source checkout** and the **Django project** (settings module, custom
  processors) on the machine that runs Ask, and the two databases reachable from a
  container there.
- A **web server in front of Chiron** that can take one more route (Caddy, nginx or
  Apache). Ask must be on Chiron's origin: a UI served from another host name cannot
  carry the Chiron login to it. A Chiron on someone's own computer needs none: see
  "A Chiron on your own computer" below.
- Sessions in Django's database or cache backends (the default), whatever the cookie is
  named.
- Docker with Compose v2.17 or newer (for `additional_contexts`), and about 2 GB of free
  memory for Ask (a little more with LiteLLM).
- Internet access to build the image and to reach the model, unless the model runs on
  this machine. Behind a proxy: `HTTPS_PROXY` and `NO_PROXY` in `.env` for answers, and
  Docker Desktop's proxy settings for the build.
- A way to reach a model: Claude (an Anthropic API key, or Amazon Bedrock through the
  machine's role or an AWS profile), OpenAI or Google Gemini (through LiteLLM,
  [compose.litellm.yml](compose.litellm.yml)), or a model on your own computer or network.
  A Claude account (Pro or Max) logged in to the container is for trying it yourself.
- **For data with PHI, a model approved for PHI.** Ask sends each question and what it
  reads from Chiron (patient rows too, when the person may see them) to the model. With
  PHI, use only a model service your organization has approved for PHI (covered by a
  business associate agreement, a BAA), or a model on your own network. Never a personal
  account.

## Install

```bash
git clone https://github.com/TojiFushiguro/chiron-mcp.git
cd chiron-mcp/askpatch
cp env.example .env && chmod 600 .env        # then fill it in: paths, databases, address
docker compose up -d --build                 # builds the image from your Chiron, starts Ask
```

`.env` names the Chiron checkout (`CHIRON_SRC`) and project (`CHIRON_PROJECT_DIR`), the
two databases **as the container reaches them** (a Postgres on the same machine is
`host.docker.internal`, not `localhost`), the site's address (`PUBLIC_URL`, with its path
prefix if it has one), and the models. Everything in it reaches the container, so a
settings module that reads environment variables gets them too. Extra Python packages the
project needs go in [requirements-extra.txt](requirements-extra.txt) before the build.

**On Windows** (Docker Desktop), run the same commands in PowerShell, with `copy` for `cp`
(no `chmod`), `python` for `python3`, and forward slashes in the paths in `.env`. Put
`VITE_ASK_URL` in the UI's `.env.local` file rather than in front of the build command.
`COMPOSE_PATH_SEPARATOR=:` in `.env` keeps the `COMPOSE_FILE` lines working. Not tried on
Windows yet; [guide.md](guide.md#on-windows) has the details.

**The route.** Add the snippet for your web server from [proxy/](proxy/) to the site
that serves Chiron, then reload it. It forwards `/ask` to `127.0.0.1:8900` (`ASK_BIND` in
`.env` to change), with the streaming setting each server needs. Under a path prefix,
the route is `<prefix>/ask`.

**The Ask tab.** In the Chiron UI (`is4r-chiron-ui`), the tab is a build-time switch:
with `VITE_ASK_URL=/ask` the build has it, without it the UI is as before.
[ui/ask.tsx](ui/ask.tsx) holds the tab and its page: copy it to the UI's `src/ask.tsx` and
wrap the deployment's own settings in `src/overrideConfig.tsx`, the UI's place for a
deployment's changes:

```tsx
import { withAsk } from "./ask";

export const overrideConfig = withAsk({
  // ...the settings that were there, unchanged...
});
```

then `VITE_ASK_URL=/ask npm run build` (with the UI's Dockerfile: `ARG VITE_ASK_URL` and
`ENV VITE_ASK_URL=${VITE_ASK_URL}` beside the `VITE_MUI_KEY` lines, and
`--build-arg VITE_ASK_URL=/ask`). Chiron's five tabs come first, or the settings' own tabs
when they list them, then Ask. To take it out, build without the switch; delete
`src/ask.tsx` and the two lines to remove the code too. A UI whose `overrideConfig.tsx`
already has an Ask tab at `http://localhost:8900` (this repository's vendored UI) takes
[ui-ask-tab.patch](ui-ask-tab.patch) instead. For the built-in Django UI, two settings lines put an Ask link in the navbar, in `settings.py`
after `TEMPLATES` is defined:

```python
TEMPLATES[0]["DIRS"].append("/path/to/chiron-mcp/askpatch/templates")
CHIRON_EXTRA_NAVBAR_ITEMS = "askpatch/ask_navbar.html"
```

In a `settings_custom.py`, which `settings.py` star-imports at its end, `TEMPLATES` is not
in scope; the comment at the top of
[templates/askpatch/ask_navbar.html](templates/askpatch/ask_navbar.html) has the form that
reaches it through the settings module.

**The model.** Set one of these in `.env` ([env.example](env.example) has them all):

- *Claude with an Anthropic API key:* `ANTHROPIC_API_KEY`.
- *Claude on Amazon Bedrock:* `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION` and
  `ANTHROPIC_MODEL`. On an EC2 instance the role is used (its metadata hop limit must be 2
  for a container to reach it); elsewhere add [compose.aws.yml](compose.aws.yml) to
  `COMPOSE_FILE` for a profile in `~/.aws`.
- *OpenAI or Google Gemini:* through LiteLLM, which takes Ask's calls (Anthropic's format)
  and passes them on in the provider's own. Keep the models you want in
  [litellm.yaml](litellm.yaml), set `OPENAI_API_KEY` and/or `GEMINI_API_KEY` and a
  `LITELLM_MASTER_KEY` (any long random string), name the models in
  `CHIRON_MCP_ASK_MODELS`, and set `COMPOSE_FILE=compose.yml:compose.litellm.yml`. Claude,
  and servers that speak OpenAI's API (vLLM, LM Studio), can be listed there too.
- *A model on your own computer or network:* Ollama speaks Ask's format itself. Set
  `ANTHROPIC_BASE_URL` (`http://host.docker.internal:11434` for Ollama on the same machine;
  on Linux start Ollama with `OLLAMA_HOST=0.0.0.0`), `ANTHROPIC_AUTH_TOKEN`,
  `DISABLE_PROMPT_CACHING=1` and `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`, and name its
  model in `CHIRON_MCP_ASK_MODELS` so every model slot uses it. It needs good tool calling
  and a context of 64k tokens or more.
- *A Claude account,* only to try it yourself: log the container in once, in a real
  terminal: `docker compose exec -it ask claude`, choose the subscription login, open the
  link it prints, approve, paste the code back, `/exit`. The login lives in the `ask-home`
  volume.

`CHIRON_MCP_ASK_MODELS` lists the models the page may switch between (`id=Label,...`;
Haiku is the default when listed). Ask was built and tuned on Claude. Other models answer
through the same tools and checks, but compare a few of their answers with Chiron before
relying on them. **With PHI, use only a model approved for PHI** (above).

**Check it**, through the same front door a browser uses:

```bash
python3 check.py              # --user alice --dataset <id> for an account of your own
```

It makes a login session for the account inside the container (no password is typed),
confirms Ask sees that login through the route, asks a question, and compares the figure
in the answer with Chiron's own count; the last line checks that answers stream through
the web server rather than arriving all at once. Every line should say PASS.

**Who it is.** `<chiron address>/ask/whoami`, opened signed in, says who Ask answers as
and their access level on each dataset. A staff account also sees the request headers
the web server passes on, with the values of any that may carry a sign-on identity
(REMOTE_USER, eppn, OBLIX_UID and the like): the way to learn what a sign-on gateway in
front of this Chiron actually sends, before building anything on it.

**Follow the calls.** The container's log has one line per model call and per Chiron
call (user, dataset, model, tokens, cost, tool, status, time), and one per answer:

```bash
docker compose logs -f --no-log-prefix ask | grep ' ask '
```

`grep thread=<id>` follows one conversation end to end. No question text, filter value or
result is in these lines; the trace under an answer has those. See "The call log" in the
main README.

**What it costs, who uses it.** `<chiron address>/ask/usage`: answers, spend, answer
time, errors, per day, per person, per model and per dataset, with a CSV download.
Anyone sees their own; a Django superuser (or a member of the group
`CHIRON_MCP_USAGE_GROUP` names in `.env`) sees everyone's and who has what access on
each dataset. The ledger
behind it keeps one row per answer and no question text, in the `ask-home` volume. See
"The usage page" in the main README.

## A Chiron on your own computer

A Chiron someone runs on their own computer (Django's dev server with the UI's dev
server, or Chiron in Docker) needs no web server. The browser opens Ask directly at
`http://localhost:8900`, and because Chiron and Ask are both on `localhost`, it shows Ask
the Chiron login. The steps change in four places:

- In `.env`, `PUBLIC_URL` is where Chiron opens (the UI's dev server, for example
  `http://localhost:3000`), and `ASK_URL=http://localhost:8900` (the port in `ASK_BIND`).
- There is no route: skip step 3.
- The tab points at Ask itself: `VITE_ASK_URL=http://localhost:8900 npm run dev` for the
  dev server, or `--build-arg VITE_ASK_URL=http://localhost:8900` for a UI image.
- Open Chiron as `http://localhost:...`, not `127.0.0.1`: the login cookie belongs to the
  host name, and Ask is reached as `localhost`.

The databases: a Postgres on the computer, or a Postgres container that publishes its
port, is `host.docker.internal:<port>`. For a Chiron in Docker whose database publishes no
port, Ask joins its network instead ([compose.network.yml](compose.network.yml):
`CHIRON_NETWORK` and `COMPOSE_FILE=compose.yml:compose.network.yml` in `.env`), and the
URLs name the database service (`db:5432`). `python3 check.py` checks Ask at `ASK_URL`.

## What people get

The **Ask** tab beside Aggregate: a question in plain words, an answer with Chiron's
figures, follow-ups, a **Query · N** button that loads the answer's cohort into Chiron's
query builder, charts, saved reports, the Results tab. What Ask may see is what the person
may see in Chiron, capped by `CHIRON_MCP_MAX_ACCESS_LEVEL` (`deid` by default). Every
question runs Claude with only the Chiron tools: no shell, no files, no web. See the
repository README for the tools and the access model.

## Run it on the host instead (a SQLite deployment, no Docker)

The same server in the deployment's own Python environment: `pip install` chiron-mcp's
dependencies there (`constraints.txt` pins what was tested, SQLAlchemy below 2.1 above
all), Node 22 with `npm install -g @anthropic-ai/claude-code`, and run

```bash
CHIRON_MCP_CHIRON_SRC=/path/to/is4r-chiron CHIRON_MCP_PROJECT_DIR=/path/to/project \
CHIRON_MCP_METADATA_DB=/path/to/db.sqlite3 CHIRON_MCP_WAREHOUSE_URL=postgresql://... \
CHIRON_MCP_UI_URL=https://chiron.example.org CHIRON_MCP_REQUIRE_SESSION=1 \
CHIRON_MCP_ALLOW_SAVE=1 python -m chiron_mcp.webapp        # :8900, under systemd
```

The route and the tab are the same. The Claude login is the user's that runs it
(`claude` then `/login`). Keep the Ask process on the machine that holds the SQLite file.

## Undo

`docker compose down -v` (removes the container, its login and conversations; without `-v`
they are kept for a return) and `docker image rm chiron-askpatch`, delete the proxy snippet
and reload the web server, rebuild the UI without `VITE_ASK_URL` (delete `src/ask.tsx` and
the two lines in `src/overrideConfig.tsx` to take the code out too), remove the two navbar
settings lines if you added them.
Chiron's code, settings and databases were never changed, so nothing there to undo.
Reports people saved through Ask stay, as any saved report would.

## Proven on

- This repository's own stack: the Docker bundle in `deploy/` is the same server wired
  the same way, and `tests/` run against it.
- A stock Chiron 6.5.4 checkout (unpatched) with its own settings, on a Mac: a fresh
  clone installed by the five steps above with Bedrock as the model; `check.py` 10 of 10
  through Caddy, nginx and Apache, each with its shipped snippet; the Ask link in the
  built-in UI's navbar; the model menu; the aggregate-only account refused and masked as
  Chiron does; `tests/ask_e2e.py` (34 cases as three users) through the front door; the
  `within` timing tests, which need the run-time fixes, inside the container; four
  `git pull` and rebuild upgrades; removal and reinstall. Details and what it found in
  [docs/plan-askpatch.md](../docs/plan-askpatch.md).
- A plain Chiron 6.5 behind nginx, its UI with no Ask code at all (23 November 2026): a
  fresh clone, the steps above with the tab from `ui/ask.tsx`, a local model through
  `ANTHROPIC_BASE_URL`; `check.py` 10 of 10; then a pause, a full removal (`down -v`, the
  route out, the UI built without the switch: `/ask` falls through to the UI and Chiron
  works as before) and a reinstall, 10 of 10 again; a setting changed with `up -d`; an
  update with `git pull` and `up -d --build`. It found the nginx redirect dropping a
  published port (now relative) and `check.py` missing a figure written "10 000".
- A Chiron on a Mac with no web server (23 November 2026): Chiron's Django dev server and
  the UI's Vite dev server, Ask opened directly at `ASK_URL`; `check.py` 10 of 10, and in
  Chrome the Ask tab inside the dev UI answered and its Query button loaded the cohort
  (686 patients) into Chiron's query builder in the top window. Then Ask on a Docker
  Chiron's network (`compose.network.yml`, the database as `db:5432`), 10 of 10; and every
  model call through LiteLLM (`compose.litellm.yml`, v1.106.12) to a model behind an
  OpenAI-format endpoint, 10 of 10. Not tried yet: real OpenAI or Gemini keys, a Linux host.

## Setup guide for an AI agent

Paste this to Claude Code on the machine that runs Chiron. Fill in the first block.

````text
CHIRON_SRC      = /srv/chiron/is4r-chiron          (the checkout holding chiron/)
PROJECT_DIR     = /srv/chiron/is4r-chiron/test_project   (the directory holding manage.py)
PUBLIC_URL      = https://chiron.example.org       (where people open Chiron, with its prefix)
WEB_SERVER      = nginx                            (caddy, nginx or apache; its config file)
METADATA_DB     = postgresql://user:pass@host.docker.internal:5432/chiron_meta
WAREHOUSE_URL   = postgresql://user:pass@host.docker.internal:5432/chiron
MODEL           = claude api key | bedrock | openai or gemini (litellm) | local model (ollama) | claude account (a trial)
PHI             = yes | no                         (does the data hold PHI?)

Add the Ask tab to this Chiron with chiron-mcp's askpatch, without changing Chiron.
Repo: https://github.com/TojiFushiguro/chiron-mcp, clone to ~/chiron-mcp (git pull if there).
Read askpatch/README.md first.

Limits: touch only ~/chiron-mcp/askpatch, the chiron-askpatch container, the one web
server snippet, and the UI build. Do not edit Chiron's code, settings or databases, and
do not run migrations. Never print or paste askpatch/.env. Ask me before reloading the
web server and before rebuilding the UI. If PHI is yes, confirm with me that the model is
approved for PHI (a BAA, or a model on the organization's own network) before
connecting it.

1. Check Docker (Compose 2.17+) and that the two databases answer from a container:
   docker run --rm postgres:17 pg_isready -d "<each URL>". If one does not, stop and tell me.
2. Write askpatch/.env from env.example with the values above (chmod 600), and
   docker compose up -d --build. Show me the last lines of the build if it fails.
3. Show me the snippet from askpatch/proxy/ for WEB_SERVER, adapted to the prefix in
   PUBLIC_URL if any, and where it goes in the config; apply it when I agree, then reload.
   If Chiron runs on this computer with no web server, skip this and follow README
   "A Chiron on your own computer" (ASK_URL, and the tab pointing at Ask itself).
4. The UI: copy askpatch/ui/ask.tsx to its src/ask.tsx and wrap the settings in
   src/overrideConfig.tsx with withAsk (README, "The Ask tab"; if that file already has an
   Ask tab at http://localhost:8900, apply askpatch/ui-ask-tab.patch instead); then rebuild
   it with VITE_ASK_URL=/ask the way this deployment builds it, when I agree.
5. The model: for a Claude API key, or OpenAI or Gemini keys, tell me to add the key to
   .env myself; for OpenAI or Gemini also set COMPOSE_FILE, LITELLM_MASTER_KEY and the
   models (README "The model", litellm.yaml); for Bedrock or a local model set the lines
   from README "The model" in .env; for a Claude account tell me to run
   `docker compose exec -it ask claude` myself and log in. Then docker compose up -d.
6. python3 askpatch/check.py --user <an account I name> and show me every line.
7. Tell me what was added (container, snippet, UI switch), what was not touched, and how
   to remove it.
````
