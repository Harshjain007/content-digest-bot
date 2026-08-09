"""GitHub repo + README fetcher using the public REST API.

Returns a dict with metadata and raw README text. Anonymous requests are
rate-limited to 60/hr; that's plenty for personal use. The bot falls back
gracefully if the API is unavailable.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

API = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json",
            "User-Agent": "content-digest-bot"}


def _owner_repo(url):
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)", url)
    if not m:
        return None, None
    owner, repo = m.group(1), m.group(2)
    repo = repo.split("/")[0].replace(".git", "")
    return owner, repo


def is_github_url(url):
    return bool(url) and "github.com" in url


def fetch_repo(url):
    """Return dict: {owner, repo, full_name, description, homepage,
    html_url, readme, stars, language} or None on failure."""
    owner, repo = _owner_repo(url)
    if not owner:
        return None
    try:
        r = requests.get(f"{API}/repos/{owner}/{repo}", headers=_HEADERS,
                         timeout=30)
        if r.status_code != 200:
            logger.warning("GitHub repo API %s: %s", r.status_code, url)
            return None
        j = r.json()
        readme = ""
        rr = requests.get(f"{API}/repos/{owner}/{repo}/readme",
                          headers={**_HEADERS,
                                   "Accept": "application/vnd.github.raw+json"},
                          timeout=30)
        if rr.status_code == 200:
            readme = rr.text
        return {
            "owner": owner,
            "repo": repo,
            "full_name": j.get("full_name"),
            "description": j.get("description") or "",
            "homepage": j.get("homepage") or "",
            "html_url": j.get("html_url"),
            "stars": j.get("stargazers_count", 0),
            "language": j.get("language") or "",
            "readme": readme[:40000],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("GitHub fetch failed for %s: %s", url, e)
        return None
