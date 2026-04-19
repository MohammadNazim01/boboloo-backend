import requests
import time
import random

API_URL = "http://127.0.0.1:8000/api/v1/toy/runtime/ask"

TOY_API_KEY = ""


questions = [
    "why sky blue",
    "why moon follow us",
    "why star sparkle",
    "how birds fly",
    "tell me story",
    "why water wet"
]


def ask_toy():

    question = random.choice(questions)

    payload = {
        "question": question,
        "battery_level": random.randint(40,100),
        "wifi_signal": random.randint(-80,-40)
    }

    headers = {
        "x-toy-key": TOY_API_KEY
    }

    r = requests.post(API_URL, json=payload, headers=headers)

    print("QUESTION:", question)
    print("STATUS:", r.status_code)

    try:
        print("RESPONSE:", r.json())
    except:
        print("RAW RESPONSE:", r.text)

    print("-" * 40)


while True:
    ask_toy()
    time.sleep(3)