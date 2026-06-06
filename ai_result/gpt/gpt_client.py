import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def ask_gpt_json(prompt: dict) -> dict:
    client = OpenAI()
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.4-nano"),
        messages=[
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
    )
    return json.loads(response.choices[0].message.content)
