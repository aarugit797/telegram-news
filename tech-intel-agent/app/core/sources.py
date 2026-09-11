"""
The content catalogue: where we fetch from.

These are plain module constants, deliberately NOT part of Settings.
A fetch URL is not a secret and differs per environment only by accident,
so making it environment-overridable would turn "which host do we talk to"
into remotely-influenceable state for no benefit. Changing a source is a
code change, reviewed like any other.
"""

# --- API endpoints ---------------------------------------------------------

HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
ARXIV_API_URL = "https://export.arxiv.org/api/query"


# --- Feeds -----------------------------------------------------------------

BLOG_FEEDS = {
    "Anthropic": "https://rsshub.bestblogs.dev/anthropic/news",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google DeepMind": "https://deepmind.google/blog/feed/basic/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
}

# NOTE: TLDR AI, The Batch and ByteByteGo were intentionally left out.
# Their feed URLs were never verified, and a wrong URL here fails silently
# on every run - feedparser returns an empty entry list rather than raising,
# so a typo'd source looks exactly like a source with no new items.
# Verify a feed actually parses before adding it.
NEWSLETTER_FEEDS = {
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    "Latent.Space": "https://latent.space/feed",
}
