# Sentient Notebooks — steps 1-5: full product, ready to deploy

A FastAPI service + a real frontend (not Swagger):

- Auth, notebooks, RAG, chat, notes — steps 1-4, unchanged from before
- **Login/signup pages and a dashboard UI** (drag-and-drop upload, notes,
  streaming chat) instead of testing through `/docs`
- **Real file storage** — uploaded files are now actually saved (local disk
  or any S3-compatible bucket), not just their extracted chunks
- **Account limits** — registration caps at `MAX_USERS` (default 5), each
  account gets exactly one notebook (auto-created at signup, no "create
  notebook" step to expose), and storage is quota'd per user (default
  ~400MB = 2GB / 5 users)
- **Groq instead of Ollama** — hosted LLM API, free tier, no server RAM to
  provision. Ollama's still there as an opt-in toggle for your own local use
- **A `/reindex` endpoint** — rebuilds the search index from stored files if
  it ever gets wiped (relevant once you're on a free host with a disk that
  doesn't survive redeploys -- more on this below)

All tested end-to-end before delivery, same as every step before this:
registration cap, auto-notebook-creation, the 1-notebook limit, raw file
upload/download/delete actually persisting to disk, quota enforcement,
chat still working with the new pluggable LLM backend, the frontend's
static files serving correctly alongside the API without route conflicts,
and reindex rebuilding a wiped collection from scratch (files + notes both
verified searchable again afterward).

## What's here (new/changed this round)

```
static/
  index.html, login.js     # login/signup page
  app.html, app.js         # dashboard: drag-drop upload, notes, chat
  style.css                # shared look
app/
  services/
    storage.py    # local disk OR any S3-compatible bucket, same interface
    quota.py      # per-user storage usage, computed on demand
    llm.py        # now pluggable: Groq (default) or Ollama (opt-in)
    indexing.py   # +reindex_document() for rebuilding from storage
  api/routes/
    documents.py  # +quota check on upload, +download endpoint
    notebooks.py  # +1-notebook-per-user limit, +/reindex endpoint
    auth.py       # +registration cap, +auto-creates the user's notebook
```

## Running it locally first

Same as before — `pip install -r requirements.txt`, copy `.env.example` to
`.env`. Two things you now need in `.env` before anything works:

1. **`JWT_SECRET`** — same as always, generate with
   `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. **`GROQ_API_KEY`** — free at [console.groq.com](https://console.groq.com),
   no credit card. Sign up, create an API key, paste it in.

Leave `STORAGE_BACKEND=local` for now — files just save to `./storage` on
your machine, nothing else to configure. Run `uvicorn app.main:app --reload`
same as before, but now open **http://127.0.0.1:8000/** (not `/docs`) —
that's the actual login page.

## What each account limit means in practice

- **`MAX_USERS=5`** — the 6th person to hit "Sign up" gets a clear "at
  capacity" message, not a confusing error. Raise this in `.env` whenever
  you want more accounts.
- **One notebook per account** — there's no notebook-picker anywhere in the
  UI because there's never more than one to pick. This was a deliberate
  simplification, not a missing feature — say if you actually want multiple
  notebooks per account later, it's a real feature to build, not a config
  flip.
- **`MAX_STORAGE_BYTES_PER_USER`** — defaults to ~400MB (2GB ÷ 5 users). A
  user who hits it gets a 413 on upload with exactly how much room they
  have left. Change the number in `.env` if you want a different split.

## Deploying this for real — the honest version

You need three things running: the app itself, a database, and somewhere
for files to live. Here's a stack that's **entirely free, no credit card,
for the scale you described** (5 users, ~2GB) — but "free tier" always
comes with a real tradeoff, so I'm naming each one rather than glossing
over it.

### 1. The app itself → Render (free web service)

Render's free tier: no card required, runs your app from a GitHub repo,
**spins down after 15 minutes of no traffic** and takes 30-60 seconds to
wake back up on the next request. For 5 people casually checking in, that's
a real but tolerable tradeoff — the alternative (always-on) starts around
$7/month once you're ready.

Steps:
1. Push this code to a GitHub repo.
2. On Render: New → Web Service → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add your `.env` values as environment variables in Render's dashboard
   (same names, no `.env` file needed on Render itself).

### 2. The database → Neon (permanent free Postgres)

Don't use Render's free Postgres for this — it **expires after 30 days**,
which will quietly delete every account and notebook. Neon's free tier has
no expiry and no card required (Supabase is an equally fine alternative if
you'd rather).

1. Sign up at neon.tech, create a project.
2. Copy the connection string it gives you.
3. Set `DATABASE_URL` in Render's env vars to that string (it'll look like
   `postgresql://user:pass@host/dbname` — the app already knows how to use
   it, nothing to change in code).

### 3. File storage → Cloudflare R2 (10GB free)

This is the piece that makes "bucket storage" literal. R2's free tier
covers your ~2GB target several times over, and — important detail — has
**no egress fees**, unlike AWS S3.

1. Sign up at Cloudflare, create an R2 bucket.
2. Create an R2 API token (gives you access key + secret).
3. Your endpoint URL is `https://<account_id>.r2.cloudflarestorage.com`
   (account ID is in the Cloudflare dashboard).
4. Set these in Render's env vars:
   ```
   STORAGE_BACKEND=s3
   S3_BUCKET=your-bucket-name
   S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
   S3_ACCESS_KEY=...
   S3_SECRET_KEY=...
   ```

### Why Chroma needs the `/reindex` safety net

Render's free tier disk is **ephemeral** — it can reset on redeploy. Your
users' accounts (Postgres) and their files (R2) are fine, those live
elsewhere. But the Chroma vector index lives on that local disk by default,
so it could get wiped.

If chat suddenly can't find content that should be there, hit
`POST /notebooks/{id}/reindex` (authenticated, same as any other endpoint)
and it rebuilds the index from the files in R2 and the notes already in
Postgres. I tested this specifically — wiped a collection, called reindex,
confirmed both a document and a note were searchable again afterward. It's
not automatic (nothing currently calls it on startup), so if this bites you
in practice and you want it automatic, that's a small addition, just say so.

### What this stack costs you

$0/month, no card, for the scale you described. The honest ceiling: Render
free cold-starts after idle, Neon free tier is 0.5GB (plenty for accounts +
metadata, this isn't where your files live), R2 free tier is 10GB, Groq
free tier is rate-limited (30 requests/min) but fine for 5 people. When any
of these stop being enough, each one has a paid tier that's a config change,
not a rewrite.

## Not in yet

- Multi-turn chat memory (still single-turn per your original CLI design)
- Automatic reindex-on-startup (manual trigger for now, see above)
- Multiple notebooks per account (deliberately capped at 1 for now)
