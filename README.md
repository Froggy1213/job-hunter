# Job Hunter Bot

Async Telegram bot that aggregates job listings (design jobs in Tokyo) from Japanese job boards.

## Features

- **Asynchronous Scraping**: Efficiently scrapes multiple job boards concurrently.
- **Modular Architecture**: Built with clean architecture principles using Strategy and Repository patterns.
- **Easy Extensibility**: Simple process to add new job board sources.

## Architecture

```
main.py                          ← Composition root (DI wiring)
├── config/settings.py           ← Pydantic Settings from .env
├── core/                        ← Infrastructure (exceptions, logging, DI container)
├── models/                      ← Domain models (JobPosting, SourcePlatform)
├── database/                    ← Repository pattern over SQLAlchemy async
│   ├── repository.py            ← JobRepository ABC
│   └── sqlalchemy_repository.py ← Concrete implementation
├── scrapers/                    ← Strategy pattern
│   ├── base.py                  ← BaseScraper ABC
│   ├── orchestrator.py          ← Runs scrapers in parallel, deduplicates
│   └── implementations/         ← One file per job board
└── bot/                         ← aiogram v3 handlers & middleware
```

### Key design decisions

- **Strategy pattern**: Each job board is one scraper class. The orchestrator treats them polymorphically.
- **Repository pattern**: Application code works with `JobPosting` domain models; SQLAlchemy is an implementation detail.
- **Manual DI**: No framework — all wiring is explicit in `main.py`.
- **Immutable domain model**: `JobPosting` is frozen after construction.
- **Single `SourcePlatform` enum**: Shared by models, ORM, scrapers, and bot — add a member here when adding a new board.

## Setup

```bash
# Install dependencies
uv sync --dev

# Install Playwright browser (needed for JS-rendered scrapers)
uv run playwright install chromium

# Copy and edit the environment file
cp .env.example .env
# Edit .env → paste your Telegram bot token from @BotFather

# Run
uv run python main.py
```

## Adding a new job board

1. Add the platform to `models/enums.py` → `SourcePlatform`
2. Create `scrapers/implementations/<board>_scraper.py` subclassing `BaseScraper`
3. Register the instance in the `scrapers` list in `main.py`

No other files need to change.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and command list |
| `/jobs` | List all scraped jobs |
| `/jobs <source>` | Filter by source (e.g. `/jobs dummy`) |

## Testing

```bash
uv run pytest -v
```
