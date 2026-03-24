# GAVIN — Generate Audio Via Intelligent Narration

**Send a book title or topic to Telegram. Get a fully researched, narrated podcast episode published to your private feed. Automatically.**

GAVIN is an end-to-end AI pipeline that transforms any research topic into a production-ready podcast episode — no human in the loop required. It uses a two-pass Claude architecture to separate deep research from long-form narration, converts the script to audio via ElevenLabs, publishes it to your Transistor.fm feed, and gets smarter with every episode through a built-in feedback loop.

---

## What It Does

```
You → Telegram: "Good to Great by Jim Collins"

                  ┌─────────────────────────────────────┐
                  │           GAVIN Pipeline             │
                  │                                     │
                  │  Pass 1 — Research                  │
                  │  Claude Sonnet 4.6 + Web Search     │
                  │  Plans research questions, runs      │
                  │  multiple searches, compiles a       │
                  │  structured research brief           │
                  │               │                     │
                  │               ▼                     │
                  │  Pass 2 — Writing                   │
                  │  Claude Opus 4.6 (search disabled)  │
                  │  Writes a 4,000–6,000 word          │
                  │  narrative script from the brief.   │
                  │  No searching — pure writing.       │
                  │               │                     │
                  │               ▼                     │
                  │  ElevenLabs TTS → MP3               │
                  │  Transistor.fm → Published          │
                  └─────────────────────────────────────┘

You ← Telegram: "✅ Your episode is ready — ~28 minutes of audio"

     ... 60 minutes later ...

GAVIN → Telegram: "⭐ How many stars would you give this episode?"

     You rate it. GAVIN learns. Every future episode is better.
```

---

## Key Features

**Two-Pass AI Architecture**
Research and writing are intentionally separated into two distinct Claude calls. Pass 1 is purely about information gathering — Claude searches aggressively, follows threads, and builds a detailed research brief without worrying about narrative quality. Pass 2 receives that brief and writes with no distractions — no searching, no tool calls, just focused long-form writing. Mixing these tasks produces worse results at both. Separating them produces better research and better writing.

**Company Context Injection**
Every episode connects the topic's insights back to your company, products, and strategy. Define your company once in `COMPANY_MEMORY.md` — your products, customers, differentiators, and strategic priorities — and GAVIN maps every framework, case study, and key insight to your specific context automatically. Update it anytime via a `/memory` Telegram command, no redeploy required.

**A Feedback Loop That Actually Learns**
60 minutes after each episode publishes, GAVIN sends a Telegram message asking for a 1–5 star rating and optional text feedback. Claude Haiku then synthesizes your complete feedback history into an evolving "lessons learned" document — what makes an episode valuable, what to emphasize, what to avoid — and prepends it to both research and writing prompts on every future run. The system compounds.

**Production-Grade and Simple to Run**
FastAPI server on Railway, persistent volume storage for memory, automatic Telegram webhook registration on startup. One `.env` file to fill in, one deploy command. No databases, no infrastructure beyond Railway.

---

## Episode Quality

Each episode follows a consistent five-part structure:

1. **Hook** — a striking question, fact, or claim that demands attention
2. **Context** — who built this idea, when, and why it matters now
3. **Deep Analysis** — core frameworks, key evidence, counterarguments, named case studies
4. **Company Applications** — specific, reasoned connections to your business (not generic tie-ins)
5. **Strategic Synthesis** — the 10 most actionable implications for your leadership team

Typical output: 4,000–6,000 words / 20–35 minutes of audio.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Server | Python, FastAPI, asyncio |
| AI Research (Pass 1) | Claude Sonnet 4.6 + Anthropic Web Search |
| AI Writing (Pass 2) | Claude Opus 4.6 |
| Feedback Synthesis | Claude Haiku |
| Text-to-Speech | ElevenLabs |
| Audio Concatenation | ffmpeg |
| Podcast Publishing | Transistor.fm REST API |
| Bot Interface | Telegram (python-telegram-bot) |
| Deployment | Railway (nixpacks, persistent volume) |
| Memory | JSON file on Railway volume |

---

## Prerequisites

You'll need accounts and API keys for:

