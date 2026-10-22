from datetime import datetime, timezone
from urllib.parse import urlparse

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.core.openrouter import create_openrouter_model
from app.news.models import (
    NewsArticle,
    NewsSummaryDraft,
    NewsSummaryResponse,
)

NEWS_SUMMARY_SYSTEM_PROMPT = """\
You write concise market intelligence for a Dubai real estate dashboard.
Use only the supplied recent headlines. Do not invent facts.

Return two sections:
- macro: central banks, rates, inflation, oil, GDP, policy, banking, capital markets, regional economy.
- real_estate: property markets, developers, rents, projects, planning, regulations, construction, Dubai/UAE real estate.

Ignore self-help, lifestyle, career advice, personal finance advice, generic investing tips, and unrelated noise.
Keep each section to 2-4 bullets. Each bullet must be direct, newsy, and under 24 words.
If there is not enough signal for a section, return one bullet saying there is no clear recent signal.
Do not use markdown.
"""

_news_summary_agent: Agent[None, NewsSummaryDraft] | None = None


def get_news_summary_agent() -> Agent[None, NewsSummaryDraft]:
    global _news_summary_agent
    if _news_summary_agent is None:
        _news_summary_agent = Agent(
            create_openrouter_model(),
            name="news_summary",
            output_type=NewsSummaryDraft,
            model_settings={"temperature": 0.1},
            system_prompt=NEWS_SUMMARY_SYSTEM_PROMPT,
        )
    return _news_summary_agent


def article_source(url: str) -> str:
    try:
        hostname = urlparse(url).hostname
        return hostname.replace("www.", "") if hostname else "source"
    except Exception:
        return "source"


def article_prompt_line(article: NewsArticle, index: int) -> str:
    publish_date = article.publish_date
    if publish_date is not None and publish_date.tzinfo is None:
        publish_date = publish_date.replace(tzinfo=timezone.utc)
    date_label = publish_date.date().isoformat() if publish_date else "undated"
    return f"{index}. [{date_label}] {article.headline} ({article_source(article.url)})"


def fallback_news_summary(articles: list[NewsArticle]) -> NewsSummaryResponse:
    real_estate_keywords = (
        "property",
        "real estate",
        "developer",
        "rent",
        "housing",
        "villa",
        "apartment",
        "dubai",
        "construction",
        "project",
    )
    macro_keywords = (
        "rate",
        "inflation",
        "oil",
        "gdp",
        "bank",
        "market",
        "economy",
        "capital",
        "investment",
    )

    def matching_bullets(keywords: tuple[str, ...], fallback: str) -> list[str]:
        bullets: list[str] = []
        for article in articles:
            headline = article.headline.strip()
            if not headline:
                continue
            lowered = headline.lower()
            if any(keyword in lowered for keyword in keywords):
                bullets.append(headline)
            if len(bullets) == 4:
                break
        return bullets or [fallback]

    return NewsSummaryResponse(
        macro={
            "title": "Macro",
            "bullets": matching_bullets(
                macro_keywords,
                "No clear recent macro signal is available from the current news feed.",
            ),
        },
        real_estate={
            "title": "Real estate",
            "bullets": matching_bullets(
                real_estate_keywords,
                "No clear recent real estate signal is available from the current news feed.",
            ),
        },
        generated_at=datetime.now(timezone.utc),
        article_count=len(articles),
    )


async def summarize_articles(articles: list[NewsArticle]) -> NewsSummaryResponse:
    if not articles:
        return NewsSummaryResponse(
            macro={
                "title": "Macro",
                "bullets": ["No recent macro signal is available from the current news feed."],
            },
            real_estate={
                "title": "Real estate",
                "bullets": [
                    "No recent real estate signal is available from the current news feed."
                ],
            },
            generated_at=datetime.now(timezone.utc),
            article_count=0,
        )

    prompt = (
        "Summarise these recent headlines for a dashboard user. "
        "Ignore personal finance advice, self-help, lifestyle, and generic tips.\n\n"
        + "\n".join(
            article_prompt_line(article, index) for index, article in enumerate(articles, start=1)
        )
    )

    try:
        result = await get_news_summary_agent().run(
            prompt,
            usage_limits=UsageLimits(request_limit=1),
        )
    except Exception:
        return fallback_news_summary(articles)

    return NewsSummaryResponse(
        macro=result.output.macro,
        real_estate=result.output.real_estate,
        generated_at=datetime.now(timezone.utc),
        article_count=len(articles),
    )
