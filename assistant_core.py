import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from serpapi import GoogleSearch
import requests
from bs4 import BeautifulSoup

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# Шаг 1 — генерация task_prompt и query
def generate_task_prompt_and_query(user_input: str) -> tuple[str, str, dict]:
    system_msg = (
        "You are an assistant that receives a user's task description."
    "Split it into three things:"
    "1. A 'task_prompt' — a clear instruction for GPT to format and present the result later."
    "2. A 'search_query' — the exact web search query to use (in English)."
    "A 'search_query' — the exact web search query to use (in English)."
    "- If the user mentions 'last 24 hours', 'today', or 'recent', preserve these expressions in the search query without modifying or replacing them."
    "- Use natural English phrases like 'for the last 24 hours' or 'recent' if the user requests fresh or up-to-date information"
    "3. A 'schedule' — when to run the task, including:"
    "- 'type': one of ['daily', 'weekly', 'monthly', 'once']"
   "- if 'daily': provide 'hour' (0–23), 'minute' (0–59), and 'timezone' (IANA format, e.g., 'US/Eastern')"
    "- if 'weekly': provide 'day_of_week' (e.g., 'tuesday'), 'hour' (0–23), 'minute' (0–59), and 'timezone'"
   "- if 'monthly': provide 'day' (1–31), 'hour' (0–23), 'minute' (0–59), and 'timezone'"
   "- if 'once': provide 'datetime' in ISO 8601 format (e.g., '2025-07-29T08:00:00') and 'timezone'"

    "Respond ONLY in valid JSON with keys: 'task_prompt', 'search_query', and 'schedule'."
    "If user asks for reminder without link and without web search, set 'search_query': null"

    "- All times must be specified in 24-hour format (0–23 for hours, 0–59 for minutes)."
    "- Always include 'timezone' for correct scheduling."

    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_input}
    ]

    response = client.chat.completions.create(
        model="gpt-4",
        messages=messages,
        temperature=0.3
    )

    content = response.choices[0].message.content

    try:
        parsed = json.loads(content)
        return parsed["task_prompt"], parsed["search_query"], parsed["schedule"]
    except Exception as e:
        raise ValueError(f"❌ Error JSON-reply GPT: {e}\nAnswer was:\n{content}")


# Шаг 2 — web search

def web_search(query: str) -> str:
    USE_SERPAPI = True  #  вот сюда добавляем флажок
    def try_serpapi():
        if not USE_SERPAPI:
            #print(" SerpAPI is disabled, switching to DuckDuckGo...")
            return None  # Заглушка для тестов: всегда None
        params = {
            "engine": "google",
            "q": query,
            "api_key": SERPAPI_KEY
        }
        try:
            #print("Search via SerpAPI...")
            search = GoogleSearch(params)
            return extract_serpapi_results(search.get_dict())
        except Exception as e:
            #print(f" SerpAPI error: {e}")
            return None

    def extract_serpapi_results(results):
        links = []
        for r in results.get("organic_results", [])[:10]:
            title = r.get("title")
            link = r.get("link")
            if title and link:
                links.append(f"[{title}]({link})")
        return "\n".join(links) if links else "No results found."

    def try_duckduckgo():
        #print("Search DuckDuckGo...")
        url = "https://html.duckduckgo.com/html/"
        params = {"q": query}
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            response = requests.post(url, data=params, headers=headers, timeout=5)
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for result in soup.select("a.result__a")[:10]:
                title = result.get_text(strip=True)
                href = result["href"]
                results.append(f"[{title}]({href})")
            return "\n".join(results) if results else "No results found."
        except Exception as e:
            return f"❌ DuckDuckGo failed: {e}"

    serp_result = try_serpapi()
    return serp_result if serp_result else try_duckduckgo()



# Шаг 3 — оформление результата через GPT
def format_result_via_gpt(task_prompt: str, raw_result: str) -> str:
    system_msg = (
    "You are NOT performing the web search — the result is already done."
    "Your job is to pass along the entire result as-is, without filtering, editing, or skipping any lines."
    "If the result is empty or says “no results,” simply explain this to the user kindly."
    "Respond as if you’re the user’s AI assistant on Telegram."
    "Include 'search_query' into greeting, for example 'Hello, here is your search results for Dark Matter'"


)


    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": task_prompt},
        {"role": "user", "content": raw_result}
    ]

    response = client.chat.completions.create(
        model="gpt-4",
        messages=messages,
        temperature=0.3
    )

    return response.choices[0].message.content