| Service | What It's For | Cost |
|---|---|---|
| [Anthropic](https://console.anthropic.com/) | Claude Sonnet + Opus for research and writing | ~$1–3/episode |
| [ElevenLabs](https://elevenlabs.io/) | Text-to-speech narration | ~$0.50–1/episode |
| [Transistor.fm](https://transistor.fm/) | Private podcast hosting and publishing | ~$19/month |
| [Telegram](https://t.me/botfather) | Bot interface for sending topics and receiving episodes | Free |
| [Railway](https://railway.app/) | Hosting the FastAPI server | ~$5/month |

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/yourusername/gavin.git
cd gavin
cp .env.example .env
```

Open `.env` and fill in every value. See `.env.example` for descriptions of each variable.

**Finding your Transistor Show ID:** Go to your Transistor dashboard → your show → Settings. The numeric ID is in the URL.

**Finding your Telegram User ID:** Message [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your ID instantly.

### 2. Set your company context

Edit `COMPANY_MEMORY.md` to describe your company. This is what makes GAVIN's analysis specific and useful rather than generic. The file includes a template with guided prompts. The more specific you are, the better.

### 3. Deploy to Railway

**Via GitHub (recommended):**

1. Push the repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. In Railway → Variables, add every key from your `.env` file
5. Railway deploys automatically and registers the Telegram webhook on startup

**Via Railway CLI:**
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Add your environment variables in the Railway dashboard Variables tab.

### 4. Verify it's working

```bash
# Check the health endpoint
curl https://your-railway-domain.up.railway.app/health
# → {"status": "ok", "service": "GAVIN"}
```

Check Railway logs for `✅ Telegram webhook registered`. Then send `/start` to your bot.

### 5. Subscribe to your podcast feed

In Transistor, copy your private RSS feed URL. Add it to Apple Podcasts, Overcast, or any podcast app. Episodes appear automatically after each run.

---

## Usage

Send any message to your bot:

```
Good to Great by Jim Collins
The Lean Startup
Naval Ravikant on wealth and happiness
Jobs-to-be-done theory
The psychology of compounding habits
```

GAVIN acknowledges immediately, runs the pipeline in the background (typically 10–20 minutes end-to-end), and sends a completion message with word count, audio duration, and a link to the episode.

---

## Telegram Commands

| Command | What It Does |
|---|---|
| Any text | Kicks off a full research and narration pipeline |
| `/memory` | View or update your company context (instant, no redeploy) |
| `/lessons` | View the synthesized lessons from all your rated episodes |
| `/episodes` | View your full rated episode history |
| `/skip` | Cancel pending feedback input |
| `/help` | Show all available commands |

---

## Memory System

GAVIN maintains two layers of persistent memory, both injected into every pipeline run.

**Company Memory** (`COMPANY_MEMORY.md`)
Defines your company context — products, customers, differentiators, strategic priorities. Stored in the repo (update via GitHub) or on the Railway volume (update via `/memory` in Telegram — takes priority, no redeploy needed).

**Episode Lessons** (synthesized automatically)
After each rated episode, Claude Haiku reads your complete feedback history and rewrites the lessons doc. What types of episodes score highest. What depth and style works. What to emphasize or avoid. Prepended to both research and writing system prompts on every run. The more you rate, the better it gets.

---

## Architecture Notes

**Why two passes?**
Single-pass "research and write" produces mediocre output at both. In research mode, the model needs to search aggressively, follow unexpected threads, and compile everything without worrying about narrative flow. In writing mode, it needs to focus entirely on structure, voice, and pacing — not fetch more information. Separating them is the single most impactful quality improvement in the architecture.

**Why Sonnet for research, Opus for writing?**
Pass 1 involves many sequential tool calls and large input token volumes from web search results. Sonnet handles this faster, at lower cost, and with higher rate limits. Pass 2 is a single generation where quality is everything — Opus consistently produces better long-form narrative writing and is worth the cost differential.

**Rate limit handling**
Web search in Pass 1 can push significant input token volume through the API quickly. GAVIN disables the Anthropic SDK's built-in retry logic and implements its own: on a 429 rate limit error, wait 65 seconds for the one-minute token window to reset, then retry. This is more reliable than exponential backoff for token-based (rather than request-based) rate limits.

**Audio pipeline**
ElevenLabs has a character limit per API request. GAVIN splits the script at sentence boundaries into ~4,800 character chunks, converts each independently via the ElevenLabs REST API, then concatenates the resulting MP3 files using ffmpeg's concat demuxer. No audio processing libraries — just the system ffmpeg binary, which Railway installs via nixpacks.

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Install ffmpeg (macOS)
brew install ffmpeg

# Copy and fill in env vars
cp .env.example .env

# Run the server
python app.py
```

To test the pipeline directly without Telegram:

```python
import asyncio
from pipeline import run_gavin_pipeline

result = asyncio.run(run_gavin_pipeline("Good to Great by Jim Collins"))
print(result)
```

---

## File Structure

```
gavin/
├── app.py              # FastAPI server, webhook endpoint, startup
├── bot.py              # Telegram handlers, feedback flow, memory commands
├── pipeline.py         # Main orchestration: research → write → audio → publish
├── research.py         # Claude API integration, two-pass architecture
├── audio.py            # ElevenLabs TTS, ffmpeg MP3 concatenation
├── publisher.py        # Transistor.fm upload and publish flow
├── memory.py           # Episode memory, company memory, Haiku synthesis
├── system_prompt.py    # Research and writing system prompts (edit to customize)
├── config.py           # Environment variable loading and validation
├── COMPANY_MEMORY.md   # Your company context — edit this
├── railway.toml        # Railway deployment config (ffmpeg, Python, start command)
├── requirements.txt    # Python dependencies
└── .env.example        # Environment variable template
```

---

## Customization

**Change episode style or length**
Edit `GAVIN_SYSTEM_PROMPT` in `system_prompt.py`. The current prompt targets 4,000–6,000 words. You can adjust length, structure, tone, section order, or any stylistic preference.

**Change research depth**
Edit `RESEARCH_SYSTEM_PROMPT` in `system_prompt.py`. Adjust the required number of searches, the sections of the research brief, and the quality bar.

**Change the voice**
Update `ELEVENLABS_VOICE_ID` in your `.env`. Browse available voices in the [ElevenLabs Voice Library](https://elevenlabs.io/voice-library).

**Use a different podcast host**
Replace `publisher.py` with an integration for your preferred platform. Transistor has a clean three-step REST API (authorize upload → PUT to S3 → create episode → publish). Most major platforms (Buzzsprout, Podbean, Simplecast) follow a similar pattern.

---

## Contributing

Pull requests welcome. If you extend GAVIN — new output formats, additional research sources, alternative TTS providers, different memory backends — open a PR.

---

## License

MIT
