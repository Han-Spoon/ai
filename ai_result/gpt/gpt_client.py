import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()
load_dotenv(Path(__file__).parents[1] / ".env")

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version="2025-04-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)


def ask_gpt_json(prompt: dict) -> dict:
    response = client.chat.completions.create(
        model="gpt-5.4-nano",
        messages=[
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
    )
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        raise ValueError(f"GPT 응답이 JSON이 아닙니다: {e}\n{response.choices[0].message.content}")
