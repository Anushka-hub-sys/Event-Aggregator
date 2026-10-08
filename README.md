# Event Aggregator

A mini-project that centralises technical events — hackathons, GDG meetups,
coding contests and Unstop challenges — into one searchable dashboard.

## Tech stack
| Layer      | Choice                              |
|------------|-------------------------------------|
| Backend    | Python 3.11+, FastAPI               |
| Database   | SQLite via SQLAlchemy 2.0           |
| Scraping   | httpx + BeautifulSoup4              |
| Notifier   | Discord Webhooks                    |
| Frontend   | Vanilla HTML / CSS / JS             |

## Setup

```bash
git clone <your-repo-url>
cd event-aggregator

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env             # then paste your Discord webhook URL

uvicorn backend.main:app --reload