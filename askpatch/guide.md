# Ask add-on for Chiron

**Ask** lets people type questions about a Chiron dataset in plain words, like "how many
patients have asthma?", and get answers built from Chiron's own numbers.

This guide shows how to add Ask to a Chiron you already run, and how to remove it again.
Ask doesn't change Chiron. You can take it out at any time, and Chiron goes back to
exactly how it was.

> [!WARNING]
> **If your Chiron holds PHI (real patient data), use a PHI-safe AI.** To answer, Ask
> sends the question and the data it looks up to the AI model. Use only an AI service your
> organization has approved for PHI (one covered by a business associate agreement, a
> BAA), or a model running on your own computer or network. Never use a personal account
> or a free plan. If you're not sure, ask your privacy or compliance office.

## In short

You add three things next to your Chiron:

1. **The Ask server.** A Docker container that reads Chiron's data as whoever is logged in.
2. **The `/ask` address.** A few lines in the web server in front of Chiron (nginx, Apache
   or Caddy).
3. **The Ask tab.** One small file and one build setting in the Chiron UI.

Remove those three and Ask is gone. If Chiron runs on your own computer, you don't even
need the web server part: see [Chiron on your own computer](#chiron-on-your-own-computer).

## What people get

- An **Ask** tab on every dataset: type a question, get an answer with Chiron's numbers,
  ask follow-up questions, see charts and tables.
- A **Query** button that opens the answer's patients in Chiron's query builder.
- **Saved conversations** they can share, copy, export as PDF, or report if an answer
  looks wrong.
- An **Admin page** for admins: usage and cost per person, who has what access, and the
  answers people reported.

People only ever see what their own Chiron account allows.

## How it works

```mermaid
flowchart LR
    U["People's browsers"] --> W["Your web server"]
    W -- "everything else" --> C["Chiron UI and API"]
    W -- "/ask (added)" --> A["Ask server (added)"]
    C --> D[("Chiron's databases")]
    A -- "reads, as the person logged in" --> D
    A -- "question and data" --> M["The AI model you choose"]
```

- Ask is on the same web address as Chiron, so it knows who is logged in. There is no
  extra login.
- For each question, Ask uses an AI model that can only use Chiron tools. It can't run
  commands, open files or browse the web.
- Every number comes from Chiron's own query engine, with the person's own permissions.
- Ask keeps its conversations and usage history in its own storage, not in Chiron.

## What you need

- **Chiron 6.5** (tested on 6.5.4), with its databases on Postgres
- **The Chiron UI source code** (`is4r-chiron-ui`, tested on 4.3), so you can rebuild it
  with the Ask tab
- **Your Chiron source code and Django project folder** on the same machine
- **Docker** with Compose 2.17 or newer, and about 1.3 GB of free disk
- **Access to the web server** in front of Chiron (nginx, Apache or Caddy). Not needed if
  Chiron runs on your own computer.
- **An AI model**: Claude, OpenAI, Gemini, or a model on your own computer. With PHI, a
  PHI-safe one (see the warning above).
- **About 2 GB of free memory** for Ask, a little more with the OpenAI/Gemini translator
- **Internet access** to build Ask, and to reach your AI model (not needed for a model on
  your own computer). Behind a proxy? See [Behind a proxy](#behind-a-proxy).
- **Chiron's normal login.** Ask sees who is signed in through Chiron's own login. Single
  sign-on in front of Chiron is fine, as long as people end up logged in to Chiron.

It works on a Mac, on a Windows PC (see [On Windows](#on-windows)) and on a server.

Other Chiron versions haven't been tested. The check in step 7 tells you quickly if
something doesn't fit.

## Add Ask in 7 steps

Do these once, on the machine that runs Chiron. Running Chiron on your own laptop or
desktop? Follow the same steps with the small changes in
[Chiron on your own computer](#chiron-on-your-own-computer). On Windows, a few commands
differ: see [On Windows](#on-windows).

### Step 1. Download the add-on

```bash
git clone https://github.com/TojiFushiguro/chiron-mcp.git
cd chiron-mcp/askpatch
cp env.example .env && chmod 600 .env
```

This downloads the add-on and creates `.env`, Ask's settings file. Run every command
after this from the `askpatch` folder.

### Step 2. Tell Ask where your Chiron is

Open `.env` and fill in these lines:

```bash
# where your Chiron code is, and the folder that has manage.py
CHIRON_SRC=/srv/chiron/is4r-chiron
CHIRON_PROJECT_DIR=/srv/chiron/is4r-chiron/my_project
# Chiron's two databases
CHIRON_MCP_METADATA_DB=postgresql://USER:PASSWORD@host.docker.internal:5432/chiron_meta
CHIRON_MCP_WAREHOUSE_URL=postgresql://USER:PASSWORD@host.docker.internal:5432/chiron
# the address people use to open Chiron
PUBLIC_URL=https://chiron.example.org
```

Good to know:

- If the database runs on the same machine, write `host.docker.internal`, not `localhost`.
- On Linux, Postgres also has to allow connections from Docker. See
  [If something goes wrong](#if-something-goes-wrong).
- Put comments on their own line, never at the end of a setting.
- `.env` holds your database password, so keep it private.

### Step 3. Pick the AI model

> [!IMPORTANT]
> If your data has PHI, pick an AI service your organization has approved for PHI, or a
> model on your own computer or network. See the warning at the top.

Pick **one** of these and add its lines to `.env`:

| AI model | What to add to `.env` |
| --- | --- |
| **Claude** with an Anthropic API key | `ANTHROPIC_API_KEY=sk-ant-...` |
| **Claude** on Amazon Bedrock | `CLAUDE_CODE_USE_BEDROCK=1` and `AWS_REGION=us-east-1` |
| **OpenAI** or **Gemini** | The lines under "OpenAI or Gemini" below |
| **A model on your own computer** (Ollama) | The lines under "A model on your own computer" below |

**OpenAI or Gemini.** Ask talks to these through a small translator, LiteLLM, which comes
with the add-on and starts next to Ask. Add your key (or both) and these lines:

```bash
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
# any long random string, for example from: python3 -c "import secrets; print(secrets.token_hex(24))"
LITELLM_MASTER_KEY=...
# the models people can pick, as id=Label, separated by commas
CHIRON_MCP_ASK_MODELS=gpt-6.2-sol=GPT-6.2 Sol,gemini-3.8-flash=Gemini 3.8 Flash
# start the translator together with Ask
COMPOSE_FILE=compose.yml:compose.litellm.yml
```

The models are listed in [litellm.yaml](litellm.yaml). To use other OpenAI or Gemini
models, change the names there and in `CHIRON_MCP_ASK_MODELS`.

**A model on your own computer** (Ollama). Nothing leaves your computer, which makes it
a PHI-safe choice:

```bash
ANTHROPIC_BASE_URL=http://host.docker.internal:11434
ANTHROPIC_AUTH_TOKEN=not-needed
DISABLE_PROMPT_CACHING=1
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
CHIRON_MCP_ASK_MODELS=your-model=Your model
```

- The model needs good tool calling and a context of at least 64k tokens. In Ollama, make
  a version of the model with `PARAMETER num_ctx 65536` in its Modelfile.
- On Linux, start Ollama with `OLLAMA_HOST=0.0.0.0`, so Ask's container can reach it.
- Other model servers (vLLM, LM Studio) work through the translator: add them to
  `litellm.yaml`.

Good to know:

- **Bedrock on an EC2 server** uses the server's own AWS role (its metadata hop limit
  must be 2). Anywhere else, set `AWS_PROFILE` in `.env` and add
  `COMPOSE_FILE=compose.yml:compose.aws.yml`.
- **The model menu:** `CHIRON_MCP_ASK_MODELS` lists the models people can switch between.
  With Claude, for example:
  `CHIRON_MCP_ASK_MODELS=claude-haiku-5-5=Haiku 5.5,claude-sonnet-5-5=Sonnet 5.5`.
  On Bedrock, the ids start with `us.anthropic.`.
- **Ask was built and tested mostly with Claude.** Other models can work well too, but
  compare a few of their answers with Chiron before you rely on them.

### Step 4. Start Ask

```bash
docker compose up -d --build
docker compose ps
```

The first start takes a few minutes. Ask is ready when `docker compose ps` shows
**healthy**.

### Step 5. Add the `/ask` address to your web server

On your own computer, skip this step.

This sends `/ask` on your Chiron address to Ask.

**nginx:** paste this into the `server` block that serves Chiron, above its
`location /`:

```nginx
location = /ask {
    absolute_redirect off;
    return 308 /ask/;
}
location /ask/ {
    proxy_pass http://127.0.0.1:8900/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

Then reload nginx: `sudo nginx -t && sudo nginx -s reload`

**Apache:** paste the lines from [proxy/ask-apache.conf](proxy/ask-apache.conf) into
Chiron's virtual host, above any `ProxyPass` for `/`. Then run `sudo apachectl graceful`.

**Caddy:** import [proxy/ask.caddy](proxy/ask.caddy) inside Chiron's site block, above
its catch-all. Then run `caddy reload --config /etc/caddy/Caddyfile`.

Good to know:

- If Chiron lives under a path like `/chiron`, use `/chiron/ask` wherever this guide says
  `/ask`.
- If your web server itself runs in Docker, point it at an address that container can
  reach, and set `ASK_BIND` in `.env` to match.

### Step 6. Add the Ask tab to the Chiron UI

In the Chiron UI source folder (`is4r-chiron-ui`):

1. Copy the tab file in:

   ```bash
   cp /path/to/chiron-mcp/askpatch/ui/ask.tsx src/ask.tsx
   ```

2. Open `src/overrideConfig.tsx` and make two changes: add the import line at the top, and
   wrap your existing settings in `withAsk( ... )`:

   ```tsx
   import { withAsk } from "./ask";

   export const overrideConfig = withAsk({
     // ...your settings, unchanged...
   });
   ```

3. Rebuild the UI with Ask turned on, then deploy it the way you usually do:

   ```bash
   VITE_ASK_URL=/ask npm run build
   ```

   Use `pnpm run build` instead if that's how you build. On your own computer, use
   `VITE_ASK_URL=http://localhost:8900` instead of `/ask`. If you build the UI with
   Docker, see [Building the UI with Docker](#building-the-ui-with-docker).

### Step 7. Check that it works

```bash
python3 check.py --user SOME_CHIRON_USER --dataset SOME_DATASET
```

Pick a Chiron account that can see that dataset. Every line should say **PASS**. No
password is needed: the check makes a short login inside Ask by itself.

Then open Chiron, pick a dataset and click the **Ask** tab.

## Chiron on your own computer

If Chiron runs on your own laptop or desktop, either with its development servers (Django
and the UI's dev server) or in Docker, you don't need a web server. Your browser opens Ask
directly at `http://localhost:8900`, and because Chiron and Ask are both on `localhost`,
Ask can see that you are logged in to Chiron.

Follow the 7 steps with these changes:

- **Step 2:** `PUBLIC_URL` is the address where you open Chiron, for example
  `http://localhost:3000`. Also add this line:

  ```bash
  ASK_URL=http://localhost:8900
  ```

- **Step 5:** skip it.
- **Step 6:** point the tab at Ask directly:
  - with the UI's dev server: `VITE_ASK_URL=http://localhost:8900 npm run dev`
  - with a UI in Docker: build its image with `--build-arg VITE_ASK_URL=http://localhost:8900`
- **Always open Chiron at `http://localhost:...`**, not `http://127.0.0.1:...`. Your login
  is saved for the name `localhost`, and that's the name Ask uses.

**Where are the databases?**

- **Postgres on your computer, or in Docker with its port published** (for example
  `5433:5432`): use `host.docker.internal` and that port in Step 2, as in
  `postgresql://USER:PASSWORD@host.docker.internal:5433/chiron`.
- **Postgres in Docker without a published port:** let Ask join Chiron's Docker network.
  Find the network's name with `docker network ls`, then add these lines to `.env`:

  ```bash
  CHIRON_NETWORK=the-network-name
  COMPOSE_FILE=compose.yml:compose.network.yml
  ```

  In the database addresses, use the database's name inside Docker (often `db`) and its
  inside port, as in `postgresql://USER:PASSWORD@db:5432/chiron`.

If port 8900 is already taken on your computer, pick another one, for example 8906:
`ASK_BIND=127.0.0.1:8906`, `ASK_URL=http://localhost:8906`, and
`VITE_ASK_URL=http://localhost:8906`.

Using the translator for OpenAI or Gemini and Chiron's Docker network together? List all
three files: `COMPOSE_FILE=compose.yml:compose.litellm.yml:compose.network.yml`.

## On Windows

Ask works with Docker Desktop on Windows too. It hasn't been tried on Windows yet, so
these are the differences to know:

- Run the commands in PowerShell, with two changes:
  - Step 1: use `copy env.example .env` instead of `cp env.example .env && chmod 600 .env`.
  - Step 7 and the key in Step 3: use `python` instead of `python3`.
- In `.env`, write paths with forward slashes, for example
  `CHIRON_SRC=C:/Users/you/is4r-chiron`.
- For the Ask tab (Step 6), put the setting in a file instead of in front of the command.
  In the UI folder, create a file named `.env.local` with this one line:

  ```bash
  VITE_ASK_URL=/ask
  ```

  (or `VITE_ASK_URL=http://localhost:8900` on your own computer). Then run
  `npm run build` or `npm run dev` as usual. To remove Ask later, delete that line.
- Postgres installed directly on Windows works as it is: nothing to change.
- If Ask can't reach Ollama on the same PC, start Ollama with `OLLAMA_HOST=0.0.0.0`.
- The `COMPOSE_FILE` lines work as written, because `.env` already has
  `COMPOSE_PATH_SEPARATOR=:`.

## Who can see what

Ask follows Chiron's own permissions. Nobody gets more than their account allows.

- **Access level, per dataset:** aggregate only, de-identified, or full. Ask also has a
  cap, `CHIRON_MCP_MAX_ACCESS_LEVEL` in `.env`. It is `deid` (de-identified) unless you
  change it to `phi`.
- **Names are a separate switch,** "may see identifying columns", per dataset. Full access
  alone doesn't show names. The switch has to be on too.
- **Aggregate-only accounts** get counts only, with small counts hidden, and never lists
  of patients.

### The Admin page

Open `/ask/usage` on your Chiron address (`http://localhost:8900/usage` on your own
computer).

- **Superusers** see everyone and can change access: open the **People** tab, click
  **Edit** next to a person, then set the level and the names switch for each dataset.
  **Make admin** makes that person a superuser in Chiron.
- **Everyone else** sees only their own usage.
- Optional: members of the group named in `CHIRON_MCP_USAGE_GROUP` can see everyone's
  usage, but can't change anything.

Changes made here are saved in Chiron itself.

**Tip:** after you give someone more access, they should start a **new conversation**.
An old conversation may repeat an earlier "no".

`/ask/whoami` shows who Ask thinks you are, and your access on each dataset.

## What gets sent to the AI model

To answer, Ask sends the question and the data it looked up (including any patient rows
it shows) to the model you picked. That's why data with PHI needs a PHI-safe AI (see the
warning at the top). If you run the model on your own computer or network, nothing leaves
it.

Ask's usage history and logs don't store question text. The trace saved with each answer
does, on your own server.

## Everyday tasks

| I want to... | Do this |
| --- | --- |
| Change a setting (model, access cap, ...) | Edit `.env`, then run `docker compose up -d` |
| See usage, costs and errors | Open the Admin page, `/ask/usage` |
| Watch what Ask is doing | `docker compose logs -f --no-log-prefix ask \| grep ' ask '` |
| Update Ask | `git pull`, then `docker compose up -d --build`, then `python3 check.py` |

Updates keep conversations, settings and usage history. Extra files you turned on with
`COMPOSE_FILE` in `.env` are picked up by every command automatically.

## Turn Ask off for a while

```bash
docker compose stop     # turn off
docker compose start    # turn on again
```

Nothing is lost. While Ask is off, the Ask tab shows an error page and the rest of Chiron
works normally. Ask stays off after a reboot until you start it again.

## Remove Ask completely

1. `docker compose down -v` stops Ask (and the translator, if you used it) and
   **deletes** its conversations and usage history. This can't be undone. Leave out `-v`
   to keep them.
2. `docker image rm chiron-askpatch` frees the disk space. If you used the translator,
   also run `docker image rm ghcr.io/berriai/litellm:v1.106.12`.
3. Delete the `/ask` lines from your web server, and reload it (not needed on your own
   computer).
4. Rebuild the UI without `VITE_ASK_URL`, and deploy it. To remove the code too, delete
   `src/ask.tsx` and undo the two changes in `src/overrideConfig.tsx`.
5. Delete the `chiron-mcp` folder. Its `.env` has your database password and AI keys.

Chiron is now exactly as before. The only things left are what people did through Ask
that Chiron allows anyway: saved reports, patient groups opened in the query builder, and
access changes made on the Admin page.

## Add it back later

Repeat steps 4 to 7. If you used `docker compose down` without `-v`, the old
conversations come back too. If you deleted the folder, start from step 1.

## If something goes wrong

| Problem | Fix |
| --- | --- |
| `docker compose ps` never shows healthy | Run `docker compose logs ask`. "No module named 'project'" means `CHIRON_PROJECT_DIR` is wrong, or `.env` needs `CHIRON_SETTINGS=your.settings.module`. |
| Ask can't connect to the database | Use `host.docker.internal`, not `localhost`. On Linux, let Postgres accept Docker connections: set `listen_addresses`, and add Docker's network (often `172.16.0.0/12`) to `pg_hba.conf`. |
| The build fails on a package version conflict | Add `USE_CONSTRAINTS=0` to `.env` and build again. |
| `/ask` shows "502 Bad Gateway" | Ask is stopped, or the web server points to the wrong place. Run `docker compose ps`, and check the address in your web server lines. |
| Ask says "Log in to Chiron first" but you are logged in | Open Ask only through `/ask` on Chiron's own address. On your own computer: open Chiron at `localhost`, not `127.0.0.1`, and make sure `ASK_URL` and `VITE_ASK_URL` use the port in `ASK_BIND`. |
| Answers appear all at once, or stop after a minute | The web server is buffering or timing out. Use the web server lines exactly as given. |
| An answer stops after 4 minutes | That's the time limit per answer (`CHIRON_MCP_TURN_SECONDS`, 240 seconds). Ask a smaller question, or raise the limit. Local models are often slower. |
| "Invalid API key" or another model error | Fix the model lines in `.env`, then run `docker compose up -d`. With OpenAI or Gemini, check `docker compose logs litellm` too. |
| OpenAI or Gemini: "model not found" | Each name in `CHIRON_MCP_ASK_MODELS` must match a `model_name` in `litellm.yaml`, and the provider must still offer that model. |
| No Ask tab in Chiron | Check the two changes in `src/overrideConfig.tsx`, and that you built with `VITE_ASK_URL`. |
| Ask won't show names after you turned them on | Start a new conversation. |

## Extra options

### Building the UI with Docker

In the UI's Dockerfile, add `ARG VITE_ASK_URL` and `ENV VITE_ASK_URL=${VITE_ASK_URL}`
next to the `VITE_MUI_KEY` lines. Then build with `--build-arg VITE_ASK_URL=/ask` (or
`--build-arg VITE_ASK_URL=http://localhost:8900` on your own computer).

### A different settings module

If your Django settings module isn't `project.settings`, add
`CHIRON_SETTINGS=your.settings.module` to `.env`.

### Chiron's built-in Django pages

If you use Chiron's own Django pages instead of the React UI, two settings lines add an
Ask link to their menu bar. See "The Ask tab" in the [README](README.md).

### Chiron with SQLite

If Chiron keeps its metadata in SQLite, Ask can't run in Docker. The
[README](README.md#run-it-on-the-host-instead-a-sqlite-deployment-no-docker) shows how to
run it directly on the server instead.

### Behind a proxy

If your network reaches the internet through a proxy:

- For building Ask, set the proxy in Docker Desktop: Settings, Resources, Proxies.
- For answers, add these lines to `.env`:

  ```bash
  HTTPS_PROXY=http://proxy.example.org:8080
  NO_PROXY=localhost,127.0.0.1,host.docker.internal,litellm
  ```

  `NO_PROXY` keeps Ask's calls to a model on your own computer, and to the translator,
  off the proxy. The translator for OpenAI or Gemini uses the same two lines.

### Trying it with your own Claude account

For a quick test by yourself, you can log the container in to your own Claude account
with `docker compose exec -it ask claude`. Don't use this for a team, and never with PHI.

## Tested on

- Chiron 6.5.4 with UI 4.3.0, behind nginx, Apache and Caddy: every step, the check,
  turning Ask off, removing it, adding it back, and updating.
- Chiron on a Mac with no web server: Chiron's development servers, with the Ask tab,
  answers and the Query button tried in a real browser; and Ask joined to the Docker
  network of a Chiron running in Docker.
- AI models: Claude on Amazon Bedrock; a model on the same computer (Ollama), both
  directly and through the OpenAI/Gemini translator.
- **Not tested yet:** real OpenAI, Gemini and Anthropic API keys, Windows, and a Linux
  server (so far every test ran on a Mac with Docker Desktop).
