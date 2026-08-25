import os
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not TAVILY_API_KEY:
    raise RuntimeError("TAVILY_API_KEY not found in .env")

client = TavilyClient(api_key=TAVILY_API_KEY)


def web_search(query: str) -> str:
    """
    Search the live web and return concise, useful results.
    """

    if not query or not query.strip():
        return "Search query is empty."

    try:
        response = client.search(
            query=query.strip(),
            search_depth="advanced",
            max_results=5,
            include_answer=True
        )

        answer = response.get("answer")

        results = response.get("results", [])

        output = []

        if answer:
            output.append(f"SUMMARY:\n{answer}")

        for result in results:
            title = result.get("title", "")
            url = result.get("url", "")
            content = result.get("content", "")

            output.append(
                f"TITLE: {title}\n"
                f"URL: {url}\n"
                f"INFO: {content}"
            )

        if not output:
            return "No useful web results found."

        return "\n\n".join(output)

    except Exception as e:
        return f"Web search failed: {e}"