# Ask add-on for Chiron

**Ask** lets people type questions about a Chiron dataset in plain words, like "how many
patients have asthma?", and get answers built from Chiron's own numbers.

This guide shows how to add Ask to a Chiron you already run, and how to remove it again.
Ask doesn't change Chiron. You can take it out at any time, and Chiron goes back to
exactly how it was.

## In short

You add three things next to your Chiron:

1. **The Ask server.** A Docker container that reads Chiron's data as whoever is logged in.
2. **The `/ask` address.** A few lines in the web server in front of Chiron (nginx, Apache
   or Caddy).
3. **The Ask tab.** One small file and one build setting in the Chiron UI.

Remove those three and Ask is gone.

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
- **Access to the web server** in front of Chiron (nginx, Apache or Caddy)
- **An AI model**: an Anthropic API key, Amazon Bedrock, or a model server you run yourself

Other Chiron versions haven't been tested. The check in step 7 tells you quickly if
something doesn't fit.

## Add Ask in 7 steps

Do these once, on the machine that runs Chiron.

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

Add **one** of these to `.env`:

| Model | Add to `.env` |
| --- | --- |
| Anthropic API key | `ANTHROPIC_API_KEY=sk-ant-...` |
| Amazon Bedrock | `CLAUDE_CODE_USE_BEDROCK=1` and `AWS_REGION=us-east-1` |
| Your own model server | The five lines below |

For your own model server (Ollama, or vLLM behind LiteLLM):

```bash
ANTHROPIC_BASE_URL=http://your-model-server:11434
ANTHROPIC_AUTH_TOKEN=not-needed
DISABLE_PROMPT_CACHING=1
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
CHIRON_MCP_ASK_MODELS=your-model=Your model
```

Good to know:

- **Bedrock on an EC2 server** uses the server's own AWS role (its metadata hop limit
  must be 2). Anywhere else, also set `AWS_PROFILE` in `.env`, and add
  `-f compose.yml -f compose.aws.yml` to every `docker compose` command.
- **The model menu** is optional. `CHIRON_MCP_ASK_MODELS` lists the models people can
  choose from, as `id=Label`, separated by commas. For example:
  `CHIRON_MCP_ASK_MODELS=claude-haiku-5-5=Haiku 5.5,claude-sonnet-5-5=Sonnet 5.5`.
  On Bedrock, the ids start with `us.anthropic.`.

### Step 4. Start Ask

```bash
docker compose up -d --build
docker compose ps
```

The first start takes a few minutes. Ask is ready when `docker compose ps` shows
**healthy**.

### Step 5. Add the `/ask` address to your web server

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

   Use `pnpm run build` instead if that's how you build. If you build the UI with
   Docker, see [Building the UI with Docker](#building-the-ui-with-docker).

### Step 7. Check that it works

```bash
python3 check.py --user SOME_CHIRON_USER --dataset SOME_DATASET
```

Pick a Chiron account that can see that dataset. Every line should say **PASS**. No
password is needed: the check makes a short login inside Ask by itself.

Then open Chiron, pick a dataset and click the **Ask** tab.

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

Open `/ask/usage` on your Chiron address.

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
it shows) to the model you picked. If you run your own model server, nothing leaves your
network.

Ask's usage history and logs don't store question text. The trace saved with each answer
does, on your own server.

## Everyday tasks

| I want to... | Do this |
| --- | --- |
| Change a setting (model, access cap, ...) | Edit `.env`, then run `docker compose up -d` |
| See usage, costs and errors | Open `/ask/usage` |
| Watch what Ask is doing | `docker compose logs -f --no-log-prefix ask \| grep ' ask '` |
| Update Ask | `git pull`, then `docker compose up -d --build`, then `python3 check.py` |

Updates keep conversations, settings and usage history.

## Turn Ask off for a while

```bash
docker compose stop     # turn off
docker compose start    # turn on again
```

Nothing is lost. While Ask is off, the Ask tab shows an error page and the rest of Chiron
works normally. Ask stays off after a reboot until you start it again.

## Remove Ask completely

1. `docker compose down -v` stops Ask and **deletes** its conversations and usage history.
   This can't be undone. Leave out `-v` to keep them.
2. `docker image rm chiron-askpatch` frees the disk space.
3. Delete the `/ask` lines from your web server, and reload it.
4. Rebuild the UI without `VITE_ASK_URL`, and deploy it. To remove the code too, delete
   `src/ask.tsx` and undo the two changes in `src/overrideConfig.tsx`.
5. Delete the `chiron-mcp` folder. Its `.env` has your database password.

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
| Ask says "Log in to Chiron first" but you are logged in | Open Ask only through `/ask` on Chiron's own address. |
| Answers appear all at once, or stop after a minute | The web server is buffering or timing out. Use the web server lines exactly as given. |
| An answer stops after 4 minutes | That's the time limit per answer (`CHIRON_MCP_TURN_SECONDS`, 240 seconds). Ask a smaller question, or raise the limit. |
| "Invalid API key" or another model error | Fix the model lines in `.env`, then run `docker compose up -d`. |
| No Ask tab in Chiron | Check the two changes in `src/overrideConfig.tsx`, and that you built with `VITE_ASK_URL=/ask`. |
| Ask won't show names after you turned them on | Start a new conversation. |

## Extra options

### Building the UI with Docker

In the UI's Dockerfile, add `ARG VITE_ASK_URL` and `ENV VITE_ASK_URL=${VITE_ASK_URL}`
next to the `VITE_MUI_KEY` lines. Then build with `--build-arg VITE_ASK_URL=/ask`.

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

### Trying it with your own Claude account

For a quick test by yourself, you can log the container in to your own Claude account
with `docker compose exec -it ask claude`. Don't use this for a team.

## Tested on

- Chiron 6.5.4 with UI 4.3.0, behind nginx, Apache and Caddy: every step, the check,
  turning Ask off, removing it, adding it back, and updating.
- Models: Amazon Bedrock, and a model server running on the same network.
- **Not tested yet:** a Linux server (so far every test ran on a Mac with Docker Desktop),
  and a real Anthropic API key.
