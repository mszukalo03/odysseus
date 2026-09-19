<p align="center">
  <img src="assets/branding/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, RSS feeds, and local model workflows.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="website/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="assets/branding/odysseus-browser.jpg" alt="Odysseus interface">
</p>

---

## Quick Start

> `dev` is the default branch and gets the newest changes first. Use [`main`](https://github.com/odysseus-dev/odysseus/tree/main) if you want the more curated branch.

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env
ODYSSEUS_BUNDLE_EXTENSIONS=true docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

`ODYSSEUS_BUNDLE_EXTENSIONS=true` includes the RSS reader and Ithaca dashboard shown above — omit it for a minimal image with neither; see [website/extensions.md](website/extensions.md) for what that flag does and how to install/remove extensions afterward.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](website/setup.md).

## Features

- **Chat + Agent** — 19+ providers out of the box (Anthropic, OpenAI, DeepSeek, Kimi/Moonshot, OpenRouter, Ollama Cloud, Groq, Mistral, Together AI, Fireworks, Gemini, xAI Grok, Z.AI, NVIDIA, OpenCode Zen/Go, and more), plus subscription bridges for GitHub Copilot and ChatGPT. Tool-using agent with MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and vLLM/llama.cpp serving, with llama.cpp auto-detection.
- **Deep Research** — multi-step web research with source reading, category-aware formatting, and report generation.
- **RSS Feed Reader** — 3-pane feed reader with nested groups, AI summaries, full-content extraction, OPML import/export, and YouTube channel support.
- **Extensions** — optional features (RSS, a home-hub dashboard) that ship separately from the base app, installable from a URL or a local build flag — see [website/extensions.md](website/extensions.md).
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, 13 canvas background animations (aurora, matrix rain, nebula, hex grid, and more), neon glow theme toggle, user avatars, 10 pluggable web search providers (SearXNG, Brave, Bing, Tavily, Serper, Exa, Firecrawl, and more), presets, sessions, and 2FA.

## Demo

A full hover-to-play tour lives on the [Odysseus landing page](https://odysseus-dev.github.io/odysseus/). Its source lives under [`website/`](website/).

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.

Deployment details are in the [setup guide](website/setup.md#security-notes).

## Star History

<a href="https://star-history.dera.page/#odysseus-dev/odysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
